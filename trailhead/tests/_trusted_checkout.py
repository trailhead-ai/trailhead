"""A checkout the pinned gateway shims accept: owned by the operator, writable by no one else.

``trailhead install`` refuses to pin a shim to a checkout a sandboxed session could write, and the
checkout the suite runs from is one (a camp worktree). A test that builds the gateway shims builds
them from a copy made here, with no sandbox-writable roots, since the temp directory it sits in is
itself one.
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

from trailhead.install import build_cli_shims

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_trusted_checkout(dest: Path, source: Path = REPO_ROOT) -> Path:
    """Copy what the pinned scripts run (``bin/``, the ``trailhead`` package, the portage plugin ``portage-eval`` imports,
    ``pyproject.toml``) of *source* to
    *dest*, closed to group and others."""
    dest.mkdir(parents=True)
    shutil.copytree(
        source / "bin", dest / "bin", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(
        source / "trailhead",
        dest / "trailhead",
        ignore=shutil.ignore_patterns("tests", "__pycache__"),
    )
    portage = Path("tools") / "portage" / "plugins" / "portage"
    shutil.copytree(source / portage, dest / portage, ignore=shutil.ignore_patterns("tests", "evals", "__pycache__"))
    shutil.copy2(source / "pyproject.toml", dest / "pyproject.toml")
    for path in [dest, *dest.rglob("*")]:
        if not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~(stat.S_IWGRP | stat.S_IWOTH))
    return dest


def build_gateway_shims(cli_tools, *, env, checkout: Path, **kwargs):
    """``build_cli_shims`` with the Outpost tool enabled, from *checkout*, treating no path as sandbox-writable."""
    kwargs.setdefault("sandbox_write_roots", ())
    return build_cli_shims(cli_tools, env=env, root=checkout, outpost_enabled=True, **kwargs)
