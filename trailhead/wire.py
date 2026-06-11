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
the live dest via ``shutil.move``.  If promotion fails mid-way (e.g. disk
full during copy), the prior live dest is untouched — staging cleanup happens
in a finally block.

A tool absent from ``selection`` is not wired at all (no dir, no entry) —
this is the preset-gating guarantee (B-5).

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

import os
import shutil
import tempfile
from pathlib import Path

from trailhead.capabilities import load_manifest
from trailhead.compose import apply_plan, compose_plan
from trailhead.paths import ensure_dir, state_dir
from trailhead.registry import generate_marketplace_json, register, rewire


def wire(
    selection: dict[str, set[str]],
    *,
    manifest_paths: dict[str, Path] | None = None,
    env: dict[str, str] | None = None,
    runner=None,
) -> None:
    """Compose and register each tool in selection.

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

        _compose_tool(
            tool=tool,
            manifest=manifest,
            caps=caps,
            mkt_root=mkt_root,
            live_dest=live_dest,
            runner=runner,
            already_wired=live_dest.exists(),
        )


def _compose_tool(
    tool: str,
    manifest,
    caps: set[str],
    mkt_root: Path,
    live_dest: Path,
    runner,
    already_wired: bool,
) -> None:
    """Compose one tool into the live dest using staging + atomic promote (R-1)."""
    staging_parent = mkt_root / "plugins"
    staging_dir_path: Path | None = None

    try:
        # Create staging dir (sibling of live_dest — same filesystem for atomic move)
        staging_dir_path = Path(
            tempfile.mkdtemp(prefix=f"_{tool}_staging_", dir=staging_parent)
        )

        # Pure plan — no writes until apply_plan
        plan = compose_plan(manifest, caps, staging_dir_path)

        # Write to staging only (S-2: always copy mode)
        apply_plan(plan, mode="copy")

        # Atomic promote: remove old live dest, move staging into place
        if live_dest.exists():
            shutil.rmtree(live_dest)
        shutil.move(str(staging_dir_path), str(live_dest))
        staging_dir_path = None  # Transferred; don't clean up in finally

    except Exception:
        # Leave the live dest untouched; clean up staging
        if staging_dir_path is not None and staging_dir_path.exists():
            shutil.rmtree(staging_dir_path, ignore_errors=True)
        raise

    # Generate marketplace.json under mkt_root/.claude-plugin/
    generate_marketplace_json(tool=tool, mkt_root=mkt_root)

    # Register or rewire via harness CLI
    if already_wired:
        rewire(tool=tool, mkt_root=mkt_root, runner=runner)
    else:
        register(tool=tool, mkt_root=mkt_root, runner=runner)


def _default_manifest_paths() -> dict[str, Path]:
    """Return the default manifest paths relative to the repo root."""
    repo_root = Path(__file__).parent.parent
    return {
        "lore": repo_root / "tools" / "lore" / "capabilities.toml",
        "camp": repo_root / "tools" / "camp" / "capabilities.toml",
        "forge": repo_root / "tools" / "forge" / "capabilities.toml",
    }
