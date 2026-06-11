#!/usr/bin/env python3
"""Forge-owned prs.json sidecar read/write helper (D-1, B-2).

The sidecar lives alongside the camp central manifest:
    <manifest_dir>/prs.json

Shape:
    {
        "schema_version": 1,
        "prs": [
            {"repo": str, "pr_number": str, "url": str, "branch": str},
            ...
        ],
        "external_tracker": null   # reserved; no connector built
    }

Write contract (B-2):
    - Atomic: temp file in parent dir + os.replace.
    - Mode 0o600 (replicates camp manifest.py:41-68 posture).
    - Raises SidecarError on any failure — never a raw exception.

Read contract (B-2):
    - Raises SidecarError on missing, malformed, or schema-invalid file.
    - Never propagates a raw KeyError, JSONDecodeError, or OSError.
    - Path always appears in the error message.

This module is stdlib-only (no camp import, no trailhead.paths — B-1 self-containment).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class SidecarError(Exception):
    """Raised on a missing, malformed, or schema-invalid prs.json sidecar.

    The message always contains the file path.
    """


def write(path: Path | str, prs: list[dict[str, str]]) -> None:
    """Write prs[] to the sidecar atomically with mode 0o600.

    Creates the parent directory if it does not exist.

    Args:
        path:  Absolute path to the sidecar file (e.g. <manifest_dir>/prs.json).
        prs:   List of PR dicts with keys {repo, pr_number, url, branch}.

    Raises:
        SidecarError: On any I/O or serialisation failure.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "schema_version": 1,
        "prs": prs,
        "external_tracker": None,
    }

    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".prs-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, str(p))
        os.chmod(str(p), 0o600)
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise SidecarError(f"release_prs_sidecar: write failed at {p}: {e}") from e


def read(path: Path | str) -> dict[str, Any]:
    """Read and validate the prs.json sidecar.

    Args:
        path:  Absolute path to the sidecar file.

    Returns:
        Parsed dict with schema_version, prs[], and external_tracker keys.

    Raises:
        SidecarError: On missing, malformed, or schema-invalid file.
    """
    p = Path(path)

    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise SidecarError(
            f"release_prs_sidecar: cannot read sidecar at {p}: {e}"
        ) from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SidecarError(
            f"release_prs_sidecar: malformed JSON in sidecar at {p}: {e}"
        ) from e

    if not isinstance(data, dict):
        raise SidecarError(
            f"release_prs_sidecar: sidecar at {p} is not a JSON object"
        )

    if "prs" not in data:
        raise SidecarError(
            f"release_prs_sidecar: sidecar at {p} is missing required 'prs' field"
        )

    return data
