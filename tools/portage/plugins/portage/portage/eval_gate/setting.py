"""The group-level switch for the eval gate: the camp group TOML's ``[release].eval_gate``.

The gate runs for a group only when the setting is the TOML boolean ``true``. An absent key, any
other value, a missing or unreadable file and invalid TOML all read as off.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def gate_enabled(toml_path: str | None) -> bool:
    if not toml_path:
        return False
    try:
        raw = tomllib.loads(Path(toml_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    release = raw.get("release")
    if not isinstance(release, dict):
        return False
    return release.get("eval_gate") is True
