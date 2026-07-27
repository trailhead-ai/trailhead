"""Shared helpers for the ``lore`` CLI command-group modules.

The per-command-group modules (``init``, ``sync``, ``record``, …) each own their
own ``cmd_*`` handlers and subparser registration; this module holds the small
set of helpers used across more than one group so they have a single home and
the command modules stay free of cross-imports for generic plumbing:

  - the config/state path resolvers (``_resolve_config_path`` and friends), which
    lazy-import ``_bootstrap`` + ``trailhead.paths`` and fall back to the XDG
    default so the CLI works in a vanilla checkout;
  - ``_load_vault_config`` — the single gate for config-driven behavior;
  - ``_resolve_all_vaults`` — the whole-install vault enumeration used by ``sync``
    and ``status``, as opposed to ``resolve_active_vault``'s ``default``-only view;
  - the git primitives (``_git`` / ``_vault_is_git_toplevel``) shared by ``sync``
    and ``flush``, plus ``_vault_drift`` — the "is this vault actually backed up?"
    probe shared by ``status`` and ``flush``;
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


def _resolve_all_vaults() -> tuple[list[tuple[str, Path]], str | None]:
    """Return ``([(name, path), …], error)`` covering EVERY configured vault.

    The whole-install counterpart to ``vault_config.resolve_active_vault``, which
    resolves the ``default``-scope vault alone. Record writes route by scope — a
    product-scope vault takes every record created from a bound repo — so any
    operation that means "the vault" in the whole-install sense (``lore sync``'s
    commit+push, ``lore status``'s drift report) must enumerate all of them.
    Covering only ``default`` lets a product vault accumulate records that are
    never committed while the tooling still reports success.

    Three cases:

    - **No ``config.json``** (vanilla usage, Axiom 3) → the single floor vault
      ``[("default", state_dir("lore")/vaults/default)]`` and ``error is None``.
    - **Config present but unparseable/invalid** → the same single-element floor
      list, plus a non-``None`` ``error`` naming the problem. Callers MUST surface
      it: degrading silently to one vault is precisely the failure mode this
      function exists to prevent, so unlike ``_load_vault_config`` (best-effort,
      returns ``None``) the breakage is reported rather than swallowed.
    - **Valid config** → one ``(name, path)`` pair per vault, in config order,
      names already normalized by ``load_config``.
    """
    config_path = _resolve_config_path()
    floor = [("default", Path(vault_config_mod.resolve_active_vault()))]
    if not config_path.exists():
        return floor, None
    try:
        vaults = vault_config_mod.load_config(str(config_path))
    except (vault_config_mod.VaultConfigError, OSError, ValueError) as exc:
        return floor, f"cannot read {config_path}: {exc}"
    return [(v.name, Path(v.path)) for v in vaults], None


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


#: Drift codes returned by :func:`_vault_drift`. Callers branch on these STABLE
#: tokens, never on the human phrasing beside them — the prose is free to be
#: reworded without silently changing which remedy a caller prints.
DRIFT_MISSING = "missing"
DRIFT_NOT_GIT = "not-git"
DRIFT_NEVER_COMMITTED = "never-committed"
DRIFT_UNCOMMITTED = "uncommitted"
DRIFT_NO_REMOTE = "no-remote"
DRIFT_NO_UPSTREAM = "no-upstream"
DRIFT_UNPUSHED = "unpushed"

#: The drift codes ``lore sync`` can actually resolve. A caller that offers
#: "run `lore sync`" as its remedy MUST filter on this set: proposing it for a
#: condition sync cannot fix (no remote, a missing directory) trains the operator
#: to ignore the notice, which is the failure this drift reporting exists to
#: prevent.
DRIFT_SYNC_FIXABLE = frozenset(
    {DRIFT_NEVER_COMMITTED, DRIFT_UNCOMMITTED, DRIFT_NO_UPSTREAM, DRIFT_UNPUSHED}
)


def _vault_head_branch(vault: Path) -> "str | None":
    """Return the checked-out branch name, or ``None`` if detached / pre-commit."""
    rc, out, _ = _git(vault, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or not out or out == "HEAD":
        return None
    return out


def _vault_has_commits(vault: Path) -> bool:
    rc, _, _ = _git(vault, "rev-parse", "--verify", "--quiet", "HEAD")
    return rc == 0


def _vault_has_upstream(vault: Path) -> bool:
    """Return ``True`` iff the current branch has a configured upstream."""
    rc, _, _ = _git(vault, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    return rc == 0


def _vault_unpushed(vault: Path) -> bool:
    """Return ``True`` iff the vault holds commits that exist nowhere else.

    Purely local — reads ``@{u}`` from the ref database, never contacts the
    remote. A branch with **no** configured upstream counts as unpushed: its
    commits exist only here, which is the state this predicate is asked about.
    Callers that must distinguish "ahead of upstream" from "has no upstream at
    all" — because the two need different git invocations to resolve — pair this
    with :func:`_vault_has_upstream` rather than re-deriving it.
    """
    if not _vault_has_commits(vault):
        return False  # no commits at all — "unpushed" is not the useful finding
    if not _vault_has_upstream(vault):
        return True
    rc, count, _ = _git(vault, "rev-list", "--count", "@{u}..HEAD")
    return rc == 0 and count.strip() not in ("", "0")


def _vault_drift(vault: Path) -> list:
    """Return ``[(code, description), …]`` for anything unsynced about ``vault``.

    An empty list means the vault is committed, pushed, and backed by a remote —
    i.e. the state every "Committed / Pushed to origin" message implies. Each
    finding names one way the vault's records exist in fewer places than the
    operator believes: a stable ``DRIFT_*`` code for callers to branch on, and a
    human phrase for them to print.

    Ordered most- to least-severe, and **short-circuiting**: a vault that is
    absent or not a git repo has no meaningful commit/remote state to report, so
    that finding is returned alone rather than followed by derivative noise.

    ``DRIFT_NO_UPSTREAM`` is kept distinct from ``DRIFT_UNPUSHED`` because the two
    do not resolve the same way — an ahead-of-upstream branch is fixed by a plain
    push, while a branch with no upstream needs ``--set-upstream`` and would
    otherwise fail every attempt with a misleading network-sounding error.

    Findings are deliberately observations, not remedies — callers (``status``,
    ``flush``) attach the remedy that fits their own context.
    """
    if not vault.exists():
        return [(DRIFT_MISSING, "directory does not exist")]
    if not _vault_is_git_toplevel(vault):
        return [
            (DRIFT_NOT_GIT, "not a git repo (or not its own toplevel) — records have no history")
        ]

    findings = []
    if not _vault_has_commits(vault):
        findings.append((DRIFT_NEVER_COMMITTED, "never committed — zero commits"))

    rc, status_out, _ = _git(vault, "status", "--porcelain")
    if rc == 0 and status_out.strip():
        n = len(status_out.splitlines())
        findings.append((DRIFT_UNCOMMITTED, f"{n} uncommitted change(s)"))

    rc_remote, remote_url, _ = _git(vault, "remote", "get-url", "origin")
    if rc_remote != 0 or not remote_url:
        findings.append((DRIFT_NO_REMOTE, "no origin remote — nothing is backed up off-disk"))
    elif _vault_has_commits(vault) and not _vault_has_upstream(vault):
        findings.append((DRIFT_NO_UPSTREAM, "never pushed — no upstream branch set"))
    elif _vault_unpushed(vault):
        findings.append((DRIFT_UNPUSHED, "unpushed commits"))

    return findings


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
