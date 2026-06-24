"""Slice 2 tests: ported canonical-status validator.

The validator answers: given a note's `type` and a candidate `status`,
is the status canonical for that type? The canonical vocabulary is ported
from the source validator and reconciled against the glossary — notably the
glossary's `scheduled` (date-bound deferral) is included so `/defer` can emit
it and the pre-commit guard (Slice 6) won't reject it.
"""

from conftest import load_script


def test_canonical_vocab_matches_source():
    """The canonical sets match the reconciled vocabulary (source validator +
    glossary's `scheduled` for deferred).

    Slice 2 retired the `shelved` status from plans/specs/sessions and the
    legacy `finalized`/`handoff` session terminal statuses — none are canonical.
    """
    sv = load_script("status_validator")
    assert sv.CANONICAL["plans"] == frozenset(
        {"draft", "ready", "in-progress", "complete", "superseded", "dropped"}
    )
    assert sv.CANONICAL["specs"] == frozenset(
        {"draft", "ready", "planned", "complete", "superseded", "dropped"}
    )
    assert sv.CANONICAL["sessions"] == frozenset({"active", "complete"})
    assert sv.CANONICAL["deferred"] == frozenset(
        {"open", "scheduled", "resolved", "dropped", "graduated", "resurfaced"}
    )
    assert sv.CANONICAL["follow-ups"] == frozenset({"active", "resolved", "dropped"})
    assert sv.CANONICAL["lessons"] == frozenset({"active", "superseded"})
    assert sv.CANONICAL["dead-ends"] == frozenset({"active", "archived"})


def test_is_valid_status_accepts_canonical():
    sv = load_script("status_validator")
    assert sv.is_valid_status("deferred", "open") is True
    assert sv.is_valid_status("deferred", "scheduled") is True
    assert sv.is_valid_status("session", "active") is True
    assert sv.is_valid_status("plan", "in-progress") is True


def test_is_valid_status_rejects_noncanonical():
    sv = load_script("status_validator")
    assert sv.is_valid_status("deferred", "active") is False
    assert sv.is_valid_status("follow-up", "open") is False
    assert sv.is_valid_status("lesson", "complete") is False


def test_is_valid_status_singular_and_plural_type():
    """type frontmatter is singular (deferred, session); dirs are plural-ish.

    is_valid_status accepts both the note `type:` form and the directory name.
    """
    sv = load_script("status_validator")
    # singular type form
    assert sv.is_valid_status("dead-end", "active") is True
    # directory form
    assert sv.is_valid_status("dead-ends", "active") is True


def test_is_valid_status_unknown_type_is_valid():
    """Types outside the validated vocabulary are not constrained → valid."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("briefing", "whatever") is True
    assert sv.is_valid_status(None, "whatever") is True


def test_permitted_statuses_lists_canonical():
    sv = load_script("status_validator")
    assert sorted(sv.permitted_statuses("follow-up")) == ["active", "dropped", "resolved"]
    assert sv.permitted_statuses("nonexistent") is None


# ---- Slice 2: shelved + legacy session terminal statuses are retired --------


def test_shelved_rejected_for_plans_specs_sessions():
    """`shelved` is no longer canonical for any note type — the shelve/pickup
    feature it backed was retired (Slice 2)."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("plan", "shelved") is False
    assert sv.is_valid_status("plans", "shelved") is False
    assert sv.is_valid_status("spec", "shelved") is False
    assert sv.is_valid_status("specs", "shelved") is False
    assert sv.is_valid_status("session", "shelved") is False
    assert sv.is_valid_status("sessions", "shelved") is False


def test_legacy_session_statuses_rejected():
    """`finalized`/`handoff` were the deprecated back-compat session terminal
    statuses; the DEPRECATED accommodation is removed (Slice 2), so they reject."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("session", "finalized") is False
    assert sv.is_valid_status("sessions", "handoff") is False


def test_deprecated_status_machinery_removed():
    """The DEPRECATED dict + helper machinery is gone — no status is `deprecated`."""
    sv = load_script("status_validator")
    assert not hasattr(sv, "DEPRECATED")
    assert not hasattr(sv, "deprecated_statuses")
    assert not hasattr(sv, "is_deprecated_status")


def test_finalized_session_rejected_with_violation(tmp_path, capsys):
    """A `finalized` session note now FAILS validation (exit 1) — no migration
    notice, a hard rejection."""
    sv = load_script("status_validator")
    note = tmp_path / "x.md"
    note.write_text("---\ntype: session\nstatus: finalized\n---\n# x\n")
    rc = sv.main([str(note)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "invalid" in captured.err.lower()
    assert "finalized" in captured.err
    assert "deprecated" not in captured.err.lower()


def test_complete_session_passes_clean(tmp_path, capsys):
    sv = load_script("status_validator")
    note = tmp_path / "x.md"
    note.write_text("---\ntype: session\nstatus: complete\n---\n# x\n")
    rc = sv.main([str(note)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "deprecated" not in captured.err.lower()
    assert "invalid" not in captured.err.lower()


def test_body_only_guid_session_note_passes_clean(tmp_path, capsys):
    """A finalized body-only GUID note keeps its status in the `.json` sidecar
    (A-sidecar), so the `.md` carries no frontmatter status — the validator must
    pass it cleanly (exit 0, no violation) rather than choke on the missing
    frontmatter."""
    sv = load_script("status_validator")
    guid = "11111111-2222-4333-8444-555555555555"
    note = tmp_path / f"{guid}.md"
    note.write_text(f"# session: {guid}\n\n- candidate ... kind=lesson\n")
    rc = sv.main([str(note)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "invalid" not in captured.err.lower()
