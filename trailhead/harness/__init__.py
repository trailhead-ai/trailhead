"""Harness registry/factory for trailhead.

Maps canonical harness names (and aliases) to :class:`~trailhead.harness.base.Harness`
implementations, and provides name-resolution + machine-detection helpers used by
``trailhead install`` / ``trailhead uninstall``.

To add a harness: implement a :class:`Harness` subclass (see
:mod:`trailhead.harness.base`) and register it in ``_HARNESSES`` below.
"""

from __future__ import annotations

import os

from trailhead.harness.base import Harness, HarnessError, SessionTranscript
from trailhead.harness.claude_code import ClaudeCodeHarness, claude_config_file

__all__ = [
    "Harness",
    "HarnessError",
    "SessionTranscript",
    "ClaudeCodeHarness",
    "claude_config_file",
    "canonical_name",
    "get_harness",
    "detect_harnesses",
    "known_harness_names",
]

# Canonical name → implementation class.
_HARNESSES: dict[str, type[Harness]] = {
    ClaudeCodeHarness.name: ClaudeCodeHarness,
}

# User-friendly aliases → canonical name.
_ALIASES: dict[str, str] = {
    "claude": "claude_code",
    "claude-code": "claude_code",
}


def canonical_name(name: str) -> str:
    """Map an alias to its canonical harness name (identity if not an alias)."""
    return _ALIASES.get(name, name)


def known_harness_names() -> list[str]:
    """Return the sorted list of canonical harness names."""
    return sorted(_HARNESSES)


def get_harness(name: str) -> Harness:
    """Return a Harness instance for ``name`` (alias-resolved).

    Raises:
        HarnessError: if the name is not a known harness.
    """
    cname = canonical_name(name)
    cls = _HARNESSES.get(cname)
    if cls is None:
        raise HarnessError(f"unknown harness {name!r}; known harnesses: {known_harness_names()}")
    return cls()


def detect_harnesses(env: dict[str, str] | None = None) -> list[Harness]:
    """Return Harness instances whose config is present on this machine."""
    _env = env if env is not None else dict(os.environ)
    return [cls() for cls in _HARNESSES.values() if cls.detect(_env)]
