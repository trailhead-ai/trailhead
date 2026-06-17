"""Slice 4 tests: capture layer labeling + shared-root refusal for `lore new`.

Test contract (all must RED before implementation, GREEN after):

1. `lore new decision` with a shared vault declared → prints [personal], writes to personal root.
2. `lore new` with only a personal layer → NO [personal] tag (D-4 suppression).
3. `lore new --vault <shared-root>` → refused with the "use lore promote" message;
   nothing written to the shared root (D-5).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import CLI_PATH

TODAY = "2026-06-10"

_CAMP_PLUGIN_ROOT = str(
    Path(__file__).resolve().parents[4] / "camp" / "plugins"
)


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
    for d in ("deferred", "dead-ends", "decisions", "follow-ups", "areas",
              "sessions", "plans", "specs"):
        (vault / d).mkdir(parents=True)
    return vault


def _write_group_config(groups_dir: Path, member_root: Path, shared_vault_root: Path) -> None:
    """Write a minimal camp group config with one shared vault."""
    groups_dir.mkdir(parents=True, exist_ok=True)
    cfg = groups_dir / "testgroup.toml"
    cfg.write_text(
        '[group]\nname = "testgroup"\n\n'
        f'[[members]]\nname = "repo"\nrepo_root = "{member_root}"\n\n'
        f'[[shared_vaults]]\nname = "team-vault"\nroot = "{shared_vault_root}"\n'
    )


# ---------------------------------------------------------------------------
# Helper: resolve the groups_dir path that layers.py uses at runtime.
# We need trailhead.paths.config_dir("camp")/"groups".
# We bypass this by injecting LORE_GROUPS_DIR into the environment.
# ---------------------------------------------------------------------------

def _fake_groups_env(groups_dir: Path) -> dict:
    """Return env overrides that steer layers.py shared-vault discovery."""
    return {"LORE_GROUPS_DIR": str(groups_dir)}


# ---------------------------------------------------------------------------
# 1. With a shared vault declared → [personal] tag appears
# ---------------------------------------------------------------------------

class TestCapturePrintsPersonalLabelWhenSharedDeclared:
    def test_lore_new_decision_prints_personal_label_with_shared_vault(
        self, tmp_path: Path
    ) -> None:
        """lore new decision with a shared vault declared → stdout contains [personal]."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()

        groups_dir = tmp_path / "camp-config" / "groups"
        # member_root must match the cwd used at runtime
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, shared_root)

        r = run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "Use postgres", "--project", "my-project"],
            env={
                "LORE_USER": "ada",
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )
        assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
        assert "[personal]" in r.stdout, (
            f"Expected [personal] label when a shared vault is declared. "
            f"stdout: {r.stdout!r}"
        )

    def test_lore_new_decision_writes_to_personal_root_not_shared(
        self, tmp_path: Path
    ) -> None:
        """lore new decision writes to the personal vault root, not the shared root."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, shared_root)

        run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "Stay in personal", "--project", "my-project"],
            env={
                "LORE_USER": "ada",
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )
        # Note must exist in personal vault
        notes = list((vault / "decisions").glob("**/*.md"))
        assert len(notes) == 1, f"Expected 1 note in personal vault, got {notes}"
        # Shared root must stay empty
        shared_notes = list(shared_root.glob("**/*.md"))
        assert not shared_notes, f"Shared root must stay empty, got {shared_notes}"


# ---------------------------------------------------------------------------
# 2. With ONLY personal layer → NO [personal] tag (D-4 suppression)
# ---------------------------------------------------------------------------

class TestCaptureSuppressesPersonalLabelWithoutSharedVault:
    def test_lore_new_without_shared_vault_has_no_personal_label(
        self, tmp_path: Path
    ) -> None:
        """lore new with no shared vault declared → NO [personal] in stdout (D-4)."""
        vault = _make_vault(tmp_path)

        # Point LORE_GROUPS_DIR at an empty directory → no group config → single personal layer
        empty_groups = tmp_path / "empty-groups"
        empty_groups.mkdir()

        r = run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "Plain personal note", "--project", "my-project"],
            env={
                "LORE_USER": "ada",
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(empty_groups),
            },
        )
        assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
        assert "[personal]" not in r.stdout, (
            f"D-4: [personal] tag must be suppressed when only one layer exists. "
            f"stdout: {r.stdout!r}"
        )

    def test_lore_new_prints_created_path_without_label(self, tmp_path: Path) -> None:
        """lore new with no shared vault still prints 'Created: <path>' (no label appended)."""
        vault = _make_vault(tmp_path)
        empty_groups = tmp_path / "empty-groups"
        empty_groups.mkdir()

        r = run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "Bare created line", "--project", "my-project"],
            env={
                "LORE_USER": "ada",
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(empty_groups),
            },
        )
        assert r.returncode == 0, r.stderr
        assert "Created:" in r.stdout
        assert "[personal]" not in r.stdout
        assert "[shared]" not in r.stdout


# ---------------------------------------------------------------------------
# 3. lore new --vault <shared-root> → refused (D-5)
# ---------------------------------------------------------------------------

class TestCaptureRefusesSharedVaultDirectly:
    def test_vault_pointing_at_shared_root_is_refused(
        self, tmp_path: Path
    ) -> None:
        """lore new --vault <shared-root> is refused; nothing written to shared root."""
        personal_vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, shared_root)

        r = run_cli(
            ["new", "decision", "--vault", str(shared_root),
             "--title", "Should not land here", "--project", "my-project"],
            env={
                "LORE_USER": "ada",
                "LORE_VAULT": str(personal_vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )
        assert r.returncode != 0, (
            "Expected nonzero exit when --vault points at a shared root"
        )
        # The refusal message must mention lore promote
        combined = r.stdout + r.stderr
        assert "lore promote" in combined.lower() or "promote" in combined.lower(), (
            f"Refusal must mention 'lore promote'. Got: {combined!r}"
        )
        # Nothing must be written to shared root
        shared_files = list(shared_root.glob("**/*.md"))
        assert not shared_files, (
            f"Shared root must remain empty after refusal, got {shared_files}"
        )

    def test_vault_pointing_at_non_shared_path_still_works(
        self, tmp_path: Path
    ) -> None:
        """lore new --vault <non-shared-path> continues to work as today (D-5 scope)."""
        personal_vault = _make_vault(tmp_path)
        alt_vault = tmp_path / "alt-vault"
        for d in ("decisions",):
            (alt_vault / d).mkdir(parents=True)

        # Empty groups dir → no shared vaults → alt_vault is not a shared root
        empty_groups = tmp_path / "empty-groups"
        empty_groups.mkdir()

        r = run_cli(
            ["new", "decision", "--vault", str(alt_vault),
             "--title", "Alt vault note", "--project", "my-project"],
            env={
                "LORE_USER": "ada",
                "LORE_VAULT": str(personal_vault),
                "LORE_GROUPS_DIR": str(empty_groups),
            },
        )
        assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
        notes = list((alt_vault / "decisions").glob("**/*.md"))
        assert len(notes) == 1, "alt-vault should receive the note"
