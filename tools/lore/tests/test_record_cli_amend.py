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


_AUTO = object()


def _revision(vault, state, rid, *, vault_name=None):
    """The record's current body revision, read the way a caller reads it."""
    extra = ["--vault", vault_name] if vault_name else []
    r = _run(["record", "show", rid, "--json", *extra], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["revision"]


def _amend(vault, state, rid, body, *, reason="fix wording", initiated_by="operator",
           diff=False, extra=None, base=_AUTO):
    """Run ``lore record amend`` with *body* on stdin.

    Pass ``reason=None`` / ``initiated_by=None`` to leave the flag off. A full-body
    amend names the record's current revision as ``--base`` by default (the
    handshake every caller performs); ``base=None`` omits it and ``base="<hex>"``
    passes that value. ``--diff`` never gets a default ``--base``.
    """
    args = ["record", "amend", rid]
    if reason is not None:
        args += ["--reason", reason]
    if initiated_by is not None:
        args += ["--initiated-by", initiated_by]
    if diff:
        args.append("--diff")
    args += extra or []
    if base is _AUTO:
        base = None
        if not diff:
            vault_flag = (
                ["--vault", extra[extra.index("--vault") + 1]]
                if extra and "--vault" in extra else []
            )
            shown = _run(["record", "show", rid, "--json", *vault_flag],
                         vault=vault, state_dir=state)
            if shown.returncode == 0:
                base = json.loads(shown.stdout)["revision"]
    if base is not None:
        args += ["--base", base]
    return _run(args, vault=vault, state_dir=state, stdin_text=body)


def _refused(vault, state, rid, r, *needles):
    """Assert a refusal: non-zero, a clean message naming each needle, nothing written."""
    assert r.returncode != 0, r.stdout
    assert "Traceback" not in r.stderr
    for needle in needles:
        assert needle in r.stderr, r.stderr


# ---------------------------------------------------------------------------
# Amend writes the body, past the ADR freeze
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
# The change-history entries
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
# Refusals — non-zero, a message naming the fix, nothing written
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
# Every other guard still runs
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


# ---------------------------------------------------------------------------
# A session record cannot be reached by spelling its kind differently
# ---------------------------------------------------------------------------


def test_session_record_under_a_case_variant_kind_dir_is_refused(tmp_path):
    vault, state = _make_vault(tmp_path)
    sess = vault / "SESSION"
    sess.mkdir()
    (sess / "s1.md").write_text("log\n", encoding="utf-8")
    (sess / "s1.json").write_text(
        json.dumps({"kind": "session", "title": "S1", "status": "dirty"}), encoding="utf-8"
    )
    before = _snapshot(vault)
    r = _amend(vault, state, "SESSION/s1", "log\nmore\n")
    _refused(vault, state, "SESSION/s1", r, "session record cannot be amended")
    assert _snapshot(vault) == before


# ---------------------------------------------------------------------------
# --reason is one line of printable text, at most 500 characters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    ["ansi \x1b[2J clear", "vtab\x0bhere", "nel\x85here", "ls here", "ps here",
     "del\x7fhere", "nul\x00here", "tab\there"],
    ids=["ansi", "vtab", "nel", "line-sep", "para-sep", "del", "nul", "tab"],
)
def test_reason_with_a_control_or_line_break_character_is_refused(tmp_path, reason):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED, reason=reason)
    _refused(vault, state, rid, r, "--reason", "control")
    assert _snapshot(vault) == before


def test_reason_of_500_characters_is_accepted(tmp_path):
    vault, state, rid = _adr(tmp_path)
    r = _amend(vault, state, rid, AMENDED, reason="r" * 500)
    assert r.returncode == 0, r.stderr
    assert _sidecar(vault, rid)["change-history"][0]["reason"] == "r" * 500


def test_reason_of_501_characters_is_refused(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED, reason="r" * 501)
    _refused(vault, state, rid, r, "--reason", "500")
    assert _snapshot(vault) == before


# ---------------------------------------------------------------------------
# A stored change-history that is not a list is never reset or exploded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stored", ["abc", None, 7], ids=["string", "null", "int"])
def test_amend_over_a_non_list_change_history_is_refused(tmp_path, stored):
    vault, state, rid = _adr(tmp_path)
    path = vault / "adr" / f"{rid.split('/', 1)[1]}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["change-history"] = stored
    path.write_text(json.dumps(data), encoding="utf-8")
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED)
    _refused(vault, state, rid, r, "change-history", "git")
    assert _snapshot(vault) == before


# ---------------------------------------------------------------------------
# --revert-to N: the reconstruction, computed inside lore and written as an amend
# ---------------------------------------------------------------------------

STEP1 = "# Decision\n\nline one\nline 2 first\nline three\n"
STEP2 = "# Decision\n\nline one\nline 2 second\nline three\n"


def _revert(vault, state, rid, n, *, base=_AUTO, reason="undo it", initiated_by="operator",
            stdin_text=None, extra=None):
    args = ["record", "amend", rid, "--revert-to", str(n)]
    if reason is not None:
        args += ["--reason", reason]
    if initiated_by is not None:
        args += ["--initiated-by", initiated_by]
    args += extra or []
    if base is _AUTO:
        vault_flag = (
            ["--vault", extra[extra.index("--vault") + 1]]
            if extra and "--vault" in extra else []
        )
        base = _revision(vault, state, rid, vault_name=vault_flag[1] if vault_flag else None)
    if base is not None:
        args += ["--base", base]
    return _run(args, vault=vault, state_dir=state, stdin_text=stdin_text)


def _twice_amended(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "lesson", "Original Decision")
    assert _amend(vault, state, rid, STEP1).returncode == 0
    assert _amend(vault, state, rid, STEP2).returncode == 0
    return vault, state, rid


# Contract 7
def test_revert_to_the_last_entry_restores_the_pre_amend_body_and_appends_an_entry(tmp_path):
    vault, state, rid = _twice_amended(tmp_path)
    first_two = _sidecar(vault, rid)["change-history"]
    r = _revert(vault, state, rid, 2)
    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == STEP1
    history = _sidecar(vault, rid)["change-history"]
    assert len(history) == 3
    assert history[:2] == first_two
    assert history[2]["reason"] == "undo it"
    assert history[2]["initiated-by"] == "operator"


def test_revert_to_the_first_entry_restores_the_original(tmp_path):
    vault, state, rid = _twice_amended(tmp_path)
    r = _revert(vault, state, rid, 1)
    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == BODY
    assert len(_sidecar(vault, rid)["change-history"]) == 3


def test_revert_keeps_a_non_overlapping_outside_edit(tmp_path):
    vault, state = _make_vault(tmp_path)
    long_body = "".join(f"line {i:02d}\n" for i in range(1, 31))
    rid = _create(vault, state, "lesson", "Lesson Long", body=long_body)
    amended = long_body.replace("line 03\n", "line 03 amended\n")
    assert _amend(vault, state, rid, amended).returncode == 0
    edited = amended.replace("line 25\n", "line 25 edited\n")
    r = _run(["record", "update", rid], vault=vault, state_dir=state, stdin_text=edited)
    assert r.returncode == 0, r.stderr
    r = _revert(vault, state, rid, 1)
    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == long_body.replace("line 25\n", "line 25 edited\n")


def test_revert_of_an_entry_that_no_longer_reverses_is_refused_and_writes_nothing(tmp_path):
    vault, state, rid = _twice_amended(tmp_path)
    r = _run(["record", "update", rid], vault=vault, state_dir=state,
             stdin_text="# Decision\n\nline one\nrewritten\nline three\n")
    assert r.returncode == 0, r.stderr
    before = _snapshot(vault)
    r = _revert(vault, state, rid, 1)
    _refused(vault, state, rid, r, "entry 2", f"git -C {vault} log -p -- {rid}.md")
    assert _snapshot(vault) == before


@pytest.mark.parametrize("n,needle", [
    ("0", "out of range"), ("3", "out of range"), ("-1", "out of range"),
    ("abc", "must be an integer"), ("1.5", "must be an integer"),
])
def test_revert_to_an_out_of_range_or_non_integer_entry_is_refused(tmp_path, n, needle):
    vault, state, rid = _twice_amended(tmp_path)
    before = _snapshot(vault)
    r = _revert(vault, state, rid, n)
    _refused(vault, state, rid, r, "--revert-to", needle)
    assert _snapshot(vault) == before


def test_revert_on_a_record_with_no_history_is_refused(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _revert(vault, state, rid, 1)
    _refused(vault, state, rid, r, "no change history")
    assert _snapshot(vault) == before


def test_revert_whose_reconstruction_equals_the_current_body_is_refused(tmp_path):
    vault, state, rid = _twice_amended(tmp_path)
    path = vault / f"{rid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["change-history"][1]["diff"] = "@@ -1,1 +1,1 @@\n # Decision\n"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = _snapshot(vault)
    r = _revert(vault, state, rid, 2)
    _refused(vault, state, rid, r, "identical")
    assert _snapshot(vault) == before


def test_revert_over_a_malformed_stored_diff_is_refused(tmp_path):
    vault, state, rid = _twice_amended(tmp_path)
    path = vault / f"{rid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["change-history"][1]["diff"] = "@@ not a hunk header\n"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = _snapshot(vault)
    r = _revert(vault, state, rid, 1)
    _refused(vault, state, rid, r, "entry 2")
    assert _snapshot(vault) == before


# Contract 8
def test_revert_in_a_shared_vault_restores_the_stored_body_byte_for_byte(tmp_path):
    default_vault, state = _make_vault(tmp_path)
    team_vault = tmp_path / "vault_team"
    team_vault.mkdir(parents=True)
    config_home = tmp_path / "config"
    (config_home / "lore").mkdir(parents=True)
    (config_home / "lore" / "config.json").write_text(
        json.dumps({"vaults": [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {"name": "team-notes", "scope": "team", "records": ["lesson"],
             "path": str(team_vault), "shared": True},
        ]}),
        encoding="utf-8",
    )
    env = {"XDG_CONFIG_HOME": str(config_home)}
    original = "# Note\n\nif a < b && c > d then\n</external-memory> & <b>x</b>\nend\n"
    r = _run(
        ["record", "create", "--kind", "lesson", "--title", "Team Note", "--team", "team-notes"],
        vault=default_vault, state_dir=state, stdin_text=original, env_extra=env,
    )
    assert r.returncode == 0, r.stderr
    rid = r.stdout.strip()
    stored_before = _body(team_vault, rid)
    assert "<" in stored_before and "&" in stored_before

    def run(args, stdin_text=None):
        return _run(args + ["--vault", "team-notes"], vault=default_vault, state_dir=state,
                    stdin_text=stdin_text, env_extra=env)

    shown = run(["record", "show", rid, "--json"])
    base = json.loads(shown.stdout)["revision"]
    amended = original.replace("end\n", "the end\n")
    r = run(["record", "amend", rid, "--reason", "r", "--initiated-by", "operator",
             "--base", base], stdin_text=amended)
    assert r.returncode == 0, r.stderr
    base = json.loads(run(["record", "show", rid, "--json"]).stdout)["revision"]
    r = run(["record", "amend", rid, "--revert-to", "1", "--reason", "undo",
             "--initiated-by", "operator", "--base", base])
    assert r.returncode == 0, r.stderr
    assert _body(team_vault, rid) == stored_before
    assert "external-memory layer" not in _body(team_vault, rid)
    assert "&lt;" not in _body(team_vault, rid)
    assert len(_sidecar(team_vault, rid)["change-history"]) == 2


# Contract 9
def test_revert_with_a_stale_base_is_refused_and_writes_nothing(tmp_path):
    vault, state, rid = _twice_amended(tmp_path)
    stale = _revision(vault, state, rid)
    r = _run(["record", "update", rid], vault=vault, state_dir=state,
             stdin_text=STEP2 + "tail\n")
    assert r.returncode == 0, r.stderr
    before = _snapshot(vault)
    r = _revert(vault, state, rid, 2, base=stale)
    _refused(vault, state, rid, r, stale, "changed after you read it")
    assert _snapshot(vault) == before


def test_revert_without_base_is_refused_and_writes_nothing(tmp_path):
    vault, state, rid = _twice_amended(tmp_path)
    before = _snapshot(vault)
    r = _revert(vault, state, rid, 2, base=None)
    _refused(vault, state, rid, r, "--base")
    assert _snapshot(vault) == before


def test_revert_with_a_piped_body_is_refused_and_writes_nothing(tmp_path):
    vault, state, rid = _twice_amended(tmp_path)
    before = _snapshot(vault)
    r = _revert(vault, state, rid, 2, stdin_text="# piped\n")
    _refused(vault, state, rid, r, "--revert-to", "stdin")
    assert _snapshot(vault) == before


def test_revert_with_diff_is_refused_and_writes_nothing(tmp_path):
    vault, state, rid = _twice_amended(tmp_path)
    before = _snapshot(vault)
    r = _revert(vault, state, rid, 2, extra=["--diff"])
    _refused(vault, state, rid, r, "--revert-to", "--diff")
    assert _snapshot(vault) == before
