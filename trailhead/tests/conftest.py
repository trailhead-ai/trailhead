"""Shared pytest fixtures/helpers for trailhead's CLI test suite."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def composed_root(tmp_path: Path) -> Path:
    """A fresh, already-created `<tmp_path>/composed` dir — the harness compose dest."""
    root = tmp_path / "composed"
    root.mkdir(parents=True)
    return root


def capturing_runner():
    """Return `(runner, calls_seen)`: a stub runner recording every argv it's called with.

    `calls_seen` accumulates a copy of each `args` list passed to `runner`, so a
    test can inspect what invocation(s) a harness method made without shelling
    out to a real CLI.
    """
    calls_seen: list[list[str]] = []

    def runner(args, **kwargs):
        calls_seen.append(list(args))

    return runner, calls_seen
