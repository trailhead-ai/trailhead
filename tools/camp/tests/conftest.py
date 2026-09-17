"""Shared pytest fixtures for camp's test suite.

Three layered guards keep this suite off the operator's live Claude Code state
(Axiom 6), because camp's launch path pre-seeds workspace trust into
``~/.claude.json`` — a file that carries OAuth secrets:

- ``_sandbox_home`` redirects the ambient ``HOME``, which is what the many
  ``{**os.environ}`` child environments here inherit;
- ``_forbid_real_home`` turns an in-process fall-through to ``Path.home()`` into
  an immediate error rather than a silent write;
- ``_forbid_live_trust_writes`` watches the real file for trust entries a
  subprocess added, which neither of the other two can see.

A fourth guard, ``_sandbox_tmux``, keeps the suite off the operator's live tmux
server, which `camp list` reads to answer its session-state column.
"""
from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _forbid_real_home(request, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any fall-through to the developer's real home a loud failure.

    `Path.home()` is the last resort of every resolver here, and the one this
    suite's other guards cannot cover: `claude_config_file` ignores the
    `TRAILHEAD_CLAUDE_DIR` seam by design, so an `env` of `None` — or one with no
    `HOME` — lands on the operator's real `~/.claude.json`, which carries OAuth
    secrets. Poisoning `Path.home` turns "the test forgot to inject `HOME`" into
    an immediate error instead of a silent write to live state (Axiom 6).

    A test that genuinely means the real home (asserting the fall-back path, or
    exercising home-path redaction) declares `@pytest.mark.real_home`.
    """
    if "real_home" in request.keywords:
        return
    real_home = Path.home()

    def _refuse() -> Path:
        raise AssertionError(
            "Path.home() reached in a test: something resolved a path from the "
            f"developer's real home ({real_home}) instead of an injected HOME. "
            "Pass env={'HOME': str(tmp_path)} (or CLAUDE_CONFIG_DIR under tmp_path) "
            "so the write lands in the sandbox. If the real home is genuinely the "
            "subject, mark the test @pytest.mark.real_home."
        )

    monkeypatch.setattr(Path, "home", staticmethod(_refuse))


@pytest.fixture(autouse=True)
def _sandbox_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the ambient `HOME` at a throwaway dir for the whole camp suite.

    camp's tests routinely hand a child process `{**os.environ}`, and camp's
    launch-time trust pre-seed resolves its target from `HOME`. Inheriting the real
    one means a `camp new` under test merges a trust entry into the developer's own
    `~/.claude.json`. Redirecting `HOME` here fixes that for every call site at
    once, including the subprocesses the in-process guards cannot see (Axiom 6).
    """
    home = tmp_path / "sandbox-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("USERPROFILE", raising=False)


#: Claude Code's real global config file, resolved ONCE at import time — before any
#: fixture redirects `HOME` — so the watchdog below always watches the operator's
#: own file rather than whatever a test redirected to.
_LIVE_CLAUDE_JSON = Path(os.path.expanduser("~")) / ".claude.json"


def _fingerprint(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _live_project_keys() -> frozenset[str]:
    try:
        data = json.loads(_LIVE_CLAUDE_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict):
        return frozenset()
    return frozenset(projects)


@pytest.fixture(autouse=True)
def _forbid_live_trust_writes(tmp_path: Path) -> Iterator[None]:
    """Fail any test that pre-seeds trust into the operator's real `~/.claude.json`.

    The in-process guards cannot see a subprocess: a test that hands a child
    `{**os.environ}` carrying the developer's real `HOME` gets a trust key written
    into live, OAuth-secret-bearing state, and nothing in-process notices. This
    catches the write however it happened (Axiom 6).

    Attribution is by this test's own `tmp_path`, which nothing else on the machine
    can produce — so a Claude session updating its own config, or a straggler
    process from some other suite, cannot be mistaken for this test's leak. The
    file is parsed only when its fingerprint moved, so the usual cost is one stat.
    """
    fingerprint_before = _fingerprint(_LIVE_CLAUDE_JSON)
    yield
    if _fingerprint(_LIVE_CLAUDE_JSON) == fingerprint_before:
        return
    leaked = sorted(k for k in _live_project_keys() if k.startswith(str(tmp_path)))
    assert not leaked, (
        f"this test pre-seeded trust into the real {_LIVE_CLAUDE_JSON} — live, "
        f"secret-bearing state — for {leaked}. Something ran with the developer's "
        "HOME instead of a sandbox: give every subprocess environment (and every "
        "`env=` argument) a tmp_path-rooted HOME."
    )


#: A `tmux` stand-in that answers every invocation the way a machine with no
#: tmux server answers: exit 1, with the stderr marker `Tmux.list_sessions`
#: reads as "answered, and the answer is empty". Written as a script rather
#: than a `monkeypatch.setattr` because camp reaches tmux by name through
#: `PATH` (`camp/launch/stop.py`'s `subprocess.run(["tmux", ...])`), so a
#: subprocess-spawning test is only covered at the `PATH` level.
_NO_SERVER_TMUX_STUB = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    'sys.stderr.write("error connecting to /tmp/no-such-tmux '
    '(No such file or directory)\\n")\n'
    "sys.exit(1)\n"
)


@pytest.fixture(autouse=True)
def _sandbox_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite off whatever tmux server the developer is running.

    `camp list` reads tmux to answer its session-state column, which makes any
    test that reaches that path observe the real sessions on the machine
    executing it — so the suite's answer depends on who ran it and what they
    had open, and a listing assertion passes on a quiet laptop and fails on a
    busy one. Prepending a no-server stub to `PATH` makes the ambient answer
    "no sessions" everywhere, deterministically.

    A test that drives tmux state prepends its own stub afterwards, which wins
    on `PATH` order; this fixture only sets the floor.
    """
    bin_dir = tmp_path / "sandbox-tmux-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "tmux"
    stub.write_text(_NO_SERVER_TMUX_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
