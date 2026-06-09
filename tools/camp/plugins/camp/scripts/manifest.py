"""Central manifest read/write/remove for camp.

The central manifest lives at:
    central_state_dir(group)/worktrees/<slug>/manifest.json

It is written atomically (temp file + os.replace) with mode 0o600.
A malformed or truncated manifest raises ManifestError, not a raw traceback.

Schema (v1):
    {
        "schema_version": 1,
        "group": "<group-name>",
        "slug": "<worktree-slug>",
        "branch": "worktree-<slug>",
        "members": [
            {
                "name": "<repo-name>",
                "repo_root": "/absolute/path/to/canonical/repo",
                "worktree_path": "/absolute/path/to/worktree",
            },
            ...
        ]
    }
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class ManifestError(Exception):
    """Raised when a central manifest file is malformed or cannot be read.

    The message always includes the file path and the failing reason.
    """


def write_central_manifest(path: Path, data: dict[str, Any]) -> None:
    """Write data to path atomically with mode 0o600.

    Uses a temp file in the same directory + os.replace for atomicity.
    Sets file mode to 0o600 after the write (umask-proof).

    Args:
        path:  Absolute path for the manifest file (parent must exist).
        data:  Dict to serialize as JSON.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".manifest-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, str(path))
        os.chmod(str(path), 0o600)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_central_manifest(path: Path) -> dict[str, Any]:
    """Read and parse the central manifest at path.

    Args:
        path:  Absolute path to the manifest file.

    Returns:
        Parsed dict.

    Raises:
        ManifestError: If the file is missing, unreadable, or contains malformed JSON.
    """
    try:
        text = path.read_text()
    except OSError as e:
        raise ManifestError(
            f"camp: cannot read manifest at {path}: {e}"
        ) from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestError(
            f"camp: malformed manifest at {path}: {e}"
        ) from e

    if not isinstance(data, dict):
        raise ManifestError(
            f"camp: manifest at {path} is not a JSON object (got {type(data).__name__})"
        )

    return data


def remove_central_manifest(path: Path) -> None:
    """Remove the central manifest file if it exists.

    Silently succeeds if the file is already gone.

    Args:
        path:  Absolute path to the manifest file.
    """
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def manifest_path_for(group: str, slug: str, *, env: dict[str, str] | None = None) -> Path:
    """Return the canonical central manifest path for (group, slug).

    Args:
        group:  Group name (validated by central_state_dir).
        slug:   Worktree slug.
        env:    Optional env override for the resolver (hermetic tests).

    Returns:
        Absolute path to manifest.json (directory may not exist yet).
    """
    from group_resolve import central_state_dir
    state_dir = central_state_dir(group, env=env)
    return state_dir / "worktrees" / slug / "manifest.json"
