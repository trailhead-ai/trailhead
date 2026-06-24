"""Behavioral tests for Slice 6 — migration adopts the ``{dirty, clean}`` session vocab.

Slice 0 changed ``record_model.STATUS_VOCAB["session"]`` to ``("dirty","clean")``.
``migrate_vault.py`` carries its **own** private ``STATUS_VOCAB`` copy and the
generic ``_STATUS_REMAP`` only fires for values *not already in* the target vocab —
so legacy ``active``/``complete`` (the OLD in-vocab session values) passed through
unremapped and would then FAIL the new ``record_model`` validator (Council Critical 1,
KU5). This slice makes the migration ADOPT the new vocab:

- private ``STATUS_VOCAB["session"]`` → ``("dirty","clean")``;
- an explicit ``_SESSION_STATUS_REMAP = {"complete":"clean","active":"dirty"}`` that
  fires on the MAPPED result for the session kind (so it covers both the direct
  ``active``/``complete`` path AND the ``shelved → complete`` second path);
- the ``session/null`` artifact (legacy ``session_id: null`` → string ``"null"``) is
  repaired by falling back to the title-derived, sanitized slug.

The migration module is **pure** (read + transcode; no writer/orchestrator), so the
"after migration" assertions operate on ``transcode`` output, and "idempotent re-run"
means re-transcoding an already-migrated record (status ``dirty``/``clean``) is a
no-op that still validates.

This module is EPHEMERAL: it is deleted together with migrate_vault.py in the
post-cutover commit.
"""

from pathlib import Path

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


def _status_errors(result) -> list[str]:
    return [e for e in result.errors if "status" in e]


# ===========================================================================
# Private vocab adoption
# ===========================================================================


def test_private_status_vocab_adopts_dirty_clean():
    """The migration's own STATUS_VOCAB copy uses the new ``(dirty, clean)`` session vocab."""
    mv = _migrate()
    assert mv.STATUS_VOCAB["session"] == ("dirty", "clean")


# ===========================================================================
# active / complete → dirty / clean (the remapped result validates)
# ===========================================================================


@pytest.mark.parametrize(
    "legacy_status,expected",
    [("active", "dirty"), ("complete", "clean")],
)
def test_legacy_session_status_remaps(tmp_path, legacy_status, expected):
    mv = _migrate()
    p = write_legacy(
        tmp_path,
        f"sessions/2026-05/s-{legacy_status}.md",
        f"type: session\nsession_id: id-{legacy_status}\nstatus: {legacy_status}\ndate: 2026-05-01",
    )
    t = mv.transcode(mv.read_legacy(p))
    assert t.kind == "session"
    assert t.sidecar["status"] == expected


def test_both_states_fixture_vault_zero_validator_violations(tmp_path):
    """A fixture vault with session records in BOTH active and complete → after migration
    EVERY migrated session record passes record_model validation (the gate proving the remap fired)."""
    mv = _migrate()
    rm = _record_model()

    specs = [
        ("active", "id-a1"),
        ("active", "id-a2"),
        ("complete", "id-c1"),
        ("complete", "id-c2"),
    ]
    expected = {"active": "dirty", "complete": "clean"}

    violations = []
    for i, (status, sid) in enumerate(specs):
        p = write_legacy(
            tmp_path,
            f"sessions/2026-05/s{i}.md",
            f"type: session\nsession_id: {sid}\nstatus: {status}\ndate: 2026-05-01",
        )
        t = mv.transcode(mv.read_legacy(p))
        assert t.sidecar["status"] == expected[status]
        result = rm.validate(t.sidecar, kind="session")
        if result.errors:
            violations.append((status, sid, result.errors))

    assert not violations, f"session records failed record_model validation: {violations!r}"


# ===========================================================================
# Second path: shelved legacy session → clean (proves the remap fires on the
# MAPPED result, covering the ``elif kind == 'session': mapped = 'complete'`` branch)
# ===========================================================================


def test_shelved_session_becomes_clean(tmp_path):
    mv = _migrate()
    rm = _record_model()
    p = write_legacy(
        tmp_path,
        "sessions/2026-05/shelved.md",
        "type: session\nsession_id: id-shelved\nstatus: shelved\ndate: 2026-05-01",
    )
    t = mv.transcode(mv.read_legacy(p))
    assert t.sidecar["status"] == "clean"
    assert not _status_errors(rm.validate(t.sidecar, kind="session"))


# ===========================================================================
# null-keyed session repair: name falls back to the sanitized title slug
# ===========================================================================


def test_null_session_id_repaired_to_title_slug(tmp_path):
    """Legacy ``session_id: null`` (parsed as the string 'null') is treated as absent and
    the record is named by its sanitized title slug, never ``null``."""
    mv = _migrate()
    p = write_legacy(
        tmp_path,
        "sessions/2026-06/2026-06-01-2008-pr-dashboard.md",
        "type: session\nsession_id: null\nstatus: complete\ndate: 2026-06-01",
        body="\n## What we did\n\nBuilt the PR dashboard.\n",
    )
    t = mv.transcode(mv.read_legacy(p))
    assert t.kind == "session"
    assert t.name != "null"
    assert t.name == "2026-06-01-2008-pr-dashboard"


def test_null_session_slug_passes_filename_sanitization(tmp_path):
    """The repaired name is a valid kebab filename (lowercase, no special chars, no slashes)."""
    import re

    mv = _migrate()
    # A messy title that needs real sanitization (uppercase, spaces, punctuation).
    p = write_legacy(
        tmp_path,
        "sessions/2026-06/PR Dashboard! (v2).md",
        "type: session\nsession_id: null\nstatus: complete\ndate: 2026-06-01",
    )
    t = mv.transcode(mv.read_legacy(p))
    assert t.name != "null"
    assert re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", t.name), f"bad slug: {t.name!r}"
    assert "/" not in t.name and ".." not in t.name


def test_null_session_id_record_validates(tmp_path):
    mv = _migrate()
    rm = _record_model()
    p = write_legacy(
        tmp_path,
        "sessions/2026-06/2026-06-01-2008-pr-dashboard.md",
        "type: session\nsession_id: null\nstatus: complete\ndate: 2026-06-01",
    )
    t = mv.transcode(mv.read_legacy(p))
    result = rm.validate(t.sidecar, kind="session")
    assert not result.errors, f"null-repaired session failed validation: {result.errors!r}"


def test_null_session_id_no_silent_missing_id_review_flag(tmp_path):
    """The null case is repaired, not flagged as a missing session_id (it has a real title)."""
    mv = _migrate()
    p = write_legacy(
        tmp_path,
        "sessions/2026-06/2026-06-01-2008-pr-dashboard.md",
        "type: session\nsession_id: null\nstatus: complete\ndate: 2026-06-01",
    )
    t = mv.transcode(mv.read_legacy(p))
    assert not [f for f in t.flags if f.kind == "review" and "session_id" in f.detail]


def test_genuinely_missing_session_id_still_repaired_to_title(tmp_path):
    """A session with no session_id at all is also repaired to its title slug (general fix)."""
    mv = _migrate()
    rm = _record_model()
    p = write_legacy(
        tmp_path,
        "sessions/2026-06/2026-06-02-some-work.md",
        "type: session\nstatus: complete\ndate: 2026-06-02",
    )
    t = mv.transcode(mv.read_legacy(p))
    assert t.name == "2026-06-02-some-work"
    assert not rm.validate(t.sidecar, kind="session").errors


# ===========================================================================
# Idempotency: re-transcoding an already-migrated record (dirty/clean) is a no-op
# ===========================================================================


@pytest.mark.parametrize("migrated_status", ["dirty", "clean"])
def test_remigration_of_migrated_session_is_noop(tmp_path, migrated_status):
    mv = _migrate()
    rm = _record_model()
    p = write_legacy(
        tmp_path,
        f"sessions/2026-05/already-{migrated_status}.md",
        f"type: session\nsession_id: id-{migrated_status}\nstatus: {migrated_status}\ndate: 2026-05-01",
    )
    t1 = mv.transcode(mv.read_legacy(p))
    t2 = mv.transcode(mv.read_legacy(p))
    assert t1.sidecar["status"] == migrated_status
    assert t2.sidecar["status"] == migrated_status
    assert t1.name == t2.name
    assert not rm.validate(t2.sidecar, kind="session").errors
