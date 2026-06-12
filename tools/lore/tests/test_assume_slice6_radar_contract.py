"""EPHEMERAL assumption-prover test for Slice 6 (radar→follow-up migration).

Resolves the Known Unknown: what is the exact data contract of vault radar/
notes, what fields does radar_due.py key on, and what does the check-radar
skill reference?

REMOVE THIS FILE after Slice 6 is implemented and a proper behavioral test
covers the migration. (File: tools/lore/tests/test_assume_slice6_radar_contract.py,
all lines.)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# The real live vault — used read-only.
LIVE_VAULT = Path("/Users/tduffield/code/brain")
RADAR_DIR = LIVE_VAULT / "radar"

# ── helpers ───────────────────────────────────────────────────────────────────

def _frontmatter_of(path: Path) -> dict[str, str]:
    """Read frontmatter from a .md file using the same parser radar_due uses."""
    import frontmatter as fm
    return fm.parse_frontmatter(path)


def _all_radar_notes() -> list[Path]:
    """All real radar/*.md notes excluding _index.md, recursing into YYYY-MM/."""
    return [
        p for p in RADAR_DIR.rglob("*.md")
        if p.name != "_index.md"
    ]


# ── 1. type: field ─────────────────────────────────────────────────────────

def test_all_radar_notes_carry_type_radar():
    """Every radar note must have frontmatter type == 'radar'.

    This is the canonical value radar_due.py-adjacent consumers (status_validator,
    regenerate_indices) key on. The migration rewrites type: radar → type: follow-up
    only if the ENTIRE set carries this value.
    """
    notes = _all_radar_notes()
    assert notes, "Expected at least one radar note in the vault"
    bad = [str(p) for p in notes if _frontmatter_of(p).get("type") != "radar"]
    assert not bad, f"Notes missing type: radar → {bad}"


# ── 2. status vocabulary ────────────────────────────────────────────────────

def test_all_radar_notes_have_canonical_or_known_off_vocab_status():
    """Capture the complete status vocabulary in the live vault.

    Canonical: active | resolved | dropped  (status_validator.CANONICAL["radar"])
    Known off-vocab found in vault: 'closed' (1 note, 2026-04-27-claude-code-egress-dns-cache-overflow.md)

    This confirms radar_due.py's _CLOSED_STATUSES and _ACTIVE_STATUS cover the
    live data — and that 'closed' is an off-vocab outlier the migration must not
    silently lose.
    """
    from status_validator import CANONICAL

    canonical = CANONICAL["radar"]
    known_off_vocab = {"closed"}  # observed in 1 live note
    allowed = canonical | known_off_vocab

    notes = _all_radar_notes()
    bad = [
        (str(p), _frontmatter_of(p).get("status"))
        for p in notes
        if _frontmatter_of(p).get("status") not in allowed
    ]
    assert not bad, f"Unexpected status values: {bad}"


def test_one_note_carries_closed_status_off_vocab():
    """Verify the 'closed' outlier note is exactly 1 note (not silently growing).

    radar_due.py treats 'closed' as skipped_legacy (not _CLOSED_STATUSES) so this
    note will be flagged on every check-radar run until fixed.
    """
    notes = _all_radar_notes()
    closed = [p for p in notes if _frontmatter_of(p).get("status") == "closed"]
    assert len(closed) == 1, (
        f"Expected exactly 1 note with status: closed (off-vocab), found {len(closed)}: {closed}"
    )


# ── 3. check field variants ─────────────────────────────────────────────────

def test_canonical_check_field_name_is_check_not_check_interval():
    """radar_due.py reads fm.get('check', 'daily').

    This test documents that the live vault has schema drift: 1 note uses
    'check_interval' (underscore), 1 uses 'check-cadence' (hyphen) — both are
    silently treated as missing (default: daily) by radar_due.py.

    The migration must NOT rename these fields — they are vault data, and
    radar_due.py already handles the absence gracefully. This is a pre-existing
    drift that the migration does not need to fix.
    """
    notes = _all_radar_notes()
    check_interval_notes = [p for p in notes if "check_interval" in _frontmatter_of(p)]
    check_cadence_notes = [p for p in notes if "check-cadence" in _frontmatter_of(p)]

    # Assert the known counts — if these change the migration scope may grow.
    assert len(check_interval_notes) == 1, (
        f"Expected 1 note with check_interval (drift), got {len(check_interval_notes)}"
    )
    assert len(check_cadence_notes) == 1, (
        f"Expected 1 note with check-cadence (drift), got {len(check_cadence_notes)}"
    )


def test_last_checked_field_name_drift():
    """radar_due.py reads fm.get('last-checked', '').

    Some notes use 'last_checked' (underscore) — these are silently treated as
    missing (empty → bootstrap-poll). Documents the exact count.
    """
    notes = _all_radar_notes()
    underscore = [p for p in notes if "last_checked" in _frontmatter_of(p) and "last-checked" not in _frontmatter_of(p)]
    assert len(underscore) == 2, (
        f"Expected 2 notes with last_checked (drift), got {len(underscore)}: {underscore}"
    )


# ── 4. radar_due.py scans radar/ dir by path, not by type: field ─────────────

def test_radar_due_scans_by_directory_path_not_type_field(tmp_path):
    """radar_due.py uses iter_note_paths(radar_dir, recursive=True) — it does NOT
    filter by type: field. The dir is the selector.

    A note placed in radar/ with type: follow-up would still be scanned.
    A note placed in follow-ups/ with type: radar would NOT be scanned.

    Therefore the migration MUST move the files to follow-ups/ (dir rename) —
    rewriting type: alone is insufficient.
    """
    from radar_due import radar_notes_due
    import datetime

    # Note with type: banana (not radar) but placed in radar/ dir → still scanned
    radar = tmp_path / "radar"
    radar.mkdir()
    note = radar / "weird.md"
    note.write_text(
        "---\ntype: banana\nstatus: active\nsource: npm\ntarget: x\ncheck: daily\nadded: 2026-01-01\n"
        "last-checked: \nlast-state: \n---\n\n## What\ntest\n"
    )
    result = radar_notes_due(tmp_path, today=datetime.date(2026, 6, 12))
    assert any(p.name == "weird.md" for p in result.due), (
        "radar_due scans by dir path, not type: field — a non-radar type still gets polled"
    )


def test_radar_due_does_not_scan_follow_ups_dir(tmp_path):
    """After the migration, notes in follow-ups/ must NOT be scanned by radar_due.py.

    radar_due.py hardcodes <vault>/radar as its scan root. A renamed dir
    follow-ups/ is invisible to it.
    """
    from radar_due import radar_notes_due
    import datetime

    # Place a note in follow-ups/ (not radar/)
    follow_ups = tmp_path / "follow-ups"
    follow_ups.mkdir()
    note = follow_ups / "active.md"
    note.write_text(
        "---\ntype: follow-up\nstatus: active\nsource: npm\ntarget: x\ncheck: daily\nadded: 2026-01-01\n"
        "last-checked: \nlast-state: \n---\n\n## What\ntest\n"
    )
    result = radar_notes_due(tmp_path, today=datetime.date(2026, 6, 12))
    assert result.due == [] and result.manual == [], (
        "radar_due must NOT scan follow-ups/ — that dir is invisible to it; "
        "the retargeted check-in skill must point at follow-ups/ explicitly"
    )


# ── 5. regenerate_indices.py: scans by dir name ─────────────────────────────

def test_regenerate_indices_keys_on_dir_name_radar():
    """regenerate_indices.py line 462 maps ('radar', 'dated', [...date-keys...]).
    It scans the dir named 'radar' by hardcoded name, NOT by type: field.

    After migration: the tuple must be updated to ('follow-ups', 'dated', [...]).
    The _index.md header text also hardcodes 'radar' in its human-readable label.
    """
    regen_path = REPO_ROOT / "plugins" / "lore" / "scripts" / "regenerate_indices.py"
    source = regen_path.read_text()
    # Confirm the dir-name coupling exists
    assert '"radar"' in source or "'radar'" in source, (
        "regenerate_indices.py must reference 'radar' dir name — confirms it is a rename target"
    )
    # Confirm the specific tuple format
    assert "radar" in source and "dated" in source, (
        "expected the ('radar', 'dated', ...) mapping in regenerate_indices.py"
    )


# ── 6. status_validator.py: keys on both type: and dir name ─────────────────

def test_status_validator_has_radar_type_alias():
    """status_validator._TYPE_ALIASES maps 'radar' (singular type: value) → 'radar' (dir key).

    After migration, both must change:
      CANONICAL: 'radar' key → 'follow-ups' key (or a new 'follow-up' key)
      _TYPE_ALIASES: 'radar' singular → 'follow-up' singular
    """
    from status_validator import CANONICAL, _TYPE_ALIASES
    assert "radar" in CANONICAL, "CANONICAL must have a 'radar' key pre-migration"
    assert "radar" in _TYPE_ALIASES, "_TYPE_ALIASES must have a 'radar' singular key pre-migration"
    assert _TYPE_ALIASES["radar"] == "radar", "expected 'radar' → 'radar' alias pre-migration"


# ── 7. note count ────────────────────────────────────────────────────────────

def test_radar_note_count():
    """Live vault has 27 radar notes (excluding _index.md), not 28 as stated in the plan.

    Plan says 28 — actual count is 27. Migration script should use the filesystem,
    not the hardcoded count, for the pre-migration manifest.
    """
    notes = _all_radar_notes()
    assert len(notes) == 27, (
        f"Expected 27 radar notes (plan says 28 — plan is stale); found {len(notes)}"
    )


# ── 8. _index.md presence and its dir-coupled wikilinks ─────────────────────

def test_radar_index_exists_and_references_radar_dir():
    """radar/_index.md exists and its wikilinks use the radar/ dir prefix.

    After migration to follow-ups/, all [[radar/...]] links in the index must
    become [[follow-ups/...]]. Since _index.md is auto-generated by
    regenerate_indices.py, this is handled automatically once the dir-name tuple
    is updated — but the migration must trigger a regeneration.
    """
    index = RADAR_DIR / "_index.md"
    assert index.exists(), "radar/_index.md must exist"
    content = index.read_text()
    assert "[[radar/" in content, (
        "_index.md must contain [[radar/...]] wikilinks that need updating post-migration"
    )


# ── 9. check-radar skill hardcodes radar/ path ───────────────────────────────

def test_check_radar_skill_hardcodes_radar_path():
    """check-radar/SKILL.md hardcodes '$LORE_VAULT/radar/*.md' as the scan path.

    After renaming to check-in/SKILL.md, the body must also update this path
    to '$LORE_VAULT/follow-ups/' (or equivalent).
    """
    skill_path = REPO_ROOT / "plugins" / "lore" / "skills" / "check-radar" / "SKILL.md"
    content = skill_path.read_text()
    assert "radar/" in content or "radar" in content, (
        "check-radar SKILL.md must reference 'radar/' path — confirms rename target"
    )
    assert "$LORE_VAULT/radar" in content, (
        "check-radar SKILL.md hardcodes '$LORE_VAULT/radar' — must be updated to follow-ups/"
    )


# ── 10. radar skill uses 'lore new radar' subcommand ─────────────────────────

def test_radar_skill_uses_lore_new_radar_subcommand():
    """radar/SKILL.md calls 'lore new radar' — the 'new' subcommand's bucket arg.

    After migration, the skill must become 'lore new follow-up' (or whatever
    the lore CLI bucket name becomes). This is distinct from the skill dir rename.
    """
    skill_path = REPO_ROOT / "plugins" / "lore" / "skills" / "radar" / "SKILL.md"
    content = skill_path.read_text()
    assert "lore new radar" in content, (
        "radar SKILL.md must call 'lore new radar' — this is the CLI bucket arg to rename"
    )
