"""Multi-tool composition orchestrator for trailhead (harness-agnostic core).

``wire()`` composes a set of plugins into ONE harness's composed tree and then
delegates the harness-specific registration tail to an injected
:class:`~trailhead.harness.base.Harness`.  It owns the generic, harness-agnostic
work — composition, staging, atomic promote, the wire lock, and best-effort
sequencing — and knows nothing about ``claude plugin`` or ``marketplace.json``.

For each tool in the selection (with its selected subagents + skills):
    1. Load the tool's plugin inventory (``capabilities.toml``).
    2. compose_plan (pure — no writes).
    3. Compose into a staging dir under ``<composed_root>/plugins/``.
    4. Atomic promote: replace the live dest with the staging dir.
    5. Record the tool as successfully promoted (on-disk truth).

Then, ONCE after the compose loop:
    6. ``harness.generate_manifest(<promoted>, composed_root)``.
    7. ``harness.register(composed_root)`` (gated on ``harness.is_registered``),
       then per promoted tool ``harness.install_tool`` (not yet installed) or
       ``harness.rewire_tool`` (already installed — self-heal).

Per-harness root (multi-harness isolation)
------------------------------------------
``composed_root = harness.composed_root(state_dir)`` →
``state_dir/composed/<harness.name>/``.  Each harness gets its own tree + its own
registration markers, so two harnesses never collide.

On-disk truth (blast-radius isolation)
--------------------------------------
The manifest lists only the tools that promoted SUCCESSFULLY this run (validity =
``live_dest/.claude-plugin/plugin.json`` exists).  A tool whose compose/promote
raised is omitted; an unselected tool is never processed → omitted.

Atomicity
---------
All writes go to a temporary staging dir first; only after a clean compose is it
atomically promoted via ``shutil.move``.  Staging cleanup always runs in a
``try/finally``, so a ``KeyboardInterrupt`` / ``SystemExit`` mid-compose
never orphans ``_<tool>_staging_*`` dirs.

Multi-tool semantics
--------------------
``wire()`` is best-effort sequential: a failure on tool N raises
``WireError(tool=N, stage=…, cause=…)`` immediately; tools 0…N-1 stay wired,
tools N+1… are not attempted.

Hermeticity
-----------
The harness-CLI runner is injectable via ``runner=`` (threaded through to the
harness).  Tests always pass a stub; ``wire()`` itself never imports subprocess.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from trailhead.capabilities import load_manifest
from trailhead.compose import apply_plan, compose_plan
from trailhead.paths import ensure_dir, state_dir

_LOCK_FILENAME = "trailhead.lock"

# A tool's selection: (subagents, skills) where each maps name -> override_path|None.
Selection = dict[str, tuple[dict[str, str | None], dict[str, str | None]]]


class LockError(Exception):
    """Raised when the shared wire lock is already held by another operation."""


def _acquire_wire_lock(lock_path: Path) -> None:
    """Acquire the exclusive O_EXCL wire lock at lock_path.

    Raises LockError if the file already exists (lock is held by another process).
    """
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, f"locked by pid {os.getpid()}\n".encode())
        os.close(fd)
    except FileExistsError:
        raise LockError(
            f"trailhead: another trailhead operation is already running "
            f"(lock file exists at {lock_path}).\n"
            f"If no other process is running, remove the lock file and retry:\n"
            f"  rm {lock_path}"
        )


def _release_wire_lock(lock_path: Path) -> None:
    """Release the wire lock (delete the file)."""
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


@contextlib.contextmanager
def wire_lock(*, env: dict[str, str] | None = None):
    """Context manager that acquires/releases the shared wire lock.

    Acquires the O_EXCL lock under state_dir("trailhead")/trailhead.lock and
    releases it in the finally block regardless of exceptions.

    Raises LockError if the lock is already held.
    """
    _environ = env if env is not None else dict(os.environ)
    _state_dir = state_dir("trailhead", env=_environ)
    ensure_dir(_state_dir)
    lock_path = _state_dir / _LOCK_FILENAME
    _acquire_wire_lock(lock_path)
    try:
        yield lock_path
    finally:
        _release_wire_lock(lock_path)


@dataclass
class WireError(Exception):
    """Raised when composing or registering a single tool fails.

    Attributes:
        tool:  The tool name that failed (e.g. ``"craft"``).
        stage: Which phase failed: ``"compose"``, ``"promote"``, or
               ``"register"`` (the install/rewire harness step).
        cause: The underlying exception (also chained via ``__cause__``).
    """

    tool: str
    stage: str
    cause: BaseException

    def __str__(self) -> str:
        return f"wire failed for tool {self.tool!r} at stage {self.stage!r}: {self.cause}"


def wire(
    selection: Selection,
    *,
    harness,
    manifest_paths: dict[str, Path] | None = None,
    env: dict[str, str] | None = None,
    runner=None,
) -> None:
    """Compose and register each tool in ``selection`` into one harness.

    Args:
        selection:      Mapping of tool name → ``(subagents, skills)``, where each
                        is a ``{name: override_path | None}`` map.  A tool with two
                        empty maps composes its always-on set only.  Tools absent
                        from ``selection`` are not wired (no dir created).
        harness:        The :class:`~trailhead.harness.base.Harness` to install into.
        manifest_paths: Override the inventory path for each tool (for testing).
                        Defaults to the repo-relative ``tools/<tool>/capabilities.toml``.
        env:            Environment dict for path resolution (supports
                        ``TRAILHEAD_STATE_DIR`` override for test hermeticity).
        runner:         Injectable harness-CLI runner (passed to the harness).

    Multi-tool semantics: best-effort sequential — a per-tool failure
    raises ``WireError`` naming the tool + stage; earlier tools stay wired.
    """
    _environ = env if env is not None else dict(os.environ)
    _manifest_paths = manifest_paths or default_manifest_paths()

    composed_root = harness.composed_root(state_dir("trailhead", env=_environ))
    ensure_dir(composed_root / "plugins")

    # On-disk truth: tools that promote successfully THIS run land in the manifest.
    promoted: list[str] = []

    try:
        for tool, (subagents, skills) in selection.items():
            manifest = load_manifest(_manifest_paths[tool])
            live_dest = composed_root / "plugins" / tool

            _compose_tool(
                tool=tool,
                manifest=manifest,
                subagents=subagents,
                skills=skills,
                composed_root=composed_root,
                live_dest=live_dest,
            )

            if (live_dest / ".claude-plugin" / "plugin.json").exists():
                promoted.append(tool)
    finally:
        # Regenerate the harness manifest from whatever promoted — even after a
        # per-tool failure, so the surviving tools stay registered and the failed
        # tool is structurally excluded.  Skip if nothing promoted.
        if promoted:
            harness.generate_manifest(promoted, composed_root)

    if not promoted:
        return

    # Register ONCE (gated on the harness's own registration marker; the call is
    # idempotent regardless).
    if not harness.is_registered(composed_root):
        harness.register(composed_root, runner=runner)

    # Per-tool install (not yet installed) or rewire (installed — self-heal).
    for tool in promoted:
        try:
            if harness.is_installed(tool, composed_root):
                harness.rewire_tool(tool, composed_root, runner=runner)
            else:
                harness.install_tool(tool, composed_root, runner=runner)
        except BaseException as exc:
            raise WireError(tool=tool, stage="register", cause=exc) from exc


def _compose_tool(
    tool: str,
    manifest,
    subagents: dict[str, str | None],
    skills: dict[str, str | None],
    composed_root: Path,
    live_dest: Path,
) -> None:
    """Compose one tool into the live dest using staging + atomic promote.

    Raises WireError naming the tool and stage on any failure.
    Staging cleanup always runs via try/finally.
    """
    staging_parent = composed_root / "plugins"
    staging_dir_path: Path | None = None

    try:
        staging_dir_path = Path(tempfile.mkdtemp(prefix=f"_{tool}_staging_", dir=staging_parent))

        try:
            plan = compose_plan(manifest, subagents, skills, staging_dir_path)
            apply_plan(plan, mode="copy")
        except BaseException as exc:
            raise WireError(tool=tool, stage="compose", cause=exc) from exc

        try:
            if live_dest.exists():
                shutil.rmtree(live_dest)
            shutil.move(str(staging_dir_path), str(live_dest))
        except BaseException as exc:
            raise WireError(tool=tool, stage="promote", cause=exc) from exc

        staging_dir_path = None  # Transferred; don't clean up in finally

    finally:
        if staging_dir_path is not None and staging_dir_path.exists():
            shutil.rmtree(staging_dir_path, ignore_errors=True)


def default_manifest_paths() -> dict[str, Path]:
    """Return the default inventory paths relative to the repo root."""
    repo_root = Path(__file__).parent.parent
    tools = ["lore", "camp", "craft", "portage", "landing"]
    return {t: repo_root / "tools" / t / "capabilities.toml" for t in tools}


# Keep the private alias for any callers that haven't migrated yet.
_default_manifest_paths = default_manifest_paths
