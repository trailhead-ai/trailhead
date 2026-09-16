"""Test contract: the meta routes refuse what they do not accept.

``version``, ``which``, ``help`` and ``session-bootstrap`` are the four routes
that answer without resolving a group. Each takes no options and no arguments,
and each used to accept any token and discard it silently — so
``camp version --json`` printed the human version string and exited 0, naming a
machine-readable form that does not exist.

That is the same defect the argparse migration fixed everywhere else, and these
four were the remainder: the migration's "unknown flags reject everywhere"
decision was not true end to end while they still swallowed them.

What is pinned here is the refusal, in camp's two kinds:

- a surplus token spelled like a flag is an ``unknown flag``;
- a surplus token spelled like a word is an ``unexpected argument``.

The refusal names the CANONICAL verb even when the operator typed the flag
spelling — ``camp --version --json`` refuses as ``camp version:``, because
``--version`` and ``version`` are one route and its messages should not vary by
which spelling reached it.

`camp session-bootstrap` is exercised for its refusal only. Its success path
reconciles the real worktree it is run in, so running it here would write
outside the test's own tmp tree; the hook tests cover that path with the
filesystem injected.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env={**os.environ},
        cwd=str(cwd) if cwd else None,
    )


# ---------------------------------------------------------------------------
# A flag-shaped surplus token is an "unknown flag".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "verb"),
    [
        (["version", "--json"], "version"),
        (["--version", "--json"], "version"),
        (["which", "--json"], "which"),
        (["--which", "--json"], "which"),
        (["help", "--json"], "help"),
        (["--help", "--json"], "help"),
        (["-h", "--json"], "help"),
        (["session-bootstrap", "--json"], "session-bootstrap"),
    ],
)
def test_a_meta_route_refuses_a_flag_it_does_not_accept(
    argv: list[str], verb: str, tmp_path: Path
) -> None:
    result = _run(argv, cwd=tmp_path)
    assert result.returncode == 1, (
        f"camp {' '.join(argv)} should refuse.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stderr == f"camp {verb}: unknown flag '--json'\n"


# ---------------------------------------------------------------------------
# A word-shaped surplus token is an "unexpected argument".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "verb", "token"),
    [
        (["version", "extra"], "version", "extra"),
        (["--version", "extra"], "version", "extra"),
        (["which", "camp"], "which", "camp"),
        (["--which", "camp"], "which", "camp"),
        (["help", "status"], "help", "status"),
        (["--help", "status"], "help", "status"),
        (["session-bootstrap", "now"], "session-bootstrap", "now"),
    ],
)
def test_a_meta_route_refuses_an_argument_it_has_no_slot_for(
    argv: list[str], verb: str, token: str, tmp_path: Path
) -> None:
    result = _run(argv, cwd=tmp_path)
    assert result.returncode == 1, (
        f"camp {' '.join(argv)} should refuse.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stderr == f"camp {verb}: unexpected argument {token!r}\n"


# ---------------------------------------------------------------------------
# The first surplus token is the one named, so the operator fixes that one.
# ---------------------------------------------------------------------------


def test_the_first_surplus_token_is_the_one_reported(tmp_path: Path) -> None:
    result = _run(["version", "--alpha", "beta"], cwd=tmp_path)
    assert result.stderr == "camp version: unknown flag '--alpha'\n"

    result = _run(["version", "beta", "--alpha"], cwd=tmp_path)
    assert result.stderr == "camp version: unexpected argument 'beta'\n"


# ---------------------------------------------------------------------------
# The bare route still answers. A refusal that also broke the success path
# would pass every test above.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["version"], ["--version"], ["which"], ["--which"]])
def test_a_bare_meta_route_still_answers(argv: list[str], tmp_path: Path) -> None:
    result = _run(argv, cwd=tmp_path)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip(), "the route printed nothing"


@pytest.mark.parametrize("argv", [["help"], ["--help"], ["-h"]])
def test_bare_help_still_prints_the_menu(argv: list[str], tmp_path: Path) -> None:
    result = _run(argv, cwd=tmp_path)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "camp — group worktree orchestration" in result.stdout
