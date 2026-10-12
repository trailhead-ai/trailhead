"""Tests for ``lore record amend``.

``amend`` rewrites the body of an existing record in one step, appending a
``change-history`` entry (who, when, who asked, why, and the diff) and getting
past the ADR body freeze that plain ``record update`` enforces. Every test runs
the CLI against a temp vault and reads back what landed on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import (  # noqa: F401
    load_script,
    make_vault as _make_vault,
    run_cli as _run,
)

BODY = "# Decision\n\nline one\nline two\nline three\n"
AMENDED = "# Decision\n\nline one\nline TWO\nline three\n"


def _create(vault, state, kind, title, *, status=None, body=BODY, extra=None):
    args = ["record", "create", "--kind", kind, "--title", title, "--keyword", "k"]
    if status:
        args += ["--status", status]
    args += extra or []
    r = _run(args, vault=vault, state_dir=state, stdin_text=body)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _sidecar(vault: Path, rid: str) -> dict:
    kind, name = rid.split("/", 1)
    return json.loads((vault / kind / f"{name}.json").read_text(encoding="utf-8"))


def _body(vault: Path, rid: str) -> str:
    kind, name = rid.split("/", 1)
    return (vault / kind / f"{name}.md").read_text(encoding="utf-8")


def _snapshot(vault: Path) -> dict[str, bytes]:
    """Every record file under the vault, by relative path, as raw bytes."""
    return {
        str(p.relative_to(vault)): p.read_bytes()
        for p in sorted(vault.rglob("*"))
        if p.is_file() and p.suffix in (".md", ".json")
    }


def _amend(vault, state, rid, body, *, reason="fix wording", initiated_by="operator",
           diff=False, extra=None):
    """Run ``lore record amend`` with *body* on stdin.

    Pass ``reason=None`` / ``initiated_by=None`` to leave the flag off.
    """
    args = ["record", "amend", rid]
    if reason is not None:
        args += ["--reason", reason]
    if initiated_by is not None:
        args += ["--initiated-by", initiated_by]
    if diff:
        args.append("--diff")
    args += extra or []
    return _run(args, vault=vault, state_dir=state, stdin_text=body)


def _refused(vault, state, rid, r, *needles):
    """Assert a refusal: non-zero, a clean message naming each needle, nothing written."""
    assert r.returncode != 0, r.stdout
    assert "Traceback" not in r.stderr
    for needle in needles:
        assert needle in r.stderr, r.stderr


# ---------------------------------------------------------------------------
# AC: amend writes the body, past the ADR freeze
# ---------------------------------------------------------------------------


def test_amend_full_body_on_active_adr_writes_body_and_keeps_everything_else(tmp_path):
    vault, state = _make_vault(tmp_path)
    other = _create(vault, state, "adr", "Other Decision", status="draft", body="# Other\n")
    rid = _create(vault, state, "adr", "Original Decision", status="active",
                  extra=["--related", f"adr={other.split('/', 1)[1]}", "--label", "craft/team=core"])
    before_sidecar = _sidecar(vault, rid)
    before_files = set(_snapshot(vault))
    assert before_sidecar["related"] and before_sidecar["labels"]

    r = _amend(vault, state, rid, AMENDED)

    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == rid
    assert _body(vault, rid) == AMENDED
    after = _sidecar(vault, rid)
    for key in ("kind", "status", "labels", "related", "title", "created-at", "created-by"):
        assert after.get(key) == before_sidecar.get(key), key
    assert len(after["change-history"]) == 1
    assert set(_snapshot(vault)) == before_files


def test_plain_update_of_the_same_active_adr_is_refused_and_names_amend(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "adr", "Original Decision", status="active")
    before = _snapshot(vault)

    r = _run(["record", "update", rid], vault=vault, state_dir=state, stdin_text=AMENDED)

    assert r.returncode != 0
    assert "[adr-active-immutable]" in r.stderr
    assert "lore record amend" in r.stderr
    assert _snapshot(vault) == before


@pytest.mark.parametrize(
    "kind,status",
    [("spec", "ready"), ("decision", "active"), ("task", "done")],
)
def test_amend_works_on_other_kinds_keeping_status_and_links(tmp_path, kind, status):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, f"A {kind}", status=status,
                  extra=["--related", "area=somewhere"])
    before = _sidecar(vault, rid)

    r = _amend(vault, state, rid, AMENDED)

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == AMENDED
    after = _sidecar(vault, rid)
    assert after["status"] == status
    assert before["related"]
    assert after["related"] == before["related"]
    assert len(after["change-history"]) == 1


def test_amend_with_diff_applies_the_diff_to_the_stored_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "A Spec", status="ready")
    store = load_script("lore.record.store")
    diff = store.make_unified_diff(BODY, AMENDED)

    r = _amend(vault, state, rid, diff, diff=True)

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == AMENDED
    assert len(_sidecar(vault, rid)["change-history"]) == 1


# ---------------------------------------------------------------------------
# AC: the change-history entries
# ---------------------------------------------------------------------------


def test_two_amends_append_two_ordered_entries_with_their_own_attestation(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "adr", "Original Decision", status="active")
    second = "# Decision\n\nline one\nline TWO\nline three\nline four\n"

    assert _amend(vault, state, rid, AMENDED, reason="first reason",
                  initiated_by="operator").returncode == 0
    assert _amend(vault, state, rid, second, reason="second reason",
                  initiated_by="agent").returncode == 0

    history = _sidecar(vault, rid)["change-history"]
    assert [(e["reason"], e["initiated-by"]) for e in history] == [
        ("first reason", "operator"),
        ("second reason", "agent"),
    ]
    assert [e["by"] for e in history] == ["tester@example.com"] * 2
    for entry in history:
        assert entry["at"].endswith("Z") and len(entry["at"]) == len("2026-01-01T00:00:00Z")
    assert history[0]["at"] <= history[1]["at"]


def test_each_stored_diff_turns_the_previous_body_into_the_next(tmp_path):
    vault, state = _make_vault(tmp_path)
    store = load_script("lore.record.store")
    rid = _create(vault, state, "adr", "Original Decision", status="active")
    second = "# Decision\n\nline one\nline TWO\nline three\nline four\n"

    _amend(vault, state, rid, AMENDED, reason="one")
    _amend(vault, state, rid, second, reason="two")

    history = _sidecar(vault, rid)["change-history"]
    step1, _ = store.apply_unified_diff(BODY, history[0]["diff"])
    assert step1 == AMENDED
    step2, _ = store.apply_unified_diff(step1, history[1]["diff"])
    assert step2 == second == _body(vault, rid)


def test_diff_is_computed_between_the_stored_fence_neutralized_bodies(tmp_path):
    vault, state = _make_vault(tmp_path)
    store = load_script("lore.record.store")
    fenced = "# Note\n\nsee <external-memory>x</external-memory> here\nend\n"
    rid = _create(vault, state, "spec", "Fenced", status="ready", body="# Note\n\nplain\nend\n")
    stored_before = _body(vault, rid)

    r = _amend(vault, state, rid, fenced)

    assert r.returncode == 0, r.stderr
    stored_after = _body(vault, rid)
    assert stored_after != fenced  # the write neutralized the live fence
    diff = _sidecar(vault, rid)["change-history"][0]["diff"]
    assert "<external-memory>" not in diff
    assert diff == store.make_unified_diff(stored_before, stored_after)
    rebuilt, _ = store.apply_unified_diff(stored_before, diff)
    assert rebuilt == stored_after


# ---------------------------------------------------------------------------
# AC: refusals — non-zero, a message naming the fix, nothing written
# ---------------------------------------------------------------------------


def _adr(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "adr", "Original Decision", status="active")
    return vault, state, rid


def test_missing_reason_is_refused(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED, reason=None)
    _refused(vault, state, rid, r, "--reason", "required")
    assert _snapshot(vault) == before


def test_empty_reason_is_refused(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED, reason="   ")
    _refused(vault, state, rid, r, "--reason", "required")
    assert _snapshot(vault) == before


def test_multi_line_reason_is_refused(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED, reason="line one\nline two")
    _refused(vault, state, rid, r, "--reason", "one line")
    assert _snapshot(vault) == before


def test_missing_initiated_by_is_refused(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED, initiated_by=None)
    _refused(vault, state, rid, r, "--initiated-by", "required", "operator", "agent")
    assert _snapshot(vault) == before


def test_invalid_initiated_by_is_refused(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED, initiated_by="robot")
    _refused(vault, state, rid, r, "--initiated-by", "robot", "operator", "agent")
    assert _snapshot(vault) == before


@pytest.mark.parametrize("diff", [False, True])
def test_empty_stdin_is_refused_never_a_metadata_only_write(tmp_path, diff):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, "", diff=diff)
    _refused(vault, state, rid, r, "no body on stdin", "--diff")
    assert _snapshot(vault) == before


def test_diff_that_does_not_apply_is_refused(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    store = load_script("lore.record.store")
    stale = store.make_unified_diff("not\nthe\nstored\nbody\n", "not\nthe\nnew\nbody\n")
    r = _amend(vault, state, rid, stale, diff=True)
    _refused(vault, state, rid, r, "rejected hunk")
    assert _snapshot(vault) == before


def test_body_identical_to_the_stored_one_is_refused(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, BODY)
    _refused(vault, state, rid, r, "identical")
    assert _snapshot(vault) == before


def test_body_identical_after_fence_neutralization_is_refused(tmp_path):
    vault, state = _make_vault(tmp_path)
    fenced = "# Note\n\nsee <external-memory>x</external-memory>\n"
    rid = _create(vault, state, "spec", "Fenced", status="ready", body=fenced)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, fenced)
    _refused(vault, state, rid, r, "identical")
    assert _snapshot(vault) == before


def test_missing_record_is_refused(tmp_path):
    vault, state = _make_vault(tmp_path)
    _create(vault, state, "adr", "Original Decision", status="active")
    before = _snapshot(vault)
    r = _amend(vault, state, "adr/no-such-record", AMENDED)
    _refused(vault, state, "adr/no-such-record", r)
    assert r.stderr == "error: record not found: adr/no-such-record\n"
    assert _snapshot(vault) == before


def test_session_record_is_refused(tmp_path):
    vault, state = _make_vault(tmp_path)
    sess = vault / "session"
    sess.mkdir()
    (sess / "s1.md").write_text("log\n", encoding="utf-8")
    (sess / "s1.json").write_text(
        json.dumps({"kind": "session", "title": "S1", "status": "dirty"}), encoding="utf-8"
    )
    before = _snapshot(vault)
    r = _amend(vault, state, "session/s1", "log\nmore\n")
    _refused(vault, state, "session/s1", r, "session record cannot be amended", "session lock")
    assert _snapshot(vault) == before


# ---------------------------------------------------------------------------
# AC: every other guard still runs
# ---------------------------------------------------------------------------


def test_amend_that_trips_a_non_freeze_guard_is_still_refused(tmp_path):
    vault, state, rid = _adr(tmp_path)
    name = rid.split("/", 1)[1]
    path = vault / "adr" / f"{name}.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["supersedes"] = [rid]
    path.write_text(json.dumps(stored), encoding="utf-8")
    before = _snapshot(vault)

    r = _amend(vault, state, rid, AMENDED)

    _refused(vault, state, rid, r, "[supersedes-self-edge]")
    assert _snapshot(vault) == before


# ---------------------------------------------------------------------------
# --vault targeting
# ---------------------------------------------------------------------------


def test_amend_with_vault_flag_targets_the_named_vault(tmp_path):
    vault, state, rid = _adr(tmp_path)
    r = _amend(vault, state, rid, AMENDED, extra=["--vault", "default"])
    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == AMENDED


def test_amend_with_unknown_vault_is_refused_writing_nothing(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED, extra=["--vault", "nope"])
    assert r.returncode != 0
    assert _snapshot(vault) == before
