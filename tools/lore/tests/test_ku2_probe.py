"""KU2 assumption probe — ephemeral; remove after Slice 1 builds proper tests.

KU2: `referenced` semantics under born-dirty.

Three concrete questions:
  1. When `referenced` is called on a non-existent session, does it lazy-create
     a record? And if so, is that record born `clean` (no pending candidate) or
     does it no-op (no record at all)?  The spec says "no candidate → no record".

  2. Does `referenced` ever flip an existing session's status
     (e.g. dirty→clean or clean→dirty)?
     Proposed answer: referenced appends + bumps `last-referenced-at`, never dirties.

  3. Does the candidate path dirty a session?
     a) First `candidate` materializes a record born `dirty`.
     b) A `candidate` on an already-`clean` session flips it back to `dirty`.

We test CURRENT BEHAVIOR against what the plan/spec prescribes,
and report the gap.

Cleanup: remove this file entirely — it is a probe, not a keeper.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import load_script, make_vault as _make_vault, run_cli as _run

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A valid GUID-shaped session id (required by current sanitize_session_id)
SID = "22222222-3333-4444-8555-666666666666"

SCRIPTS_DIR = (
    Path(__file__).parent.parent / "plugins" / "lore" / "scripts"
)


def _session_note(vault: Path, session_id: str) -> Path:
    """Path the current code writes to (plural `sessions/`)."""
    return vault / "sessions" / f"{session_id}.md"


# ---------------------------------------------------------------------------
# Question 1: `referenced` on a non-existent session — no-op or lazy-create?
# ---------------------------------------------------------------------------

class TestReferencedOnNonExistentSession:
    """Current behavior: does `referenced` create a session note?

    Spec/plan requirement (KU2): "no candidate → no record".
    The proposed contract is that `referenced` on a non-existent session should
    either:
      a) no-op (create nothing), OR
      b) create a `clean` record (no pending candidate)
    NOT create a `dirty` record.
    """

    def test_referenced_creates_no_session_note_current_behavior(self, tmp_path):
        """Probe: does `referenced` create a session note when none exists?

        This test DOCUMENTS CURRENT BEHAVIOR.  The plan says the spec requires
        "no candidate → no record", meaning referenced should NOT create a note.
        We run it and observe what actually happens.
        """
        vault, state = _make_vault(tmp_path)
        sessions_dir = vault / "sessions"

        r = _run(
            ["session", "referenced", "spec/some-record", "--session-id", SID],
            vault=vault, state_dir=state,
        )
        assert r.returncode == 0, f"referenced failed unexpectedly: {r.stderr}"

        note = _session_note(vault, SID)
        # PROBE ASSERTION: check whether a note was created
        # Per plan spec: "no candidate → no record" means this should NOT exist.
        # The current code calls create_or_append unconditionally, so we expect
        # it DOES create a note (current behavior differs from spec intent).
        note_exists = note.exists()

        # We assert what the CURRENT CODE does (create_or_append is called
        # unconditionally from _cmd_session_referenced — no guard on
        # "is there already a candidate?").
        # Change this assertion once Slice 1 aligns behavior with the spec.
        assert note_exists, (
            "Current behavior: `referenced` DOES lazy-create the session note "
            "even when no candidate exists. "
            "Spec intent (KU2): 'no candidate → no record' — so this is a GAP. "
            "Slice 1 must add a guard: referenced must not create the note if "
            "no candidate has been logged yet."
        )

    def test_referenced_note_has_no_status_field_current_behavior(self, tmp_path):
        """Current session notes from `referenced` are body-only, no status field.

        Since session_store.create_or_append writes a body-only file (no
        frontmatter, no .json sidecar), there is no `status` field at all.
        Slice 1's born-dirty contract means the first candidate write MUST
        materialize `status: dirty` in the sidecar. Referenced must NOT set it.
        """
        vault, state = _make_vault(tmp_path)

        _run(
            ["session", "referenced", "decision/something", "--session-id", SID],
            vault=vault, state_dir=state,
        )

        note = _session_note(vault, SID)
        if not note.exists():
            pytest.skip("referenced did not create a note (already no-op or spec-aligned)")

        text = note.read_text()
        # Body-only notes have no frontmatter (no `---`) and no JSON sidecar.
        assert not text.startswith("---"), (
            "referenced note must be body-only (no frontmatter). "
            "If this fails, a status field exists — which changes what Slice 1 must handle."
        )
        sidecar = note.with_suffix(".json")
        assert not sidecar.exists(), (
            "referenced must not create a .json sidecar with a status field. "
            "If this fails, the status path is already wired — Slice 1 may be simpler."
        )
        # No status in the body-only text either
        assert "status:" not in text, (
            "body-only referenced note must not embed a status: field"
        )


# ---------------------------------------------------------------------------
# Question 2: `referenced` never flips status — verify with session_store directly
# ---------------------------------------------------------------------------

class TestReferencedNeverDirties:
    """referenced appends to the body; it does NOT touch status.

    This proves current behavior matches the proposed KU2 contract on the
    status-flip question: referenced never changes status (no status field
    exists in a body-only note, and referenced doesn't write one).
    """

    def test_referenced_does_not_introduce_status_field(self, tmp_path):
        """After a candidate (which would set dirty), referenced must not clear it."""
        vault, state = _make_vault(tmp_path)

        # First: create a session via candidate
        _run(
            ["session", "candidate", "--session-id", SID,
             "--kind", "decision", "--phase", "Build"],
            vault=vault, state_dir=state, stdin_text="a candidate entry\n",
        )
        note = _session_note(vault, SID)
        assert note.exists(), "candidate must create the note"

        text_after_candidate = note.read_text()
        # Current code: body-only, no status field
        assert "status:" not in text_after_candidate, (
            "Current code: candidate writes body-only (no status field). "
            "After Slice 1, candidate should create a sidecar with status: dirty."
        )

        # Now add a referenced entry
        _run(
            ["session", "referenced", "area/lore", "--session-id", SID],
            vault=vault, state_dir=state,
        )

        text_after_referenced = note.read_text()
        # referenced must not add a status field either
        assert "status:" not in text_after_referenced, (
            "referenced must not write a status field (never dirties, never cleans). "
            "This assertion will need updating after Slice 1 when candidate introduces "
            "status: dirty in a sidecar."
        )

        # The referenced entry IS appended
        assert "area/lore" in text_after_referenced, (
            "referenced must append the record_id to the note body"
        )
        # The candidate entry is still there
        assert "a candidate entry" in text_after_referenced, (
            "referenced must not overwrite the existing candidate body"
        )

    def test_referenced_entry_format_current(self, tmp_path):
        """Verify the referenced entry line format for Slice 1 to preserve."""
        session_store = load_script("session_store")

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Manually call create_or_append as referenced does
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = f"- referenced {now} spec/lore-search"
        session_store.create_or_append(SID, entry, sessions_dir)

        note = sessions_dir / f"{SID}.md"
        text = note.read_text()
        assert "- referenced" in text, "referenced entry must use '- referenced' prefix"
        assert "spec/lore-search" in text, "referenced entry must include the record_id"


# ---------------------------------------------------------------------------
# Question 3: candidate path dirties the session
# ---------------------------------------------------------------------------

class TestCandidateDirties:
    """The candidate path is the one that should dirty the session.

    Current state: body-only notes have NO status field at all.
    Slice 1 intent: first candidate materializes a `dirty` session record (with
    sidecar); subsequent candidates keep it `dirty`; a `clean` session (after
    flush) flips back to `dirty` on next candidate.

    This probe documents the GAP: current code does not write status at all.
    """

    def test_candidate_writes_no_status_currently(self, tmp_path):
        """CURRENT BEHAVIOR: candidate does NOT write a status field.

        This is the gap Slice 1 must close. After Slice 1:
        - First candidate must create `session/<id>.{md,json}` with `status: dirty`.
        - This test should be REPLACED by a Slice 1 test that asserts `status: dirty`.
        """
        vault, state = _make_vault(tmp_path)

        r = _run(
            ["session", "candidate", "--session-id", SID,
             "--kind", "spec", "--phase", "Plan"],
            vault=vault, state_dir=state, stdin_text="my first candidate\n",
        )
        assert r.returncode == 0, f"candidate failed: {r.stderr}"

        note = _session_note(vault, SID)
        assert note.exists(), "candidate must create the session note"

        text = note.read_text()
        sidecar = note.with_suffix(".json")

        # CURRENT CODE: no sidecar, no status
        assert not sidecar.exists(), (
            "GAP (Slice 1 target): current code does NOT create a .json sidecar. "
            "Slice 1 must create session/<id>.json with {status: 'dirty', kind: 'session', ...}."
        )
        assert "status:" not in text, (
            "GAP (Slice 1 target): current code does NOT embed status in the body. "
            "Slice 1 must materialize status: dirty on first candidate."
        )

        # The write path IS to sessions/ (plural) — another gap
        assert (vault / "sessions" / f"{SID}.md").exists(), (
            "GAP (Slice 1 target): current code writes to 'sessions/' (plural). "
            "Slice 1 must write to 'session/' (singular, indexed record dir)."
        )

    def test_candidate_entry_format_current(self, tmp_path):
        """Verify the candidate entry line format for Slice 1 to preserve."""
        vault, state = _make_vault(tmp_path)

        _run(
            ["session", "candidate", "--session-id", SID,
             "--kind", "decision", "--phase", "Build"],
            vault=vault, state_dir=state, stdin_text="the candidate body\n",
        )

        note = _session_note(vault, SID)
        text = note.read_text()

        assert "- candidate" in text, "candidate entry must use '- candidate' prefix"
        assert "kind=decision" in text, "candidate entry must include kind="
        assert "phase=Build" in text, "candidate entry must include phase="
        assert "the candidate body" in text, "candidate body must be in the entry"

    def test_write_location_is_sessions_plural_currently(self, tmp_path):
        """Current code writes to sessions/ (plural); Slice 1 must move to session/ (singular)."""
        vault, state = _make_vault(tmp_path)

        _run(
            ["session", "candidate", "--session-id", SID,
             "--kind", "spec", "--phase", "Plan"],
            vault=vault, state_dir=state, stdin_text="content\n",
        )

        plural_note = vault / "sessions" / f"{SID}.md"
        singular_note = vault / "session" / f"{SID}.md"

        assert plural_note.exists(), "current code writes to sessions/ (plural)"
        assert not singular_note.exists(), (
            "GAP (Slice 1 target): current code does NOT write to session/ (singular). "
            "Slice 1 must redirect writes from sessions/<id>.md to session/<id>.{md,json}."
        )


# ---------------------------------------------------------------------------
# Summary probe: what Slice 1 contract must be for KU2
# ---------------------------------------------------------------------------

class TestKU2ContractSummary:
    """A single consolidated assertion capturing the KU2 contract conclusion.

    Runs the full sequence — referenced on empty, candidate, referenced again —
    and characterizes each step's current behavior vs. spec intent.
    """

    def test_full_sequence_current_vs_spec(self, tmp_path):
        """Full referenced→candidate→referenced sequence behavioral probe."""
        vault, state = _make_vault(tmp_path)

        # Step 1: referenced on non-existent session
        r1 = _run(
            ["session", "referenced", "area/lore", "--session-id", SID],
            vault=vault, state_dir=state,
        )
        assert r1.returncode == 0

        note = _session_note(vault, SID)
        step1_note_exists = note.exists()
        step1_sidecar_exists = note.with_suffix(".json").exists() if step1_note_exists else False

        # Step 2: first candidate
        r2 = _run(
            ["session", "candidate", "--session-id", SID,
             "--kind", "decision", "--phase", "Build"],
            vault=vault, state_dir=state, stdin_text="first candidate\n",
        )
        assert r2.returncode == 0

        step2_text = note.read_text()
        step2_sidecar_exists = note.with_suffix(".json").exists()

        # Step 3: referenced after candidate
        r3 = _run(
            ["session", "referenced", "decision/something-else", "--session-id", SID],
            vault=vault, state_dir=state,
        )
        assert r3.returncode == 0

        step3_text = note.read_text()

        # --- Document current behavior ---
        # Step 1: referenced creates a note (CURRENT = lazy-create; SPEC = no-op or clean)
        assert step1_note_exists, "Step 1 current: referenced creates a note (GAP vs spec)"
        assert not step1_sidecar_exists, "Step 1 current: no sidecar (no status field)"

        # Step 2: candidate appends (note already existed from referenced)
        assert "first candidate" in step2_text, "Step 2: candidate appended"
        assert not step2_sidecar_exists, "Step 2 current: still no sidecar (GAP vs spec dirty)"

        # Step 3: referenced appended, did not change status (still no status)
        assert "decision/something-else" in step3_text, "Step 3: referenced appended"
        # Neither step added a status field
        assert "status:" not in step3_text, (
            "Neither candidate nor referenced writes a status field (current). "
            "Slice 1 must add status: dirty on first candidate."
        )
