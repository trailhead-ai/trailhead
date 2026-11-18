"""Which task wrapper a run records, and where a workspace keeps it.

A workspace that records a wrapper writes it twice: as ``task_wrapper`` in its
manifest and as ``<slug>.task-wrapper.json``, a sibling of the workspace dir beside
the slug lock (mode 0600, written with the manifest's own atomic writer). The
sidecar is outside the workspace dir so a session granted the workspace cannot
rewrite it, and it is written before the manifest so a manifest that records a
wrapper never lacks one. A workspace with no wrapper has no sidecar.

`read_prior` decides, for one run, the manifest to carry forward and the wrapper to
record:

- a readable sidecar is the recorded wrapper, whatever the manifest says; a
  manifest that names another wrapper, or none, is rewritten to the sidecar's.
- a sidecar that exists but cannot be read raises ManifestError naming it: what
  the workspace recorded is unknown, so nothing is rebuilt or run.
- no sidecar and a readable manifest that names no wrapper: none; the
  environment never overrides it.
- no sidecar and a readable manifest that names a wrapper: ManifestError naming
  both files. The manifest is inside the workspace, so a session granted it could
  have written that wrapper; camp runs only a wrapper read from the sidecar, and
  neither runs the manifest's nor falls back to running bare.
- no sidecar and a manifest that exists but cannot be read: reconcile and seed
  rebuild it, as they always have, and the workspace never had a wrapper.
- no sidecar and no manifest: a creation. The wrapper is the environment's, if any.

`recorded_wrapper` answers the same question for a caller that only runs steps, and
`restore_manifest_wrapper` rewrites a manifest that disagrees with its sidecar.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..group.manifest import (
    TASK_WRAPPER_KEY,
    ManifestError,
    read_central_manifest,
    task_wrapper_of,
    write_central_manifest,
)
from .wrapper import env_wrapper

_SIDECAR_KEY = "task_wrapper"


@dataclass(frozen=True)
class PriorWorkspace:
    """manifest: the readable existing manifest, else None. wrapper: what to record."""

    manifest: dict[str, Any] | None
    wrapper: Any


def sidecar_path(mpath: Path) -> Path:
    ws_dir = mpath.parent
    return ws_dir.parent / f"{ws_dir.name}.task-wrapper.json"


def _read_sidecar(mpath: Path) -> tuple[bool, Any]:
    """(present, wrapper) for the sidecar of *mpath*; an unreadable sidecar raises."""
    sidecar = sidecar_path(mpath)
    if not sidecar.exists() and not sidecar.is_symlink():
        return False, None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get(_SIDECAR_KEY), str):
            raise ValueError(f"no {_SIDECAR_KEY} string")
    except (OSError, ValueError) as exc:
        raise ManifestError(
            f"camp: cannot tell which task wrapper the workspace recorded: its wrapper "
            f"record at {sidecar} cannot be read ({exc}); its manifest is at {mpath}"
        ) from exc
    return True, data[_SIDECAR_KEY]


def _refuse_manifest_only_wrapper(mpath: Path, manifest: dict[str, Any]) -> None:
    if task_wrapper_of(manifest) is not None:
        raise ManifestError(
            f"camp: the workspace manifest at {mpath} names a task wrapper but its "
            f"wrapper record at {sidecar_path(mpath)} does not exist; the manifest is "
            f"writable from inside the workspace, so camp will not run a wrapper "
            f"it names, and will not run the workspace's steps without one"
        )


def read_prior(mpath: Path, env: Mapping[str, str] | None) -> PriorWorkspace:
    """The manifest to carry forward and the wrapper to record for a run on *mpath*."""
    present, sidecar_wrapper = _read_sidecar(mpath)
    manifest: dict[str, Any] | None = None
    manifest_exists = mpath.exists()
    if manifest_exists:
        try:
            manifest = read_central_manifest(mpath)
        except ManifestError:
            manifest = None
    if present:
        return PriorWorkspace(manifest, sidecar_wrapper)
    if manifest is not None:
        _refuse_manifest_only_wrapper(mpath, manifest)
        return PriorWorkspace(manifest, None)
    return PriorWorkspace(None, None if manifest_exists else env_wrapper(env))


def recorded_wrapper(mpath: Path, manifest: dict[str, Any]) -> Any:
    """The wrapper a run of *manifest*'s steps applies: the sidecar's, else none.

    A manifest that names a wrapper with no sidecar raises ManifestError.
    """
    present, sidecar_wrapper = _read_sidecar(mpath)
    if present:
        return sidecar_wrapper
    _refuse_manifest_only_wrapper(mpath, manifest)
    return None


def restore_manifest_wrapper(mpath: Path) -> None:
    """Rewrite the manifest's wrapper to the sidecar's when they differ. Hold the manifest lock."""
    present, sidecar_wrapper = _read_sidecar(mpath)
    if not present:
        return
    manifest = read_central_manifest(mpath)
    if manifest.get(TASK_WRAPPER_KEY) != sidecar_wrapper:
        manifest[TASK_WRAPPER_KEY] = sidecar_wrapper
        write_central_manifest(mpath, manifest)


def record_sidecar(mpath: Path, wrapper: Any) -> None:
    """Write *wrapper* beside the workspace dir; a workspace with no wrapper gets no sidecar."""
    if wrapper is not None:
        write_central_manifest(sidecar_path(mpath), {_SIDECAR_KEY: wrapper})


def reap_sidecar(mpath: Path) -> None:
    """Remove the sidecar of a workspace that no longer exists."""
    sidecar_path(mpath).unlink(missing_ok=True)
