"""Workspace-manifest abstraction for camp.

Quarried from zenith/scripts/dev_env/repos.py; de-zenithed:
- Removed _FILE_ANCHORED_CANONICAL / canonical_root() — the __file__-anchored
  canonical pointer is replaced by trailhead.paths.state_dir("camp") in Slice 1.
  root_dir() (active-worktree, cwd/override-based) is kept.
- Removed the ["platform","mobile-app"] defaults from validate_worktree_context().
- parents[N] offset updated for new location:
    tools/camp/plugins/camp/scripts/repos.py
    parents[0] = scripts/
    parents[1] = camp/      (plugin)
    parents[2] = plugins/
    parents[3] = camp/      (tool)
    parents[4] = tools/
    parents[5] = trailhead/ (repo root)
  The fallback root_dir() now points at parents[5] (trailhead root) when no
  DEV_ENV_WORKTREE_ROOT override is set. The override path is unaffected.

Source: zenith/scripts/dev_env/repos.py (quarry provenance — Slice 0).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_WORKTREE_MARKER = "/.claude/worktrees/"
_MANIFEST_FILENAME = ".workspace-manifest.json"
_SIBLING_MARKER = ".workspace-sibling"


def root_dir() -> Path:
    """Return the checkout this code is running from.

    Resolves to the worktree root when invoked from a worktree, or the
    trailhead repo root otherwise. Use this for anything that needs the
    actual working tree (sibling validation, branch state).

    When ``DEV_ENV_WORKTREE_ROOT`` is set (e.g. by ``camp fire`` so that the
    canonical ``dev_env_cli.py`` binary resolves the correct worktree regardless
    of its own ``__file__`` location), returns the override path. Falls back to
    the file-anchored default (trailhead root) when the variable is unset.
    """
    override = os.environ.get("DEV_ENV_WORKTREE_ROOT")
    if override:
        return Path(override)
    # parents[5] = trailhead repo root from this file's location:
    # tools/camp/plugins/camp/scripts/repos.py
    return Path(__file__).resolve().parents[5]


def workspace_root() -> Path:
    """Return ``$WORKSPACE_ROOT`` (default ``$HOME/code``).

    Under the sibling-repo model, canonical repos live at
    ``$WORKSPACE_ROOT/<name>`` and their worktrees at
    ``$WORKSPACE_ROOT/<name>/.claude/worktrees/<worktree-name>``.
    """
    configured = os.environ.get("WORKSPACE_ROOT")
    if configured:
        return Path(configured)
    return Path.home() / "code"


def manifest_path_for(root: Path) -> Path:
    """Return the canonical manifest path for the given worktree root."""
    return root / _MANIFEST_FILENAME


def read_manifest(root: Path) -> dict[str, Any] | None:
    """Read the workspace manifest for a worktree root.

    Returns the parsed JSON manifest, or ``None`` if the root is not
    workspace-managed (no manifest present).
    """
    direct = manifest_path_for(root)
    if direct.is_file():
        try:
            return json.loads(direct.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def resolve_manifest_for(root: Path) -> dict[str, Any] | None:
    """Resolve and read the manifest for ``root``.

    If ``root`` is a camp worktree (contains ``.workspace-manifest.json``),
    reads the manifest directly. If ``root`` is a sibling worktree (contains
    ``.workspace-sibling`` pointing at the manifest), follows the pointer.
    Returns ``None`` when no manifest can be located.
    """
    direct = read_manifest(root)
    if direct is not None:
        return direct

    marker = root / _SIBLING_MARKER
    if marker.is_file():
        try:
            target = Path(marker.read_text().strip())
        except OSError:
            return None
        if target.is_file():
            try:
                return json.loads(target.read_text())
            except (json.JSONDecodeError, OSError):
                return None
    return None


def sibling_worktree(manifest: dict[str, Any], repo_name: str) -> Path | None:
    """Return the worktree path for a given sibling repo in ``manifest``.

    Looks up the entry in ``manifest["repos"]`` matching ``repo_name`` and
    returns its ``worktree_path``. Returns ``None`` if the repo is not present.
    """
    for entry in manifest.get("repos", []) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == repo_name:
            path = entry.get("worktree_path")
            if isinstance(path, str) and path:
                return Path(path)
    return None


def repo_path(root: Path, repo_name: str) -> Path:
    """Return the working-tree path for ``repo_name`` relative to ``root``.

    Resolution order:
      1. If ``root`` has a workspace manifest and the manifest lists
         ``repo_name``, return the manifest's ``worktree_path`` for that repo.
      2. If a sibling worktree at ``$WORKSPACE_ROOT/<repo_name>/.claude/worktrees/<root.name>``
         exists (manifest missing but convention holds), return it.
      3. Fall back to ``root / repo_name`` (pre-sibling layout / test tmpdirs).
    """
    manifest = resolve_manifest_for(root)
    if manifest is not None:
        path = sibling_worktree(manifest, repo_name)
        if path is not None:
            return path

    # Sibling-convention fallback: manifest absent but this looks like a
    # worktree under $WORKSPACE_ROOT/<repo>/.claude/worktrees/<name>/.
    resolved = root.as_posix()
    if _WORKTREE_MARKER in resolved:
        worktree_name = root.name
        candidate = workspace_root() / repo_name / ".claude" / "worktrees" / worktree_name
        if candidate.exists():
            return candidate

    return root / repo_name


def validate_worktree_context(root: Path, repos: list[str] | None = None) -> list[str]:
    """Return list of problems with the worktree context. Empty means ok.

    A valid worktree is one where:
      - ``root`` is a git worktree (``.git`` is a file, not a directory)
      - a workspace manifest exists either at ``root`` or via a sibling marker
      - every repo listed in ``repos`` has an existing worktree directory

    Unlike the zenith version, there are no default repos — callers must pass
    the repo list explicitly (de-zenithed: no ["platform","mobile-app"] default).
    """
    problems: list[str] = []
    git_path = root / ".git"
    if git_path.is_dir():
        problems.append(f"Running from main git checkout, not a worktree: {root}")
    elif not git_path.exists():
        problems.append(f"Not a git repository: {root}")

    manifest = resolve_manifest_for(root)
    if manifest is None:
        problems.append(f"Missing workspace manifest: {root}")
        for repo in (repos or []):
            repo_dir = root / repo
            if not repo_dir.exists():
                problems.append(f"Missing sibling worktree: {repo_dir}")
        return problems

    for repo in (repos or []):
        sibling = sibling_worktree(manifest, repo)
        if sibling is None:
            if repos is not None and repo in repos:
                problems.append(f"Repo not in manifest: {repo}")
            continue
        if not sibling.exists():
            problems.append(f"Missing sibling worktree: {sibling}")
    return problems
