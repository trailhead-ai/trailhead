"""Shared helpers for the ``lore`` CLI command-group modules.

The per-command-group modules (``init``, ``sync``, ``record``, …) each own their
own ``cmd_*`` handlers and subparser registration; this module holds the small
set of helpers used across more than one group so they have a single home and
the command modules stay free of cross-imports for generic plumbing:

  - the config/state path resolvers (``_resolve_config_path`` and friends), which
    lazy-import ``_bootstrap`` + ``trailhead.paths`` and fall back to the XDG
    default so the CLI works in a vanilla checkout;
  - ``_load_vault_config`` — the single gate for config-driven behavior;
  - the git primitives (``_git`` / ``_vault_is_git_toplevel``) shared by ``sync``
    and ``flush``;
  - the shared ``--session-id`` / ``--worktree`` subparser selectors;
  - the shared stdin read (``_read_stdin_body``).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..vault import config as vault_config_mod


def _read_stdin_body() -> str:
    """Return the piped stdin body, or ``""`` when stdin is a TTY (no pipe).

    The shared read used by every ``lore record``/``session`` write path. An empty
    return covers both a TTY and an empty/closed pipe — callers that need to tell
    "no stdin" from "empty body" key on ``== ""`` (see the record-update metadata-
    only path).
    """
    return "" if sys.stdin.isatty() else sys.stdin.read()


def _resolve_xdg_dir(
    *, kind: str, xdg_var: str, fallback_base: tuple[str, ...], suffix: tuple[str, ...] = ()
) -> Path:
    """Return trailhead's ``<kind>_dir("lore")`` plus ``suffix``, honoring XDG overrides.

    Shared by ``_resolve_config_path`` / ``_resolve_vaults_root`` /
    ``_resolve_lore_state_dir``: lazy-imports ``_bootstrap`` + ``trailhead.paths``
    and falls back to the XDG env var (or its plain-POSIX default under
    ``$HOME``) on any import failure, so the CLI works in a vanilla checkout with
    no trailhead install.
    """
    try:
        import _bootstrap
        _bootstrap.ensure_trailhead_importable()
        import trailhead.paths as _paths
        root = getattr(_paths, f"{kind}_dir")("lore")
    except (ImportError, SystemExit):
        base = os.environ.get(xdg_var, "").strip()
        if base and os.path.isabs(base):
            root = Path(base) / "lore"
        else:
            root = Path.home().joinpath(*fallback_base) / "lore"
    return root.joinpath(*suffix) if suffix else root


def _resolve_config_path() -> Path:
    """Return ``config_dir("lore")/config.json``, honoring XDG overrides."""
    return _resolve_xdg_dir(
        kind="config", xdg_var="XDG_CONFIG_HOME", fallback_base=(".config",), suffix=("config.json",)
    )


def _resolve_vaults_root() -> Path:
    """Return ``state_dir("lore")/vaults``, honoring XDG overrides.

    The confinement root for ``vault delete --remove-from-disk``: a vault whose
    resolved path is not within this root (or reaches it via a symlink) is refused.
    """
    return _resolve_xdg_dir(
        kind="state", xdg_var="XDG_STATE_HOME", fallback_base=(".local", "state"), suffix=("vaults",)
    )


def _resolve_lore_state_dir() -> Path:
    """Return ``state_dir("lore")``, honoring XDG overrides."""
    return _resolve_xdg_dir(kind="state", xdg_var="XDG_STATE_HOME", fallback_base=(".local", "state"))


def _resolve_groups_dir() -> "Path | None":
    """Return the camp groups directory, or None if unavailable.

    Checks LORE_GROUPS_DIR first (for tests and overrides), then falls back
    to trailhead.paths.config_dir("camp")/"groups" via the bootstrap.
    """
    env_override = os.environ.get("LORE_GROUPS_DIR", "").strip()
    if env_override:
        return Path(env_override)
    try:
        import _bootstrap
        _bootstrap.ensure_trailhead_importable()
        import trailhead.paths as _paths
        return _paths.config_dir("camp") / "groups"
    except (ImportError, SystemExit):
        return None


def _load_vault_config():
    """Return ``(config_path, list[Vault])`` if config.json exists & loads, else ``None``.

    The single gate for all config-driven behavior: routing, config-
    sourced ``shared`` reindex, config-freshness signal, and the orphan-ID guard
    all fire **only** when this returns a value. A missing ``config.json`` returns
    ``None`` so every path falls back to vanilla (Axiom 3 — support vanilla usage).

    A *present but malformed/invalid* config returns ``None`` too: the freshness +
    routing layers are best-effort, and a broken config must not brick a plain
    ``record create``; ``lore vault ls``/``add`` are the surfaces that surface the
    config error explicitly.
    """
    config_path = _resolve_config_path()
    if not config_path.exists():
        return None
    try:
        vaults = vault_config_mod.load_config(str(config_path))
    except (vault_config_mod.VaultConfigError, OSError, ValueError):
        return None
    return config_path, vaults


def _git(vault: Path, *args: str) -> tuple[int, str, str]:
    """Run a git command in the vault. Returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), *args],
            capture_output=True, text=True, timeout=60,
        )
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def _vault_is_git_toplevel(vault: Path) -> bool:
    rc, out, _ = _git(vault, "rev-parse", "--show-toplevel")
    if rc != 0 or not out:
        return False
    try:
        return Path(out).resolve() == vault.resolve()
    except Exception:
        return False


def _add_session_selectors(p) -> None:
    """Shared overrides for the auto-detected session note."""
    p.add_argument(
        "--session-id", dest="session_id", default=None,
        help="Session id to target (default: $CLAUDE_CODE_SESSION_ID)",
    )
    p.add_argument(
        "--worktree", default=None,
        help="Worktree name for the fallback (default: auto-detected)",
    )
