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

    Slice 0 updated the session vocab to {dirty, clean} and moved it to the
    singular key 'session'. Slice 2 retired `shelved` and the legacy
    `finalized`/`handoff` session terminal statuses — none are canonical.
    Slice 7 singularized the remaining CANONICAL keys (plan/spec/follow-up/
    lesson/dead-end); `deferred` is already singular-shaped and retained.
    """
    sv = load_script("status_validator")
    assert sv.CANONICAL["plan"] == frozenset(
        {"draft", "ready", "in-progress", "complete", "superseded", "dropped"}
    )
    assert sv.CANONICAL["spec"] == frozenset(
        {"draft", "ready", "planned", "complete", "superseded", "dropped"}
    )
    assert sv.CANONICAL["session"] == frozenset({"dirty", "clean"})
    assert sv.CANONICAL["deferred"] == frozenset(
        {"open", "scheduled", "resolved", "dropped", "graduated", "resurfaced"}
    )
    assert sv.CANONICAL["follow-up"] == frozenset({"active", "resolved", "dropped"})
    assert sv.CANONICAL["lesson"] == frozenset({"active", "superseded"})
    assert sv.CANONICAL["dead-end"] == frozenset({"active", "archived"})


def test_is_valid_status_accepts_canonical():
    sv = load_script("status_validator")
    assert sv.is_valid_status("deferred", "open") is True
    assert sv.is_valid_status("deferred", "scheduled") is True
    assert sv.is_valid_status("session", "dirty") is True
    assert sv.is_valid_status("session", "clean") is True
    assert sv.is_valid_status("plan", "in-progress") is True


def test_is_valid_status_rejects_noncanonical():
    sv = load_script("status_validator")
    assert sv.is_valid_status("deferred", "active") is False
    assert sv.is_valid_status("follow-up", "open") is False
    assert sv.is_valid_status("lesson", "complete") is False


def test_is_valid_status_keys_are_singular_only():
    """Slice 7: CANONICAL keys are singular; the plural directory form no longer
    resolves (vault dirs standardized on singular, so the singular→plural alias
    map was dropped).

    The singular `type:` form is the only accepted key. An unrecognized type
    (the old plural directory name) is treated as untracked → unconstrained →
    always valid (the validator never constrains types outside its vocab).
    """
    sv = load_script("status_validator")
    # singular type form resolves to the real vocab
    assert sv.is_valid_status("dead-end", "active") is True
    assert sv.is_valid_status("dead-end", "bogus") is False
    # plural directory form is no longer a tracked key → untracked → unconstrained
    assert sv.permitted_statuses("dead-ends") is None
    assert sv.is_valid_status("dead-ends", "anything") is True


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

def test_shelved_rejected_for_plan_spec_session():
    """`shelved` is no longer canonical for any note type — the shelve/pickup
    feature it backed was retired (Slice 2). Slice 7 keys are singular; the
    plural directory forms are untracked (unconstrained) so only the singular
    `type:` forms are asserted as rejecting `shelved`."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("plan", "shelved") is False
    assert sv.is_valid_status("spec", "shelved") is False
    assert sv.is_valid_status("session", "shelved") is False


def test_legacy_session_statuses_rejected():
    """`finalized`/`handoff` were the deprecated back-compat session terminal
    statuses; the DEPRECATED accommodation is removed (Slice 2), so they reject.
    The `sessions` plural key is gone (Slice 0); use the singular `session`."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("session", "finalized") is False
    assert sv.is_valid_status("session", "handoff") is False


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


def test_clean_session_passes_validation(tmp_path, capsys):
    """A session note with status: clean validates successfully (Slice 0).

    The old test checked `status: complete`; after Slice 0 the canonical
    values are {dirty, clean} and complete is rejected.
    """
    sv = load_script("status_validator")
    note = tmp_path / "x.md"
    note.write_text("---\ntype: session\nstatus: clean\n---\n# x\n")
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


# ---- Slice 0: session vocab → {dirty, clean}; singular key; alias dropped ---


def test_session_canonical_key_is_singular():
    """CANONICAL uses the singular key 'session', not plural 'sessions' (Slice 0)."""
    sv = load_script("status_validator")
    assert "session" in sv.CANONICAL
    assert "sessions" not in sv.CANONICAL


def test_session_canonical_vocab_is_dirty_clean():
    """session canonical set is exactly {dirty, clean} after Slice 0."""
    sv = load_script("status_validator")
    assert sv.CANONICAL["session"] == frozenset({"dirty", "clean"})


def test_session_alias_not_needed():
    """The session→sessions alias is dropped; 'session' resolves directly (Slice 0)."""
    sv = load_script("status_validator")
    # permitted_statuses("session") must work — direct key, no alias redirect
    assert sv.permitted_statuses("session") == frozenset({"dirty", "clean"})


def test_session_dirty_is_valid():
    """dirty is canonical for session (Slice 0)."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("session", "dirty") is True


def test_session_clean_is_valid():
    """clean is canonical for session (Slice 0)."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("session", "clean") is True


def test_session_active_rejected_behavioral():
    """active is no longer canonical for session — is_valid_status returns False
    AND the CLI exits non-zero (behavioral, not just a message check)."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("session", "active") is False


def test_session_active_rejected_cli(tmp_path, capsys):
    """active session note is rejected by the CLI (exit 1) after Slice 0."""
    sv = load_script("status_validator")
    note = tmp_path / "active_session.md"
    note.write_text("---\ntype: session\nstatus: active\n---\n# s\n")
    rc = sv.main([str(note)])
    assert rc != 0, "CLI must exit non-zero for active session status"
    captured = capsys.readouterr()
    assert "active" in captured.err


def test_session_complete_rejected_behavioral():
    """complete is no longer canonical for session — is_valid_status returns False."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("session", "complete") is False


def test_session_complete_rejected_cli(tmp_path, capsys):
    """complete session note is rejected by the CLI (exit 1) after Slice 0."""
    sv = load_script("status_validator")
    note = tmp_path / "complete_session.md"
    note.write_text("---\ntype: session\nstatus: complete\n---\n# s\n")
    rc = sv.main([str(note)])
    assert rc != 0, "CLI must exit non-zero for complete session status"


def test_session_shelved_rejected_behavioral():
    """shelved is not in the new session vocab — rejected (Slice 0)."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("session", "shelved") is False


def test_session_shelved_rejected_cli(tmp_path, capsys):
    """shelved session note is rejected by the CLI (exit 1)."""
    sv = load_script("status_validator")
    note = tmp_path / "shelved_session.md"
    note.write_text("---\ntype: session\nstatus: shelved\n---\n# s\n")
    rc = sv.main([str(note)])
    assert rc != 0, "CLI must exit non-zero for shelved session status"


def test_session_handoff_rejected_behavioral():
    """handoff is not in the session vocab — rejected (Slice 0)."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("session", "handoff") is False


def test_session_handoff_rejected_cli(tmp_path, capsys):
    """handoff session note is rejected by the CLI (exit 1)."""
    sv = load_script("status_validator")
    note = tmp_path / "handoff_session.md"
    note.write_text("---\ntype: session\nstatus: handoff\n---\n# s\n")
    rc = sv.main([str(note)])
    assert rc != 0, "CLI must exit non-zero for handoff session status"


def test_session_finalized_rejected_behavioral():
    """finalized is not in the session vocab — rejected (Slice 0)."""
    sv = load_script("status_validator")
    assert sv.is_valid_status("session", "finalized") is False


def test_session_finalized_rejected_cli(tmp_path, capsys):
    """finalized session note is rejected by the CLI (exit 1)."""
    sv = load_script("status_validator")
    note = tmp_path / "finalized_session.md"
    note.write_text("---\ntype: session\nstatus: finalized\n---\n# s\n")
    rc = sv.main([str(note)])
    assert rc != 0, "CLI must exit non-zero for finalized session status"
