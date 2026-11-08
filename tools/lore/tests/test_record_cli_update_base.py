"""``record update --base``: the stale-write handshake, and the rule that a frozen
spec's ``## Follow-ups`` and ``## Waiting`` ledgers can only be changed against a
revision just read."""

from __future__ import annotations


from conftest import make_vault as _make_vault, run_cli as _run
from test_record_cli_amend import (
    _body, _create, _revision, _sidecar, _snapshot,
)
from test_record_cli_update import _create_routed, _run_cfg, _two_team_config

SPEC = "# Spec\n\nintro text\n\n## Acceptance Criteria\n\n- AC1 first\n"
SLICES_1 = "\n## Slices\n\n- [slice one](task/one) covers AC1\n"
FOLLOWUPS = "\n## Follow-ups\n\n- F1 first idea\n- F4 fourth idea\n"
FOLLOWUPS_CHANGED = "\n## Follow-ups\n\n- F1 first idea\n"
WAITING = (
    "\n## Waiting\n\n- **AC1.** set aside\n  - waits on: a vendor\n"
    "  - 2026-11-03 waiting: parked\n"
)
WAITING_ENTRY_2 = "\n- **AC2.** set aside\n  - waits on: a date\n  - 2026-11-04 waiting: later\n"


def _update(vault, state, rid, body, *flags):
    return _run(["record", "update", rid, *flags], vault=vault, state_dir=state,
                stdin_text=body)


def _spec(tmp_path, status, body):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "Loop spec", status=status, body=body)
    return vault, state, rid


def test_update_with_the_revision_just_read_writes_the_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "adr", "A Decision", status="draft")
    rev = _revision(vault, state, rid)

    r = _update(vault, state, rid, "# Decision\n\nnew text\n", "--base", rev)

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == "# Decision\n\nnew text\n"


def test_update_with_a_stale_base_writes_nothing_and_says_to_retry_the_update(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "adr", "A Decision", status="draft")
    stale = _revision(vault, state, rid)
    assert _update(vault, state, rid, "# Decision\n\nsomeone else\n").returncode == 0
    before = _snapshot(vault)

    r = _update(vault, state, rid, "# Decision\n\nmine\n", "--base", stale)

    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert "lore record show" in r.stderr
    assert "retry the update with the new revision" in r.stderr
    assert "amend again" not in r.stderr
    assert _snapshot(vault) == before


def test_ready_spec_follow_ups_change_without_base_is_refused_and_writes_nothing(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC + FOLLOWUPS)
    before = _snapshot(vault)

    r = _update(vault, state, rid, SPEC + FOLLOWUPS_CHANGED)

    assert r.returncode != 0
    assert "--base" in r.stderr
    assert "lore record show" in r.stderr
    assert _snapshot(vault) == before


def test_ready_spec_follow_ups_change_with_current_base_is_written(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC + FOLLOWUPS)

    r = _update(vault, state, rid, SPEC + FOLLOWUPS_CHANGED,
                "--base", _revision(vault, state, rid))

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == SPEC + FOLLOWUPS_CHANGED


def test_ready_spec_follow_ups_removal_without_base_is_refused(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC + FOLLOWUPS)
    before = _snapshot(vault)

    r = _update(vault, state, rid, SPEC)

    assert r.returncode != 0
    assert "--base" in r.stderr
    assert _snapshot(vault) == before


def test_ready_spec_creating_the_follow_ups_section_without_base_is_refused(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC)
    before = _snapshot(vault)

    r = _update(vault, state, rid, SPEC + FOLLOWUPS)

    assert r.returncode != 0
    assert "--base" in r.stderr
    assert _snapshot(vault) == before


def test_ready_spec_creating_the_follow_ups_section_with_current_base_is_written(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC)

    r = _update(vault, state, rid, SPEC + FOLLOWUPS,
                "--base", _revision(vault, state, rid))

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == SPEC + FOLLOWUPS


def test_ready_spec_slices_only_update_without_base_is_accepted(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC + FOLLOWUPS)
    grown = _body(vault, rid) + SLICES_1

    r = _update(vault, state, rid, grown)

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == grown


def test_ready_spec_without_follow_ups_slices_append_without_base_is_accepted(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC)

    r = _update(vault, state, rid, SPEC + SLICES_1)

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == SPEC + SLICES_1


def test_slices_write_composed_without_a_follow_up_entry_is_refused_and_keeps_it(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC + FOLLOWUPS_CHANGED)
    stale_copy = _body(vault, rid)
    grown = stale_copy.replace("\n## Follow-ups", SLICES_1 + "\n## Follow-ups")
    assert "F4" not in grown
    # another writer lands F4 after the copy was read
    landed = _update(vault, state, rid, SPEC + FOLLOWUPS,
                     "--base", _revision(vault, state, rid))
    assert landed.returncode == 0, landed.stderr
    before = _snapshot(vault)

    r = _update(vault, state, rid, grown)

    assert r.returncode != 0
    assert "--base" in r.stderr
    assert "F4" in _body(vault, rid)
    assert _snapshot(vault) == before


def test_draft_spec_follow_ups_change_without_base_is_accepted(tmp_path):
    vault, state, rid = _spec(tmp_path, "draft", SPEC + FOLLOWUPS)

    r = _update(vault, state, rid, SPEC + FOLLOWUPS_CHANGED)

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == SPEC + FOLLOWUPS_CHANGED


def test_ordinary_update_without_base_behaves_as_before(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "adr", "A Decision", status="draft")

    r = _update(vault, state, rid, "# Decision\n\nreplaced\n")

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == "# Decision\n\nreplaced\n"
    assert _sidecar(vault, rid)["status"] == "draft"


def test_stale_base_on_the_relocation_path_refuses_and_moves_nothing(tmp_path):
    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
    stale = _revision(vault_a, state, rid)
    changed = _run_cfg(["record", "amend", rid, "--reason", "r", "--initiated-by", "agent",
                        "--base", stale], vault=vault_a, state=state,
                       config_home=config_home, stdin_text="someone else\n")
    assert changed.returncode == 0, changed.stderr
    before = _snapshot(vault_a)

    r = _run_cfg(["record", "update", rid, "--team", "beta", "--base", stale],
                 vault=vault_a, state=state, config_home=config_home, stdin_text="")

    assert r.returncode != 0
    assert "retry the update with the new revision" in r.stderr
    assert _snapshot(vault_a) == before
    assert not list(vault_b.rglob("*.md"))


def test_both_base_refusals_tell_the_writer_to_reapply_its_change_on_a_fresh_read(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC + FOLLOWUPS)
    stale = _revision(vault, state, rid)
    assert _update(vault, state, rid, SPEC + FOLLOWUPS + "- F5 late idea\n",
                   "--base", stale).returncode == 0
    before = _snapshot(vault)

    missing = _update(vault, state, rid, SPEC + FOLLOWUPS_CHANGED)
    outdated = _update(vault, state, rid, SPEC + FOLLOWUPS_CHANGED, "--base", stale)

    for r in (missing, outdated):
        assert r.returncode != 0
        assert "reapply your change" in r.stderr
        assert "lore record show" in r.stderr
    assert "followups_gate.py --prior" in missing.stderr
    assert "that read's revision" in missing.stderr
    assert _snapshot(vault) == before


def test_write_landing_between_the_relocation_lock_spans_is_refused_against_a_base_current_at_the_first(
    tmp_path, monkeypatch
):
    from lore import locking

    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
    base = _revision(vault_a, state, rid)
    kind, name = rid.split("/", 1)
    body_path = vault_a / kind / f"{name}.md"
    real = locking.vault_write_locks

    def write_lands_between_spans(*roots):
        body_path.write_text("someone else's body\n", encoding="utf-8")
        return real(*roots)

    monkeypatch.setattr(locking, "vault_write_locks", write_lands_between_spans)

    r = _run_cfg(["record", "update", rid, "--team", "beta", "--base", base],
                 vault=vault_a, state=state, config_home=config_home,
                 stdin_text="")

    assert r.returncode != 0
    assert "changed after you read it" in r.stderr
    assert body_path.read_text(encoding="utf-8") == "someone else's body\n"
    assert not list(vault_b.rglob("*.md"))


def test_ready_spec_waiting_section_added_with_current_base_lands_and_reads_back(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC)

    r = _update(vault, state, rid, SPEC + WAITING, "--base", _revision(vault, state, rid))

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == SPEC + WAITING


def test_ready_spec_waiting_change_without_base_is_refused_naming_the_section_and_its_gate(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC)
    before = _snapshot(vault)

    r = _update(vault, state, rid, SPEC + WAITING)

    assert r.returncode != 0
    assert "## Waiting" in r.stderr
    assert "waiting_gate.py --prior" in r.stderr
    assert "--base" in r.stderr
    assert _snapshot(vault) == before


def test_ready_spec_waiting_change_with_a_stale_base_is_refused_and_writes_nothing(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC + WAITING)
    stale = _revision(vault, state, rid)
    assert _update(vault, state, rid, SPEC + WAITING + WAITING_ENTRY_2,
                   "--base", stale).returncode == 0
    before = _snapshot(vault)

    r = _update(vault, state, rid, SPEC, "--base", stale)

    assert r.returncode != 0
    assert "retry the update with the new revision" in r.stderr
    assert _snapshot(vault) == before


def test_slices_write_from_a_copy_predating_a_waiting_entry_is_refused_with_a_reread_remedy(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC)
    stale_copy = _body(vault, rid)
    landed = _update(vault, state, rid, SPEC + WAITING, "--base", _revision(vault, state, rid))
    assert landed.returncode == 0, landed.stderr
    before = _snapshot(vault)

    r = _update(vault, state, rid, stale_copy + SLICES_1)

    assert r.returncode != 0
    assert "--base" in r.stderr
    assert "## Waiting" in r.stderr
    assert "if your ## Waiting change was not intended" in r.stderr
    assert "re-read the spec and reapply your ## Slices change" in r.stderr
    assert "if it was intended, re-run waiting_gate.py --prior on that read" in r.stderr
    assert "without running the waiting gate" not in r.stderr
    assert _snapshot(vault) == before


def test_fresh_slices_only_write_leaving_waiting_unchanged_needs_no_base(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC + WAITING)

    r = _update(vault, state, rid, SPEC + WAITING + SLICES_1)

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == SPEC + WAITING + SLICES_1


def test_draft_spec_waiting_change_without_base_is_accepted(tmp_path):
    vault, state, rid = _spec(tmp_path, "draft", SPEC + WAITING)

    r = _update(vault, state, rid, SPEC)

    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == SPEC


def test_ready_spec_with_two_waiting_headings_is_not_exempted(tmp_path):
    twice = SPEC + WAITING + WAITING
    vault, state, rid = _spec(tmp_path, "draft", twice)
    assert _update(vault, state, rid, twice, "--status", "ready").returncode == 0
    before = _snapshot(vault)

    r = _update(vault, state, rid, twice + WAITING_ENTRY_2,
                "--base", _revision(vault, state, rid))

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _snapshot(vault) == before


def test_missing_base_refusal_for_follow_ups_does_not_point_at_the_waiting_gate(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC)

    r = _update(vault, state, rid, SPEC + FOLLOWUPS)

    assert r.returncode != 0
    assert "followups_gate.py --prior" in r.stderr
    assert "waiting_gate.py" not in r.stderr


def test_missing_base_refusal_names_both_ledgers_when_both_changed(tmp_path):
    vault, state, rid = _spec(tmp_path, "ready", SPEC)

    r = _update(vault, state, rid, SPEC + FOLLOWUPS + WAITING)

    assert r.returncode != 0
    assert "followups_gate.py --prior" in r.stderr
    assert "waiting_gate.py --prior" in r.stderr


def test_with_waiting_unregistered_a_waiting_change_is_refused_as_authored_and_still_parses(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from lore.record import sections

    craft_scripts = Path(__file__).resolve().parents[2] / "craft" / "plugins" / "craft" / "scripts"
    monkeypatch.syspath_prepend(str(craft_scripts))
    import waiting

    vault, state, rid = _spec(tmp_path, "ready", SPEC + WAITING)
    monkeypatch.setitem(
        sections.PIPELINE_SECTIONS,
        "spec",
        tuple(s for s in sections.PIPELINE_SECTIONS["spec"] if s.heading != "## Waiting"),
    )
    before = _snapshot(vault)

    r = _update(vault, state, rid, SPEC + WAITING + WAITING_ENTRY_2,
                "--base", _revision(vault, state, rid))

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _snapshot(vault) == before
    assert [e.criterion_id for e in waiting.parse_waiting(_body(vault, rid))] == ["AC1"]
