"""Which task wrapper a run records, and where a workspace keeps it.

A workspace that records a wrapper writes it twice: as ``task_wrapper`` in its
manifest and as ``task-wrapper.json`` beside the manifest (mode 0600, written with
the manifest's own atomic writer). A workspace with no wrapper has no sidecar.

`read_prior` decides, for one run, the manifest to carry forward and the wrapper to
record:

- no manifest: a creation. The wrapper is the environment's, if any.
- a readable manifest: the wrapper it recorded; the environment never overrides it.
- a manifest that exists but cannot be read: reconcile and seed rebuild it, as they
  always have, and the wrapper comes from the sidecar. No sidecar means the workspace
  never had a wrapper and records none.
- a sidecar that exists but cannot be read raises ManifestError naming both files:
  what the workspace recorded is unknown, so nothing is rebuilt or run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..group.manifest import ManifestError, read_central_manifest, task_wrapper_of, write_central_manifest
from .wrapper import env_wrapper

SIDECAR_NAME = "task-wrapper.json"
_SIDECAR_KEY = "task_wrapper"


@dataclass(frozen=True)
class PriorWorkspace:
    """manifest: the readable existing manifest, else None. wrapper: what to record."""

    manifest: dict[str, Any] | None
    wrapper: Any


def sidecar_path(mpath: Path) -> Path:
    return mpath.parent / SIDECAR_NAME


def _read_sidecar(mpath: Path, manifest_error: ManifestError) -> Any:
    sidecar = sidecar_path(mpath)
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get(_SIDECAR_KEY), str):
            raise ValueError(f"no {_SIDECAR_KEY} string")
    except (OSError, ValueError) as exc:
        raise ManifestError(
            f"camp: cannot tell which task wrapper the workspace recorded: its manifest "
            f"at {mpath} cannot be read ({manifest_error}) and its wrapper record at "
            f"{sidecar} cannot be read ({exc})"
        ) from exc
    return data[_SIDECAR_KEY]


def read_prior(mpath: Path, env: Mapping[str, str] | None) -> PriorWorkspace:
    """The manifest to carry forward and the wrapper to record for a run on *mpath*."""
    if not mpath.exists():
        return PriorWorkspace(None, env_wrapper(env))
    try:
        manifest = read_central_manifest(mpath)
    except ManifestError as exc:
        return PriorWorkspace(None, _read_sidecar(mpath, exc))
    return PriorWorkspace(manifest, task_wrapper_of(manifest))


def record_sidecar(mpath: Path, wrapper: Any) -> None:
    """Write *wrapper* beside the manifest; a workspace with no wrapper gets no sidecar."""
    if wrapper is not None:
        write_central_manifest(sidecar_path(mpath), {_SIDECAR_KEY: wrapper})
