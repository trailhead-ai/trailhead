"""KU1 assumption probe — EPHEMERAL, clean up after Slice 4.

Proves (or disproves) the assumption behind Slice 4's hook design:
  - git rev-parse --show-toplevel with cwd = vault repo root yields that vault's
    root path (the assumption git invokes pre-commit with cwd = repo top level).
  - When that derived path is used to drive regenerate_indices.py, indices are
    regenerated for THAT vault, not for any config-default vault at a different path.
  - When git rev-parse --show-toplevel fails (cwd is not a git repo), the Slice 4
    hook pattern exits 0 under set -euo pipefail — never blocks a commit.

All fixtures use SYNTHETIC vocabulary (synth-*) per fixture discipline axiom.
No real vaults, no real config dirs are touched (Axiom 6).

Clean up:
  tools/lore/tests/test_ku1_assumption_probe.py  (entire file)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


# ---------------------------------------------------------------------------
# Minimal vault/fixture builders
# ---------------------------------------------------------------------------


def _make_vault(base: Path, name: str, *subdirs: str) -> Path:
    vault = base / name
    vault.mkdir(parents=True)
    for d in subdirs:
        (vault / d).mkdir(parents=True)
    return vault


def _git_init_vault(base: Path, name: str, *subdirs: str) -> Path:
    vault = _make_vault(base, name, *subdirs)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(vault), "config", "user.email", "ku1@test.example"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(vault), "config", "user.name", "KU1 Tester"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
        check=True, capture_output=True,
    )
    return vault


def _write_deferred(vault: Path, slug: str) -> Path:
    p = vault / "deferred" / f"{slug}.md"
    p.write_text(
        f"---\ntype: deferred\nstatus: open\nvalue: high\neffort: S\n"
        f"revisit-after: 2099-06-01\n---\n\n# {slug}\n\nSynthetic deferred item.\n"
    )
    return p


# ---------------------------------------------------------------------------
# KU1 tests
# ---------------------------------------------------------------------------


class TestKU1GitCwdAssumption:
    """Prove that git rev-parse --show-toplevel with cwd = vault repo yields the
    vault root, and that using this derived path to run regen targets THAT vault.
    """

    def test_git_revparse_toplevel_yields_vault_root(self, tmp_path):
        """Core git behavior: git rev-parse --show-toplevel with cwd=vault repo
        returns the vault root path.

        This is the fundamental assumption — if it holds, the Slice 4 approach of
        deriving the vault from git rev-parse (instead of LORE_VAULT) is safe.
        """
        vault = _git_init_vault(tmp_path, "synth-ku1-git-root-vault")

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(vault),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"git rev-parse failed unexpectedly: {result.stderr}"
        # Resolve both to handle macOS /tmp -> /private/tmp symlink
        reported = Path(result.stdout.strip()).resolve()
        expected = vault.resolve()
        assert reported == expected, (
            f"git rev-parse --show-toplevel returned {reported!r}, expected {expected!r}"
        )

    def test_git_revparse_toplevel_from_non_repo_exits_nonzero(self, tmp_path):
        """Git behavior: git rev-parse --show-toplevel with cwd=non-repo exits non-zero.

        This is the failure path Slice 4 must handle gracefully (exit 0, never block).
        """
        non_repo = tmp_path / "synth-ku1-not-a-repo"
        non_repo.mkdir()

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(non_repo),
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            "Expected non-zero exit for git rev-parse in a non-git directory, "
            f"but got rc=0 with output: {result.stdout.strip()!r}"
        )

    def test_hook_pattern_exits_zero_when_git_fails(self, tmp_path):
        """The planned hook pattern exits 0 when git rev-parse fails (never blocks commit).

        Simulates the exact Slice 4 shell construct under set -euo pipefail:
            VAULT="$(git rev-parse --show-toplevel 2>/dev/null)" \\
                || { echo "not a git repo — skipping" >&2; exit 0; }
        Proves the || rescue arm triggers and exits 0 rather than letting -e abort
        with a non-zero exit that would block the commit.
        """
        non_repo = tmp_path / "synth-ku1-hook-non-repo"
        non_repo.mkdir()

        hook_script = tmp_path / "simulated-ku1-hook.sh"
        hook_script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'VAULT="$(git rev-parse --show-toplevel 2>/dev/null)"'
            ' || { echo "lore regen-indices: not a git repo — skipping" >&2; exit 0; }\n'
            'echo "VAULT=$VAULT"\n'
            "exit 0\n"
        )
        hook_script.chmod(0o755)

        result = subprocess.run(
            [str(hook_script)],
            cwd=str(non_repo),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Hook blocked the commit (rc={result.returncode}) when git rev-parse failed. "
            f"stderr: {result.stderr!r}. "
            "The || rescue arm must exit 0 to satisfy the never-block-a-commit invariant."
        )
        assert "skipping" in result.stderr, (
            "Expected the skip message on stderr; got: " + result.stderr
        )

    def test_hook_targets_committed_vault_not_config_default(self, tmp_path):
        """Semantic correctness: hook using git rev-parse drives regen for the committed
        vault, NOT a config-default vault at a different path.

        Uses a NON-DEFAULT vault path (an arbitrary tmp dir distinct from any
        config-default path) to prove regen targets the committed repo.
        This is the semantic-correctness point of the git-root decision in Slice 4.
        """
        # A "config default" vault at a DIFFERENT path (not being committed)
        config_default_vault = _make_vault(
            tmp_path, "synth-ku1-config-default-vault", "deferred"
        )
        _write_deferred(config_default_vault, "synth-ku1-decoy-item")

        # The vault being committed — at a NON-DEFAULT path
        committed_vault = _git_init_vault(
            tmp_path, "synth-ku1-committed-vault", "deferred"
        )
        _write_deferred(committed_vault, "synth-ku1-committed-record")

        # Simulated hook: derive vault from git rev-parse (Slice 4's approach),
        # then run regen against that vault via LORE_VAULT env (current interface;
        # Slice 4 will switch to sys.argv[1] — the env form is used here only to
        # prove the git-root derivation is correct without pre-implementing Slice 4).
        regen_script = SCRIPTS_DIR / "regenerate_indices.py"
        hook_script = tmp_path / "simulated-ku1-regen-hook.sh"
        hook_script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'VAULT="$(git rev-parse --show-toplevel 2>/dev/null)"'
            ' || { echo "lore regen-indices: not a git repo — skipping" >&2; exit 0; }\n'
            # Prove VAULT is the committed vault, not the config default, by passing it to regen
            f'LORE_VAULT="$VAULT" {sys.executable} {regen_script}\n'
            "exit 0\n"
        )
        hook_script.chmod(0o755)

        # Invoke hook with cwd = committed vault (mirroring how git invokes pre-commit)
        result = subprocess.run(
            [str(hook_script)],
            cwd=str(committed_vault),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Simulated hook exited with rc={result.returncode}. "
            f"stdout: {result.stdout!r}  stderr: {result.stderr!r}"
        )

        # ASSERT: committed vault has its index regenerated
        committed_index = committed_vault / "deferred" / "_index.md"
        assert committed_index.exists(), (
            f"committed vault deferred/_index.md was NOT regenerated by the hook. "
            f"hook stdout: {result.stdout!r}"
        )
        committed_content = committed_index.read_text()
        assert "synth-ku1-committed-record" in committed_content, (
            "committed vault _index.md does not mention the committed note"
        )

        # ASSERT: config-default vault was NOT touched
        default_index = config_default_vault / "deferred" / "_index.md"
        assert not default_index.exists(), (
            f"config-default vault deferred/_index.md was incorrectly regenerated. "
            "The hook must target the git-derived vault, not any other vault."
        )
