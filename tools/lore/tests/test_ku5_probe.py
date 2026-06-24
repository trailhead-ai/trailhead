"""KU5 assumption probe — ephemeral; delete after Slice 6 is shipped.

Resolves 6 claims in KU5 (plan lore-clean-dirty-sessions-flush-and-singular-dir-standardization):

1. migrate_vault.py carries its own STATUS_VOCAB copy with session:("active","complete") — independent of record_model.
2. _STATUS_REMAP only fires for values NOT already in the target vocab.
3. THE BUG: because active/complete ARE in the private vocab, the generic remap never fires
   for them — so after migration a session record still carries active/complete, which
   Slice 0's validator (session:{"dirty","clean"}) now REJECTS.
4. The proposed fix shape (update private vocab to ("dirty","clean") + explicit
   _SESSION_STATUS_REMAP = {"complete":"clean","active":"dirty"} applied BEFORE the generic
   remap) resolves the bug: complete→clean, active→dirty, zero validator violations.
5. The session/null artifact: session_id: null in YAML is read as string "null" by the
   bespoke parser, passes the isinstance(str)+truthy check, so name="null" → session/null.json.
6. Idempotency: re-running transcode on an already-migrated record (active/complete) is
   NOT idempotent post-Slice-0 — it still emits active/complete — but the FIX is idempotent
   (dirty/clean → dirty/clean unchanged on re-run).

This file: tools/lore/tests/test_ku5_probe.py
Clean up after Slice 6 is merged.
"""

from pathlib import Path
from typing import Any

import pytest

from conftest import load_script


def _migrate():
    return load_script("migrate_vault")


def _record_model():
    return load_script("record_model")


def write_legacy(root: Path, rel: str, frontmatter: str, body: str = "\nBody.\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Claim 1: migrate_vault.py has its own STATUS_VOCAB with session:("active","complete")
# ---------------------------------------------------------------------------


def test_claim1_migrate_vault_has_own_status_vocab_with_legacy_session_vocab():
    """migrate_vault.STATUS_VOCAB["session"] == ("active","complete") — independent copy."""
    mod = _migrate()
    assert "STATUS_VOCAB" in dir(mod), "migrate_vault must expose STATUS_VOCAB"
    assert mod.STATUS_VOCAB["session"] == ("active", "complete"), (
        f"Expected ('active','complete') but got {mod.STATUS_VOCAB['session']!r}. "
        "Claim 1 FAILS: the private copy was already updated."
    )


def test_claim1_private_vocab_differs_from_record_model():
    """The migrate_vault STATUS_VOCAB is NOT the same object as record_model.STATUS_VOCAB."""
    mv = _migrate()
    rm = _record_model()
    # After Slice 0, record_model has ("dirty","clean"); migrate_vault still has ("active","complete")
    assert rm.STATUS_VOCAB["session"] == ("dirty", "clean"), (
        "record_model.STATUS_VOCAB['session'] must be ('dirty','clean') — Slice 0 must be done."
    )
    assert mv.STATUS_VOCAB["session"] != rm.STATUS_VOCAB["session"], (
        "migrate_vault and record_model SESSION vocabs should differ — the private copy is stale."
    )


# ---------------------------------------------------------------------------
# Claim 2: _STATUS_REMAP only fires for values NOT already in the target vocab
# ---------------------------------------------------------------------------


def test_claim2_generic_remap_skips_values_already_in_vocab(tmp_path):
    """active/complete pass through _map_status unchanged because they ARE in the private vocab."""
    mod = _migrate()

    # active is in ("active","complete") — so _map_status returns it as-is
    status, prose, flag = mod._map_status("active", "session")
    assert status == "active", f"Expected 'active' unchanged, got {status!r}"
    assert flag is None, f"Expected no flag for in-vocab value, got {flag!r}"

    # complete is in ("active","complete") — same
    status, prose, flag = mod._map_status("complete", "session")
    assert status == "complete", f"Expected 'complete' unchanged, got {status!r}"
    assert flag is None, f"Expected no flag for in-vocab value, got {flag!r}"

    # Verify the remap path: a value NOT in the vocab gets remapped
    # e.g. "resolved" is in _STATUS_REMAP -> "active"
    status, prose, flag = mod._map_status("resolved", "session")
    assert status == "active", f"Expected 'resolved'→'active' via remap, got {status!r}"


# ---------------------------------------------------------------------------
# Claim 3: THE BUG — after transcode, session records carry active/complete
#          which Slice 0's validator now REJECTS
# ---------------------------------------------------------------------------


def test_claim3_bug_active_session_fails_record_model_validation(tmp_path):
    """Transcoded session with status:active fails record_model validation (new vocab: dirty/clean)."""
    mv = _migrate()
    rm = _record_model()

    # Write a legacy session with status: active
    path = write_legacy(
        tmp_path,
        "sessions/2026-05-01-pr-dashboard.md",
        "type: session\nsession_id: abc-123\nstatus: active\ndate: 2026-05-01",
    )
    legacy = mv.read_legacy(path)
    transcoded = mv.transcode(legacy)

    assert transcoded.kind == "session"
    assert transcoded.sidecar["status"] == "active", (
        f"Bug not present: expected 'active' in transcoded status, got {transcoded.sidecar['status']!r}"
    )

    # Now validate against record_model (Slice 0's new vocab)
    result = rm.validate(transcoded.sidecar, kind="session")
    status_errors = [e for e in result.errors if "status" in e]
    assert status_errors, (
        "BUG NOT REPRODUCED: record_model accepted 'active' for session. "
        f"All errors: {result.errors!r}"
    )


def test_claim3_bug_complete_session_fails_record_model_validation(tmp_path):
    """Transcoded session with status:complete fails record_model validation (new vocab: dirty/clean)."""
    mv = _migrate()
    rm = _record_model()

    path = write_legacy(
        tmp_path,
        "sessions/2026-05-02-work.md",
        "type: session\nsession_id: def-456\nstatus: complete\ndate: 2026-05-02",
    )
    legacy = mv.read_legacy(path)
    transcoded = mv.transcode(legacy)

    assert transcoded.sidecar["status"] == "complete", (
        f"Bug not present: expected 'complete', got {transcoded.sidecar['status']!r}"
    )

    result = rm.validate(transcoded.sidecar, kind="session")
    status_errors = [e for e in result.errors if "status" in e]
    assert status_errors, (
        "BUG NOT REPRODUCED: record_model accepted 'complete' for session. "
        f"All errors: {result.errors!r}"
    )


# ---------------------------------------------------------------------------
# Claim 4: The proposed fix shape resolves the bug
#          Monkeypatch: STATUS_VOCAB["session"] = ("dirty","clean")
#          + _SESSION_STATUS_REMAP = {"complete":"clean","active":"dirty"}
#          applied BEFORE the generic remap.
# ---------------------------------------------------------------------------


def _map_status_fixed(value: Any, kind: str, mv_mod) -> tuple[str, Any, Any]:
    """Apply the proposed fix: explicit session remap BEFORE generic _map_status.

    This simulates the Slice 6 fix shape without modifying migrate_vault.py:
    - If kind=="session" and value is "active" or "complete", remap explicitly.
    - Then delegate to _map_status with the UPDATED private vocab.
    """
    _SESSION_STATUS_REMAP = {"complete": "clean", "active": "dirty"}
    _FIXED_SESSION_VOCAB = ("dirty", "clean")

    if kind == "session" and isinstance(value, str) and value.strip() in _SESSION_STATUS_REMAP:
        return _SESSION_STATUS_REMAP[value.strip()], None, None

    # For any other session status, patch the private vocab and call the real function
    original_vocab = mv_mod.STATUS_VOCAB["session"]
    mv_mod.STATUS_VOCAB["session"] = _FIXED_SESSION_VOCAB
    try:
        return mv_mod._map_status(value, kind)
    finally:
        mv_mod.STATUS_VOCAB["session"] = original_vocab


@pytest.mark.parametrize("legacy_status,expected_new_status", [
    ("active", "dirty"),
    ("complete", "clean"),
])
def test_claim4_fix_shape_remaps_correctly(tmp_path, legacy_status, expected_new_status):
    """With the fix shape, active→dirty and complete→clean for session kind."""
    mv = _migrate()
    rm = _record_model()

    path = write_legacy(
        tmp_path,
        f"sessions/2026-05-fix-{legacy_status}.md",
        f"type: session\nsession_id: fix-{legacy_status}\nstatus: {legacy_status}\ndate: 2026-05-01",
    )
    legacy = mv.read_legacy(path)
    transcoded = mv.transcode(legacy)

    # Apply the fix shape to the already-transcoded sidecar
    fixed_status, _, _ = _map_status_fixed(legacy_status, "session", mv)
    fixed_sidecar = dict(transcoded.sidecar)
    fixed_sidecar["status"] = fixed_status

    assert fixed_sidecar["status"] == expected_new_status, (
        f"Fix shape: {legacy_status!r} should → {expected_new_status!r}, "
        f"got {fixed_sidecar['status']!r}"
    )

    # Zero validator violations after fix
    result = rm.validate(fixed_sidecar, kind="session")
    status_errors = [e for e in result.errors if "status" in e]
    assert not status_errors, (
        f"Fix shape still fails validation: {status_errors!r}"
    )


def test_claim4_fix_shape_both_states_zero_validator_violations(tmp_path):
    """Fixture vault with active AND complete session records → zero validator violations after fix."""
    mv = _migrate()
    rm = _record_model()
    _SESSION_STATUS_REMAP = {"complete": "clean", "active": "dirty"}

    violations = []
    for i, status in enumerate(["active", "complete"]):
        path = write_legacy(
            tmp_path,
            f"sessions/2026-05-test-{i}.md",
            f"type: session\nsession_id: test-{i}\nstatus: {status}\ndate: 2026-05-01",
        )
        legacy = mv.read_legacy(path)
        transcoded = mv.transcode(legacy)

        fixed_sidecar = dict(transcoded.sidecar)
        if fixed_sidecar.get("status") in _SESSION_STATUS_REMAP:
            fixed_sidecar["status"] = _SESSION_STATUS_REMAP[fixed_sidecar["status"]]

        result = rm.validate(fixed_sidecar, kind="session")
        status_errors = [e for e in result.errors if "status" in e]
        if status_errors:
            violations.append((status, status_errors))

    assert not violations, (
        f"Fix shape: {len(violations)} session record(s) still fail validation: {violations!r}"
    )


# ---------------------------------------------------------------------------
# Claim 5: The session/null artifact — session_id: null in YAML → name="null"
# ---------------------------------------------------------------------------


def test_claim5_yaml_null_session_id_produces_null_name(tmp_path):
    """session_id: null in YAML is read as string 'null' by the bespoke parser.

    Because isinstance('null', str) and 'null'.strip() is truthy, the transcode
    session-naming block sets name='null' → session/null.{md,json}.
    """
    mv = _migrate()

    # The legacy frontmatter parser reads 'null' as a Python string (not None)
    fm = mv._parse_frontmatter("type: session\nsession_id: null\nstatus: complete")
    session_id_value = fm.get("session_id")
    assert session_id_value == "null", (
        f"Expected string 'null', got {session_id_value!r}. "
        "The bespoke parser must be treating YAML null as Python None."
    )
    # The transcode check: isinstance(str) and truthy → name = 'null'
    assert isinstance(session_id_value, str) and session_id_value.strip(), (
        "String 'null' must pass the isinstance+strip check so name='null'"
    )


def test_claim5_transcode_produces_null_name(tmp_path):
    """Transcoding a session with session_id: null yields name='null'."""
    mv = _migrate()

    path = write_legacy(
        tmp_path,
        "sessions/2026-06-01-pr-dashboard.md",
        "type: session\nsession_id: null\nstatus: complete\ndate: 2026-06-01",
        body="\n## What we did\n\nBuild stuff.\n",
    )
    legacy = mv.read_legacy(path)
    transcoded = mv.transcode(legacy)

    assert transcoded.name == "null", (
        f"Expected name='null', got {transcoded.name!r}. "
        "The null-key production path is not present as expected."
    )
    assert transcoded.kind == "session"
    # No Flag.review for session_id because 'null' IS a string — the bug is silent
    review_flags = [f for f in transcoded.flags if f.kind == "review" and "session_id" in f.detail]
    assert not review_flags, (
        "Expected NO review flag for session_id (the null string passes the isinstance check). "
        f"Got: {review_flags!r}"
    )


def test_claim5_repair_slug_from_title(tmp_path):
    """Repair: the real title '2026-06-01-2008-pr-dashboard' sanitizes to a valid slug.

    The slug must pass filename sanitization: lowercase-kebab, no special chars.
    This proves the repair path (rename null.json to its title-derived slug) is valid.
    """
    title = "2026-06-01-2008-pr-dashboard"
    # A basic sanitize: keep only [a-z0-9-], lowercase, strip leading/trailing hyphens
    import re
    slug = re.sub(r"[^a-z0-9-]", "-", title.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    assert slug == "2026-06-01-2008-pr-dashboard", f"Unexpected slug: {slug!r}"
    # The slug is already valid (no uppercase, no special chars) — round-trip is identity
    assert re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", slug), f"Slug fails pattern: {slug!r}"


# ---------------------------------------------------------------------------
# Claim 6: Idempotency — re-running transcode on already-migrated records
# ---------------------------------------------------------------------------


def test_claim6_migration_not_idempotent_prefix_bug(tmp_path):
    """PRE-FIX: migrating active/complete again still emits active/complete (NOT idempotent
    after Slice 0 because the validator rejects both values)."""
    mv = _migrate()
    rm = _record_model()

    path = write_legacy(
        tmp_path,
        "sessions/2026-05-idempotent.md",
        "type: session\nsession_id: idem-1\nstatus: active\ndate: 2026-05-01",
    )
    # First run
    t1 = mv.transcode(mv.read_legacy(path))
    assert t1.sidecar["status"] == "active"

    # Second run on same input — still emits active (no change, but still broken)
    t2 = mv.transcode(mv.read_legacy(path))
    assert t2.sidecar["status"] == "active", (
        "Pre-fix: re-running transcode should still produce 'active' (no change to input)"
    )

    # Both fail validation — idempotent but BROKEN
    r1 = rm.validate(t1.sidecar, kind="session")
    r2 = rm.validate(t2.sidecar, kind="session")
    assert [e for e in r1.errors if "status" in e], "Run 1 must fail validation"
    assert [e for e in r2.errors if "status" in e], "Run 2 must fail validation"


def test_claim6_fix_is_idempotent(tmp_path):
    """POST-FIX: a record already carrying dirty/clean passes through unchanged (idempotent re-run).

    With the fix in place (private vocab updated to ("dirty","clean")), a record that
    already has status:dirty or status:clean will pass `if base in vocab: return base`
    unchanged, so a re-run is a no-op.
    """
    mv = _migrate()
    rm = _record_model()
    _FIXED_SESSION_VOCAB = ("dirty", "clean")

    for already_migrated_status in ("dirty", "clean"):
        # Simulate a record that was already fixed (has dirty/clean in its status)
        path = write_legacy(
            tmp_path,
            f"sessions/2026-05-already-{already_migrated_status}.md",
            f"type: session\nsession_id: already-{already_migrated_status}\nstatus: {already_migrated_status}\ndate: 2026-05-01",
        )
        legacy = mv.read_legacy(path)

        # With fix applied (patch private vocab to dirty/clean)
        original_vocab = mv.STATUS_VOCAB["session"]
        mv.STATUS_VOCAB["session"] = _FIXED_SESSION_VOCAB
        try:
            transcoded = mv.transcode(legacy)
        finally:
            mv.STATUS_VOCAB["session"] = original_vocab

        # Status should be unchanged — idempotent
        assert transcoded.sidecar["status"] == already_migrated_status, (
            f"Fix not idempotent: {already_migrated_status!r} → {transcoded.sidecar['status']!r}"
        )

        # And still valid
        result = rm.validate(transcoded.sidecar, kind="session")
        status_errors = [e for e in result.errors if "status" in e]
        assert not status_errors, (
            f"Fixed re-run of {already_migrated_status!r} still fails: {status_errors!r}"
        )
