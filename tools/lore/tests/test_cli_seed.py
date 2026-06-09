"""Tests for `lore seed <name>` — scaffold + open an inbox capture.

Covers:
- seed writes a note <kebab>.md under inbox/ (no date prefix)
- frontmatter carries type: inbox, a date, and a status
- the H1 heading is the human-readable name (not the kebab slug)
- no unresolved {{...}} placeholder survives
- status passes the status_validator (inbox is an untracked, unconstrained type)
- re-seeding the same name is idempotent: opens the existing note,
  never overwrites it, never creates a duplicate
- the note is opened in Obsidian via an obsidian://open?path=<abs> URI
- --no-open skips the launch entirely

All fixtures are SYNTHETIC. Tests run against a temp vault — never $LORE_VAULT.
The Obsidian launch is exercised through a recorder stub wired via
$LORE_OPEN_BIN, so no test ever spawns a real Obsidian.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from conftest import CLI_PATH, load_script

TODAY = "2026-01-15"  # frozen for determinism


def run_cli(args, env=None, cwd=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    full_env.setdefault("LORE_TODAY", TODAY)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env,
        cwd=str(cwd) if cwd else None,
    )


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for d in ("inbox", "sessions"):
        (vault / d).mkdir(parents=True)
    return vault


def _recorder(tmp_path: Path) -> tuple[Path, Path]:
    """Create an executable stub that records its argv to a marker file.

    Returns (opener_path, marker_path). Wire it in via $LORE_OPEN_BIN so the
    seed command's open path is exercised without launching real Obsidian.
    """
    marker = tmp_path / "opened.txt"
    opener = tmp_path / "rec.sh"
    opener.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" >> {marker}\n'
    )
    opener.chmod(0o755)
    return opener, marker


class TestSeed:
    def test_creates_note_in_inbox(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["seed", "Artist plugin idea", "--vault", str(vault), "--no-open"],
        )
        assert r.returncode == 0, r.stderr + r.stdout
        note = vault / "inbox" / "artist-plugin-idea.md"
        assert note.is_file()

    def test_frontmatter_type_is_inbox(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(["seed", "Artist plugin idea", "--vault", str(vault), "--no-open"])
        fm_mod = load_script("frontmatter")
        note = vault / "inbox" / "artist-plugin-idea.md"
        fm = fm_mod.parse_frontmatter(note)
        assert fm["type"] == "inbox"

    def test_frontmatter_has_date_and_status(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(["seed", "Artist plugin idea", "--vault", str(vault), "--no-open"])
        fm_mod = load_script("frontmatter")
        note = vault / "inbox" / "artist-plugin-idea.md"
        fm = fm_mod.parse_frontmatter(note)
        assert fm["date"] == TODAY
        assert fm.get("status")

    def test_heading_is_human_name(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(["seed", "Artist plugin idea", "--vault", str(vault), "--no-open"])
        note = vault / "inbox" / "artist-plugin-idea.md"
        assert "# Artist plugin idea" in note.read_text()

    def test_no_unresolved_placeholders(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(["seed", "Artist plugin idea", "--vault", str(vault), "--no-open"])
        note = vault / "inbox" / "artist-plugin-idea.md"
        text = note.read_text()
        assert not re.search(r"\{\{[a-z][a-z0-9_-]*\}\}", text), (
            "Rendered inbox note still contains unresolved {{...}} placeholder(s)"
        )

    def test_status_passes_validator(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(["seed", "Artist plugin idea", "--vault", str(vault), "--no-open"])
        sv = load_script("status_validator")
        fm_mod = load_script("frontmatter")
        note = vault / "inbox" / "artist-plugin-idea.md"
        fm = fm_mod.parse_frontmatter(note)
        assert sv.is_valid_status(fm["type"], fm["status"])

    def test_reseed_is_idempotent(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(["seed", "Artist plugin idea", "--vault", str(vault), "--no-open"])
        note = vault / "inbox" / "artist-plugin-idea.md"
        # User edits the note after the first seed.
        note.write_text(note.read_text() + "\nMy own notes here.\n")

        # Re-seed on a DIFFERENT day — the name has no date prefix, so it must
        # still resolve to the same note rather than creating a second one.
        r2 = run_cli(
            ["seed", "Artist plugin idea", "--vault", str(vault), "--no-open"],
            env={"LORE_TODAY": "2026-02-20"},
        )
        assert r2.returncode == 0, r2.stderr + r2.stdout
        # No duplicate created.
        assert list((vault / "inbox").glob("*.md")) == [note]
        # Existing content preserved (not overwritten).
        assert "My own notes here." in note.read_text()

    def test_open_invoked_with_obsidian_uri(self, tmp_path):
        vault = _make_vault(tmp_path)
        opener, marker = _recorder(tmp_path)
        r = run_cli(
            ["seed", "Artist plugin idea", "--vault", str(vault)],
            env={"LORE_OPEN_BIN": str(opener)},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert marker.is_file(), "opener was never invoked"
        recorded = marker.read_text().strip()
        note = vault / "inbox" / "artist-plugin-idea.md"
        assert recorded.startswith("obsidian://open?path=")
        assert str(note) in recorded

    def test_no_open_skips_launch(self, tmp_path):
        vault = _make_vault(tmp_path)
        opener, marker = _recorder(tmp_path)
        r = run_cli(
            ["seed", "Artist plugin idea", "--vault", str(vault), "--no-open"],
            env={"LORE_OPEN_BIN": str(opener)},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert not marker.exists(), "opener should not run under --no-open"
