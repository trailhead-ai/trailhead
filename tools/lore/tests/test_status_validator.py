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
    glossary's `scheduled` for deferred)."""
    sv = load_script("status_validator")
    assert sv.CANONICAL["plans"] == frozenset(
        {"draft", "ready", "in-progress", "complete", "superseded", "dropped", "shelved"}
    )
    assert sv.CANONICAL["specs"] == frozenset(
        {"draft", "ready", "planned", "complete", "superseded", "dropped", "shelved"}
    )
    assert sv.CANONICAL["sessions"] == frozenset(
        {"active", "complete", "shelved", "finalized", "handoff"}
    )
    assert sv.CANONICAL["deferred"] == frozenset(
        {"open", "scheduled", "resolved", "dropped", "graduated", "resurfaced"}
    )
    assert sv.CANONICAL["radar"] == frozenset({"active", "resolved", "dropped"})
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
    assert sv.is_valid_status("radar", "open") is False
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
    assert sorted(sv.permitted_statuses("radar")) == ["active", "dropped", "resolved"]
    assert sv.permitted_statuses("nonexistent") is None


# ---- P1-B: back-compat for brain's legacy session terminal statuses ---------

def test_legacy_session_statuses_accepted():
    """Existing brain session notes use `finalized`/`handoff`; lore must accept
    them so they pass the guard at cutover."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("session", "finalized") is True
    assert sv.is_valid_status("sessions", "handoff") is True


def test_legacy_session_statuses_are_deprecated():
    sv = load_script("status_validator")
    assert sv.deprecated_statuses("sessions") == frozenset({"finalized", "handoff"})
    assert sv.is_deprecated_status("session", "finalized") is True
    assert sv.is_deprecated_status("session", "handoff") is True
    # canonical terminal status is NOT deprecated
    assert sv.is_deprecated_status("session", "complete") is False
    # other note types have no deprecated statuses
    assert sv.deprecated_statuses("plans") == frozenset()


def test_finalized_session_accepted_with_deprecation_notice(tmp_path, capsys):
    """A `finalized` session note passes (exit 0) but emits a migration notice."""
    sv = load_script("status_validator")
    note = tmp_path / "x.md"
    note.write_text("---\ntype: session\nstatus: finalized\n---\n# x\n")
    rc = sv.main([str(note)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "deprecated" in captured.err.lower()
    assert "finalized" in captured.err


def test_complete_session_emits_no_deprecation_notice(tmp_path, capsys):
    sv = load_script("status_validator")
    note = tmp_path / "x.md"
    note.write_text("---\ntype: session\nstatus: complete\n---\n# x\n")
    rc = sv.main([str(note)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "deprecated" not in captured.err.lower()
