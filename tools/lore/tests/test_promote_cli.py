"""Slice 4 tests: `lore promote` CLI command.

Test contract (all must RED before implementation, GREEN after):

A-1: piped / non-TTY stdin → refused before any mint, stderr, nonzero,
     message includes the exact `lore promote … --to …` command.
D-2: `lore promote --yes` → refused.
D-1: no shared vault declared → named actionable error with config path + TOML snippet.
Happy path (single shared layer, simulated y):
  - preview shows source + dest + WARNING: SHARED line
  - on y → note COPIED to shared root, personal original intact, Promoted: line printed.
  - on n/EOF → nothing written, clean exit.
Multiple shared layers + no --to → "specify --to <name>" listing names, nothing written.
--to unknown layer → named error, no write.
note doesn't exist → named error, no write.
C-4: two groups claim cwd → promote raises overlap error (does NOT pick one).
Space/quote in note path → promoted copy round-trips correctly.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from conftest import CLI_PATH

TODAY = "2026-06-10"


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
    for d in ("deferred", "dead-ends", "decisions", "radar", "areas",
              "sessions", "plans", "specs"):
        (vault / d).mkdir(parents=True)
    return vault


def _make_note(vault: Path, subdir: str = "decisions", name: str = "my-note.md") -> Path:
    note_dir = vault / subdir / "2026-06"
    note_dir.mkdir(parents=True, exist_ok=True)
    note = note_dir / name
    note.write_text("---\ntype: decision\n---\n\n# My Decision\n\nBody text.\n")
    return note


def _write_group_config(
    groups_dir: Path,
    member_root: Path,
    shared_vaults: list[dict],
    group_name: str = "testgroup",
    filename: str | None = None,
) -> Path:
    """Write a minimal camp group config with shared vaults."""
    groups_dir.mkdir(parents=True, exist_ok=True)
    fn = filename or f"{group_name}.toml"
    cfg = groups_dir / fn
    lines = [
        f'[group]\nname = "{group_name}"\n\n',
        f'[[members]]\nname = "repo"\nrepo_root = "{member_root}"\n',
    ]
    for sv in shared_vaults:
        lines.append(
            f'\n[[shared_vaults]]\nname = "{sv["name"]}"\nroot = "{sv["root"]}"\n'
        )
    cfg.write_text("".join(lines))
    return cfg


# ---------------------------------------------------------------------------
# A-1: non-TTY stdin → refused before any mint
# ---------------------------------------------------------------------------

class TestPromoteRefusesNonTTY:
    def test_piped_stdin_refused_before_mint(self, tmp_path: Path) -> None:
        """A-1: lore promote with piped input (non-TTY) → nonzero exit, stderr, no mint."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault)

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        # Piping "y\n" simulates the agent self-approval attempt
        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode != 0, (
            "Expected nonzero exit when stdin is piped (non-TTY)"
        )
        # Refusal must go to stderr
        assert r.stderr, "Refusal message must appear on stderr"
        # Nothing written to shared root
        shared_files = list(shared_root.glob("**/*"))
        shared_files = [f for f in shared_files if f.is_file()]
        assert not shared_files, f"Nothing must be written to shared root: {shared_files}"

    def test_non_tty_refusal_includes_exact_command(self, tmp_path: Path) -> None:
        """D-2: non-TTY refusal message includes the exact lore promote command."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault)

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode != 0
        # The exact command the human must run must be in stderr
        assert "lore promote" in r.stderr, (
            f"D-2: refusal must include 'lore promote' command. stderr: {r.stderr!r}"
        )
        assert str(note) in r.stderr or note.name in r.stderr, (
            f"D-2: refusal must include the note path. stderr: {r.stderr!r}"
        )


# ---------------------------------------------------------------------------
# D-2: --yes is refused
# ---------------------------------------------------------------------------

class TestPromoteRefusesYesFlag:
    def test_yes_flag_refused(self, tmp_path: Path) -> None:
        """D-2: lore promote --yes → refused; no non-interactive promote allowed."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault)

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        r = run_cli(
            ["promote", str(note), "--yes"],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )
        assert r.returncode != 0, "Expected nonzero exit for --yes (non-interactive refused)"
        combined = r.stdout + r.stderr
        assert "promote" in combined.lower(), (
            f"Refusal for --yes must mention promote path. Got: {combined!r}"
        )
        # Nothing written to shared root
        shared_files = [f for f in shared_root.glob("**/*") if f.is_file()]
        assert not shared_files, f"Nothing written to shared root on --yes: {shared_files}"


# ---------------------------------------------------------------------------
# D-1: no shared vault declared → actionable error with config path + TOML snippet
# ---------------------------------------------------------------------------

class TestPromoteNoSharedVaultDeclared:
    def test_no_shared_vault_prints_actionable_error(self, tmp_path: Path) -> None:
        """D-1: lore promote with no shared vault → named actionable error."""
        vault = _make_vault(tmp_path)
        note = _make_note(vault)

        # Point at a group config with NO shared_vaults
        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        # Write a group config with no [[shared_vaults]]
        groups_dir.mkdir(parents=True)
        cfg = groups_dir / "testgroup.toml"
        cfg.write_text(
            '[group]\nname = "testgroup"\n\n'
            f'[[members]]\nname = "repo"\nrepo_root = "{cwd_dir}"\n'
        )

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            cwd=cwd_dir,
        )
        assert r.returncode != 0, "Expected nonzero exit when no shared vault"
        combined = r.stdout + r.stderr
        # Must mention shared vault and how to add one
        assert "shared" in combined.lower(), (
            f"Error must mention 'shared'. Got: {combined!r}"
        )
        # Must include TOML snippet hint
        assert "shared_vaults" in combined or "[[shared_vaults]]" in combined, (
            f"D-1: error must include [[shared_vaults]] snippet. Got: {combined!r}"
        )

    def test_no_group_at_all_prints_actionable_error(self, tmp_path: Path) -> None:
        """D-1: lore promote with no group config → actionable error with config path."""
        vault = _make_vault(tmp_path)
        note = _make_note(vault)

        # No group config at all — use empty groups dir
        empty_groups = tmp_path / "empty-groups"
        empty_groups.mkdir()

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(empty_groups),
                "LORE_SIMULATE_TTY": "1",
            },
        )
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "shared" in combined.lower(), (
            f"Error must mention 'shared'. Got: {combined!r}"
        )
        # Must mention how to add a shared vault
        assert "shared_vaults" in combined or "[[shared_vaults]]" in combined, (
            f"D-1: error must include [[shared_vaults]] snippet. Got: {combined!r}"
        )


# ---------------------------------------------------------------------------
# Happy path: single shared layer, confirm y
# ---------------------------------------------------------------------------

class TestPromoteHappyPath:
    def test_preview_shows_source_dest_and_warning(self, tmp_path: Path) -> None:
        """Preview includes source path, dest path, and WARNING: SHARED line."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault)

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        # Monkeypatch isatty to return True by injecting env var the CLI reads
        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="n\n",
            cwd=cwd_dir,
        )
        # n → clean exit, nothing written
        assert r.returncode == 0, f"n should produce clean exit. stderr: {r.stderr}"
        combined = r.stdout + r.stderr
        assert str(note) in combined or note.name in combined, (
            f"Preview must show source path. Got: {combined!r}"
        )
        assert "WARNING:" in combined, (
            f"D-5: WARNING: prefix required (not solely ⚠ glyph). Got: {combined!r}"
        )
        # Check dest is in output
        assert str(shared_root) in combined or "team" in combined.lower(), (
            f"Preview must show destination. Got: {combined!r}"
        )

    def test_confirm_y_copies_note_to_shared_root(self, tmp_path: Path) -> None:
        """On simulated y (TTY), note is copied to shared root; personal original intact."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault, name="my-decision.md")
        original_content = note.read_text()

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
        # Note copied to shared root
        dest = shared_root / note.name
        assert dest.exists(), f"Note must be copied to shared root: {shared_root}"
        assert dest.read_text() == original_content, "Copied content must match original"
        # Personal original still present and unchanged
        assert note.exists(), "Personal original must not be deleted (copy, not move)"
        assert note.read_text() == original_content, "Personal original must be unchanged"
        # Promoted: line in output
        combined = r.stdout + r.stderr
        assert "Promoted:" in combined, f"Expected 'Promoted:' in output. Got: {combined!r}"

    def test_confirm_n_writes_nothing(self, tmp_path: Path) -> None:
        """On n/EOF → nothing written to shared root, clean exit."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault, name="my-decision.md")

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="n\n",
            cwd=cwd_dir,
        )
        assert r.returncode == 0, f"n should produce clean exit. stderr: {r.stderr}"
        shared_files = [f for f in shared_root.glob("**/*") if f.is_file()]
        assert not shared_files, f"n should write nothing: {shared_files}"

    def test_confirm_eof_writes_nothing(self, tmp_path: Path) -> None:
        """On EOF → nothing written to shared root, clean exit."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault, name="my-decision.md")

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="",  # EOF
            cwd=cwd_dir,
        )
        assert r.returncode == 0, f"EOF should produce clean exit. stderr: {r.stderr}"
        shared_files = [f for f in shared_root.glob("**/*") if f.is_file()]
        assert not shared_files, f"EOF should write nothing: {shared_files}"

    def test_promoted_success_line_format(self, tmp_path: Path) -> None:
        """Promoted: line format: 'Promoted: <personal> → [shared:<name>] <dest>'."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault, name="promoted-note.md")

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode == 0, r.stderr
        combined = r.stdout + r.stderr
        assert "Promoted:" in combined
        assert "[shared:team]" in combined or "[shared:" in combined, (
            f"Expected [shared:<name>] in output. Got: {combined!r}"
        )


# ---------------------------------------------------------------------------
# Multiple shared layers + no --to
# ---------------------------------------------------------------------------

class TestPromoteMultipleSharedLayers:
    def test_multiple_shared_no_to_prints_specify_error(self, tmp_path: Path) -> None:
        """Multiple shared layers + no --to → 'specify --to <name>' error, nothing written."""
        vault = _make_vault(tmp_path)
        shared_a = tmp_path / "shared-a"
        shared_a.mkdir()
        shared_b = tmp_path / "shared-b"
        shared_b.mkdir()
        note = _make_note(vault)

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [
            {"name": "alpha", "root": str(shared_a)},
            {"name": "beta", "root": str(shared_b)},
        ])

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode != 0, "Expected nonzero exit when multiple shared layers and no --to"
        combined = r.stdout + r.stderr
        assert "specify" in combined.lower() or "--to" in combined, (
            f"Error must say 'specify --to'. Got: {combined!r}"
        )
        # Must list available names
        assert "alpha" in combined and "beta" in combined, (
            f"Error must list available layer names. Got: {combined!r}"
        )
        # Nothing written
        for shared in (shared_a, shared_b):
            files = [f for f in shared.glob("**/*") if f.is_file()]
            assert not files, f"Nothing must be written to {shared}: {files}"

    def test_multiple_shared_with_to_works(self, tmp_path: Path) -> None:
        """Multiple shared layers + --to <name> → copies to the named layer."""
        vault = _make_vault(tmp_path)
        shared_a = tmp_path / "shared-a"
        shared_a.mkdir()
        shared_b = tmp_path / "shared-b"
        shared_b.mkdir()
        note = _make_note(vault, name="multi-note.md")
        original_content = note.read_text()

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [
            {"name": "alpha", "root": str(shared_a)},
            {"name": "beta", "root": str(shared_b)},
        ])

        r = run_cli(
            ["promote", str(note), "--to", "alpha"],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
        # Copied to alpha
        dest = shared_a / note.name
        assert dest.exists(), "Note must be copied to alpha"
        assert dest.read_text() == original_content
        # beta stays empty
        beta_files = [f for f in shared_b.glob("**/*") if f.is_file()]
        assert not beta_files, f"beta must stay empty: {beta_files}"


# ---------------------------------------------------------------------------
# --to unknown layer
# ---------------------------------------------------------------------------

class TestPromoteUnknownToLayer:
    def test_to_unknown_layer_named_error(self, tmp_path: Path) -> None:
        """--to naming an unknown layer → named error, no write."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault)

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        r = run_cli(
            ["promote", str(note), "--to", "nonexistent-layer"],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode != 0, "Expected nonzero exit for unknown --to layer"
        combined = r.stdout + r.stderr
        assert "nonexistent-layer" in combined or "unknown" in combined.lower(), (
            f"Error must name the unknown layer. Got: {combined!r}"
        )
        shared_files = [f for f in shared_root.glob("**/*") if f.is_file()]
        assert not shared_files, f"Nothing written on unknown --to: {shared_files}"


# ---------------------------------------------------------------------------
# Note doesn't exist
# ---------------------------------------------------------------------------

class TestPromoteNonexistentNote:
    def test_nonexistent_note_named_error(self, tmp_path: Path) -> None:
        """lore promote <missing-note> → named error, no write."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        missing = tmp_path / "vault" / "decisions" / "does-not-exist.md"

        r = run_cli(
            ["promote", str(missing)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode != 0, "Expected nonzero exit for missing note"
        combined = r.stdout + r.stderr
        assert "not found" in combined.lower() or "does not exist" in combined.lower() or \
               "no such file" in combined.lower() or missing.name in combined, (
            f"Error must name the missing note. Got: {combined!r}"
        )


# ---------------------------------------------------------------------------
# C-4: two groups claim cwd → promote raises overlap error
# ---------------------------------------------------------------------------

class TestPromoteOverlapBlocks:
    def test_two_groups_claiming_cwd_blocks_promote(self, tmp_path: Path) -> None:
        """C-4: two groups claiming the cwd → promote raises legible overlap error."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault)

        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()

        groups_dir = tmp_path / "camp-config" / "groups"
        groups_dir.mkdir(parents=True)

        # Write two group configs both claiming cwd_dir
        cfg_a = groups_dir / "group-a.toml"
        cfg_a.write_text(
            '[group]\nname = "group-a"\n\n'
            f'[[members]]\nname = "repo"\nrepo_root = "{cwd_dir}"\n\n'
            f'[[shared_vaults]]\nname = "vault-a"\nroot = "{shared_root}"\n'
        )
        cfg_b = groups_dir / "group-b.toml"
        cfg_b.write_text(
            '[group]\nname = "group-b"\n\n'
            f'[[members]]\nname = "repo"\nrepo_root = "{cwd_dir}"\n\n'
            f'[[shared_vaults]]\nname = "vault-b"\nroot = "{shared_root}"\n'
        )

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode != 0, "Expected nonzero exit when two groups claim cwd"
        combined = r.stdout + r.stderr
        # Must surface the overlap (not silently pick one)
        assert (
            "multiple" in combined.lower()
            or "overlap" in combined.lower()
            or "group-a" in combined
            or "group-b" in combined
        ), f"Must mention overlap/multiple groups. Got: {combined!r}"


# ---------------------------------------------------------------------------
# Space/quote round-trip in note path
# ---------------------------------------------------------------------------

class TestPromoteSpaceQuoteInPath:
    def test_note_with_space_in_name_copies_correctly(self, tmp_path: Path) -> None:
        """Promoted copy round-trips a note whose path contains spaces (copy, not f-string)."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()

        note_dir = vault / "decisions" / "2026-06"
        note_dir.mkdir(parents=True, exist_ok=True)
        note = note_dir / "note with spaces.md"
        note.write_text("---\ntype: decision\n---\n\n# Space note\n")
        original_content = note.read_text()

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
        dest = shared_root / note.name
        assert dest.exists(), f"Note with spaces must be promoted: {dest}"
        assert dest.read_text() == original_content, "Content must round-trip intact"
        assert note.exists(), "Personal original must survive"

    def test_note_with_quote_in_name_copies_correctly(self, tmp_path: Path) -> None:
        """Promoted copy round-trips a note whose path contains single quotes."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()

        note_dir = vault / "decisions" / "2026-06"
        note_dir.mkdir(parents=True, exist_ok=True)
        note = note_dir / "it's-a-note.md"
        note.write_text("---\ntype: decision\n---\n\n# Quote note\n")
        original_content = note.read_text()

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        r = run_cli(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
        dest = shared_root / note.name
        assert dest.exists(), "Note with quote must be promoted"
        assert dest.read_text() == original_content
        assert note.exists(), "Personal original must survive"
