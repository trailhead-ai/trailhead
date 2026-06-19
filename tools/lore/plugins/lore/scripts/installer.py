"""Installer helpers for ``lore init`` (Slice 1, S5).

Provides three pure-ish functions used by ``cmd_init``:

- ``resolve_targets(local)`` — return ``(settings_path, rules_path)`` for the
  target settings file and rules file based on whether ``--local`` was passed.
  Global: ``~/.claude/settings.json`` + ``~/CLAUDE.md``.
  Local: ``<git-root>/.claude/settings.local.json`` + ``<git-root>/CLAUDE.md``.
  Raises ``ValueError`` when ``local=True`` and no git root is found.

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


def _find_git_root(start: Path) -> Path | None:
    """Walk upward from *start* to find a directory containing ``.git``."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_targets(local: bool) -> tuple[Path, Path]:
    """Return ``(settings_path, rules_path)`` for the given install mode.

    Args:
        local: When ``True``, resolve paths relative to the git repo root of
               the current working directory (project-local install). When
               ``False``, resolve paths relative to ``~`` (global user install).

    Returns:
        A ``(settings_path, rules_path)`` tuple. ``settings_path`` is the
        ``settings.json`` or ``settings.local.json`` file. ``rules_path`` is
        the ``CLAUDE.md`` file that should receive the injected agent-rules
        block (Slice 4).

    Raises:
        ValueError: When ``local=True`` and no git root can be found from the
                    current working directory.
    """
    import os

    if local:
        git_root = _find_git_root(Path(os.getcwd()))
        if git_root is None:
            raise ValueError(
                "lore init --local must be run inside a git repository"
            )
        settings = git_root / ".claude" / "settings.local.json"
        rules = git_root / "CLAUDE.md"
    else:
        home = Path.home()
        settings = home / ".claude" / "settings.json"
        rules = home / "CLAUDE.md"

    return settings, rules


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
            _git_init(default)
        return default

    default.mkdir(parents=True, exist_ok=True)
    _git_init(default)
    return default


def _git_init(path: Path) -> None:
    """Run ``git init`` on *path*, ignoring failures (warning only)."""
    result = subprocess.run(
        ["git", "init", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        import sys
        print(
            f"lore: warning: git init failed: {result.stderr.strip()}",
            file=sys.stderr,
        )


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
