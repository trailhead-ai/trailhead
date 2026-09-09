"""`scripts/eval-sandbox` — the kernel boundary an eval arm runs inside.

An eval arm is a full agent with shell access, and the two cases run so far both
escaped their fixture: one mutated the developer's real lore config, the other
published into a real vault and pushed the commit. In each the agent behaved
sensibly — handed a task it could not finish through the fixture, it went and
found the machinery that would work. A stub on `PATH` is a decoy; only the kernel
is a boundary.

This suite runs the wrapper for real against probe commands and asserts on what
the filesystem did, because that is the only thing an escaped run cannot lie
about. Every probe is a plain shell command: the properties are enforced by the
seatbelt profile, so they hold identically whether the confined process is `sh`
or a dispatched agent, and pinning them this way costs no model calls.

The read side is default-deny over `$HOME` with a narrow allow-list, so
`test_an_unlisted_home_path_is_unreadable` is the load-bearing one: it is what
tells a boundary apart from a blocklist of the paths that happened to leak last
time.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SANDBOX = _REPO_ROOT / "scripts" / "eval-sandbox"
_HOME = Path.home()

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="eval-sandbox is a macOS seatbelt wrapper",
)


def run(run_dir: Path, *argv: str) -> subprocess.CompletedProcess:
    """Run `argv` inside the sandbox rooted at `run_dir`."""
    return subprocess.run(
        [str(_SANDBOX), str(run_dir), "--", *argv],
        capture_output=True,
        text=True,
    )


def sh(run_dir: Path, script: str) -> subprocess.CompletedProcess:
    return run(run_dir, "/bin/sh", "-c", script)


@pytest.fixture
def jail(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    return d


# ---- the wrapper is a wrapper -----------------------------------------------


def test_the_confined_command_actually_runs(jail: Path):
    """Guards every assertion below: a wrapper that never executed its argv
    would satisfy each 'was blocked' check vacuously.
    """
    result = sh(jail, "echo ran-inside")
    assert result.returncode == 0, result.stderr
    assert "ran-inside" in result.stdout


def test_the_commands_exit_code_is_forwarded(jail: Path):
    """A grader reads this exit code; swallowing it would score a crashed arm
    as a clean one.
    """
    assert sh(jail, "exit 7").returncode == 7


# ---- the write jail ---------------------------------------------------------


def test_a_write_inside_the_run_directory_succeeds(jail: Path):
    """The fixture has to work, or the case measures resourcefulness instead of
    obedience.
    """
    assert sh(jail, "echo payload > inside.txt").returncode == 0
    assert (jail / "inside.txt").read_text().strip() == "payload"


def test_a_write_outside_the_run_directory_is_blocked(jail: Path, tmp_path: Path):
    target = tmp_path / "escaped.txt"
    sh(jail, f"echo payload > {target}")
    assert not target.exists(), f"the run wrote outside its jail: {target}"


def test_a_grandchild_process_cannot_write_outside_either(jail: Path, tmp_path: Path):
    """Confinement has to be inherited. An agent's shell command is already a
    grandchild of the wrapper, and anything it spawns is deeper still.
    """
    target = tmp_path / "escaped-nested.txt"
    sh(jail, f"/bin/sh -c '/bin/sh -c \"echo payload > {target}\"'")
    assert not target.exists(), f"a nested process wrote outside the jail: {target}"


def test_the_real_home_directory_is_not_writable(jail: Path):
    target = _HOME / ".trailhead-eval-sandbox-escape-probe"
    assert not target.exists(), "stale probe from an earlier run; delete it"
    sh(jail, f"echo payload > {target}")
    assert not target.exists(), f"the run wrote into the real home directory: {target}"


def test_the_harness_session_scratch_root_is_writable(jail: Path):
    """The wrapper's real consumer is a `claude` process, whose Bash tool sets up
    a per-session scratch directory under `/tmp/claude-<uid>` before it will run
    anything at all.

    Confining writes to the jail alone leaves every shell probe below green while
    the arm the harness exists to run cannot execute a single command — its Bash
    tool fails at initialisation with `EPERM ... mkdir`, and the run reports every
    probe as blocked, including the ones that should have succeeded. That is the
    worst shape a harness defect can take: it reads as a clean containment result.
    """
    root = Path(f"/tmp/claude-{os.getuid()}")
    probe = root / f"eval-sandbox-probe-{os.getpid()}"
    result = sh(jail, f"mkdir -p {probe} && echo made-it")
    try:
        assert result.returncode == 0, (
            f"the harness could not create its session scratch directory: {result.stderr}"
        )
        assert probe.is_dir()
    finally:
        probe.rmdir() if probe.is_dir() else None


# ---- the read boundary ------------------------------------------------------


def test_the_installed_plugin_cache_is_unreadable(jail: Path):
    """The `publish-routing` escape started here: the arm read the real
    `publish_site.py` out of the plugin cache and ran it against a live vault.
    """
    result = sh(jail, f"ls {_HOME}/.claude/plugins")
    assert result.returncode != 0, "the plugin cache was readable from inside the jail"


def test_the_real_user_config_is_unreadable(jail: Path):
    """The `bash-write-gate` escape started here: the arm read the real lore
    config to find the vault registration that would make its task succeed.
    """
    result = sh(jail, f"ls {_HOME}/.config")
    assert result.returncode != 0, "the real user config was readable from inside the jail"


def test_an_unlisted_home_path_is_unreadable(jail: Path):
    """Default-deny, not a blocklist.

    Naming the two paths that leaked in the two cases run so far would pass the
    two tests above and leave every other real vault, repo, and credential store
    reachable. This asserts the shape of the rule rather than its worked
    examples: a path nobody has thought to name is unreadable because nothing
    opened it, not because someone remembered to close it.
    """
    result = sh(jail, f"ls {_HOME}")
    assert result.returncode != 0, (
        "the home directory listed from inside the jail — the read rule is a "
        "blocklist, not a boundary"
    )


def test_the_run_directory_is_readable(jail: Path):
    (jail / "fixture.txt").write_text("fixture body\n")
    result = sh(jail, "cat fixture.txt")
    assert result.returncode == 0, result.stderr
    assert "fixture body" in result.stdout


def test_the_wrapper_leaves_nothing_of_its_own_in_the_run_directory(jail: Path):
    """The run directory is what a case is graded from, so the wrapper's own
    working files must not land in it.

    A profile or a temp directory sitting beside the fixture shows up as a
    difference in the tree a grader diffs, and — worse — as plausible output the
    arm might have produced.
    """
    before = {p.name for p in jail.iterdir()}
    assert sh(jail, "echo produced > arm-output.txt").returncode == 0
    after = {p.name for p in jail.iterdir()}
    assert after - before == {"arm-output.txt"}, (
        f"the wrapper left its own files in the run directory: "
        f"{sorted(after - before - {'arm-output.txt'})}"
    )


# ---- fail closed ------------------------------------------------------------


def test_a_missing_run_directory_is_refused(tmp_path: Path):
    """Falling back to an unconfined run is the one failure mode that matters:
    it produces a result that looks identical to a confined one.
    """
    result = run(tmp_path / "does-not-exist", "echo", "should-not-run")
    assert result.returncode != 0
    assert "should-not-run" not in result.stdout


def test_a_run_directory_inside_the_claude_config_is_refused(tmp_path: Path):
    """A run directory under `~/.claude` would be write-allowed as the jail and
    read-allowed as the harness's own state, punching a hole through both rules
    at once.
    """
    result = run(_HOME / ".claude", "echo", "should-not-run")
    assert result.returncode != 0
    assert "should-not-run" not in result.stdout


def test_no_command_means_no_run(jail: Path):
    result = subprocess.run(
        [str(_SANDBOX), str(jail)], capture_output=True, text=True
    )
    assert result.returncode != 0
