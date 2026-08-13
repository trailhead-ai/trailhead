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
    and ``status``, as opposed to ``resolve_active_vault``'s ``default``-only view,
    plus ``_partition_writable_vaults`` — the ``shared: true`` exclusion every
    WRITE/PUSH fan-out over that enumeration applies;
  - the git primitives (``_git`` / ``_vault_is_git_toplevel``) shared by ``sync``
    and ``flush``, plus ``_vault_drift`` — the "is this vault actually backed up?"
    probe shared by ``status`` and ``flush``;
  - the shared ``--session-id`` / ``--worktree`` subparser selectors;
  - the shared stdin read (``_read_stdin_body``).
"""
from __future__ import annotations

import os
import select
import subprocess
import sys
from pathlib import Path

from ..vault import config as vault_config_mod


#: How long ``_read_stdin_body`` waits for stdin to become ready (data or EOF)
#: before concluding it is a silent, never-EOF'ing pipe. Kept short: a closed
#: pipe / ``/dev/null`` / a heredoc's already-written bytes all show up as
#: "ready" near-instantly, so this only ever costs real wall-clock time on the
#: pathological case it exists to catch.
_STDIN_READY_TIMEOUT_S = 0.5


class StdinSilentError(Exception):
    """Raised by ``_read_stdin_body`` instead of blocking in ``.read()``.

    Fires when stdin is a non-tty pipe that is open, has delivered no data, and
    has not closed (no EOF) within :data:`_STDIN_READY_TIMEOUT_S` — the
    never-EOF case in lesson/lore-record-update-blocks-forever-on-a-silent-open-stdin.
    Callers catch this and refuse the write rather than hanging in ``.read()``.
    """


def _read_stdin_body() -> str:
    """Return the piped stdin body, or ``""`` when stdin is a TTY (no pipe).

    The shared read used by every ``lore record``/``session`` write path. An empty
    return covers both a TTY and an empty/closed pipe — callers that need to tell
    "no stdin" from "empty body" key on ``== ""`` (see the record-update metadata-
    only path).

    Raises :class:`StdinSilentError` rather than blocking when stdin is a
    non-tty pipe that is open but silent (no data, no EOF) past the ready
    timeout — see the class docstring.
    """
    if sys.stdin.isatty():
        return ""
    ready, _, _ = select.select([sys.stdin], [], [], _STDIN_READY_TIMEOUT_S)
    if not ready:
        raise StdinSilentError(
            "stdin is open but silent — pass a body, redirect from a source "
            "that closes (e.g. `</dev/null`), or omit the pipe"
        )
    return sys.stdin.read()


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
    config error explicitly. This includes a well-formed-JSON-but-wrong-shape
    config (e.g. a top-level list, or a ``"vaults"`` array of non-dict entries) —
    ``validate_config`` normalizes those shapes to ``VaultConfigError`` (its
    single parse+validate boundary), so catching that one type here covers them
    too, and callers must not brick on them either.
    """
    config_path = _resolve_config_path()
    if not config_path.exists():
        return None
    try:
        vaults = vault_config_mod.load_config(str(config_path))
    except (
        vault_config_mod.VaultConfigError,
        OSError,
        ValueError,
    ):
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
      returns ``None``) the breakage is reported rather than swallowed. This case
      also covers a well-formed-JSON-but-wrong-shape config (e.g. a top-level
      list, or non-dict ``"vaults"`` entries) — ``validate_config`` normalizes
      those shapes to ``VaultConfigError`` too, so this catches them the same
      way it catches any other invalid config.
    - **Valid config** → one ``(name, path)`` pair per vault, in config order,
      names already normalized by ``load_config``.
    """
    config_path = _resolve_config_path()
    floor = [("default", Path(vault_config_mod.resolve_active_vault()))]
    if not config_path.exists():
        return floor, None
    try:
        vaults = vault_config_mod.load_config(str(config_path))
    except (
        vault_config_mod.VaultConfigError,
        OSError,
        ValueError,
    ) as exc:
        return floor, f"cannot read {config_path}: {exc}"
    return [(v.name, Path(v.path)) for v in vaults], None


def _resolve_all_vaults_strict(what: str) -> list[tuple[str, Path]] | None:
    """``_resolve_all_vaults`` that REFUSES on an unreadable config.

    Returns the ``(name, path)`` pairs, or ``None`` after printing the diagnostic —
    the caller then exits non-zero without touching a vault.

    The session surface resolves a key by asking every configured vault whether it
    holds it, so a config that will not load makes "no vault holds it" unknowable
    rather than false. Degrading to the floor list turned that into a confident
    wrong answer: ``lore flush`` printed "no session exists — nothing to flush" and
    exited 0 for a session sitting ``dirty`` in a vault the broken config never
    named. Same refusing posture as ``lore sync``'s ``_select_targets`` — *what*
    names the operation being refused (e.g. ``"flush"``).
    """
    vaults, error = _resolve_all_vaults()
    if error is not None:
        print(f"error: {error}", file=sys.stderr)
        print(
            f"  Aborting — refusing to {what} against a partial vault set.",
            file=sys.stderr,
        )
        return None
    return vaults


def _shared_vault_paths() -> set[str]:
    """Resolved paths of every ``shared: true`` vault in the live config.

    A ``shared`` vault is a multi-user vault: its content is UNTRUSTED input, so
    no local command may let it actuate a write, a commit, or a push under this
    user's git identity. This is the lookup behind
    :func:`_writable_vaults`; reads are fenced separately (the index's
    ``shared`` column) and are not restricted here.

    Vanilla usage (no ``config.json``) and an unreadable config both yield an
    empty set — the former has no shared vault to name, and the latter is
    already a REFUSAL at every write surface (:func:`_resolve_all_vaults_strict`)
    that reaches this helper, so there is no path on which an empty set here
    silently widens a write.
    """
    loaded = _load_vault_config()
    if loaded is None:
        return set()
    _, vaults = loaded
    return {
        str(Path(v.path).resolve())
        for v in vaults
        if vault_config_mod.is_shared(v)
    }


def _resolve_all_vaults_and_shared() -> tuple[list[tuple[str, Path]], set[str], str | None]:
    """Single-read counterpart to calling ``_resolve_all_vaults`` then
    deriving the shared set from the same config read.

    Calling those two helpers separately reads and re-parses ``config.json``
    twice; if the file changes between the reads (a concurrent ``lore vault
    add``/edit), the vault list and the shared-set can disagree — and the
    shared filter fails **open** (empty set) rather than closed, which is the
    wrong direction for a trust boundary (an undelimited plaintext area menu
    reaching agent context). This function's ONE read of ``config.json`` — the
    single ``load_config`` call below — is what the vault list and shared-set
    both derive from, so the two views can never diverge from each other. The
    floor vault (``resolve_active_vault()``) is resolved separately and only
    lazily, on the two paths that actually need it (no config / unreadable
    config); it plays no part in the divergence this function exists to
    prevent, since the valid-config path never touches it.

    The shared set is keyed by ``Vault.name`` — the config's own
    globally-unique-after-normalization key (``validate_config`` rejects a
    duplicate) — rather than by resolved path string. A resolved-path key
    lets two config entries pointing at the same physical directory under
    different casing (e.g. ``vaults/SharedTeam`` vs ``vaults/sharedteam``)
    each resolve to a *different* string on a case-insensitive filesystem —
    ``Path.resolve()`` normalizes ``..`` and symlinks but never casefolds —
    so a non-shared alias of a ``shared: true`` vault would resolve to a
    string absent from a path-keyed set and slip past the filter. Keying on
    ``name`` instead carries the authoritative ``Vault.shared`` flag through
    by construction: whether a root is excluded depends on what its own
    config entry declared, never on how its path happens to compare against
    another entry's.

    Same three-case contract as :func:`_resolve_all_vaults` (no config →
    floor vault + no error; unparseable/wrong-shape config → floor vault +
    named error; valid config → full vault list), with the shared-name set
    derived from that same read (empty in both floor cases).
    """
    config_path = _resolve_config_path()
    if not config_path.exists():
        floor = [("default", Path(vault_config_mod.resolve_active_vault()))]
        return floor, set(), None
    try:
        vaults = vault_config_mod.load_config(str(config_path))
    except (
        vault_config_mod.VaultConfigError,
        OSError,
        ValueError,
    ) as exc:
        floor = [("default", Path(vault_config_mod.resolve_active_vault()))]
        return floor, set(), f"cannot read {config_path}: {exc}"
    all_vaults = [(v.name, Path(v.path)) for v in vaults]
    shared_names = {v.name for v in vaults if vault_config_mod.is_shared(v)}
    return all_vaults, shared_names, None


def _partition_writable_vaults(vaults) -> tuple[list, list]:
    """Split ``(name, path)`` pairs into ``(writable, shared)``, order preserved.

    The session surface resolves a key by asking every configured vault whether
    it holds it. That is fine for reads, but it also made every configured vault
    a WRITE target: a dirty session record planted in a shared vault would be
    flipped ``clean``, committed, and pushed by a bare ``lore flush`` — untrusted
    content actuating a local commit under the operator's git identity.
    Excluding ``shared: true`` vaults from a write fan-out is the same default
    ``lore record rename``'s reference sweep already takes.

    The ``shared`` half is RETURNED rather than dropped so callers can name what
    they skipped once they know it was relevant: an operator whose session really
    does live in a shared vault must be told why nothing happened, and an
    operator who merely HAS a shared vault must not be told anything at all.

    ``vaults`` may be ``(name, path)`` pairs or bare paths; the shared half is
    matched on the resolved path either way.
    """
    shared_paths = _shared_vault_paths()
    if not shared_paths:
        return list(vaults), []
    writable, shared = [], []
    for entry in vaults:
        path = entry[1] if isinstance(entry, tuple) else entry
        (shared if str(Path(path).resolve()) in shared_paths else writable).append(entry)
    return writable, shared


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
