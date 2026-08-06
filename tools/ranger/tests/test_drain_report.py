"""Tests for ``ranger.drain.report`` — the durable drain exit report and
outcome/cap substrate.

Test contract:
- Outcome parse: four tokens, each with a mandatory argument; first-line-only
  parsing; missing/empty outcome file synthesizes a FAILED line
  (``read_outcome``, reused from ``ranger.sweep.report``); task-id
  confinement (``outcome_path``, reused as-is).
- Report: idempotent re-append; credential scrub applied to untrusted text;
  a corrupt ``.state.json`` is refused by name, naming the held lock.
- Cap accounting: the in-flight set grows on ``mark_in_flight`` and shrinks
  on either a monitor-terminal outcome (``resolve_monitor_outcome``) or a
  deadline expiry (``expire_in_flight``, which buckets ``monitor-timeout``
  and preserves the workspace). The count survives a process restart (a
  fresh state-file read alone). Degraded mode has no in-flight bucketing.
- Pushed-bucket split: merged / in-flight / awaiting-human-approval /
  monitor-timeout each render a distinct line shape; awaiting-approval
  carries the ``gh pr edit ... --add-label human-approved`` command;
  cap-blocking entries are flagged.
- Still-standing workspaces render one ``camp remove <slug>`` line each;
  the degraded banner renders iff the drain ran degraded; an empty queue
  renders a clean, valid, no-op report.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_RANGER_PLUGIN_DIR = _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger"

if str(_RANGER_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_RANGER_PLUGIN_DIR))

from ranger.drain import report  # noqa: E402


def _env(tmp_path: Path) -> dict[str, str]:
    return {"RANGER_STATE_DIR": str(tmp_path / "state")}


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# Outcome grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", sorted(report.DRAIN_OUTCOME_TOKENS))
def test_each_token_requires_its_argument(token):
    assert report.parse_drain_outcome(token) == (None, token)
    parsed, arg = report.parse_drain_outcome(f"{token} some-arg")
    assert parsed == token
    assert arg == "some-arg"


def test_pushed_carries_its_full_three_field_argument():
    parsed, arg = report.parse_drain_outcome(
        "PUSHED craft/some-task abc1234 1 file changed, 3 insertions(+)"
    )
    assert parsed == "PUSHED"
    assert arg == "craft/some-task abc1234 1 file changed, 3 insertions(+)"


def test_unrecognized_token_fails_to_parse():
    assert report.parse_drain_outcome("PROMOTED nope") == (None, "PROMOTED nope")


def test_empty_outcome_fails_to_parse():
    assert report.parse_drain_outcome("") == (None, "")


def test_only_the_first_line_is_parsed():
    token, arg = report.parse_drain_outcome("BLOCKED needs review\nextra commentary")
    assert token == "BLOCKED"
    assert arg == "needs review"


def test_missing_outcome_file_synthesizes_a_failed_line(tmp_path):
    report_path = report.start("g", "v", 0, env=_env(tmp_path))
    line = report.read_outcome(report_path, "task/nope")
    assert line.startswith("FAILED")


def test_task_id_confinement_is_reused_from_sweep_report(tmp_path):
    report_path = report.start("g", "v", 0, env=_env(tmp_path))
    with pytest.raises(report.ReportError):
        report.outcome_path(report_path, "../../etc/passwd")


# ---------------------------------------------------------------------------
# Idempotent append + credential scrub
# ---------------------------------------------------------------------------


def test_appending_the_same_task_id_twice_is_a_no_op(tmp_path):
    report_path = report.start("g", "v", 1, env=_env(tmp_path))

    report.append_failed(report_path, "task/a", "first reason")
    report.append_failed(report_path, "task/a", "second reason should be ignored")

    text = report_path.read_text()
    assert text.count("task/a") == 1
    assert "first reason" in text
    assert "second reason" not in text


def test_untrusted_text_is_credential_scrubbed(tmp_path):
    report_path = report.start("g", "v", 1, env=_env(tmp_path))

    report.append_blocked(report_path, "task/a", "leaked STRIPE_SECRET_KEY=sk_live_abc123")

    text = report_path.read_text()
    assert "sk_live_abc123" not in text
    assert "[REDACTED]" in text


def test_corrupt_state_file_is_refused_by_name_and_names_the_held_lock(tmp_path):
    report_path = report.start("g", "v", 0, env=_env(tmp_path))
    state_path = report_path.with_suffix(".state.json")
    state_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(report.ReportError) as exc:
        report.append_failed(report_path, "task/a", "reason")

    assert "unreadable JSON" in str(exc.value)
    assert "lock" in str(exc.value)
    assert state_path.read_text() == "{not json"


def test_report_and_state_are_created_0600(tmp_path):
    report_path = report.start("g", "v", 0, env=_env(tmp_path))
    assert _mode(report_path) == 0o600
    assert _mode(report_path.with_suffix(".state.json")) == 0o600


# ---------------------------------------------------------------------------
# Cap accounting
# ---------------------------------------------------------------------------


def test_in_flight_count_grows_on_mark_and_shrinks_on_monitor_terminal_outcome(tmp_path):
    report_path = report.start("g", "v", 1, env=_env(tmp_path))
    assert report.in_flight_count(report_path) == 0

    report.mark_in_flight(
        report_path, "task/a", branch="b", sha="s", diffstat="d", workspace="ws-a",
    )
    assert report.in_flight_count(report_path) == 1

    report.resolve_monitor_outcome(report_path, "task/a", "MERGED")
    assert report.in_flight_count(report_path) == 0


def test_in_flight_count_shrinks_on_deadline_expiry_and_buckets_monitor_timeout(tmp_path):
    report_path = report.start("g", "v", 1, env=_env(tmp_path))
    now = datetime.now(timezone.utc)
    report.mark_in_flight(
        report_path, "task/a", branch="b", sha="s", diffstat="d", workspace="ws-a",
        deadline_hours=1, now=now - timedelta(hours=2),
    )
    assert report.in_flight_count(report_path) == 1

    reclaimed = report.expire_in_flight(report_path, now=now)

    assert reclaimed == ["task/a"]
    assert report.in_flight_count(report_path) == 0
    text = report_path.read_text()
    assert "Monitor timeout" in text
    assert "ws-a" in text
    assert "preserved" in text


def test_in_flight_count_survives_a_fresh_report_object_reading_only_the_state_file(tmp_path):
    report_path = report.start("g", "v", 1, env=_env(tmp_path))
    report.mark_in_flight(
        report_path, "task/a", branch="b", sha="s", diffstat="d", workspace="ws-a",
    )

    # No object identity carried forward — a brand-new call against the same
    # path is the process-restart scenario.
    assert report.in_flight_count(Path(str(report_path))) == 1


def test_degraded_mode_never_bucketing_in_flight(tmp_path):
    report_path = report.start("g", "v", 0, degraded=True, env=_env(tmp_path))
    with pytest.raises(report.ReportError, match="degraded"):
        report.mark_in_flight(
            report_path,
            "task/a",
            branch="branch-a",
            sha="sha1",
            diffstat="1 file changed",
            workspace="ws-a",
        )
    assert report.in_flight_count(report_path) == 0
    reloaded = report._load_state(report_path)
    assert reloaded["in_flight"] == {}


# ---------------------------------------------------------------------------
# Pushed-bucket split
# ---------------------------------------------------------------------------


def test_merged_renders_a_distinct_line(tmp_path):
    report_path = report.start("g", "v", 1, env=_env(tmp_path))
    report.append_pushed_merged(report_path, "task/a", "branch-a", "sha1", "1 file changed")
    text = report_path.read_text()
    assert "### Merged" in text
    assert "branch-a" in text


def test_in_flight_renders_cap_blocking_flag(tmp_path):
    report_path = report.start("g", "v", 1, env=_env(tmp_path))
    report.mark_in_flight(
        report_path, "task/a", branch="branch-a", sha="sha1", diffstat="1 file changed",
        workspace="ws-a", cap_blocking=True,
    )
    text = report_path.read_text()
    assert "### In flight" in text
    assert "holding the concurrency cap" in text


def test_awaiting_human_approval_carries_the_gh_command(tmp_path):
    report_path = report.start("g", "v", 1, env=_env(tmp_path))
    report.append_pushed_awaiting_approval(
        report_path, "task/a", "branch-a", "sha1", "1 file changed",
        pr_url_or_number="https://github.com/org/repo/pull/7",
    )
    text = report_path.read_text()
    assert "### Awaiting human approval" in text
    assert "gh pr edit https://github.com/org/repo/pull/7 --add-label human-approved" in text


def test_monitor_timeout_preserves_workspace_and_renders_distinct_line(tmp_path):
    report_path = report.start("g", "v", 1, env=_env(tmp_path))
    report.append_pushed_monitor_timeout(
        report_path, "task/a", "branch-a", "sha1", "1 file changed", workspace="ws-a",
    )
    text = report_path.read_text()
    assert "### Monitor timeout" in text
    assert "ws-a" in text
    assert "preserved" in text


def test_each_pushed_substate_fixture_renders_a_distinct_line_shape(tmp_path):
    report_path = report.start("g", "v", 4, env=_env(tmp_path))
    report.append_pushed_merged(report_path, "task/merged", "b1", "s1", "d1")
    report.mark_in_flight(
        report_path, "task/inflight", branch="b2", sha="s2", diffstat="d2", workspace="ws2",
    )
    report.append_pushed_awaiting_approval(
        report_path, "task/approval", "b3", "s3", "d3", pr_url_or_number="42",
    )
    report.append_pushed_monitor_timeout(
        report_path, "task/timeout", "b4", "s4", "d4", workspace="ws4",
    )

    text = report_path.read_text()
    for heading in ("Merged", "In flight", "Awaiting human approval", "Monitor timeout"):
        assert f"### {heading}" in text
    assert "task/merged" in text
    assert "task/inflight" in text
    assert "task/approval" in text
    assert "task/timeout" in text


# ---------------------------------------------------------------------------
# Still-standing workspaces + degraded banner + empty queue
# ---------------------------------------------------------------------------


def test_still_standing_workspaces_render_one_camp_remove_line_each(tmp_path):
    report_path = report.start("g", "v", 0, env=_env(tmp_path))
    report.finish(report_path, still_standing=["ws-a", "ws-b"])
    text = report_path.read_text()
    assert "camp remove ws-a" in text
    assert "camp remove ws-b" in text


def test_degraded_banner_renders_only_when_drain_ran_degraded(tmp_path):
    degraded_path = report.start("g", "v", 0, degraded=True, env=_env(tmp_path))
    normal_path = report.start("g", "v", 0, degraded=False, env=_env(tmp_path))

    assert "degraded" in degraded_path.read_text().lower()
    assert "degraded" not in normal_path.read_text().lower()


def test_empty_queue_renders_a_clean_no_op_report(tmp_path):
    report_path = report.start("g", "v", 0, env=_env(tmp_path))
    report.finish(report_path)

    text = report_path.read_text()
    for bucket in report.BUCKETS:
        assert f"## {report._BUCKET_HEADINGS[bucket]}" in text
    assert "Report written to" in text
    # Valid, parseable JSON state alongside it.
    state = json.loads(report_path.with_suffix(".state.json").read_text())
    assert state["finished"] is True
