"""Multi-tool composition orchestrator for trailhead.

This module is a **thin orchestrator** — it has no composition or registration
logic of its own.  It sequences:

    for each tool in selection with at least one capability (or base-only):
        1. Load the tool's manifest.
        2. compose_plan (pure — no writes).
        3. Compose into a staging dir under state_dir("trailhead")/composed/tmp/.
        4. Atomic promote: replace the live dest with the staging dir.
        5. generate_marketplace_json.
        6. register (or rewire if already registered) via the harness CLI.

R-1 atomicity
-------------
All filesystem writes go to a temporary staging directory first.  Only after
a clean compose (no exceptions) is the staging dir atomically promoted into
the live dest via ``shutil.move``.  Staging cleanup is always performed in a
``try/finally`` block, so a ``KeyboardInterrupt`` or ``SystemExit`` mid-compose
does not orphan ``_<tool>_staging_*`` directories (C-1.1).

The promote step is a ``rmtree`` + ``shutil.move`` pair.  The window between
those two operations is a known crash risk for this single-user tool: if the
process dies after ``rmtree`` but before ``move`` completes, the live dest is
gone but the staging dir has not taken its place.  Acceptable for the current
use case; a future hardening pass could use ``os.replace`` via a temp sibling
for a near-atomic swap (Minor-1).

A tool absent from ``selection`` is not wired at all (no dir, no entry) —
this is the preset-gating guarantee (B-5).

C-1.2 / I-1 multi-tool semantics
---------------------------------
``wire()`` is **best-effort sequential**: it processes tools in iteration
order.  A failure on tool N raises ``WireError(tool=N, stage=..., cause=...)``
immediately; tools already processed (0…N-1) remain fully wired.  Tool N+1…
are not attempted.  This is intentional — partial wiring is visible and named,
not silently swallowed.  Full multi-tool rollback is out of scope.

C-2 register-vs-rewire decision
---------------------------------
The register-vs-rewire decision is keyed on the ``<mkt_root>/.trailhead-registered``
sentinel file written by ``registry.register`` after both CLI steps succeed.
This avoids the wedge where a prior ``register`` promoted the plugin tree but
failed before ``install`` completed — in that case the dir exists but the
marker does not, so the next ``wire`` call re-attempts ``register`` (self-
healing) instead of calling ``plugin update`` on a never-installed plugin.

S-2
---
``apply_plan`` is always called with ``mode="copy"``.  The ``mode`` argument
is never exposed to any caller or config value.

B-3 hermeticity
---------------
The harness-CLI runner is injectable via ``runner=``.  Tests always pass a
stub; the default is the real ``subprocess.run`` path inside
``trailhead.registry``.  ``wire()`` itself never imports subprocess.
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
from trailhead.registry import generate_marketplace_json, register, rewire

_REGISTERED_MARKER = ".trailhead-registered"
_LOCK_FILENAME = "trailhead.lock"


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
    """Context manager that acquires/releases the shared wire lock (R-8).

    Acquires the O_EXCL lock under state_dir("trailhead")/trailhead.lock.
    Releases it in the finally block regardless of exceptions.

    Usage::

        with wire_lock(env=_env):
            wire(selection, env=_env)

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
               ``"register"``/``"rewire"``.
        cause: The underlying exception (also chained via ``__cause__``).
    """

    tool: str
    stage: str
    cause: BaseException

    def __str__(self) -> str:
        return (
            f"wire failed for tool {self.tool!r} at stage {self.stage!r}: {self.cause}"
        )


def wire(
    selection: dict[str, set[str]],
    *,
    manifest_paths: dict[str, Path] | None = None,
    env: dict[str, str] | None = None,
    runner=None,
) -> None:
    """Compose and register each tool in selection.

    Multi-tool semantics (C-1.2/I-1)
    ---------------------------------
    ``wire()`` is **best-effort sequential**: tools are processed in iteration
    order.  A failure on any tool raises ``WireError`` naming the tool and the
    stage (``compose``, ``promote``, or ``register``/``rewire``).  Tools
    processed before the failure remain fully wired; tools after the failure
    are not attempted.  Full rollback across all tools is out of scope.

    Args:
        selection:      Mapping of tool name → set of capability names to wire.
                        A tool with an empty set means base-only (always-on set).
                        Tools absent from selection are not wired (no dir created).
        manifest_paths: Override the manifest path for each tool (for testing).
                        Defaults to the repo-relative ``tools/<tool>/capabilities.toml``.
        env:            Environment dict for path resolution.  Supports
                        ``TRAILHEAD_STATE_DIR`` override for test hermeticity (B-3).
                        Defaults to ``os.environ``.
        runner:         Injectable harness-CLI runner (see ``registry.py``).
                        Pass a stub in tests; never invoke the real CLI in tests.
    """
    _environ = env if env is not None else dict(os.environ)
    _manifest_paths = manifest_paths or _default_manifest_paths()

    composed_root = state_dir("trailhead", env=_environ) / "composed"

    for tool, caps in selection.items():
        manifest_path = _manifest_paths[tool]
        manifest = load_manifest(manifest_path)
        mkt_root = composed_root / tool
        live_dest = mkt_root / "plugins" / tool

        ensure_dir(mkt_root / "plugins")

        # C-2: key register-vs-rewire on the registration-state marker,
        # not on dir existence — so a half-registered tool (dir exists, marker
        # absent) self-heals via register instead of wedging on plugin update.
        already_registered = (mkt_root / _REGISTERED_MARKER).exists()

        _compose_tool(
            tool=tool,
            manifest=manifest,
            caps=caps,
            mkt_root=mkt_root,
            live_dest=live_dest,
            runner=runner,
            already_registered=already_registered,
        )


def _compose_tool(
    tool: str,
    manifest,
    caps: set[str],
    mkt_root: Path,
    live_dest: Path,
    runner,
    already_registered: bool,
) -> None:
    """Compose one tool into the live dest using staging + atomic promote (R-1).

    Raises WireError (C-1.2) naming the tool and stage on any failure.
    Staging cleanup always runs via try/finally (C-1.1).
    """
    staging_parent = mkt_root / "plugins"
    staging_dir_path: Path | None = None

    try:
        # Create staging dir (sibling of live_dest — same filesystem for atomic move)
        staging_dir_path = Path(
            tempfile.mkdtemp(prefix=f"_{tool}_staging_", dir=staging_parent)
        )

        try:
            # Pure plan — no writes until apply_plan
            plan = compose_plan(manifest, caps, staging_dir_path)
            # Write to staging only (S-2: always copy mode)
            apply_plan(plan, mode="copy")
        except BaseException as exc:
            raise WireError(tool=tool, stage="compose", cause=exc) from exc

        try:
            # Atomic promote: remove old live dest, move staging into place.
            # Minor-1: the rmtree + move pair has a crash window (process dies
            # between the two calls → live dest gone, staging not yet in place).
            # Acceptable for this single-user tool; a near-atomic swap via
            # os.replace would eliminate the window if hardening is needed later.
            if live_dest.exists():
                shutil.rmtree(live_dest)
            shutil.move(str(staging_dir_path), str(live_dest))
        except BaseException as exc:
            raise WireError(tool=tool, stage="promote", cause=exc) from exc

        staging_dir_path = None  # Transferred; don't clean up in finally

    finally:
        # C-1.1: always clean up the staging dir unless it was successfully
        # promoted (staging_dir_path is set to None on success).
        if staging_dir_path is not None and staging_dir_path.exists():
            shutil.rmtree(staging_dir_path, ignore_errors=True)

    # Generate marketplace.json under mkt_root/.claude-plugin/
    generate_marketplace_json(tool=tool, mkt_root=mkt_root)

    # Register or rewire via harness CLI (C-2: marker-keyed decision)
    try:
        if already_registered:
            rewire(tool=tool, mkt_root=mkt_root, runner=runner)
        else:
            register(tool=tool, mkt_root=mkt_root, runner=runner)
    except BaseException as exc:
        stage = "rewire" if already_registered else "register"
        raise WireError(tool=tool, stage=stage, cause=exc) from exc


def default_manifest_paths() -> dict[str, Path]:
    """Return the default manifest paths relative to the repo root."""
    repo_root = Path(__file__).parent.parent
    return {
        "lore": repo_root / "tools" / "lore" / "capabilities.toml",
        "camp": repo_root / "tools" / "camp" / "capabilities.toml",
        "craft": repo_root / "tools" / "craft" / "capabilities.toml",
    }


# Keep the private alias for any callers that haven't migrated yet.
_default_manifest_paths = default_manifest_paths
