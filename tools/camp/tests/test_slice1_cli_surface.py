"""Slice 1 test contract: command skeleton — renames, disable-stubs, help.

TDD tests written before implementation.

Contract:
- camp group/ai/rm/cd/enter/setup each dispatch to their handler (RESERVED updated).
- bare slug (camp foo) → exit non-zero, message names 'camp ai foo'.
- restock/sweep/code/fire → disabled message + non-zero exit; NOT in camp help.
- removed verbs init/open/break → legible error pointing at new verb.
- camp help: golden-structure assert (new verbs, none disabled) — per lesson
  [[2026-06-04-port-parity-golden-structure-not-substring]].
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
# camp help: golden-structure assert
# The lesson [[2026-06-04-port-parity-golden-structure-not-substring]] says:
# assert the STRUCTURE (all active sections, none of the disabled ones),
# not just a substring.
# ---------------------------------------------------------------------------

_EXPECTED_ACTIVE_VERBS = ("group", "ai", "rm", "cd", "enter", "setup")
_EXPECTED_ACTIVE_SECTIONS = ("Setup", "Workspace")
_DISABLED_VERBS = ("restock", "sweep", "code", "fire")
_LEGACY_VERBS = ("init", "open", "break")


def test_camp_help_contains_all_active_verbs() -> None:
    result = _run(["help"])
    assert result.returncode == 0, f"help exited {result.returncode}\n{result.stderr}"
    output = result.stdout
    for verb in _EXPECTED_ACTIVE_VERBS:
        assert verb in output, (
            f"Expected active verb {verb!r} in camp help output.\n"
            f"Output:\n{output}"
        )


def test_camp_help_does_not_contain_disabled_verbs() -> None:
    result = _run(["help"])
    assert result.returncode == 0
    output = result.stdout
    for verb in _DISABLED_VERBS:
        assert verb not in output, (
            f"Disabled verb {verb!r} must NOT appear in camp help output.\n"
            f"Output:\n{output}"
        )


def test_camp_help_does_not_contain_legacy_verbs() -> None:
    """init/open/break are renamed; they must not appear in help as commands."""
    result = _run(["help"])
    assert result.returncode == 0
    output = result.stdout
    for verb in _LEGACY_VERBS:
        # The verb may appear as part of the "use camp <new>" pointer, but it
        # must NOT appear as a standalone command listing (e.g., "  camp init").
        assert f"  camp {verb}" not in output, (
            f"Legacy verb {verb!r} must not appear as a command in help.\n"
            f"Output:\n{output}"
        )


def test_camp_help_golden_structure() -> None:
    """Full golden-structure check: new verbs present, disabled/legacy absent."""
    result = _run(["help"])
    assert result.returncode == 0
    output = result.stdout

    # Must contain active verb surface
    for verb in _EXPECTED_ACTIVE_VERBS:
        assert verb in output, f"Missing active verb: {verb!r}"

    # Must NOT contain disabled verbs
    for verb in _DISABLED_VERBS:
        assert verb not in output, f"Disabled verb in help: {verb!r}"

    # Must reference camp as the tool (header / title)
    assert "camp" in output.lower()

    # Must have usage section
    assert "usage" in output.lower() or "Usage" in output


# ---------------------------------------------------------------------------
# camp group → dispatches (stub behavior for Slice 1)
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
# camp ai → dispatches (stub behavior for Slice 1)
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


def test_camp_ai_is_in_reserved() -> None:
    """'ai' must be in RESERVED so bare-slug dispatch doesn't consume it."""
    scripts_dir = _PLUGIN_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from spine import RESERVED
    assert "ai" in RESERVED, f"'ai' must be in RESERVED, got: {RESERVED}"


def test_camp_rm_is_in_reserved() -> None:
    """'rm' must be in RESERVED."""
    scripts_dir = _PLUGIN_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from spine import RESERVED
    assert "rm" in RESERVED, f"'rm' must be in RESERVED, got: {RESERVED}"


def test_camp_group_is_in_reserved() -> None:
    """'group' must be in RESERVED."""
    scripts_dir = _PLUGIN_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from spine import RESERVED
    assert "group" in RESERVED, f"'group' must be in RESERVED, got: {RESERVED}"


def test_camp_cd_is_in_reserved() -> None:
    """'cd' must be in RESERVED."""
    scripts_dir = _PLUGIN_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from spine import RESERVED
    assert "cd" in RESERVED, f"'cd' must be in RESERVED, got: {RESERVED}"


def test_camp_enter_is_in_reserved() -> None:
    """'enter' must be in RESERVED."""
    scripts_dir = _PLUGIN_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from spine import RESERVED
    assert "enter" in RESERVED, f"'enter' must be in RESERVED, got: {RESERVED}"


def test_camp_setup_is_in_reserved() -> None:
    """'setup' must be in RESERVED."""
    scripts_dir = _PLUGIN_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from spine import RESERVED
    assert "setup" in RESERVED, f"'setup' must be in RESERVED, got: {RESERVED}"


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
        f"bare slug should exit non-zero.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_bare_slug_names_camp_ai() -> None:
    """camp foo → stderr names 'camp ai foo' as the correct command."""
    result = _run(["my-feature-slug"])
    combined = result.stdout + result.stderr
    assert "camp ai" in combined, (
        f"bare slug error must name 'camp ai'.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
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
        f"camp open should redirect to 'camp ai'.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
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
# camp cd/enter/setup → stub "not yet implemented in this slice" + non-zero
# ---------------------------------------------------------------------------


# NOTE: 'setup' graduated from a stub to real behavior in Slice 3 (and 'ai' too);
# only 'cd' (Slice 7) and 'enter' (Slice 5) remain stubs here.
@pytest.mark.parametrize("verb", ["cd", "enter"])
def test_stub_verb_exits_nonzero(verb: str) -> None:
    result = _run([verb, "dummy"])
    assert result.returncode != 0, (
        f"Stub verb {verb!r} must exit non-zero (not yet implemented).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.parametrize("verb", ["cd", "enter"])
def test_stub_verb_prints_not_implemented_message(verb: str) -> None:
    result = _run([verb, "dummy"])
    combined = result.stdout + result.stderr
    assert "not yet" in combined.lower() or "slice" in combined.lower() or "implement" in combined.lower(), (
        f"Stub verb {verb!r} must print 'not yet implemented' message.\n"
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
def test_group_path_disabled_verb_exits_nonzero(
    verb: str, stub_group_env: dict[str, str]
) -> None:
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


# camp ai via group-aware path → real (Slice 3): seeds pending + spawns provisioner.
# CAMP_NO_LAUNCH suppresses the placeholder claude exec (the real launch is Slice 6).


def test_group_path_ai_seeds_and_exits_zero(
    stub_group_env: dict[str, str], tmp_path: Path
) -> None:
    """camp ai <slug> via group path seeds the workspace and exits 0 (no claude)."""
    env = {**stub_group_env, "CAMP_STATE_DIR": str(tmp_path / "state"),
           "CAMP_NO_LAUNCH": "1"}
    result = _run_group(["ai", "my-slug"], group_env=env)
    assert result.returncode == 0, (
        f"camp ai via group path should seed + exit 0 with CAMP_NO_LAUNCH.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_group_path_ai_announces_background_provisioning(
    stub_group_env: dict[str, str], tmp_path: Path
) -> None:
    """camp ai reports that provisioning runs in the background."""
    env = {**stub_group_env, "CAMP_STATE_DIR": str(tmp_path / "state"),
           "CAMP_NO_LAUNCH": "1"}
    result = _run_group(["ai", "my-slug"], group_env=env)
    combined = (result.stdout + result.stderr).lower()
    assert "background" in combined or "camp status" in combined, (
        f"camp ai must announce background provisioning.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# camp rm via group-aware path → stub (not real reconcile_break)


def test_group_path_rm_is_stubbed(stub_group_env: dict[str, str]) -> None:
    """camp rm via group-aware path exits non-zero (Slice 1 stub)."""
    result = _run_group(["rm", "--name", "my-slug"], group_env=stub_group_env)
    assert result.returncode != 0, (
        f"camp rm via group-aware path must exit non-zero (stub in Slice 1).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_group_path_rm_stub_mentions_slice2(stub_group_env: dict[str, str]) -> None:
    """camp rm stub message mentions Slice 2."""
    result = _run_group(["rm", "--name", "my-slug"], group_env=stub_group_env)
    combined = result.stdout + result.stderr
    assert "slice 2" in combined.lower() or "not yet" in combined.lower(), (
        f"camp rm stub must mention Slice 2 or 'not yet implemented'.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
