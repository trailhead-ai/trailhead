"""Test contract: command skeleton — renames, disable-stubs, aliases.

TDD tests written before implementation.

Contract (post-rename surface):
- camp group/new/remove/activate/pwd/setup each dispatch to their handler.
- rm is an alias for remove; ls is an alias for list (alias parity).
- bare slug (camp foo) → exit non-zero, message names 'camp new foo'.
- restock/sweep/code/fire → disabled message + non-zero exit; NOT in camp help.
- removed verbs: camp ai → names 'camp new'; camp cd → errors; camp enter →
  names 'camp activate'.
- legacy redirects: init→group, open→new (direct), break→remove (direct).
- group-aware path (_dispatch_group_command): disabled verbs, new, remove all
  dispatch correctly when a group resolves (CAMP_CONFIG_DIR + --group flag).
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
    combined = result.stdout + result.stderr
    assert "camp new" not in combined, (
        f"camp group should not route to bare-slug error path.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_camp_group_without_args_shows_usage() -> None:
    """camp group with no args prints usage (not a bare-slug error)."""
    result = _run(["group"])
    combined = result.stdout + result.stderr
    assert "camp new" not in combined, (
        f"camp group with no args should not give bare-slug error.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# camp new → dispatches (stub behavior)
# ---------------------------------------------------------------------------


def test_camp_new_dispatches_not_error(tmp_path: Path) -> None:
    """camp new <slug> dispatches to the new handler, not an unknown-command error.

    CAMP_CONFIG_DIR must point at an empty dir: this test runs from inside a real
    trailhead camp worktree, so without isolation cwd resolves the REAL trailhead
    group and this subprocess creates an actual 'my-feature' workspace on disk.
    Pointing at an empty config dir means no group resolves, so the command falls
    through to the spine's cmd_needs_group("new") — still exercising the "not an
    unknown-command/bare-slug error" contract this test is about, with no group
    handler ever invoked.
    """
    result = _run(["new", "my-feature"], env={"CAMP_CONFIG_DIR": str(tmp_path)})
    combined = result.stdout + result.stderr
    assert "unknown command" not in combined.lower(), (
        f"camp new should not show unknown-command error.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "bare slug dispatch is no longer supported" not in combined, (
        f"camp new must not fall through to the bare-slug error.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# camp activate → dispatches (rename of enter; pure rename)
# ---------------------------------------------------------------------------


def test_camp_activate_dispatches_not_bare_slug_error() -> None:
    """camp activate dispatches to the activate handler, not the bare-slug error."""
    result = _run(["activate"])
    combined = result.stdout + result.stderr
    assert "bare slug dispatch is no longer supported" not in combined, (
        f"camp activate must dispatch to its handler, not the bare-slug error.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# bare slug → non-zero exit + message naming 'camp new <name>'
# ---------------------------------------------------------------------------


def test_bare_slug_exits_nonzero() -> None:
    """camp foo (bare slug, no group context) → non-zero exit."""
    result = _run(["my-feature-slug"])
    assert result.returncode != 0, (
        f"bare slug should exit non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_bare_slug_names_camp_new() -> None:
    """camp foo → stderr names 'camp new foo' as the correct command."""
    result = _run(["my-feature-slug"])
    combined = result.stdout + result.stderr
    assert "camp new" in combined, (
        f"bare slug error must name 'camp new'.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_bare_slug_names_the_slug_in_error() -> None:
    """The bare-slug error message includes the slug name."""
    result = _run(["my-feature-slug"])
    combined = result.stdout + result.stderr
    assert "my-feature-slug" in combined, (
        f"bare slug error must include the slug name.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_bare_slug_message_is_single_source_of_truth() -> None:
    """Both dispatchers emit the SAME bare-slug error, which lives in
    verb_taxonomy.bare_slug_message — not duplicated inline in cli/camp + spine."""
    import sys

    sys.path.insert(0, str(_PLUGIN_DIR))
    from camp.workspace.verb_taxonomy import bare_slug_message

    msg = bare_slug_message("my-feature-slug")
    assert "bare slug dispatch is no longer supported" in msg
    assert "camp new my-feature-slug" in msg
    # The token is interpolated, so a different token yields a different message.
    assert "camp new other" in bare_slug_message("other")


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
# Removed verbs: camp ai / camp cd / camp enter → legible redirect/error
# ---------------------------------------------------------------------------


def test_camp_ai_redirects_to_new() -> None:
    """camp ai <slug> errors and names 'camp new' as the replacement."""
    result = _run(["ai", "my-slug"])
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"camp ai should exit non-zero (removed verb).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "camp new" in combined, (
        f"camp ai must name 'camp new' as the replacement.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_camp_cd_errors() -> None:
    """camp cd is absent from the working surface and exits non-zero."""
    result = _run(["cd"])
    assert result.returncode != 0, (
        f"camp cd should exit non-zero (absent verb).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_camp_enter_redirects_to_activate() -> None:
    """camp enter errors and names 'camp activate' as the replacement."""
    result = _run(["enter", "some-member"])
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"camp enter should exit non-zero (removed verb).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "camp activate" in combined, (
        f"camp enter must name 'camp activate' as the replacement.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Legacy verbs init/open/break → legible error pointing at new canonical
# ---------------------------------------------------------------------------


def test_camp_init_gives_legible_redirect() -> None:
    """camp init → error pointing at 'camp group'."""
    result = _run(["init"])
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"camp init should redirect (non-zero).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "group" in combined, (
        f"camp init error must mention 'camp group'.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_camp_open_redirects_directly_to_new() -> None:
    """camp open → error pointing at 'camp new' (direct, not through ai)."""
    result = _run(["open", "my-slug"])
    combined = result.stdout + result.stderr
    assert "camp new" in combined, (
        f"camp open should redirect to 'camp new'.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "camp ai" not in combined, (
        f"camp open must not chain through the removed 'ai' verb.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_camp_break_redirects_directly_to_remove() -> None:
    """camp break → error pointing at 'camp remove' (direct, not through rm)."""
    result = _run(["break", "--name", "dummy"])
    combined = result.stdout + result.stderr
    assert "camp remove" in combined, (
        f"camp break should redirect to 'camp remove'.\n"
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

    CAMP_STATE_DIR is ALSO overridden here (Axiom 6 — tests must never touch
    real state): without it, any handler reached by a test built on this
    fixture (bookmark, resume, new, remove, …) resolves the developer's real
    ``~/.local/state/camp`` and reads/writes real bookmarks.json/manifests.
    """
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    return {
        "CAMP_CONFIG_DIR": str(tmp_path),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }


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


# camp new via group-aware path → seeds pending + spawns provisioner.
# the launch/session surface is stripped from the handler, so there is no
# claude exec to suppress (no CAMP_TEST_NO_EXEC needed).


def test_group_path_new_seeds_and_exits_zero(stub_group_env: dict[str, str]) -> None:
    """camp new <slug> via group path seeds the workspace and exits 0 (no claude)."""
    result = _run_group(["new", "my-slug"], group_env=stub_group_env)
    assert result.returncode == 0, (
        f"camp new via group path should seed + exit 0.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Output contract: stdout carries ONLY the workspace abs path (one line).
    assert result.stdout.endswith("\n") and result.stdout.count("\n") == 1, (
        f"camp new stdout must be exactly one line (the abs path).\n"
        f"stdout: {result.stdout!r}"
    )
    assert result.stdout.strip().startswith("/"), (
        f"camp new stdout must be the workspace abs path.\nstdout: {result.stdout!r}"
    )


def test_group_path_new_announces_background_provisioning(
    stub_group_env: dict[str, str],
) -> None:
    """camp new reports that provisioning runs in the background (on stderr)."""
    result = _run_group(["new", "my-slug"], group_env=stub_group_env)
    assert "background" in result.stderr.lower() or "camp status" in result.stderr, (
        f"camp new must announce background provisioning on stderr.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# camp remove (and its rm alias) via group-aware path → routes to reconcile_break;
# exits non-zero for an unknown slug (no manifest present).


def test_group_path_remove_unknown_slug_exits_nonzero(stub_group_env: dict[str, str]) -> None:
    """camp remove with an unknown slug exits non-zero (manifest not found)."""
    result = _run_group(["remove", "--name", "my-slug"], group_env=stub_group_env)
    assert result.returncode != 0, (
        f"camp remove with an unknown slug should exit non-zero (no manifest).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_group_path_rm_alias_matches_remove(stub_group_env: dict[str, str]) -> None:
    """The rm alias dispatches to the same handler as remove (same non-zero outcome)."""
    remove_result = _run_group(["remove", "--name", "my-slug"], group_env=stub_group_env)
    rm_result = _run_group(["rm", "--name", "my-slug"], group_env=stub_group_env)
    assert rm_result.returncode == remove_result.returncode, (
        "rm alias must dispatch to the same handler as remove.\n"
        f"rm: {rm_result.stderr}\nremove: {remove_result.stderr}"
    )
    assert "bare slug dispatch is no longer supported" not in (rm_result.stdout + rm_result.stderr)


# camp bookmark — a group-aware verb; reaches its handler (never the bare-slug
# error), and without a resolved group emits the standard needs-group message.


def test_group_path_bookmark_reaches_its_handler(stub_group_env: dict[str, str]) -> None:
    """camp bookmark dispatches to the capture handler, which refuses outside a
    workspace — NOT to the bare-slug error."""
    result = _run_group(["bookmark"], group_env=stub_group_env)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "bare slug dispatch is no longer supported" not in combined
    assert "camp bookmark:" in combined


def test_bookmark_without_a_group_says_so(tmp_path: Path) -> None:
    """With no group resolvable, camp bookmark emits the needs-group error."""
    result = _run(
        ["bookmark"],
        env={
            "CAMP_CONFIG_DIR": str(tmp_path / "empty"),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
        },
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "group" in combined.lower()
    assert "bare slug dispatch is no longer supported" not in combined


def test_bookmark_is_listed_in_help() -> None:
    """The verb is discoverable: camp help names it."""
    result = _run(["help"])
    assert "camp bookmark" in result.stdout


# --group is resolved upstream by _resolve_group_for_command but stays in argv
# for the handler to see; each bookmark subverb must drop it (matching
# resume's `_consume_flag_value` idiom) instead of tripping its own
# stray-positional refusal.


def test_group_path_bookmark_capture_consumes_group_flag(
    stub_group_env: dict[str, str],
) -> None:
    """camp bookmark --group <name> reaches the workspace-resolution refusal,
    not a stray-positional error about --group itself."""
    result = _run_group(["bookmark"], group_env=stub_group_env)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "unexpected argument" not in combined
    assert "this is not a camp workspace" in combined


def test_group_path_bookmark_ls_consumes_group_flag(
    stub_group_env: dict[str, str],
) -> None:
    """camp bookmark ls --group <name> succeeds instead of refusing --group as
    a stray positional."""
    result = _run_group(["bookmark", "ls"], group_env=stub_group_env)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "unexpected argument" not in combined


def test_group_path_bookmark_rm_consumes_group_flag(
    stub_group_env: dict[str, str],
) -> None:
    """camp bookmark rm --group <name> reaches the missing-ref usage refusal,
    not a stray-positional error about --group itself."""
    result = _run_group(["bookmark", "rm"], group_env=stub_group_env)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "unexpected argument" not in combined
    assert "usage: camp bookmark rm <ref>" in combined


# camp resume — a group-aware verb; reaches its handler (never the bare-slug
# error), and without a resolved group emits the standard needs-group message.


def test_group_path_resume_reaches_its_handler(stub_group_env: dict[str, str]) -> None:
    """camp resume <ref> dispatches to the resume handler, which refuses an
    unknown ref — NOT to the bare-slug error.

    CAMP_SHELL_INTEGRATION must be set: without it, resume's shell-integration
    guard (checked before ref resolution) fires first, and the test would
    never actually reach the unknown-ref refusal its docstring claims to
    exercise.
    """
    env = {**stub_group_env, "CAMP_SHELL_INTEGRATION": "1"}
    result = _run_group(["resume", "no-such-ref"], group_env=env)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "bare slug dispatch is no longer supported" not in combined
    assert "camp resume:" in combined


def test_group_path_resume_prints_nothing_on_stdout_when_it_refuses(
    stub_group_env: dict[str, str],
) -> None:
    """The two-line machine contract is all-or-nothing: a refusal leaves stdout
    empty so the shell wrapper can never act on a partial answer."""
    env = {**stub_group_env, "CAMP_SHELL_INTEGRATION": "1"}
    result = _run_group(["resume", "no-such-ref"], group_env=env)
    assert result.returncode != 0
    assert result.stdout == ""


def test_resume_without_a_group_reaches_its_handler(tmp_path: Path) -> None:
    """With no group resolvable, camp resume still answers on its own terms: a ref
    is addressed without knowing its group, so the refusal names the REF."""
    result = _run(
        ["resume", "x"],
        env={
            "CAMP_CONFIG_DIR": str(tmp_path / "empty"),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
            "CAMP_SHELL_INTEGRATION": "1",
        },
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "no bookmark named 'x'" in combined
    assert "no group resolved" not in combined
    assert "bare slug dispatch is no longer supported" not in combined


def test_resume_is_listed_in_help() -> None:
    """The verb is discoverable: camp help names it."""
    result = _run(["help"])
    assert "camp resume" in result.stdout
