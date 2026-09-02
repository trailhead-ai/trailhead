"""Tests for ranger.sweep.queue — sweep queue derivation and classification.

Test contract:
- Shape gate: parented and childed tasks are excluded even when their status
  matches, mirroring refine's standalone gate.
- Each of the four buckets (dispatchable, escalated-awaiting-operator,
  blocked-answered, blocked-still-waiting) is produced from a fixture vault.
- An `open` task carrying an *answered* escalation section is dispatchable
  (the answer re-entry path).
- Answered predicate: a `**Answer:**` line inside the `## Refine —
  unresolved` section counts; the same line elsewhere in the body does not;
  unrelated body/timestamp/status edits alone do not change the predicate;
  the heading is matched exactly (a wrapped or en-dash heading does not
  match).
- Near-miss detection: a case-variant answer line inside the section, or an
  exact `**Answer:**` line outside the section, sets `answer_near_miss` while
  leaving the task guarded/waiting; an exact in-section match sets no flag.
- Ordering is oldest-first by `created-at` with a record-name tiebreak, and
  is stable regardless of the raw listing order.
- The lore CLI runner is injectable, and a nonzero exit from either `lore
  task list` or `lore record show` surfaces as `queue.QueueDeriveError`, not
  a crash.
- Every lore read names the elected vault explicitly: the stub runner below
  refuses a `record show` that omits `--vault <elected>`, so no test can
  pass while the derivation reads whichever vault lore's config lists first.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from ranger.sweep import queue  # noqa: E402

_VAULT = "myvault"


def _task_entry(
    name: str,
    status: str,
    *,
    created_at: str = "2026-01-01T00:00:00Z",
    updated_at: str = "2026-01-01T00:00:00Z",
    parent: str | None = None,
    depends_on: list[str] | None = None,
    children: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "status": status,
        "created-at": created_at,
        "updated-at": updated_at,
        "parent": parent,
        "depends-on": list(depends_on or []),
        "children": list(children or []),
    }


def _make_runner(*, entries=None, bodies=None, list_rc=0, list_stderr="", show_rc_overrides=None):
    """A fake `lore` CLI runner: dispatches on argv shape, ignores real subprocess.

    `entries` backs `lore task list --vault ... --json`; `bodies` (a
    name -> body-text map) backs `lore record show task/<name> --vault ...
    --json`. `show_rc_overrides` (name -> (rc, stderr)) lets a test force one
    `record show` call to fail without touching the others.

    Both lore reads must name the elected vault, and the stub enforces it: a
    bare `record show` is located by a cwd-blind first-match scan across the
    configured vaults in declaration order, so a task name that collides
    across two vaults would be classified from the wrong body entirely.
    """
    entries = entries if entries is not None else []
    bodies = bodies if bodies is not None else {}
    show_rc_overrides = show_rc_overrides or {}

    def _assert_targets_the_elected_vault(cmd):
        assert "--vault" in cmd, f"lore read must target a vault explicitly: {cmd!r}"
        assert cmd[cmd.index("--vault") + 1] == _VAULT, f"wrong vault targeted: {cmd!r}"

    def runner(cmd, **kwargs):
        if cmd[:3] == ["lore", "task", "list"]:
            _assert_targets_the_elected_vault(cmd)
            stdout = json.dumps(entries) if list_rc == 0 else ""
            return subprocess.CompletedProcess(cmd, list_rc, stdout=stdout, stderr=list_stderr)
        if cmd[:3] == ["lore", "record", "show"]:
            _assert_targets_the_elected_vault(cmd)
            record_id = cmd[3]
            name = record_id.split("/", 1)[1]
            rc, stderr = show_rc_overrides.get(name, (0, ""))
            if rc != 0:
                return subprocess.CompletedProcess(cmd, rc, stdout="", stderr=stderr)
            payload = {
                "record_id": record_id,
                "kind": "task",
                "name": name,
                "sidecar": {},
                "body": bodies.get(name, "# t\n\nSome prose.\n"),
            }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        raise AssertionError(f"unexpected cmd: {cmd!r}")

    return runner


def _no_section_body() -> str:
    return "# a task\n\nSome captured prose. Nothing escalated here.\n"


def _unresolved_body(*, answer_line: str | None = None, extra_before_heading: str = "") -> str:
    lines = [
        "# a task\n",
        "\n",
        extra_before_heading,
        "## Refine — unresolved\n",
        "\n",
        "**Question:** decide the surviving thing?\n",
        "\n",
    ]
    if answer_line is not None:
        lines.append(f"{answer_line}\n")
        lines.append("\n")
    lines.append("**Evidence gathered:** some/file.py:12\n")
    return "".join(line for line in lines if line != "")


# ---------------------------------------------------------------------------
# Shape gate
# ---------------------------------------------------------------------------


def test_excludes_parented_and_childed_tasks_even_when_status_matches():
    entries = [
        _task_entry("standalone-open", "open"),
        _task_entry("has-parent", "open", parent="some-parent"),
        _task_entry("has-children", "blocked", children=["some-child"]),
    ]
    runner = _make_runner(entries=entries, bodies={"standalone-open": _no_section_body()})

    result = queue.derive_queue(_VAULT, runner=runner)

    names = {e["name"] for e in result}
    assert names == {"standalone-open"}


def test_assumption_probe_blocked_child_of_in_progress_parent_is_excluded():
    """ASSUMPTION PROBE (ephemeral) — see the-driver-escalation-contract task.

    A `blocked` task carrying a `parent` edge (as the driver's escalation
    contract would write) must never surface in the refine sweep's queue,
    regardless of its status or its parent's status.
    """
    entries = [
        _task_entry("standalone-open", "open"),
        _task_entry("escalation-child", "blocked", parent="in-progress-slice-parent"),
    ]
    runner = _make_runner(entries=entries, bodies={"standalone-open": _no_section_body()})

    result = queue.derive_queue(_VAULT, runner=runner)

    names = {e["name"] for e in result}
    assert "escalation-child" not in names
    assert names == {"standalone-open"}


# ---------------------------------------------------------------------------
# The four buckets
# ---------------------------------------------------------------------------


def test_open_with_no_escalation_is_dispatchable():
    entries = [_task_entry("t1", "open")]
    runner = _make_runner(entries=entries, bodies={"t1": _no_section_body()})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "dispatchable"
    assert result[0]["answer_near_miss"] is False


def test_open_with_unanswered_escalation_is_escalated_awaiting_operator():
    entries = [_task_entry("t1", "open")]
    runner = _make_runner(entries=entries, bodies={"t1": _unresolved_body()})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "escalated-awaiting-operator"
    assert result[0]["answer_near_miss"] is False


def test_blocked_with_answer_is_blocked_answered():
    entries = [_task_entry("t1", "blocked")]
    runner = _make_runner(entries=entries, bodies={"t1": _unresolved_body(answer_line="**Answer:** go with X")})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "blocked-answered"
    assert result[0]["answer_near_miss"] is False


def test_blocked_without_answer_is_blocked_still_waiting():
    entries = [_task_entry("t1", "blocked")]
    runner = _make_runner(entries=entries, bodies={"t1": _unresolved_body()})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "blocked-still-waiting"


def test_open_with_answered_escalation_is_dispatchable_answer_reentry():
    entries = [_task_entry("t1", "open")]
    runner = _make_runner(entries=entries, bodies={"t1": _unresolved_body(answer_line="**Answer:** go with X")})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "dispatchable"
    assert result[0]["answer_near_miss"] is False


# ---------------------------------------------------------------------------
# Answered predicate specifics
# ---------------------------------------------------------------------------


def test_answer_line_elsewhere_in_body_does_not_count_as_answered():
    body = "**Answer:** stray, not in the section\n\n" + _unresolved_body()
    entries = [_task_entry("t1", "blocked")]
    runner = _make_runner(entries=entries, bodies={"t1": body})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "blocked-still-waiting"
    assert result[0]["answer_near_miss"] is True


def test_unrelated_body_edit_does_not_change_classification():
    body_v1 = _unresolved_body()
    body_v2 = _unresolved_body() + "\n\nA later, unrelated edit to the prose.\n"
    entries = [_task_entry("t1", "open")]

    result_v1 = queue.derive_queue(_VAULT, runner=_make_runner(entries=entries, bodies={"t1": body_v1}))
    result_v2 = queue.derive_queue(_VAULT, runner=_make_runner(entries=entries, bodies={"t1": body_v2}))

    assert result_v1[0]["bucket"] == result_v2[0]["bucket"] == "escalated-awaiting-operator"


def test_timestamp_and_status_alone_do_not_satisfy_the_answer_predicate():
    entries = [_task_entry("t1", "open", updated_at="2026-06-01T00:00:00Z")]
    runner = _make_runner(entries=entries, bodies={"t1": _unresolved_body()})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "escalated-awaiting-operator"


def test_wrapped_heading_does_not_match():
    body = (
        "# a task\n\n"
        "## Refine —\n"
        "unresolved\n\n"
        "**Question:** decide?\n"
    )
    entries = [_task_entry("t1", "open")]
    runner = _make_runner(entries=entries, bodies={"t1": body})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "dispatchable"


def test_en_dash_heading_does_not_match():
    body = (
        "# a task\n\n"
        "## Refine – unresolved\n\n"  # en dash, not the em dash the heading requires
        "**Question:** decide?\n"
    )
    entries = [_task_entry("t1", "open")]
    runner = _make_runner(entries=entries, bodies={"t1": body})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "dispatchable"


# ---------------------------------------------------------------------------
# Near-miss detection
# ---------------------------------------------------------------------------


def test_case_variant_no_bold_inside_section_is_a_near_miss():
    entries = [_task_entry("t1", "open")]
    runner = _make_runner(entries=entries, bodies={"t1": _unresolved_body(answer_line="Answer: go with X")})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "escalated-awaiting-operator"
    assert result[0]["answer_near_miss"] is True


def test_case_variant_lowercase_bold_inside_section_is_a_near_miss():
    entries = [_task_entry("t1", "blocked")]
    runner = _make_runner(entries=entries, bodies={"t1": _unresolved_body(answer_line="**answer:** go with X")})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "blocked-still-waiting"
    assert result[0]["answer_near_miss"] is True


def test_exact_answer_outside_section_is_a_near_miss():
    # A later `##` heading closes the unresolved section, so the `**Answer:**`
    # line after it is genuinely outside the section's bounds, not just
    # trailing content with nothing to close it.
    body = _unresolved_body() + "\n\n## Unrelated later section\n\n**Answer:** trailing, outside the section\n"
    entries = [_task_entry("t1", "blocked")]
    runner = _make_runner(entries=entries, bodies={"t1": body})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "blocked-still-waiting"
    assert result[0]["answer_near_miss"] is True


def test_exact_match_inside_section_sets_no_near_miss_flag():
    entries = [_task_entry("t1", "blocked")]
    runner = _make_runner(entries=entries, bodies={"t1": _unresolved_body(answer_line="**Answer:** go with X")})

    result = queue.derive_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "blocked-answered"
    assert result[0]["answer_near_miss"] is False


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_ordering_is_oldest_first_by_created_at_with_name_tiebreak():
    entries = [
        _task_entry("zeta", "open", created_at="2026-01-02T00:00:00Z"),
        _task_entry("beta", "open", created_at="2026-01-01T00:00:00Z"),
        _task_entry("alpha", "open", created_at="2026-01-01T00:00:00Z"),
    ]
    bodies = {name: _no_section_body() for name in ("zeta", "beta", "alpha")}
    runner = _make_runner(entries=entries, bodies=bodies)

    result = queue.derive_queue(_VAULT, runner=runner)

    assert [e["name"] for e in result] == ["alpha", "beta", "zeta"]


def test_ordering_is_stable_regardless_of_raw_listing_order():
    entries_forward = [
        _task_entry("alpha", "open", created_at="2026-01-01T00:00:00Z"),
        _task_entry("beta", "open", created_at="2026-01-01T00:00:00Z"),
    ]
    entries_reversed = list(reversed(entries_forward))
    bodies = {name: _no_section_body() for name in ("alpha", "beta")}

    result_forward = queue.derive_queue(_VAULT, runner=_make_runner(entries=entries_forward, bodies=bodies))
    result_reversed = queue.derive_queue(_VAULT, runner=_make_runner(entries=entries_reversed, bodies=bodies))

    assert [e["name"] for e in result_forward] == [e["name"] for e in result_reversed] == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Runner injection + error surfacing
# ---------------------------------------------------------------------------


def test_read_body_targets_the_named_vault_explicitly():
    """`lore record show` without `--vault` scans vaults in config order.

    The scan is cwd-blind, so a task name present in two configured vaults is
    read from whichever one lore's config happens to declare first — and the
    sweep would then classify, and extract an escalated question from, a
    record belonging to someone else's camp group.
    """
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        payload = {"record_id": cmd[3], "kind": "task", "name": "t1", "sidecar": {}, "body": ""}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    queue.read_body("t1", vault=_VAULT, runner=runner)

    assert calls == [["lore", "record", "show", "task/t1", "--vault", _VAULT, "--json"]]


def test_lore_task_list_failure_raises_named_error_not_a_crash():
    runner = _make_runner(list_rc=1, list_stderr="lore: vault 'myvault' is not configured")

    with pytest.raises(queue.QueueDeriveError, match="myvault"):
        queue.derive_queue(_VAULT, runner=runner)


def test_absent_lore_cli_raises_a_named_error_with_remediation(monkeypatch, tmp_path):
    """`lore` missing from PATH must name itself, not raise FileNotFoundError.

    The sweep's runtime startup checks are its only dependency guard, and every
    one of them shells out through here — so an uninstalled (or unreachable)
    `lore` has to arrive at the CLI as a named error with a remediation, the
    same shape as every other precondition failure, rather than as a traceback.
    """
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(queue.QueueDeriveError) as exc:
        queue.derive_queue(_VAULT)

    assert "lore CLI not found on PATH" in str(exc.value)
    assert "install lore or adjust PATH" in str(exc.value)


def test_unrunnable_lore_cli_raises_a_named_error_not_a_crash(monkeypatch, tmp_path):
    """A `lore` on PATH that the OS refuses to exec is the same class of failure."""
    fake = tmp_path / "lore"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o600)  # present, but not executable
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(queue.QueueDeriveError) as exc:
        queue.derive_queue(_VAULT)

    assert "lore CLI could not be run" in str(exc.value)


def test_lore_record_show_failure_raises_named_error_not_a_crash():
    entries = [_task_entry("t1", "open")]
    runner = _make_runner(
        entries=entries,
        show_rc_overrides={"t1": (1, "error: no task named 't1'")},
    )

    with pytest.raises(queue.QueueDeriveError, match="t1"):
        queue.derive_queue(_VAULT, runner=runner)


# ---------------------------------------------------------------------------
# actionable() — the loop's own view of what is left
# ---------------------------------------------------------------------------


def test_actionable_keeps_only_the_buckets_the_loop_dispatches():
    entries = [
        {"name": "a", "bucket": "dispatchable"},
        {"name": "b", "bucket": "escalated-awaiting-operator"},
        {"name": "c", "bucket": "blocked-answered"},
        {"name": "d", "bucket": "blocked-still-waiting"},
    ]

    assert [e["name"] for e in queue.actionable(entries)] == ["a", "c"]


def test_actionable_covers_every_bucket_exactly_once():
    """The partition must stay total: a new bucket has to be classified here.

    `actionable` and its complement are how the loop decides what to dispatch
    and what to report-and-leave. A bucket in neither set is one the sweep
    would silently never act on and never mention.
    """
    entries = [{"name": b, "bucket": b} for b in queue.BUCKETS]
    kept = {e["bucket"] for e in queue.actionable(entries)}

    assert kept == set(queue.ACTIONABLE_BUCKETS)
    assert kept | (set(queue.BUCKETS) - kept) == set(queue.BUCKETS)
    assert set(queue.ACTIONABLE_BUCKETS) <= set(queue.BUCKETS), (
        "an actionable bucket that derivation never produces is dead configuration"
    )


def test_actionable_preserves_derivation_order():
    """Order is the loop's queue order — oldest first — and must survive the filter."""
    entries = [
        {"name": "old", "bucket": "dispatchable"},
        {"name": "mid", "bucket": "blocked-answered"},
        {"name": "new", "bucket": "dispatchable"},
    ]

    assert [e["name"] for e in queue.actionable(entries)] == ["old", "mid", "new"]


def test_actionable_is_a_filter_not_a_mutation():
    """The caller may still need the unfiltered list (§2.5 records from it)."""
    entries = [
        {"name": "a", "bucket": "dispatchable"},
        {"name": "b", "bucket": "blocked-still-waiting"},
    ]

    result = queue.actionable(entries)

    assert len(entries) == 2, "the input list must not be mutated in place"
    assert result[0] is entries[0], "entries are passed through, not copied and diverged"
