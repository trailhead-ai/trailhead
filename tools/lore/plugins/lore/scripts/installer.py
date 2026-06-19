"""Installer helpers for ``lore init`` (Slices 1 + 4, S5).

Provides five pure-ish functions used by ``cmd_init``:

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

- ``inject_agent_rules(rules_path, extra_paths=None)`` — inject a
  marker-delimited lore block into *rules_path* (always) and into each path in
  *extra_paths* that already exists (never creates stray files). Re-runs replace
  the block in place (no duplicates). A ``fcntl.flock`` exclusive lock guards
  each injection so concurrent ``lore init`` runs cannot double-inject (KU3,
  proven S2). (Slice 4, S5.)

- ``scan_for_rules_drift(search_root)`` — scan *search_root* for known
  rules-file candidates (``.cursorrules``, ``AGENTS.md``, ``.windsurfrules``)
  that are present but lack the marker block, and return them as a list of
  ``Path`` objects. Empty list means no drift. (Slice 4, S5.)

All filesystem operations use ``pathlib.Path``; no third-party deps (Axiom:
pure stdlib only). ``fcntl`` is POSIX stdlib (darwin/linux; trailhead targets
macOS/Linux only — KU3).
"""
from __future__ import annotations

import fcntl
import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# Agent-rules block content and markers (Slice 4, S5)
# ---------------------------------------------------------------------------

# Stable delimiters that bracket the injected block.  Every consumer that
# checks for the block (scan_for_rules_drift, idempotent re-inject) keys on
# the START marker; NEVER change these once deployed.
_RULES_START_MARKER = "<!-- lore:agent-rules:start -->"
_RULES_END_MARKER = "<!-- lore:agent-rules:end -->"

# The injected content.
#
# CRITICAL content requirement (Slice 4 plan):
#   (a) Vault records are written ONLY via the ``lore`` CLI — NEVER by direct
#       file edits AND NEVER via Bash/shell redirection or file commands
#       (``> file``, ``tee``, ``sed -i``, ``cp``, ``mv``).  The Claude Code
#       PreToolUse guardrail covers Edit/Write/MultiEdit/NotebookEdit but is
#       OPAQUE to Bash-mediated writes — this agent-rules block is the ONLY
#       protection for that gap.
#   (b) A brief pointer to the lore docs for agent-driven procedures.
#   (c) Documentation of the non-Claude-Code degradation (rules-only guardrail
#       — harnesses that lack a PreToolUse hook depend entirely on this block).
_RULES_BLOCK_BODY = """\
## Lore vault — mandatory write rules

**All** lore vault records are written **only** via the `lore` CLI.
**Never** write to vault files by any other means, including:

- Direct file edits (Edit / Write / MultiEdit tools)
- Bash or shell redirection: `> file`, `>> file`, `tee`, `sed -i`, `cp`, `mv`

The Claude Code PreToolUse guardrail blocks Edit/Write/MultiEdit/NotebookEdit
but is **opaque to Bash-mediated writes** — this rule is the **only**
protection for that gap.  Violating it silently corrupts vault records.

For harnesses without a PreToolUse hook (Cursor, Codex, etc.) this block is
the **sole guardrail** — treat it as binding regardless of harness.

Agent-driven lore procedures: see `lore --help` and the lore skills
(`/lore:decision`, `/lore:defer`, `/lore:finish`, etc.).
"""

_RULES_BLOCK = (
    f"{_RULES_START_MARKER}\n"
    f"{_RULES_BLOCK_BODY}"
    f"{_RULES_END_MARKER}\n"
)

# Known rules-file candidates to scan for drift (Slice 4 plan).
# Also referenced by ``cmd_init`` to enumerate extra injection targets.
RULES_CANDIDATES = [".cursorrules", "AGENTS.md", ".windsurfrules"]


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


# ---------------------------------------------------------------------------
# inject_agent_rules (Slice 4, S5)
# ---------------------------------------------------------------------------


def inject_agent_rules(
    rules_path: Path,
    extra_paths: list[Path] | None = None,
) -> None:
    """Inject a marker-delimited lore block into *rules_path* and any already-present extras.

    *rules_path* is always touched (created if absent; the canonical rules file
    for the install target — ``~/CLAUDE.md`` for global, ``<git-root>/CLAUDE.md``
    for ``--local``).

    Each path in *extra_paths* is injected **only if it already exists** — never
    creates stray files. This respects the plan invariant: "only touch rules files
    that already exist plus the canonical one".

    Injection is idempotent: the block between :data:`_RULES_START_MARKER` /
    :data:`_RULES_END_MARKER` is replaced in-place on re-runs (no duplicates).

    Concurrency: each file injection is guarded by a ``fcntl.flock`` LOCK_EX on
    a sibling ``.lore-rules.lock`` file so concurrent ``lore init`` runs cannot
    double-inject (KU3, proven S2 — same pattern as ``session_store.py``).

    Args:
        rules_path:   The canonical rules file to inject into (created if absent).
        extra_paths:  Additional rules files to inject into IF they already exist.
    """
    targets = [rules_path]
    for p in (extra_paths or []):
        if p.exists():
            targets.append(p)
    for target in targets:
        _inject_single(target)


def _inject_single(path: Path) -> None:
    """Inject the lore block into *path*, holding an exclusive flock (KU3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / ".lore-rules.lock"

    lock_fd = open(lock_path, "a")  # create-or-open, no truncate
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)  # blocks until exclusive
        _inject_locked(path)
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def _inject_locked(path: Path) -> None:
    """Write the lore block into *path*, replacing an existing block in-place.

    Must be called while holding the flock on the parent directory's lock file.
    """
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    else:
        existing = ""

    if _RULES_START_MARKER in existing:
        # Replace the existing block (between markers, inclusive).
        start = existing.index(_RULES_START_MARKER)
        end = existing.index(_RULES_END_MARKER) + len(_RULES_END_MARKER)
        # Consume a trailing newline after the end marker if present.
        if end < len(existing) and existing[end] == "\n":
            end += 1
        new_content = existing[:start] + _RULES_BLOCK + existing[end:]
    else:
        # Append to existing content (with a blank separator if content exists).
        if existing and not existing.endswith("\n\n"):
            separator = "\n" if existing.endswith("\n") else "\n\n"
        else:
            separator = ""
        new_content = existing + separator + _RULES_BLOCK

    path.write_text(new_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# scan_for_rules_drift (Slice 4, S5)
# ---------------------------------------------------------------------------


def scan_for_rules_drift(search_root: Path) -> list[Path]:
    """Return rules-file candidates under *search_root* that lack the marker block.

    Checks each known candidate filename (``_RULES_CANDIDATES``) directly under
    *search_root* (not recursive). A file is "drifted" if it exists AND does not
    contain :data:`_RULES_START_MARKER`.

    Returns an empty list when all present candidates carry the block.

    Args:
        search_root: Directory to scan (typically the git root or ``~``).

    Returns:
        List of ``Path`` objects for candidate files present but lacking the block.
    """
    drifted = []
    for name in RULES_CANDIDATES:
        candidate = search_root / name
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8")
            if _RULES_START_MARKER not in content:
                drifted.append(candidate)
    return drifted
