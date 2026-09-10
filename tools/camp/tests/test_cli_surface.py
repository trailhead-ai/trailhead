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
import re
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


# ---------------------------------------------------------------------------
# Legacy verbs init/open/break → legible error pointing at new canonical
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "names", "forbids"),
    [
        (["ai", "my-slug"], "camp new", None),
        (["cd"], None, None),
        (["enter", "some-member"], "camp activate", None),
        (["init"], "group", None),
        # open and break redirect straight at the canonical verb rather than
        # chaining through the verb that itself was removed.
        (["open", "my-slug"], "camp new", "camp ai"),
        (["break", "--name", "dummy"], "camp remove", None),
    ],
    ids=["ai", "cd", "enter", "init", "open", "break"],
)
def test_a_retired_verb_exits_nonzero_and_names_its_replacement(argv, names, forbids) -> None:
    """Every retired verb refuses and points at the verb that replaced it.

    `camp cd` has no replacement to name — it is gone rather than renamed — so it
    only has to refuse.
    """
    result = _run(argv)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"camp {argv[0]} should exit non-zero (retired verb).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    if names is not None:
        assert names in combined, (
            f"camp {argv[0]} must name {names!r} as the replacement.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    if forbids is not None:
        assert forbids not in combined, (
            f"camp {argv[0]} must not chain through the removed {forbids!r} verb.\n"
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
    fixture (new, remove, launch, …) resolves the developer's real
    ``~/.local/state/camp`` and reads/writes real manifests.
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
# ---------------------------------------------------------------------------
# A malformed sibling group toml must not abort fully-groupless commands.
#
# _resolve_group_for_command calls load_all_groups(config_dir), which loads
# and validates EVERY group toml up front; a GroupConfigError from any one of
# them was treated as a hard failure BEFORE the fully-groupless verbs — none of
# which need any particular group — ever got a chance to run.
# ---------------------------------------------------------------------------


@pytest.fixture()
def corrupt_sibling_env(tmp_path: Path) -> dict[str, str]:
    """CAMP_CONFIG_DIR with one valid group and one group toml that raises
    GroupConfigError (unknown hook kind, same malformed-config shape used by
    test_activation.py's regression coverage for _resolve_group_for_command)."""
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    (groups_dir / "badgroup.toml").write_text(
        '[group]\nname = "badgroup"\n\n'
        '[[members]]\nname = "myrepo"\nrepo_root = "/tmp/fake-myrepo"\n\n'
        '[[members.hooks]]\nkind = "not-a-valid-kind"\ncmd = ["echo", "hi"]\n'
    )
    return {"CAMP_CONFIG_DIR": str(tmp_path), "CAMP_STATE_DIR": str(tmp_path / "state")}


def test_kill_reaches_its_handler_despite_a_corrupt_sibling_group_toml(
    corrupt_sibling_env: dict[str, str],
) -> None:
    """`camp kill` is what an operator reaches for when something is already
    wrong, so a sibling group's malformed config must not abort it."""
    result = _run(["kill", "no-such-ref"], env=corrupt_sibling_env)
    combined = result.stdout + result.stderr
    assert "config error" not in combined, combined
    assert "camp kill:" in combined
    assert result.returncode != 0


def test_a_group_taking_verb_still_surfaces_the_corrupt_sibling_group_toml(
    corrupt_sibling_env: dict[str, str],
) -> None:
    """`camp new` DOES need a resolved group, so the corrupt sibling must still
    surface for it rather than being skipped past."""
    result = _run(["new", "some-slug", "--group", "testgrp"], env=corrupt_sibling_env)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "bare slug dispatch is no longer supported" not in combined


# ---------------------------------------------------------------------------
# --all-groups / -g refusal ordering.
#
# `corrupt_sibling_env` carries a group toml (badgroup) that fails to load —
# if the refusal below ran AFTER _resolve_group_for_command's config load, it
# would surface as "camp: config error: ..." instead of the refusal itself,
# because load_all_groups raises on the FIRST malformed file it walks past.
# That makes this a real regression check on WHERE the refusal sits, not just
# on whether it fires.
# ---------------------------------------------------------------------------


def test_all_groups_and_a_named_group_refuse_before_any_config_loads(
    corrupt_sibling_env: dict[str, str],
) -> None:
    result = _run(
        ["sessions", "--all-groups", "--group", "testgrp"], env=corrupt_sibling_env
    )
    _assert_clean_refusal(result, needle="--all-groups", verb="sessions")
    assert "config error" not in (result.stdout + result.stderr)


def test_all_groups_short_spelling_and_a_named_group_also_refuse(
    corrupt_sibling_env: dict[str, str],
) -> None:
    result = _run(["sessions", "-g", "--group", "testgrp"], env=corrupt_sibling_env)
    _assert_clean_refusal(result, needle="--all-groups", verb="sessions")
    assert "config error" not in (result.stdout + result.stderr)


def test_all_groups_has_no_meaning_for_a_verb_that_does_not_read_it(
    corrupt_sibling_env: dict[str, str],
) -> None:
    """`--all-groups` is rejected outright where it has no meaning, rather than
    being silently ignored — checked on a verb that reaches this refusal
    before group config is ever loaded, same as the mutual-exclusion case."""
    result = _run(["status", "--all-groups"], env=corrupt_sibling_env)
    _assert_clean_refusal(result, needle="--all-groups", verb="status")
    assert "config error" not in (result.stdout + result.stderr)


def test_camp_foreach_passes_a_payload_flag_named_like_all_groups_through_unchanged(
    tmp_path: Path,
) -> None:
    """`camp foreach <cmd…>` forwards EVERYTHING after the verb (and its own
    `--name`/`--fail-fast`/`--json` flags) to the wrapped command verbatim —
    `-g`/`--all-groups` there belongs to that command, not to camp. The
    pinned regression: `--all-groups`/`-g` used to be scanned for across the
    WHOLE of argv before the verb was even classified, so `camp foreach git
    log -g` died with "--all-groups has no meaning here" instead of ever
    reaching `git log`.
    """
    worktree = tmp_path / "trailhead" / ".claude" / "worktrees" / "myslug"
    worktree.mkdir(parents=True)
    (worktree / ".workspace-manifest.json").write_text(
        '{"name": "myslug", "repos": [{"name": "repo-a"}]}', encoding="utf-8"
    )
    env = {
        "WORKSPACE_ROOT": str(tmp_path),
        "CAMP_CONFIG_DIR": str(tmp_path / "config"),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }

    result = _run(
        ["foreach", "--name", "myslug", "echo", "-g", "--all-groups"], env=env
    )

    assert "has no meaning here" not in result.stderr
    assert result.returncode == 0, result.stderr
    assert "-g --all-groups" in result.stdout


def _assert_clean_refusal(result, *, needle: str, verb: str) -> None:
    assert result.returncode != 0, result.stdout
    assert result.stdout == ""
    lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith(f"camp {verb}: "), lines[0]
    assert needle in lines[0], lines[0]


# ---------------------------------------------------------------------------
# camp help — the launch surface's addressing forms and exit-code contract.
#
# The help menu is the operator's index of what camp can do, and `camp launch`
# now has three mutually exclusive addressing forms rather than one. These
# assert against `cmd_help`'s own output (via the real binary) rather than
# against a copy of the block, so editing the emitter is what moves them.
#
# The exit-code contract gets its own pin because `camp launch` grew a non-zero
# that carries information: an ambiguous `--resume` ref exits 2 with the
# candidates on stdout. A reader applying the ordinary "non-zero means it
# broke" heuristic would report a resolvable ambiguity as a hard failure, so
# the contract has to be stated where it is read. That the CLI actually returns
# the codes named here is driven separately, in test_session_cli.py.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def help_text() -> str:
    result = _run(["help"])
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_help_names_every_camp_launch_addressing_form(help_text: str) -> None:
    """A slug, a directory, and a session reference — all three discoverable."""
    assert "camp launch <slug>" in help_text
    assert "camp launch --dir <path> --group <name>" in help_text
    assert "camp launch --resume <ref>" in help_text


def _launch_exit_code_contract(help_text: str) -> dict[int, str]:
    """The exit codes `camp help` documents for `camp launch`, code → its prose.

    Read out of the emitter rather than compared against a literal, so an added
    or dropped code changes what these assertions see. test_session_cli.py reads
    the same block for itself and drives every code it finds there, which is
    what ties the documented contract to the one the binary honors.
    """
    heading = re.search(r"^Exit codes \(camp launch\):$", help_text, re.MULTILINE)
    assert heading, f"no launch exit-code contract in:\n{help_text}"
    block = help_text[heading.end() :]
    block = block.split("\nFlags:", 1)[0]

    contract: dict[int, str] = {}
    current: int | None = None
    for line in block.splitlines():
        match = re.match(r"^\s{2}(\d+)\s{2,}(.*)$", line)
        if match:
            current = int(match.group(1))
            contract[current] = match.group(2).strip()
        elif current is not None and line.strip():
            contract[current] += " " + line.strip()
    return contract


# ---------------------------------------------------------------------------
# --host <name> — option seam, applicability, and refusal ordering.
#
# Mirrors the --all-groups section above: `corrupt_sibling_env` proves a
# refusal fires before `_resolve_group_for_command` ever loads a sibling
# group's malformed toml, exactly as it does for --all-groups.
# ---------------------------------------------------------------------------


@pytest.fixture()
def corrupt_sibling_env_with_hosts(tmp_path: Path, corrupt_sibling_env: dict[str, str]) -> dict[str, str]:
    """`corrupt_sibling_env`'s config dir, plus a hosts.toml declaring two
    hosts — for refusals that need a real declared-hosts list to differ
    from the "nothing declared" case, still proven against a corrupt sibling
    group config."""
    (tmp_path / "hosts.toml").write_text(
        "[hosts.andromeda]\n[hosts.workshop]\n", encoding="utf-8"
    )
    return corrupt_sibling_env


def test_host_named_but_not_declared_lists_the_declared_hosts(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """A host named that isn't among the declared ones names it AND lists
    what *is* declared — the operator corrects a typo from the line alone."""
    result = _run(["list", "--host", "nope"], env=corrupt_sibling_env_with_hosts)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert result.stdout == ""
    assert "no host named 'nope' is declared" in combined, combined
    assert "declared hosts are: andromeda, workshop" in combined, combined
    assert "config error" not in combined


def test_host_named_with_no_hosts_toml_at_all_points_at_the_file(
    corrupt_sibling_env: dict[str, str],
) -> None:
    """With no hosts.toml at all, the refusal names no host list and points
    at the file to declare one in instead — the trailing clause an operator
    reads to know he's declared nothing yet, not merely mistyped a name."""
    result = _run(["sessions", "--host", "nope"], env=corrupt_sibling_env)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert result.stdout == ""
    assert "no host named 'nope' is declared" in combined, combined
    assert "declared hosts are:" not in combined, combined
    assert "no hosts are declared in ~/.config/camp/hosts.toml" in combined, combined
    assert "config error" not in combined


def test_host_and_group_refuse_before_any_config_loads(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """`--host` and `--group` name a remote machine and a local group at
    once — refused, mirroring --all-groups + --group's existing shape."""
    result = _run(
        ["sessions", "--host", "andromeda", "--group", "testgrp"],
        env=corrupt_sibling_env_with_hosts,
    )
    _assert_clean_refusal(result, needle="--host", verb="sessions")
    combined = result.stdout + result.stderr
    assert "--group" in combined, combined
    assert "config error" not in combined


def test_host_has_no_meaning_for_a_verb_that_does_not_read_it(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """`--host` is rejected outright on a verb it has no meaning for, before
    the hosts file or any group config is ever loaded."""
    result = _run(["status", "--host", "andromeda"], env=corrupt_sibling_env_with_hosts)
    _assert_clean_refusal(result, needle="has no meaning here", verb="status")
    combined = result.stdout + result.stderr
    assert "config error" not in combined
    # Distinguishes this refusal from a *resolved* --host reaching the
    # not-yet-wired downstream handler — the hosts file must never even be
    # consulted for a verb the option has no meaning for.
    assert "is declared" not in combined, combined


def test_host_with_a_missing_value_refuses(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """`--host` as the trailing token, with nothing after it, refuses rather
    than silently dropping the flag or falling through to an unrelated
    error path."""
    result = _run(["list", "--host"], env=corrupt_sibling_env_with_hosts)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert result.stdout == ""
    assert "--host" in combined, combined
    assert "requires a value" in combined, combined
    assert "config error" not in combined


def test_host_followed_by_group_refuses_as_a_missing_value(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """`--host --group testgrp` is a missing --host value, not a lookup for a
    host literally named '--group' — the message must say so, not name
    '--group' as an undeclared host."""
    result = _run(
        ["list", "--host", "--group", "testgrp"], env=corrupt_sibling_env_with_hosts
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert result.stdout == ""
    assert "requires a value" in combined, combined
    assert "no host named" not in combined, combined
    assert "config error" not in combined


def test_host_with_an_empty_equals_value_refuses_as_a_missing_value(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """`--host=` (empty value after the equals sign) refuses with the
    missing-value message, not a host-lookup failure for the empty string."""
    result = _run(["list", "--host="], env=corrupt_sibling_env_with_hosts)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert result.stdout == ""
    assert "requires a value" in combined, combined
    assert "no host named" not in combined, combined
    assert "config error" not in combined


# ---------------------------------------------------------------------------
# --host + --all-groups — the two "every group" spellings name different
# scopes (every group on a named remote host vs. every group on this
# machine), and the surrounding design refuses scope collisions rather than
# silently picking one. `--all-groups` is read and dispatched FIRST in
# main() (a long-established, load-bearing ordering) — these regression
# tests prove the refusal fires despite that, through the real CLI entry
# point, since a unit test of either reader alone cannot see the join.
# ---------------------------------------------------------------------------


def test_all_groups_and_host_refuse_for_list(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """The reported defect: `camp list --host andromeda --all-groups` used to
    silently ignore --host and answer for local groups instead, with exit 0.
    It must refuse, naming both options, and exit nonzero."""
    result = _run(
        ["list", "--host", "andromeda", "--all-groups"],
        env=corrupt_sibling_env_with_hosts,
    )
    _assert_clean_refusal(result, needle="--all-groups", verb="list")
    combined = result.stdout + result.stderr
    assert "--host" in combined, combined
    assert "config error" not in combined


def test_all_groups_and_host_refuse_for_sessions(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """Same defect, `camp sessions` path."""
    result = _run(
        ["sessions", "--host", "andromeda", "--all-groups"],
        env=corrupt_sibling_env_with_hosts,
    )
    _assert_clean_refusal(result, needle="--all-groups", verb="sessions")
    combined = result.stdout + result.stderr
    assert "--host" in combined, combined
    assert "config error" not in combined


def test_all_groups_and_host_refuse_regardless_of_flag_order(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """Argument order must not decide whether an operator gets a wrong
    answer or a refusal — `--all-groups --host andromeda` refuses
    identically to `--host andromeda --all-groups`."""
    result = _run(
        ["list", "--all-groups", "--host", "andromeda"],
        env=corrupt_sibling_env_with_hosts,
    )
    _assert_clean_refusal(result, needle="--all-groups", verb="list")
    combined = result.stdout + result.stderr
    assert "--host" in combined, combined
    assert "config error" not in combined


def test_all_groups_host_and_group_together_refuses_deterministically(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """All three at once: whichever refusal fires, it fires deterministically
    — pinned here as the existing --all-groups+--group refusal, since that
    check runs before the new --all-groups+--host check."""
    result = _run(
        ["list", "--host", "andromeda", "--all-groups", "--group", "testgrp"],
        env=corrupt_sibling_env_with_hosts,
    )
    _assert_clean_refusal(result, needle="--all-groups", verb="list")
    combined = result.stdout + result.stderr
    assert "--group" in combined, combined
    assert "config error" not in combined


def test_all_groups_and_host_with_a_missing_host_value_still_refuses(
    corrupt_sibling_env_with_hosts: dict[str, str],
) -> None:
    """`--host` with no value, alongside `--all-groups`, must not silently
    succeed — pinned as the same both-options refusal, since --host's
    presence is detected by scanning argv the same way the --group check
    beside it does, independent of whether --host carries a value."""
    result = _run(
        ["list", "--host", "--all-groups"], env=corrupt_sibling_env_with_hosts
    )
    _assert_clean_refusal(result, needle="--all-groups", verb="list")
    combined = result.stdout + result.stderr
    assert "--host" in combined, combined
    assert "config error" not in combined


# ---------------------------------------------------------------------------
# read_host_option — direct unit tests. The CLI-level refusals above prove
# the message an operator sees; these prove the argv `read_host_option`
# hands back to its caller, which a subprocess-level assertion cannot see.
# ---------------------------------------------------------------------------


def _dispatch_module():
    sys.path.insert(0, str(_PLUGIN_DIR))
    from camp.cli import dispatch

    return dispatch


def test_read_host_option_swallows_nothing_when_the_value_is_missing_before_a_flag() -> None:
    """`--host --group g` must raise before consuming `--group` as the host
    value — the defect this fix closes did exactly that."""
    dispatch = _dispatch_module()
    args = ["--host", "--group", "g"]
    with pytest.raises(dispatch._HostFlagMissingValue):
        dispatch.read_host_option(args)
    # read_host_option operates on a copy; the caller's own list, and thus
    # what a caller re-reads after catching the exception, is untouched.
    assert args == ["--host", "--group", "g"]


def test_read_host_option_swallows_nothing_when_the_value_is_missing_before_all_groups() -> None:
    dispatch = _dispatch_module()
    args = ["--host", "--all-groups"]
    with pytest.raises(dispatch._HostFlagMissingValue):
        dispatch.read_host_option(args)
    assert args == ["--host", "--all-groups"]


def test_read_host_option_trailing_token_still_raises() -> None:
    """Regression guard: the originally-implemented case must not break."""
    dispatch = _dispatch_module()
    with pytest.raises(dispatch._HostFlagMissingValue):
        dispatch.read_host_option(["--host"])


def test_read_host_option_empty_equals_value_raises_missing_value() -> None:
    dispatch = _dispatch_module()
    with pytest.raises(dispatch._HostFlagMissingValue):
        dispatch.read_host_option(["--host="])


def test_read_host_option_parses_a_real_host_name_and_never_mistakes_it_for_a_flag() -> None:
    dispatch = _dispatch_module()
    remaining, host_name = dispatch.read_host_option(["--host", "andromeda"])
    assert host_name == "andromeda"
    assert remaining == []


def test_camp_foreach_passes_a_payload_flag_named_like_host_through_unchanged(
    tmp_path: Path,
) -> None:
    """`camp foreach <cmd…>` forwards `--host x` in the wrapped command's own
    argv verbatim — it never belongs to camp's own option there."""
    worktree = tmp_path / "trailhead" / ".claude" / "worktrees" / "myslug"
    worktree.mkdir(parents=True)
    (worktree / ".workspace-manifest.json").write_text(
        '{"name": "myslug", "repos": [{"name": "repo-a"}]}', encoding="utf-8"
    )
    env = {
        "WORKSPACE_ROOT": str(tmp_path),
        "CAMP_CONFIG_DIR": str(tmp_path / "config"),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }

    result = _run(
        ["foreach", "--name", "myslug", "echo", "--host", "x"], env=env
    )

    assert "has no meaning here" not in result.stderr
    assert "is declared" not in result.stderr
    assert result.returncode == 0, result.stderr
    assert "--host x" in result.stdout
