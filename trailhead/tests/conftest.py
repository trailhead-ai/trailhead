"""Shared pytest fixtures/helpers for trailhead's CLI test suite."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _redirect_claude_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point this suite's ambient environment at a throwaway Claude dir.

    Scope is `trailhead/tests/` only — this conftest does not reach the tool
    suites under `tools/`, which pin their own locations per test.

    Code under test falls back to `os.environ` whenever `env` is None, and any
    env dict built from `os.environ` inherits a developer's real
    `CLAUDE_CONFIG_DIR`/`HOME`. Pinning `TRAILHEAD_CLAUDE_DIR` — the override
    that wins over both — makes it impossible for a test to write into the live
    `~/.claude` (Axiom 6). Tests that need their own location still pass an
    explicit `env`, which takes precedence over this.
    """
    monkeypatch.setenv("TRAILHEAD_CLAUDE_DIR", str(tmp_path / "ambient-claude"))


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


@pytest.fixture()
def claude_dir(tmp_path: Path) -> Path:
    """The Claude config dir this suite's ambient environment resolves to.

    Registration and per-tool install markers are per config dir, so a test that
    lets the harness resolve its own env finds them here — the same location
    `_redirect_claude_dir` pins `TRAILHEAD_CLAUDE_DIR` to.
    """
    d = tmp_path / "ambient-claude"
    d.mkdir(parents=True, exist_ok=True)
    return d
