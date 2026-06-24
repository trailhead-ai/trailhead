"""Installer helpers for ``lore init``.

Provides three pure-ish functions used by ``cmd_init``:

- ``resolve_targets()`` — return the user-global ``~/.claude/settings.json``
  path (the guardrail install target). User-level only: there is no ``--local``
  project mode (the agent-ruleset install is a single user-global install
  delivered through the trailhead ``Harness`` seam, not a per-project file).

- ``bootstrap_vault(vaults_root, vault_path=None)`` — ensure
  ``vaults_root/default`` exists as a git-initialised directory (or a symlink
  to *vault_path* when provided). Never re-git-inits an existing repo. Returns
  the resolved ``Path`` of the default vault.

- ``provision_index_location(lore_state_dir)`` — ensure ``lore_state_dir``
  itself (the *sibling* of ``vaults/``) exists. The index lives there, never
  inside a vault.

All filesystem operations use ``pathlib.Path``; no third-party deps (Axiom:
pure stdlib only).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# resolve_targets
# ---------------------------------------------------------------------------


def resolve_targets() -> Path:
    """Return the user-global ``~/.claude/settings.json`` path.

    User-level only — the guardrail is always installed into the user's global
    Claude settings. The lore agent-ruleset is installed separately via the
    trailhead ``Harness`` seam (``install_user_ruleset``), so there is no
    project-local rules file to resolve here.
    """
    return Path.home() / ".claude" / "settings.json"


# ---------------------------------------------------------------------------
# bootstrap_vault
# ---------------------------------------------------------------------------


def bootstrap_vault(vaults_root: Path, vault_path: Path | None = None) -> Path:
    """Ensure ``vaults_root/default`` is a git repo (or a symlink to *vault_path*).

    When *vault_path* is provided, creates ``vaults_root/default`` as a
    **symlink** pointing to *vault_path*. Does NOT run ``git init`` on the
    target (it is assumed to already be a git repo).

    When *vault_path* is ``None``, creates ``vaults_root/default`` as a plain
    directory and runs ``git init`` if it is not already a git repo.

    Idempotent: if ``vaults_root/default`` already exists (as a dir, symlink,
    or git repo), the operation is a no-op.

    Args:
        vaults_root: The ``$XDG_STATE_HOME/lore/vaults`` directory.
        vault_path:  When set, the existing repo to symlink as ``default``.

    Returns:
        The ``Path`` of the default vault (``vaults_root/default``).
    """
    vaults_root.mkdir(parents=True, exist_ok=True)
    default = vaults_root / "default"

    if vault_path is not None:
        # Symlink mode: create a symlink if not already one pointing correctly.
        target = vault_path.resolve()
        if default.is_symlink():
            return default
        if default.exists():
            return default
        default.symlink_to(target)
        return default

    # Plain directory mode.
    if default.exists() or default.is_symlink():
        # Already present — check if git init is needed.
        if default.is_dir() and not (default / ".git").is_dir():
            git_init(default)
        return default

    default.mkdir(parents=True, exist_ok=True)
    git_init(default)
    return default


def git_init(path: Path) -> None:
    """Run ``git init`` on *path*; raise ``ValueError`` on failure.

    The single implementation of the vault-is-a-git-repo contract, shared by
    ``bootstrap_vault`` (``lore init``) and ``lore vault add`` so vault
    scaffolding has one source of truth. The contract is load-bearing —
    downstream `lore sync` and record-commit paths assume it. A silently-failed
    init that still let the caller report success would surface only later as a
    confusing commit failure, so a failed init is a clean named error here, not
    a warning.
    """
    result = subprocess.run(
        ["git", "init", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"git init failed for vault at {path}: {result.stderr.strip()}")


# ---------------------------------------------------------------------------
# provision_index_location
# ---------------------------------------------------------------------------


def provision_index_location(lore_state_dir: Path) -> None:
    """Ensure *lore_state_dir* (the index parent directory) exists.

    The lore index lives at ``$XDG_STATE_HOME/lore/lore.db`` — i.e. directly
    inside *lore_state_dir*, which is **not** under ``vaults/``. This function
    creates the directory if absent.

    Args:
        lore_state_dir: ``$XDG_STATE_HOME/lore`` (sibling of ``vaults/``).
    """
    lore_state_dir.mkdir(parents=True, exist_ok=True)
