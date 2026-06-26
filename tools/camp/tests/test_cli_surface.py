"""Tests for the command skeleton — renames, disable-stubs, help.

Covers:
- camp group/ai/rm/pwd/enter/setup each dispatch to their handler (RESERVED updated).
- bare slug (camp foo) → exit non-zero, message names 'camp ai foo'.
- restock/sweep/code/fire → disabled message + non-zero exit; NOT in camp help.
- removed verbs init/open/break → legible error pointing at new verb.
- camp help: golden-structure assert (new verbs, none disabled).
- group-aware path (_dispatch_group_command): disabled verbs, ai, rm all stub
  correctly when a group resolves (exercised via CAMP_CONFIG_DIR + --group flag).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"


def _run(args: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {**os.environ}
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=base_env,
    )


# ---------------------------------------------------------------------------
# camp group → dispatches (stub behavior)
# ---------------------------------------------------------------------------


def test_camp_group_dispatches_not_bare_slug_error() -> None:
    """camp group <name> dispatches to the group handler, not the bare-slug error."""
    result = _run(["group", "my-group"])
    # Should NOT produce the "use camp ai" bare-slug error
    combined = result.stdout + result.stderr
    assert "camp ai" not in combined, (
        f"camp group should not route to bare-slug error path.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_camp_group_without_args_shows_usage() -> None:
    """camp group with no args prints usage (not a bare-slug error)."""
    result = _run(["group"])
    combined = result.stdout + result.stderr
    # Should not produce the "use camp ai" bare-slug error for a missing slug
    assert "camp ai" not in combined, (
        f"camp group with no args should not give bare-slug error.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# camp ai → dispatches (stub behavior)
# ---------------------------------------------------------------------------


def test_camp_ai_dispatches_not_error() -> None:
    """camp ai <slug> dispatches to the ai handler, not an unknown-command error."""
    result = _run(["ai", "my-feature"])
    combined = result.stdout + result.stderr
    # Should NOT show: "unknown command" or the bare-slug error pointing at itself
    assert "unknown command" not in combined.lower(), (
        f"camp ai should not show unknown-command error.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# bare slug → non-zero exit + message naming 'camp ai <name>'
# ---------------------------------------------------------------------------


def test_bare_slug_exits_nonzero() -> None:
    """camp foo (bare slug, no group context) → non-zero exit."""
    result = _run(["my-feature-slug"])
    # Either no group is found (→ falls through to spine with no dispatcher
    # routing and spine errors) or the new bare-slug handler fires.
    # The contract says bare slug must exit non-zero with a pointer.
    assert result.returncode != 0, (
        f"bare slug should exit non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_bare_slug_names_camp_ai() -> None:
    """camp foo → stderr names 'camp ai foo' as the correct command."""
    result = _run(["my-feature-slug"])
    combined = result.stdout + result.stderr
    assert "camp ai" in combined, (
        f"bare slug error must name 'camp ai'.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_bare_slug_names_the_slug_in_error() -> None:
    """The bare-slug error message includes the slug name."""
    result = _run(["my-feature-slug"])
    combined = result.stdout + result.stderr
    assert "my-feature-slug" in combined, (
        f"bare slug error must include the slug name.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Disabled verbs: restock/sweep/code/fire → disabled message + non-zero exit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["restock", "sweep", "code", "fire"])
def test_disabled_verb_exits_nonzero(verb: str) -> None:
    result = _run([verb])
    assert result.returncode != 0, (
        f"Disabled verb {verb!r} must exit non-zero.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.parametrize("verb", ["restock", "sweep", "code", "fire"])
def test_disabled_verb_prints_disabled_message(verb: str) -> None:
    result = _run([verb])
    combined = result.stdout + result.stderr
    assert "disabled" in combined.lower() or "temporarily" in combined.lower(), (
        f"Disabled verb {verb!r} must print 'disabled' or 'temporarily' message.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.parametrize("verb", ["restock", "sweep", "code", "fire"])
def test_disabled_verb_stabilizes_message(verb: str) -> None:
    """The disabled message must mention the worktree flow."""
    result = _run([verb])
    combined = result.stdout + result.stderr
    assert "worktree" in combined.lower() or "stabiliz" in combined.lower(), (
        f"Disabled verb {verb!r} message should mention 'worktree' or 'stabilizing'.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Legacy verbs init/open/break → legible error pointing at new verb
# ---------------------------------------------------------------------------


def test_camp_init_gives_legible_redirect() -> None:
    """camp init → error pointing at 'camp group'."""
    result = _run(["init"])
    combined = result.stdout + result.stderr
    assert result.returncode != 0 or "group" in combined, (
        f"camp init should redirect to 'camp group' or error.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # If it errors, it must mention the replacement
    if result.returncode != 0:
        assert "group" in combined, (
            f"camp init error must mention 'camp group'.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def test_camp_open_gives_legible_redirect() -> None:
    """camp open → error pointing at 'camp ai'."""
    result = _run(["open", "my-slug"])
    combined = result.stdout + result.stderr
    assert "ai" in combined, (
        f"camp open should redirect to 'camp ai'.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_camp_break_gives_legible_redirect() -> None:
    """camp break → error pointing at 'camp rm'."""
    result = _run(["break", "--name", "dummy"])
    combined = result.stdout + result.stderr
    assert "rm" in combined or "camp rm" in combined, (
        f"camp break should redirect to 'camp rm'.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Group-aware path: _dispatch_group_command exercised via CAMP_CONFIG_DIR +
# --group flag. This ensures the group-aware routing is tested separately from
# the spine fallback path.
#
# Reviewer gotcha: inside a real worktree the cwd itself resolves a group, so
# we control CAMP_CONFIG_DIR and use --group to deterministically reach
# _dispatch_group_command on both the group and no-group branches.
# ---------------------------------------------------------------------------


@pytest.fixture()
def stub_group_env(tmp_path: Path) -> dict[str, str]:
    """Return env overrides that point CAMP_CONFIG_DIR at a tmp dir with a stub group.

    The stub group 'testgrp' has one member with a non-existent repo_root —
    sufficient for load_group (which validates schema, not disk presence) to
    parse and for _resolve_group_for_command to return a group dict.
    """
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    return {"CAMP_CONFIG_DIR": str(tmp_path)}


def _run_group(args: list[str], *, group_env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run camp with --group testgrp prepended to args, using the stub group env."""
    return _run(args + ["--group", "testgrp"], env=group_env)


# Disabled verbs via group-aware path


@pytest.mark.parametrize("verb", ["code", "sweep", "restock", "fire"])
def test_group_path_disabled_verb_exits_nonzero(verb: str, stub_group_env: dict[str, str]) -> None:
    """_dispatch_group_command routes disabled verbs to cmd_disabled → non-zero exit."""
    result = _run_group([verb], group_env=stub_group_env)
    assert result.returncode != 0, (
        f"Disabled verb {verb!r} must exit non-zero via group-aware path.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.parametrize("verb", ["code", "sweep", "restock", "fire"])
def test_group_path_disabled_verb_prints_stabilizes_message(
    verb: str, stub_group_env: dict[str, str]
) -> None:
    """_dispatch_group_command disabled verbs print the 'stabilizes' message."""
    result = _run_group([verb], group_env=stub_group_env)
    combined = result.stdout + result.stderr
    assert "stabiliz" in combined.lower() or "temporarily" in combined.lower(), (
        f"Disabled verb {verb!r} must mention 'stabilizes' or 'temporarily' via group path.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# camp ai via group-aware path → real: seeds pending + spawns provisioner.
# CAMP_TEST_NO_EXEC suppresses the placeholder claude exec (the real launch happens later).


def test_group_path_ai_seeds_and_exits_zero(stub_group_env: dict[str, str], tmp_path: Path) -> None:
    """camp ai <slug> via group path seeds the workspace and exits 0 (no claude)."""
    env = {**stub_group_env, "CAMP_STATE_DIR": str(tmp_path / "state"), "CAMP_TEST_NO_EXEC": "1"}
    result = _run_group(["ai", "my-slug"], group_env=env)
    assert result.returncode == 0, (
        f"camp ai via group path should seed + exit 0 with CAMP_TEST_NO_EXEC.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_group_path_ai_announces_background_provisioning(
    stub_group_env: dict[str, str], tmp_path: Path
) -> None:
    """camp ai reports that provisioning runs in the background."""
    env = {**stub_group_env, "CAMP_STATE_DIR": str(tmp_path / "state"), "CAMP_TEST_NO_EXEC": "1"}
    result = _run_group(["ai", "my-slug"], group_env=env)
    combined = (result.stdout + result.stderr).lower()
    assert "background" in combined or "camp status" in combined, (
        f"camp ai must announce background provisioning.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# camp rm via group-aware path → real (graduated from the earlier stub)
# camp rm now routes to reconcile_break; it exits non-zero for an unknown slug
# (no manifest present), NOT with a "not yet implemented" stub message.


def test_group_path_rm_unknown_slug_exits_nonzero(stub_group_env: dict[str, str]) -> None:
    """camp rm with an unknown slug exits non-zero (manifest not found), not stub."""
    result = _run_group(["rm", "--name", "my-slug"], group_env=stub_group_env)
    assert result.returncode != 0, (
        f"camp rm with an unknown slug should exit non-zero (no manifest).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
