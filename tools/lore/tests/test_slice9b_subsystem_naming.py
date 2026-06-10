"""Slice 9b tests: area notes use bare-name (no date prefix) filenames + recall integration.

Covers (rederived from D23 area/D8b/D1 invariants):
- lore new area --title "Auth Flow" → areas/auth-flow.md (no date prefix; stem matches name)
- Re-running the same command → non-zero exit, no second file
- deferred/dead-end still get YYYY-MM-DD- prefixed names (area change is scoped to areas/)
- INTEGRATION: create area via real CLI, capture deferred with --surfaces, recall surfaces it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import CLI_PATH, SCRIPTS_DIR, load_script

TODAY = "2026-06-02"


def run_cli(args, env=None, input_text=None, cwd=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    full_env.setdefault("LORE_TODAY", TODAY)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, input=input_text,
        cwd=str(cwd) if cwd else None,
    )


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for d in ("deferred", "dead-ends", "decisions", "radar", "areas", "sessions",
              "lessons"):
        (vault / d).mkdir(parents=True)
    return vault


def _find_notes(dir_path: Path) -> list[Path]:
    return sorted(list(dir_path.glob("*.md")) + list(dir_path.glob("*/*.md")))


# ---------------------------------------------------------------------------
# Area notes must use bare-name (no date prefix) filenames
# ---------------------------------------------------------------------------

class TestAreaNoDatePrefix:
    def test_area_note_has_no_date_prefix(self, tmp_path):
        """lore new area --title 'Auth Flow' must produce
        areas/auth-flow.md — NOT areas/2026-06-02-auth-flow.md."""
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "area", "--vault", str(vault),
            "--title", "Auth Flow",
            "--keywords", "auth,oauth",
        ])
        assert r.returncode == 0, r.stderr
        notes = _find_notes(vault / "areas")
        assert len(notes) == 1, f"Expected 1 note, got {[n.name for n in notes]}"
        note = notes[0]
        assert note.name == "auth-flow.md", (
            f"Expected auth-flow.md, got {note.name!r}"
        )

    def test_area_stem_matches_logical_name(self, tmp_path):
        """The file stem of an area note must be the bare kebab title
        (no date, no numeric suffix), so recall can match it by name."""
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "area", "--vault", str(vault),
            "--title", "auth module",
            "--keywords", "auth",
        ])
        assert r.returncode == 0, r.stderr
        notes = _find_notes(vault / "areas")
        assert len(notes) == 1
        assert notes[0].stem == "auth-module", (
            f"Expected stem 'auth-module', got {notes[0].stem!r}"
        )

    def test_area_frontmatter_name_agrees_with_stem(self, tmp_path):
        """The note's name: frontmatter field must match the filename stem."""
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "area", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        notes = _find_notes(vault / "areas")
        assert len(notes) == 1
        fm = fm_mod.parse_frontmatter(notes[0])
        assert fm.get("name") == notes[0].stem, (
            f"name frontmatter {fm.get('name')!r} does not match stem {notes[0].stem!r}"
        )


# ---------------------------------------------------------------------------
# Re-creating an existing area profile must be refused
# ---------------------------------------------------------------------------

class TestAreaRefuseOnDuplicate:
    def test_second_creation_exits_nonzero(self, tmp_path):
        """Running lore new area twice with the same title must fail
        on the second run (non-zero exit)."""
        vault = _make_vault(tmp_path)
        r1 = run_cli([
            "new", "area", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r1.returncode == 0, r1.stderr

        r2 = run_cli([
            "new", "area", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r2.returncode != 0, (
            "Expected non-zero exit when recreating existing area profile"
        )

    def test_second_creation_writes_no_second_file(self, tmp_path):
        """The second lore new area must not create a second file."""
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "area", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        run_cli([
            "new", "area", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        notes = _find_notes(vault / "areas")
        assert len(notes) == 1, (
            f"Expected exactly 1 note after duplicate create, got {[n.name for n in notes]}"
        )


# ---------------------------------------------------------------------------
# Other note types still get YYYY-MM-DD- prefixes (change is scoped to areas/)
# ---------------------------------------------------------------------------

class TestDatedNamingUnchangedForOtherTypes:
    def test_deferred_note_still_has_date_prefix(self, tmp_path):
        """Deferred notes must still use YYYY-MM-DD-<slug>.md naming."""
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "a deferred thing",
        ])
        assert r.returncode == 0, r.stderr
        notes = _find_notes(vault / "deferred")
        assert len(notes) == 1
        assert notes[0].name.startswith(TODAY), (
            f"Deferred note should start with date {TODAY!r}, got {notes[0].name!r}"
        )

    def test_dead_end_note_still_has_date_prefix(self, tmp_path):
        """Dead-end notes must still use YYYY-MM-DD-<slug>.md naming."""
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "a dead end",
        ])
        assert r.returncode == 0, r.stderr
        notes = _find_notes(vault / "dead-ends")
        assert len(notes) == 1
        assert notes[0].name.startswith(TODAY), (
            f"Dead-end note should start with date {TODAY!r}, got {notes[0].name!r}"
        )


# ---------------------------------------------------------------------------
# INTEGRATION TEST: capture→recall with area created via real CLI
# ---------------------------------------------------------------------------

class TestCaptureRecallIntegrationViaCLI:
    """Proves that the capture→recall loop closes end-to-end when the area
    profile is created via the real `lore new area` CLI (not a hand-authored
    fixture)."""

    def test_recall_returns_area_name_not_dated(self, tmp_path):
        """After `lore new area --title payments --keywords pay`,
        `lore recall --areas payments` must succeed (not fail with 'no match')
        and show 'payments' in the banner — not a dated stem."""
        vault = _make_vault(tmp_path)

        r = run_cli([
            "new", "area", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r.returncode == 0, r.stderr

        r_recall = run_cli(["recall", "--areas", "payments", "--vault", str(vault)])
        assert r_recall.returncode == 0, f"lore recall failed: {r_recall.stderr}"

        assert "payments" in r_recall.stdout, (
            f"Expected 'payments' in recall banner, got: {r_recall.stdout!r}"
        )
        assert not any(
            d in r_recall.stdout
            for d in (f"{TODAY}-payments",)
        ), "Recall banner shows dated name — area file has date prefix"

    def test_full_capture_recall_loop_via_cli_area(self, tmp_path):
        """End-to-end integration:
        1. Create area via `lore new area --title payments --keywords pay`
        2. Capture `lore new deferred --surfaces payments`
        3. `lore recall --areas payments` surfaces the deferred note.
        """
        vault = _make_vault(tmp_path)

        r_area = run_cli([
            "new", "area", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r_area.returncode == 0, r_area.stderr

        r_def = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "Fix payment retry logic",
            "--surfaces", "payments",
        ])
        assert r_def.returncode == 0, r_def.stderr

        r_recall = run_cli(["recall", "--areas", "payments", "--vault", str(vault)])
        assert r_recall.returncode == 0, f"lore recall failed: {r_recall.stderr}"

        deferred_notes = _find_notes(vault / "deferred")
        assert len(deferred_notes) == 1
        note_stem = deferred_notes[0].stem
        assert note_stem in r_recall.stdout, (
            f"Capture→recall BROKEN: deferred note {note_stem!r} not in recall banner.\n"
            f"stdout:\n{r_recall.stdout}"
        )
        assert "Recalled (areas:" in r_recall.stdout
