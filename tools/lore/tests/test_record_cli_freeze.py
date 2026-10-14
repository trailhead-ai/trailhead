"""The per-kind freeze, through the real ``lore record update`` / ``amend`` CLI.

Every test runs the CLI against a temp vault and judges the refusal on stderr
and the bytes left on disk. Tests are labelled with the acceptance criterion
they pin (AC8-AC14 of the freeze spec).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_vault as _make_vault, run_cli as _run

PLAIN = "# Title\n\nline one\nline two\nline three\n"
EDITED = "# Title\n\nline one\nline TWO\nline three\n"

SPEC_LEDGER = "## Slices\n\n- [ ] first slice\n"
SPEC = "# Spec\n\nintro text\n\n" + SPEC_LEDGER
TASK_RUN = "## Driver run\n\n- built\n"
TASK = "# Task\n\nintro text\n"


_LIVE_STATUS = {"adr": "active", "spec": "ready", "decision": "active", "task": "open"}


def _create(vault, state, kind, status, body=PLAIN, title=None, extra=None):
    """Create a record at *status*; ``superseded`` is reached through the verb."""
    title = title or f"A {kind} at {status}"
    args = ["record", "create", "--kind", kind, "--title", title,
            "--keyword", "k", "--status",
            _LIVE_STATUS[kind] if status == "superseded" else status, *(extra or [])]
    r = _run(args, vault=vault, state_dir=state, stdin_text=body)
    assert r.returncode == 0, r.stderr
    rid = r.stdout.strip()
    if status == "superseded":
        heir = _run(["record", "create", "--kind", "adr", "--title", f"Heir of {title}",
                     "--keyword", "k", "--status", "active"],
                    vault=vault, state_dir=state, stdin_text=PLAIN)
        assert heir.returncode == 0, heir.stderr
        done = _run(["record", "supersede", rid, "--by", heir.stdout.strip()],
                    vault=vault, state_dir=state)
        assert done.returncode == 0, done.stderr
    return rid


def _paths(vault: Path, rid: str):
    kind, name = rid.split("/", 1)
    return vault / kind / f"{name}.md", vault / kind / f"{name}.json"


def _snapshot(vault: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(vault)): p.read_bytes()
        for p in sorted(vault.rglob("*"))
        if p.is_file() and p.suffix in (".md", ".json")
    }


def _update(vault, state, rid, body=None, args=()):
    return _run(["record", "update", rid, *args], vault=vault, state_dir=state, stdin_text=body)


def _status(vault, rid):
    return json.loads(_paths(vault, rid)[1].read_text(encoding="utf-8"))["status"]


def _revision(vault, state, rid):
    r = _run(["record", "show", rid, "--json"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["revision"]


# --- AC8: each side of each kind's boundary ---------------------------------

REFUSED = [
    ("adr", "active"),
    ("spec", "ready"),
    ("spec", "complete"),
    ("spec", "dropped"),
    ("decision", "active"),
    ("decision", "superseded"),
    ("task", "done"),
    ("task", "superseded"),
]
ALLOWED = [
    ("adr", "draft"),
    ("spec", "draft"),
    ("task", "in-progress"),
    ("task", "blocked"),
]


@pytest.mark.parametrize("kind,status", REFUSED)
def test_ac8_body_update_refused_at_frozen_status_and_nothing_written(tmp_path, kind, status):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, status)
    before = _snapshot(vault)

    r = _update(vault, state, rid, EDITED)

    assert r.returncode != 0
    assert "graph-guard [frozen-body]" in r.stderr
    assert _snapshot(vault) == before


@pytest.mark.parametrize("kind,status", ALLOWED)
def test_ac8_body_update_allowed_below_the_freeze(tmp_path, kind, status):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, status)

    r = _update(vault, state, rid, EDITED)

    assert r.returncode == 0, r.stderr
    assert _paths(vault, rid)[0].read_text(encoding="utf-8") == EDITED


def test_ac8_prior_status_counts_spec_draft_to_ready_with_new_body_lands(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "draft")

    r = _update(vault, state, rid, EDITED, ["--status", "ready"])

    assert r.returncode == 0, r.stderr
    assert _status(vault, rid) == "ready"
    assert _paths(vault, rid)[0].read_text(encoding="utf-8") == EDITED


def test_ac8_prior_status_counts_task_in_progress_to_done_with_diff_lands(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "in-progress", body="a\nb\nc\n")
    diff = "--- a\n+++ b\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"

    r = _update(vault, state, rid, diff, ["--status", "done", "--diff"])

    assert r.returncode == 0, r.stderr
    assert _status(vault, rid) == "done"
    assert _paths(vault, rid)[0].read_text(encoding="utf-8") == "a\nB\nc\n"


def test_ac8_prior_status_counts_task_done_to_open_with_new_body_refused(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "done")
    before = _snapshot(vault)

    r = _update(vault, state, rid, EDITED, ["--status", "open"])

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _snapshot(vault) == before


# --- AC9: the refusal names amend for that record ---------------------------


@pytest.mark.parametrize("kind,status", [("spec", "ready"), ("decision", "active"), ("task", "done")])
def test_ac9_refusal_carries_tag_and_amend_command_for_that_record(tmp_path, kind, status):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, status)

    r = _update(vault, state, rid, EDITED)

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert f"lore record amend {rid!r} --reason" in r.stderr
    assert "--initiated-by" in r.stderr
    assert "--base <revision>" in r.stderr
    assert kind in r.stderr and status in r.stderr


def test_ac9_task_refusal_names_the_status_only_reopen_route(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "done")

    r = _update(vault, state, rid, EDITED)

    assert "--status" in r.stderr and "reopen" in r.stderr.lower()


def test_ac9_non_task_refusal_does_not_offer_a_reopen_route(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "decision", "active")

    r = _update(vault, state, rid, EDITED)

    assert "reopen" not in r.stderr.lower()


@pytest.mark.parametrize("kind,status,passes,not_passes", [
    ("spec", "ready", ["## Slices"], ["## Driver run"]),
    ("task", "done", ["## Driver run"], ["## Slices"]),
    ("decision", "active", [], ["## Slices", "## Driver run"]),
])
def test_ac9_refusal_names_only_the_kinds_own_plain_update_section(
    tmp_path, kind, status, passes, not_passes
):
    """The refusal tells the writer which section of THIS kind a plain update may
    still change, and never offers a section another kind owns."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, status)

    r = _update(vault, state, rid, EDITED)

    assert "[frozen-body]" in r.stderr
    for section in passes:
        assert f"Only {section} passes a plain update" in r.stderr
    for section in not_passes:
        assert section not in r.stderr
    if not passes:
        assert "No section passes a plain update" in r.stderr


@pytest.mark.parametrize("kind,status", [("spec", "ready"), ("decision", "active"), ("task", "done")])
def test_ac9_refusal_tells_an_agent_to_pass_initiated_by_agent(tmp_path, kind, status):
    """The amend command offers ``agent|operator``; the refusal must also say which
    one an agent picks, so an agent cannot read the choice as its own to make."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, status)

    r = _update(vault, state, rid, EDITED)

    assert "[frozen-body]" in r.stderr
    assert "An agent always passes `--initiated-by agent`" in r.stderr


def test_ac9_adr_refusal_names_the_supersede_verb_for_this_record(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "adr", "active")

    r = _update(vault, state, rid, EDITED)

    assert f"lore record supersede {rid} --by adr/<successor>" in r.stderr


def test_ac9_non_adr_refusal_calls_a_scope_change_a_supersession_and_names_the_verb(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "ready")

    r = _update(vault, state, rid, EDITED)

    assert "supersession" in r.stderr
    assert f"lore record supersede {rid} --by <successor-id>" in r.stderr


@pytest.mark.parametrize("kind,live,dead", [
    ("adr", "active", "dropped"), ("spec", "ready", "dropped"),
    ("decision", "active", "dropped"), ("task", "done", "dropped"),
])
def test_a_frozen_body_refusal_names_the_verb_only_where_it_can_run(tmp_path, kind, live, dead):
    vault, state = _make_vault(tmp_path)
    live_rid = _create(vault, state, kind, live, title="Live one")
    dead_rid = _create(vault, state, kind, dead, title="Dead one")

    live_msg = _update(vault, state, live_rid, EDITED).stderr
    dead_msg = _update(vault, state, dead_rid, EDITED).stderr

    assert "[frozen-body]" in live_msg and "[frozen-body]" in dead_msg
    assert f"lore record supersede {live_rid} " in live_msg
    assert "lore record supersede" not in dead_msg


@pytest.mark.parametrize("kind,live", [("adr", "active"), ("spec", "ready")])
def test_a_frozen_status_refusal_names_the_verb_only_where_it_can_run(tmp_path, kind, live):
    vault, state = _make_vault(tmp_path)
    live_rid = _create(vault, state, kind, live, title="Live one")
    dead_rid = _create(vault, state, kind, "dropped", title="Dead one")

    live_msg = _update(vault, state, live_rid, None, ["--status", "draft"]).stderr
    dead_msg = _update(vault, state, dead_rid, None, ["--status", "draft"]).stderr

    assert "[frozen-status]" in live_msg and "[frozen-status]" in dead_msg
    assert f"lore record supersede {live_rid} " in live_msg
    assert "lore record supersede" not in dead_msg


# --- AC10: leaving a frozen status ------------------------------------------


@pytest.mark.parametrize("kind,status", [("adr", "active"), ("spec", "ready")])
def test_ac10_frozen_adr_or_spec_cannot_return_to_draft(tmp_path, kind, status):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, status)
    before = _snapshot(vault)

    r = _update(vault, state, rid, None, ["--status", "draft"])

    assert r.returncode != 0
    assert "graph-guard [frozen-status]" in r.stderr
    assert _snapshot(vault) == before


def test_ac10_spec_may_advance_from_ready_to_complete(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "ready")

    r = _update(vault, state, rid, None, ["--status", "complete"])

    assert r.returncode == 0, r.stderr
    assert _status(vault, rid) == "complete"


@pytest.mark.parametrize("status", ["done", "dropped", "superseded"])
def test_ac10_task_may_leave_a_frozen_status_for_in_progress(tmp_path, status):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", status)

    r = _update(vault, state, rid, None, ["--status", "in-progress"])

    assert r.returncode == 0, r.stderr
    assert _status(vault, rid) == "in-progress"


# --- AC11: pipeline sections pass a plain update ----------------------------


def test_ac11_ready_spec_full_body_that_only_appends_to_slices_is_allowed(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "ready", body=SPEC)
    grown = SPEC + "- [ ] second slice\n"

    r = _update(vault, state, rid, grown)

    assert r.returncode == 0, r.stderr
    assert _paths(vault, rid)[0].read_text(encoding="utf-8") == grown


def test_ac11_done_task_diff_that_only_appends_a_driver_run_block_is_allowed(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "done", body=TASK)
    diff = "--- a\n+++ b\n@@ -1,3 +1,6 @@\n # Task\n \n intro text\n+\n+## Driver run\n+\n+- built\n"

    r = _update(vault, state, rid, diff, ["--diff"])

    assert r.returncode == 0, r.stderr
    assert "## Driver run" in _paths(vault, rid)[0].read_text(encoding="utf-8")


def test_ac11_one_character_changed_above_slices_is_refused(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "ready", body=SPEC)
    before = _snapshot(vault)
    changed = SPEC.replace("intro text", "intro texT") + "- [ ] second slice\n"

    r = _update(vault, state, rid, changed)

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _snapshot(vault) == before


def test_ac11_mixed_write_refusal_names_the_authored_line_not_the_ledger_line(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "ready", body=SPEC)
    mixed = SPEC.replace("intro text", "intro TEXT") + "- [ ] ledger append\n"

    r = _update(vault, state, rid, mixed)

    assert r.returncode != 0
    assert "intro TEXT" in r.stderr
    assert "ledger append" not in r.stderr


# --- AC13: unfrozen kinds ----------------------------------------------------


@pytest.mark.parametrize("kind,status", [("lesson", "active"), ("area", "active")])
def test_ac13_lesson_and_area_take_body_updates(tmp_path, kind, status):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, status)

    r = _update(vault, state, rid, EDITED)

    assert r.returncode == 0, r.stderr
    assert _paths(vault, rid)[0].read_text(encoding="utf-8") == EDITED


def test_ac13_session_takes_a_body_update(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "session", "dirty")

    r = _update(vault, state, rid, EDITED)

    assert r.returncode == 0, r.stderr
    assert _paths(vault, rid)[0].read_text(encoding="utf-8") == EDITED


# --- AC14: metadata-only writes ---------------------------------------------


@pytest.mark.parametrize("kind,status,flag", [
    ("task", "done", ["--label", "craft/team=core"]),
    ("task", "done", ["--related", "area=somewhere"]),
    ("task", "done", ["--status", "dropped"]),
    ("spec", "ready", ["--label", "craft/team=core"]),
    ("spec", "ready", ["--related", "area=somewhere"]),
    ("spec", "ready", ["--status", "planned"]),
])
def test_ac14_metadata_only_update_of_a_frozen_record_is_allowed(tmp_path, kind, status, flag):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, status)

    r = _update(vault, state, rid, None, flag)

    assert r.returncode == 0, r.stderr
    assert _paths(vault, rid)[0].read_text(encoding="utf-8") == PLAIN


# --- amend, move path, round-trip -------------------------------------------


@pytest.mark.parametrize(
    "kind,status",
    [("spec", "ready"), ("decision", "active"), ("task", "done"), ("adr", "active")],
)
def test_amend_still_lands_on_every_frozen_kind_and_records_history(tmp_path, kind, status):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, status)

    r = _run(["record", "amend", rid, "--reason", "fix", "--initiated-by", "agent",
              "--base", _revision(vault, state, rid)],
             vault=vault, state_dir=state, stdin_text=EDITED)

    assert r.returncode == 0, r.stderr
    assert _paths(vault, rid)[0].read_text(encoding="utf-8") == EDITED
    assert len(json.loads(_paths(vault, rid)[1].read_text(encoding="utf-8"))["change-history"]) == 1


@pytest.mark.parametrize("kind,status,body", [
    ("adr", "active", PLAIN),
    ("spec", "ready", SPEC),
    ("decision", "active", PLAIN),
    ("task", "done", TASK + "\n" + TASK_RUN),
])
def test_round_trip_current_body_back_is_allowed_as_full_body_and_empty_diff(tmp_path, kind, status, body):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, status, body=body)
    stored = _paths(vault, rid)[0].read_text(encoding="utf-8")

    full = _update(vault, state, rid, stored)
    empty = _update(vault, state, rid, "", ["--diff"])

    assert full.returncode == 0, full.stderr
    assert empty.returncode == 0, empty.stderr
    assert _paths(vault, rid)[0].read_text(encoding="utf-8") == stored


def test_move_path_with_a_changed_body_against_a_frozen_record_is_refused(tmp_path):
    vault_a, state = _make_vault(tmp_path)
    vault_b = tmp_path / "vault_b"
    vault_b.mkdir()
    cfg = tmp_path / "config"
    (cfg / "lore").mkdir(parents=True)
    (cfg / "lore" / "config.json").write_text(json.dumps({"vaults": [
        {"name": "default", "scope": "default", "path": str(vault_a)},
        {"name": "alpha", "scope": "team", "records": ["decision"], "path": str(vault_a)},
        {"name": "beta", "scope": "team", "records": ["decision"], "path": str(vault_b)},
    ]}), encoding="utf-8")
    extra = {"env_extra": {"XDG_CONFIG_HOME": str(cfg)}}
    r = _run(["record", "create", "--kind", "decision", "--title", "Moving", "--keyword", "k",
              "--team", "alpha"], vault=vault_a, state_dir=state, stdin_text=PLAIN, **extra)
    assert r.returncode == 0, r.stderr
    rid = r.stdout.strip()

    r2 = _run(["record", "update", rid, "--team", "beta"], vault=vault_a, state_dir=state,
              stdin_text=EDITED, **extra)

    assert r2.returncode != 0
    assert "[frozen-body]" in r2.stderr
    assert _paths(vault_a, rid)[0].read_text(encoding="utf-8") == PLAIN
    assert not (vault_b / "decision").exists() or not list((vault_b / "decision").iterdir())


# --- a cross-vault move onto an existing destination id honours its freeze ---

SOURCE_BODY = "# Same\n\nSOURCE BODY\n"
DEST_BODY = "# Same\n\nFROZEN DEST BODY\n"


def _two_routed_vaults(tmp_path):
    vault_a, state = _make_vault(tmp_path)
    vault_b = tmp_path / "vault_b"
    vault_b.mkdir()
    cfg = tmp_path / "config"
    (cfg / "lore").mkdir(parents=True)
    kinds = ["decision", "spec", "task"]
    (cfg / "lore" / "config.json").write_text(json.dumps({"vaults": [
        {"name": "default", "scope": "default", "path": str(vault_a)},
        {"name": "alpha", "scope": "team", "records": kinds, "path": str(vault_a)},
        {"name": "beta", "scope": "team", "records": kinds, "path": str(vault_b)},
    ]}), encoding="utf-8")
    return vault_a, vault_b, state, {"env_extra": {"XDG_CONFIG_HOME": str(cfg)}}


def _create_in_team(vault, state, extra, kind, status, team, body):
    r = _run(["record", "create", "--kind", kind, "--title", "Same", "--keyword", "k",
              "--status", status, "--team", team],
             vault=vault, state_dir=state, stdin_text=body, **extra)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


MOVE_ONTO_FROZEN = [
    ("decision", "active", "active"),
    ("spec", "ready", "ready"),
    ("task", "in-progress", "done"),
]


@pytest.mark.parametrize("kind,source_status,dest_status", MOVE_ONTO_FROZEN)
def test_move_onto_a_frozen_destination_id_is_refused_and_both_vaults_are_unchanged(
    tmp_path, kind, source_status, dest_status
):
    vault_a, vault_b, state, extra = _two_routed_vaults(tmp_path)
    rid = _create_in_team(vault_a, state, extra, kind, source_status, "alpha", SOURCE_BODY)
    assert _create_in_team(vault_b, state, extra, kind, dest_status, "beta", DEST_BODY) == rid
    before_a, before_b = _snapshot(vault_a), _snapshot(vault_b)

    r = _run(["record", "update", rid, "--team", "beta"], vault=vault_a, state_dir=state, **extra)

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _snapshot(vault_a) == before_a
    assert _snapshot(vault_b) == before_b


def test_move_back_to_draft_onto_a_frozen_destination_is_refused_as_frozen_status(tmp_path):
    vault_a, vault_b, state, extra = _two_routed_vaults(tmp_path)
    rid = _create_in_team(vault_a, state, extra, "spec", "draft", "alpha", DEST_BODY)
    _create_in_team(vault_b, state, extra, "spec", "ready", "beta", DEST_BODY)
    before_a, before_b = _snapshot(vault_a), _snapshot(vault_b)

    r = _run(["record", "update", rid, "--team", "beta"], vault=vault_a, state_dir=state, **extra)

    assert r.returncode != 0
    assert "[frozen-status]" in r.stderr
    assert _snapshot(vault_a) == before_a
    assert _snapshot(vault_b) == before_b


def test_move_onto_a_non_frozen_destination_id_still_overwrites_it(tmp_path):
    vault_a, vault_b, state, extra = _two_routed_vaults(tmp_path)
    rid = _create_in_team(vault_a, state, extra, "task", "in-progress", "alpha", SOURCE_BODY)
    _create_in_team(vault_b, state, extra, "task", "in-progress", "beta", DEST_BODY)

    r = _run(["record", "update", rid, "--team", "beta"], vault=vault_a, state_dir=state, **extra)

    assert r.returncode == 0, r.stderr
    assert _paths(vault_b, rid)[0].read_text(encoding="utf-8") == SOURCE_BODY
    assert not _paths(vault_a, rid)[0].exists()


def test_move_onto_an_absent_destination_id_lands(tmp_path):
    vault_a, vault_b, state, extra = _two_routed_vaults(tmp_path)
    rid = _create_in_team(vault_a, state, extra, "decision", "active", "alpha", SOURCE_BODY)

    r = _run(["record", "update", rid, "--team", "beta"], vault=vault_a, state_dir=state, **extra)

    assert r.returncode == 0, r.stderr
    assert _paths(vault_b, rid)[0].read_text(encoding="utf-8") == SOURCE_BODY


# --- line-terminator-only differences still refuse with the amend route -----

TERMINATOR_CASES = [
    ("no-trailing-newline", "# T\n\nline one\n", "# T\n\nline one"),
    ("extra-trailing-newline", "# T\n\nline one\n", "# T\n\nline one\n\n"),
    ("form-feed", "# T\n\nline one\n", "# T\n\nline one\x0c\n"),
    ("u2028", "# T\n\nline one\n", "# T\n\nline one \n"),
    ("empty-prior", "", "# T\n"),
    ("only-newline-new", "# T\n", "\n"),
    ("prefix-longer-new", "# T\n", "# T\n\nmore\n"),
    ("prefix-shorter-new", "# T\n\nmore\n", "# T\n"),
]


@pytest.mark.parametrize("kind", ["adr", "decision"])
@pytest.mark.parametrize("label,prior,new", TERMINATOR_CASES, ids=[c[0] for c in TERMINATOR_CASES])
def test_terminator_only_difference_is_refused_with_amend_route(tmp_path, kind, label, prior, new):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, kind, "active", body=prior)
    md, _ = _paths(vault, rid)
    before = md.read_bytes()
    r = _update(vault, state, rid, body=new)
    assert r.returncode != 0
    assert "record update failed" not in r.stderr
    assert "Traceback" not in r.stderr
    assert "graph-guard [frozen-body]" in r.stderr
    assert f"lore record amend '{rid}'" in r.stderr
    assert md.read_bytes() == before


def test_terminator_only_difference_names_the_terminator(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "decision", "active", body="# T\n\nline one\n")
    r = _update(vault, state, rid, body="# T\n\nline one")
    assert "authored-content line 3 differs only in its line terminator" in r.stderr
