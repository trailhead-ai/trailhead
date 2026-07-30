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

from ..record.store import write_temp_then_rename


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
        if not (default.is_symlink() or default.exists()):
            default.symlink_to(target)
        # The adopted repo is a vault like any other — it needs the ignore too
        # (scaffold_gitignore no-ops on a dangling target).
        scaffold_gitignore(default)
        return default

    # Plain directory mode.
    if default.exists() or default.is_symlink():
        # Already present — check if git init is needed.
        if default.is_dir() and not (default / ".git").is_dir():
            git_init(default)
        scaffold_gitignore(default)
        return default

    default.mkdir(parents=True, exist_ok=True)
    git_init(default)
    scaffold_gitignore(default)
    return default


# Patterns a freshly-initialised vault ignores. ``*.lock`` covers the
# ``session/<key>.lock`` flock sidecars the capture path creates AND the
# ``.lore.lock`` write lock at the vault root: without this, ``lore sync``'s
# ``git add -A`` (the only catch-all stage path) would commit them. The flush
# commit path uses explicit paths and is unaffected.
_GITIGNORE_PATTERNS = ("*.lock",)


def scaffold_gitignore(vault: Path) -> None:
    """Ensure the vault carries a ``.gitignore`` covering the flock sidecars.

    Every path that produces a vault owes it this ignore — ``lore init`` in both
    plain-dir and symlink mode, and ``lore vault add`` — because a lock file
    appears at the root of *any* vault the first time anything writes to it, and
    ``reindex`` writes to every configured vault.

    An existing ``.gitignore`` is **appended to**, not skipped: an adopted repo
    vault virtually always has one, and skipping meant such a vault never ignored
    ``*.lock`` at all. Only the missing patterns are appended and the file's
    existing lines are preserved verbatim, so the operation is idempotent and a
    user's own entries are never clobbered. A *missing vault dir* is a no-op —
    the ignore never conjures the directory it would live in.
    """
    if not vault.is_dir():
        return
    gitignore = vault / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("\n".join(_GITIGNORE_PATTERNS) + "\n", encoding="utf-8")
        return

    existing = gitignore.read_text(encoding="utf-8")
    present = {line.strip() for line in existing.splitlines()}
    missing = [p for p in _GITIGNORE_PATTERNS if p not in present]
    if not missing:
        return
    separator = "" if existing.endswith("\n") or existing == "" else "\n"
    # Atomic append (temp file + os.replace), not read -> concat -> write_text
    # (truncate in place): the target is a pre-existing, user-owned file in an
    # adopted repo, and a crash mid-write must never leave it truncated.
    write_temp_then_rename(
        gitignore, existing + separator + "\n".join(missing) + "\n"
    )


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
