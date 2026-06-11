"""Shared camp central manifest reader (stdlib-only, B-1 self-contained).

Extracted from detect_repos.py and merge_prs.py to avoid the dual-class problem:
two scripts that each define their own ManifestReadError mean a caller catching
one exception type silently misses the other.

Both scripts import from here:
    from manifest_read import ManifestReadError, load_manifest
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ManifestReadError(Exception):
    """Raised on a missing or malformed manifest (path always in the message)."""


def load_manifest(manifest_path: str) -> dict[str, Any]:
    """Load and parse a camp central manifest using stdlib json.

    Args:
        manifest_path: Absolute path to the manifest.json file.

    Returns:
        Parsed manifest dict.

    Raises:
        ManifestReadError: On a missing, unreadable, or malformed file.
    """
    p = Path(manifest_path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestReadError(
            f"cannot read manifest at {manifest_path}: {e}"
        ) from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestReadError(
            f"malformed manifest at {manifest_path}: {e}"
        ) from e
    if not isinstance(data, dict):
        raise ManifestReadError(
            f"manifest at {manifest_path} is not a JSON object"
        )
    return data
