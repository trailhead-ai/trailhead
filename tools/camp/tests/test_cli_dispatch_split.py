"""Test contract: the per-command-group CLI split preserved dispatch wiring.

`cli/camp` was split from a ~1173-line monolith into `camp.cli.{dispatch,status,
group,lifecycle,workspace,inject}`, with `dispatch.main` routing verb strings to
handlers now living in sibling modules. camp's dispatch is hand-rolled (not
argparse), so alias resolution and the unknown-command / bare-slug error paths
are more fragile than a declarative parser split. These tests exercise the REAL
`cli/camp` binary end-to-end and assert:

1. Smoke: every verb group (top-level meta-flags + each verb, via the group-aware
   path where relevant) runs without a Python traceback — proving every handler
   module loads and its lazy cross-module imports resolve.
2. Alias resolution survived the split: `rm`→remove (now in lifecycle) and
   `ls`→list (now in workspace) route to their canonical handler, with explicit
   exit-code AND message assertions — not just help-text parity.
3. The unknown-verb / bare-slug and malformed-flag error paths survived, with
   explicit exit-code AND message assertions per surface.

All invocations run from an isolated cwd with CAMP_CONFIG_DIR / CAMP_STATE_DIR
pointed at empty tmp dirs so no real group resolves — fully read-only against the
developer's own camp data.
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

_TRACEBACK_MARKER = "Traceback (most recent call last)"


@pytest.fixture()
def isolated_env(tmp_path: Path) -> dict[str, str]:
    """Env pointing camp's config/state at empty tmp dirs (no real group resolves)."""
    cfg = tmp_path / "config"
    state = tmp_path / "state"
    cfg.mkdir()
    state.mkdir()
    return {
        **os.environ,
        "CAMP_CONFIG_DIR": str(cfg),
        "CAMP_STATE_DIR": str(state),
    }


@pytest.fixture()
def stub_group_env(tmp_path: Path) -> dict[str, str]:
    """Env with a parseable stub group 'testgrp' so --group reaches the group-aware
    router (`_dispatch_group_command`) deterministically, independent of cwd."""
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    return {
        **os.environ,
        "CAMP_CONFIG_DIR": str(tmp_path),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }


def _run(args: list[str], *, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
    )


# ---------------------------------------------------------------------------
# 1. Smoke: every verb group runs without a Python traceback.
# ---------------------------------------------------------------------------

_SMOKE_INVOCATIONS = [
    ["--help"],
    ["-h"],
    ["help"],
    ["--version"],
    ["version"],
    ["--which"],
    ["status"],
    ["status", "--json"],
    ["list"],
    ["ls"],
    ["group"],
    ["group", "--help"],
    ["new"],
    ["remove"],
    ["rm"],
    ["setup"],
    ["setup", "--status"],
    ["sync"],
    ["rebase"],
    ["pwd"],
    ["activate"],
    ["inject", "--drain"],
    ["restock"],
    ["sweep"],
    ["code"],
    ["fire"],
    ["open"],
    ["break"],
    ["init"],
    ["ai"],
    ["enter"],
    ["kill"],
    ["bogusverb"],
]


@pytest.mark.parametrize("argv", _SMOKE_INVOCATIONS, ids=lambda a: " ".join(a) or "(none)")
def test_verb_smoke_no_traceback(argv, isolated_env, tmp_path) -> None:
    """Every verb group loads and runs — no uncaught Python traceback on any path."""
    result = _run(argv, env=isolated_env, cwd=tmp_path)
    assert _TRACEBACK_MARKER not in result.stderr, (
        f"'camp {' '.join(argv)}' surfaced a raw traceback (handler module or its "
        f"lazy imports broke in the split).\nstderr: {result.stderr}"
    )


def test_help_exits_zero_and_names_command_groups(isolated_env, tmp_path) -> None:
    """Top-level --help exits 0 and lists the major command groups."""
    result = _run(["--help"], env=isolated_env, cwd=tmp_path)
    assert result.returncode == 0, f"--help exited {result.returncode}\n{result.stderr}"
    combined = (result.stdout + result.stderr).lower()
    for verb in ("status", "new", "setup", "activate"):
        assert verb in combined, f"--help omits {verb!r}\n{result.stdout}"


def test_version_routes_through_status_module(isolated_env, tmp_path) -> None:
    """--version (handled by camp.cli.status) prints the version + the cli/camp binary path."""
    result = _run(["--version"], env=isolated_env, cwd=tmp_path)
    assert result.returncode == 0
    assert "camp 0.1.0" in result.stdout
    assert str(_CLI_CAMP) in result.stdout, (
        f"--version must name the cli/camp binary.\nstdout: {result.stdout}"
    )


def test_which_routes_through_status_module(isolated_env, tmp_path) -> None:
    """--which (handled by camp.cli.status) prints the cli/camp binary path, exit 0."""
    result = _run(["--which"], env=isolated_env, cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == str(_CLI_CAMP)


# ---------------------------------------------------------------------------
# 2. Alias resolution survived the split (exit code + message), group-aware path.
# ---------------------------------------------------------------------------


def test_alias_rm_routes_to_remove_handler(stub_group_env, tmp_path) -> None:
    """`rm` canonicalizes to `remove` and reaches `_cmd_remove_group_cli`
    (camp.cli.lifecycle): with no resolvable slug it emits the remove handler's
    own slug error and exits non-zero — NOT a bare-slug/unknown-verb error."""
    result = _run(["rm", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert result.returncode != 0, (
        f"`rm` (→remove) with no slug must exit non-zero.\nstderr: {result.stderr}"
    )
    assert "camp remove:" in result.stderr, (
        f"`rm` must route to the remove handler (message names 'camp remove').\n"
        f"stderr: {result.stderr}"
    )


def test_alias_rm_matches_canonical_remove(stub_group_env, tmp_path) -> None:
    """`rm` and `remove` produce byte-identical exit/stderr via the group-aware path."""
    alias = _run(["rm", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    canonical = _run(["remove", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert (alias.returncode, alias.stdout, alias.stderr) == (
        canonical.returncode,
        canonical.stdout,
        canonical.stderr,
    )


def test_alias_ls_routes_to_list_handler(stub_group_env, tmp_path) -> None:
    """`ls` canonicalizes to `list` and reaches `_cmd_ls_group_cli`
    (camp.cli.workspace): an empty group lists nothing and exits 0."""
    result = _run(["ls", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert result.returncode == 0, (
        f"`ls` (→list) on an empty group must exit 0.\nstderr: {result.stderr}"
    )


def test_alias_ls_matches_canonical_list(stub_group_env, tmp_path) -> None:
    """`ls` and `list` produce byte-identical exit/stdout via the group-aware path."""
    alias = _run(["ls", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    canonical = _run(["list", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert (alias.returncode, alias.stdout, alias.stderr) == (
        canonical.returncode,
        canonical.stdout,
        canonical.stderr,
    )


# ---------------------------------------------------------------------------
# 3. Unknown-verb / malformed-input error paths survived (exit code + message).
# ---------------------------------------------------------------------------


def test_group_aware_unknown_verb_is_bare_slug_error(stub_group_env, tmp_path) -> None:
    """An unknown token on the group-aware path (`_dispatch_group_command`) hits the
    bare-slug error and exits non-zero — no traceback, no silent no-op."""
    result = _run(["bogusverb", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert result.returncode != 0
    assert "bare slug dispatch is no longer supported" in result.stderr, (
        f"unknown group-aware verb must emit the bare-slug error.\nstderr: {result.stderr}"
    )
    assert "camp new bogusverb" in result.stderr


def test_no_group_unknown_verb_exits_nonzero_with_message(isolated_env, tmp_path) -> None:
    """An unknown top-level token with no group resolved falls through to spine and
    still exits non-zero with a message (not a traceback)."""
    result = _run(["bogusverb"], env=isolated_env, cwd=tmp_path)
    assert result.returncode != 0
    assert result.stderr.strip() != ""
    assert _TRACEBACK_MARKER not in result.stderr


def test_group_unknown_flag_errors(isolated_env, tmp_path) -> None:
    """An unknown flag to `group` is rejected by camp.cli.group's arg parser."""
    result = _run(["group", "testgrp", "--bogus-flag"], env=isolated_env, cwd=tmp_path)
    assert result.returncode != 0
    assert "unknown flag" in result.stderr
    assert "--bogus-flag" in result.stderr


def test_group_malformed_member_errors(isolated_env, tmp_path) -> None:
    """A malformed --member (no '=') is rejected by camp.cli.group's parser."""
    result = _run(
        ["group", "testgrp", "--member", "noequals"], env=isolated_env, cwd=tmp_path
    )
    assert result.returncode != 0
    assert "malformed --member" in result.stderr


def test_disabled_verb_group_path_exits_nonzero_with_message(stub_group_env, tmp_path) -> None:
    """A disabled verb on the group-aware path emits the disabled message + non-zero."""
    result = _run(["restock", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "temporarily" in combined or "stabiliz" in combined


def test_legacy_redirect_group_path_names_replacement(stub_group_env, tmp_path) -> None:
    """A legacy verb on the group-aware path redirects to its canonical name."""
    result = _run(["ai", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert result.returncode != 0
    assert "camp new" in result.stderr, (
        f"legacy 'ai' must name its replacement 'camp new'.\nstderr: {result.stderr}"
    )


def test_kill_is_routed_without_resolving_a_group(isolated_env, tmp_path) -> None:
    """`camp kill` is ref-addressed, so it must answer from a cwd where no group
    resolves — the situation it exists to be usable in. Reaching the needs-a-group
    refusal or the bare-slug error would mean it never got routed at all."""
    result = _run(["kill", "some-ref"], env=isolated_env, cwd=tmp_path)

    combined = result.stdout + result.stderr
    assert _TRACEBACK_MARKER not in combined
    assert "camp kill: " in combined
    assert "--group" not in combined


# ---------------------------------------------------------------------------
# --host <name> — the seam dispatch.main() hands a resolved Host through.
#
# In-process (imports camp.cli.dispatch directly, monkeypatches sys.argv/env)
# rather than the subprocess `_run` used everywhere above in this file: these
# two assertions — "the downstream handler received a clean argv" and "no
# transport call was attempted" — need visibility into which Python object
# was called with what, which a subprocess's exit code and stdout/stderr text
# cannot show.
# ---------------------------------------------------------------------------


def _dispatch_module():
    if str(_PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_DIR))
    import importlib

    return importlib.import_module("camp.cli.dispatch")


@pytest.fixture()
def hosts_env(tmp_path: Path) -> dict[str, str]:
    """CAMP_CONFIG_DIR with hosts.toml declaring one host, config/state dirs
    isolated in tmp_path."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text("[hosts.andromeda]\n", encoding="utf-8")
    return {"CAMP_CONFIG_DIR": str(cfg), "CAMP_STATE_DIR": str(tmp_path / "state")}


@pytest.mark.parametrize("verb", ["list", "sessions"])
@pytest.mark.parametrize(
    "flag_argv",
    [["--host", "andromeda"], ["--host=andromeda"]],
    ids=["space", "equals"],
)
def test_host_option_both_spellings_hand_the_same_clean_argv_downstream(
    monkeypatch: pytest.MonkeyPatch,
    hosts_env: dict[str, str],
    verb: str,
    flag_argv: list[str],
) -> None:
    dispatch = _dispatch_module()
    for k, v in hosts_env.items():
        monkeypatch.setenv(k, v)

    calls: list[tuple] = []
    monkeypatch.setattr(
        dispatch, "_dispatch_host_command", lambda *args: calls.append(args)
    )
    monkeypatch.setattr(sys, "argv", ["camp", verb, *flag_argv, "--json"])

    try:
        dispatch.main()
    except SystemExit as exc:
        pytest.fail(f"unexpected exit {exc.code} before reaching the handler")

    assert len(calls) == 1, calls
    canonical, host, host_name, rest = calls[0]
    assert canonical == verb
    assert host_name == "andromeda"
    assert host.ssh == "andromeda"
    # The handler received a clean argv — no stray --host of either spelling,
    # but the verb's OTHER flags (here --json) are still there.
    assert not any(a == "--host" or a.startswith("--host=") for a in rest), rest
    assert "--json" in rest, rest


def test_host_named_but_undeclared_never_calls_the_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    dispatch = _dispatch_module()
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text(
        "[hosts.andromeda]\n[hosts.workshop]\n", encoding="utf-8"
    )
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    import importlib

    transport = importlib.import_module("camp.host.transport")

    def _boom(*args, **kwargs):
        raise AssertionError("transport.run_camp must not be called for an undeclared host")

    monkeypatch.setattr(transport, "run_camp", _boom)
    monkeypatch.setattr(sys, "argv", ["camp", "list", "--host", "nope"])

    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()
    assert excinfo.value.code != 0
    captured = capsys.readouterr()
    assert "no host named 'nope' is declared" in captured.err


def test_host_and_group_together_never_call_the_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    dispatch = _dispatch_module()
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text("[hosts.andromeda]\n", encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))

    import importlib

    transport = importlib.import_module("camp.host.transport")

    def _boom(*args, **kwargs):
        raise AssertionError("transport.run_camp must not be called when --host and --group collide")

    monkeypatch.setattr(transport, "run_camp", _boom)
    monkeypatch.setattr(
        sys, "argv", ["camp", "sessions", "--host", "andromeda", "--group", "testgrp"]
    )

    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()
    assert excinfo.value.code != 0
    captured = capsys.readouterr()
    # Pins WHICH refusal fired, not just that something exited non-zero — a
    # missing --host/--group mutual-exclusion check would instead fall through
    # to the unrelated "no group resolved from cwd" refusal and still exit 1.
    assert "--host" in captured.err, captured.err
    assert "--group" in captured.err, captured.err
    assert "no group resolved from cwd" not in captured.err, captured.err


def test_trailing_host_flag_on_inapplicable_verb_reports_no_meaning_not_missing_value(
    isolated_env: dict[str, str], tmp_path: Path
) -> None:
    """`--host` with no value at all, on a verb --host has no meaning for
    regardless of a value (status is not in `_HOST_VERBS`), must report
    "has no meaning here" — not "requires a value", which would tell the
    operator to go supply a value that would be refused anyway."""
    result = _run(["status", "--host"], env=isolated_env, cwd=tmp_path)

    assert result.returncode != 0
    assert "has no meaning here" in result.stderr, result.stderr
    assert "requires a value" not in result.stderr, result.stderr


def test_groups_verb_with_host_flag_refuses_instead_of_answering_locally(
    isolated_env: dict[str, str], tmp_path: Path
) -> None:
    """`camp groups` is read-only and dispatched before group resolution —
    its early return in main() used to precede the --host reader entirely,
    so `camp groups --host andromeda` silently answered LOCALLY at exit 0
    instead of refusing. --host must never be silently dropped."""
    result = _run(["groups", "--host", "andromeda"], env=isolated_env, cwd=tmp_path)

    assert result.returncode != 0
    assert "--host" in result.stderr
    assert "no groups configured" not in result.stdout


def test_group_verb_with_host_flag_refuses_instead_of_answering_locally(
    isolated_env: dict[str, str], tmp_path: Path
) -> None:
    result = _run(["group", "testgrp", "--host", "andromeda"], env=isolated_env, cwd=tmp_path)

    assert result.returncode != 0
    assert "--host" in result.stderr


# ---------------------------------------------------------------------------
# `launch` joins `_HOST_VERBS` as the first STATE-CHANGING member — it is
# refused for --all-hosts/-a with its own wording (not the generic "has no
# meaning here"), and a `--host launch` requires an explicit --group rather
# than colliding with it the way the read verbs do. Both refusals fire
# before any host is contacted; the last test proves the passing case builds
# a clean argv that carries only the operator's named group, never one
# resolved from cwd.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("all_hosts_flag", ["--all-hosts", "-a"])
def test_all_hosts_launch_refuses_with_state_changing_wording_and_never_calls_transport(
    monkeypatch: pytest.MonkeyPatch, isolated_env: dict[str, str], tmp_path: Path, all_hosts_flag: str
) -> None:
    dispatch = _dispatch_module()
    for k, v in isolated_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    import importlib

    transport = importlib.import_module("camp.host.transport")

    def _boom(*args, **kwargs):
        raise AssertionError("transport.run_camp must not be called for a refused -a launch")

    monkeypatch.setattr(transport, "run_camp", _boom)
    monkeypatch.setattr(sys, "argv", ["camp", "launch", all_hosts_flag, "myslug"])

    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()
    assert excinfo.value.code != 0


def test_all_hosts_launch_refusal_is_distinguishable_from_a_read_verbs_generic_refusal(
    isolated_env: dict[str, str], tmp_path: Path
) -> None:
    """The all-hosts refusal for a state-changing verb (launch) must read as a
    DECISION — the verb changes state, so it acts on one named machine — never
    as the generic "has no meaning here" a verb merely lacking the option
    gets. Asserted against each verb's own wording, per the design doc's
    "Launching is never a broadcast"."""
    launch_result = _run(["launch", "-a", "myslug"], env=isolated_env, cwd=tmp_path)
    assert launch_result.returncode != 0
    assert "changes state" in launch_result.stderr, launch_result.stderr
    assert "has no meaning here" not in launch_result.stderr, launch_result.stderr
    assert "--host" in launch_result.stderr, launch_result.stderr

    read_verb_result = _run(["status", "-a"], env=isolated_env, cwd=tmp_path)
    assert read_verb_result.returncode != 0
    assert "has no meaning here" in read_verb_result.stderr, read_verb_result.stderr
    assert "changes state" not in read_verb_result.stderr, read_verb_result.stderr


def test_host_launch_without_group_refuses_before_connecting_and_never_calls_transport(
    monkeypatch: pytest.MonkeyPatch, hosts_env: dict[str, str], tmp_path: Path
) -> None:
    dispatch = _dispatch_module()
    for k, v in hosts_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    import importlib

    transport = importlib.import_module("camp.host.transport")

    def _boom(*args, **kwargs):
        raise AssertionError(
            "transport.run_camp must not be called for a --host launch missing --group"
        )

    monkeypatch.setattr(transport, "run_camp", _boom)
    monkeypatch.setattr(sys, "argv", ["camp", "launch", "--host", "andromeda", "myslug"])

    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()
    assert excinfo.value.code != 0


def test_host_launch_without_group_names_group_as_the_thing_to_supply(
    hosts_env: dict[str, str], tmp_path: Path
) -> None:
    result = _run(["launch", "--host", "andromeda", "myslug"], env=hosts_env, cwd=tmp_path)
    assert result.returncode != 0
    assert "--group" in result.stderr, result.stderr
    assert "--host" in result.stderr, result.stderr


@pytest.fixture()
def hosts_and_local_group_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """A declared host `andromeda`, AND a real local group `testgrp` whose one
    member's `repo_root` is a directory this fixture also creates — so a cwd
    inside it makes `resolve_from_cwd` actually resolve "testgrp" locally.
    Returns (env, member_dir) so a test can assert the local group would have
    resolved, and then prove a `--host launch` never uses it anyway.
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text("[hosts.andromeda]\n", encoding="utf-8")
    member_dir = tmp_path / "member-a-repo"
    member_dir.mkdir()
    groups_dir = cfg / "groups"
    groups_dir.mkdir()
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        f'[[members]]\nname = "member-a"\nrepo_root = "{member_dir}"\n',
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "CAMP_CONFIG_DIR": str(cfg),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }
    return env, member_dir


@pytest.mark.parametrize("run_from_member_dir", [False, True], ids=["isolated-cwd", "inside-local-group"])
def test_host_launch_remote_argv_carries_only_the_named_group_never_cwd_resolved(
    monkeypatch: pytest.MonkeyPatch,
    hosts_and_local_group_env: tuple[dict[str, str], Path],
    tmp_path: Path,
    run_from_member_dir: bool,
) -> None:
    """AC27 regression: with --group and a slug, a `--host` launch's remote
    argv carries only the group the operator named, never one this side would
    otherwise resolve from cwd — proven by parametrizing the ONE input that
    must not change the answer: run once from a cwd where NO local group
    resolves, and once from inside a directory a REAL local group ("testgrp")
    would resolve for, and assert the captured argv is identical either way
    and never mentions "testgrp"."""
    dispatch = _dispatch_module()
    env, member_dir = hosts_and_local_group_env
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    calls: list[tuple] = []
    monkeypatch.setattr(
        dispatch, "_dispatch_host_command", lambda *args: calls.append(args)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["camp", "launch", "--host", "andromeda", "--group", "prodgroup", "myslug"],
    )
    monkeypatch.chdir(member_dir if run_from_member_dir else tmp_path)

    try:
        dispatch.main()
    except SystemExit as exc:
        pytest.fail(f"unexpected exit {exc.code} before reaching the handler")

    assert len(calls) == 1, calls
    canonical, host, host_name, rest = calls[0]
    assert canonical == "launch"
    assert host_name == "andromeda"
    assert "--group" in rest and "prodgroup" in rest, rest
    assert "myslug" in rest, rest
    assert "testgrp" not in rest, (
        f"a --host launch must never carry a cwd-resolved group: {rest}"
    )


# ---------------------------------------------------------------------------
# `kill` joins `_HOST_VERBS` alongside `launch` — but the two verbs now split
# across TWO different sets rather than one. Both are STATE-CHANGING (drives
# the --all-hosts refusal wording), but only `launch` requires an explicit
# --group: a stop is groupless, so `--host` + `--group` together on `kill`
# takes the same collision refusal `list`/`sessions`/`attach` already take,
# never the "requires an explicit --group" wording. Each test below runs the
# SAME assertion against both verbs to prove the sets actually separated,
# per `task/kill-joins-the-host-verbs-and-the-group-requirement-stops-riding-
# on-state-changing`'s test contract.
# ---------------------------------------------------------------------------


def _rig_kill_relay(monkeypatch: pytest.MonkeyPatch, *, capture_argv: list | None = None):
    """Point `camp.host.relay.answer_payload_for_host` at a canned "stopped"
    answer so a `--host` kill can reach all the way through `dispatch.main()`
    to `_cmd_kill_host_cli` without any real SSH transport running."""
    import importlib

    relay = importlib.import_module("camp.host.relay")

    answer = relay.HostPayloadAnswer(
        obj={"session_id": "sess-1", "tmux_name": "camp-feat-x-sess1", "outcome": "stopped"},
        rows=None,
        certainty=relay.Certainty.HAPPENED,
        notices=[],
        exit_code=0,
    )

    def fake_answer_payload_for_host(verb, host, host_name, remote_argv, **kwargs):
        if capture_argv is not None:
            capture_argv.append((verb, list(remote_argv)))
        return answer

    monkeypatch.setattr(relay, "answer_payload_for_host", fake_answer_payload_for_host)
    return relay


def test_host_kill_reaches_the_real_kill_handler_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    hosts_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """`camp kill <ref> --host <name>` — with NO --group — must reach the real
    routed handler and succeed, proving the dispatch wiring this task adds
    (not a mocked `_dispatch_host_command`, unlike the routing tests above:
    this is the end-to-end CLI reach the task's scope facts say is new)."""
    dispatch = _dispatch_module()
    for k, v in hosts_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    captured_argv: list = []
    _rig_kill_relay(monkeypatch, capture_argv=captured_argv)
    monkeypatch.setattr(sys, "argv", ["camp", "kill", "sess-1", "--host", "andromeda"])

    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "sess-1\n"
    assert captured_argv == [("kill", ["kill", "sess-1", "--json"])]


@pytest.mark.parametrize(
    "verb,ref_or_slug,expect_group_required",
    [("kill", "sess-1", False), ("launch", "myslug", True)],
    ids=["kill-groupless", "launch-requires-group"],
)
def test_group_requirement_applies_to_launch_only_not_kill(
    monkeypatch: pytest.MonkeyPatch,
    hosts_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    verb: str,
    ref_or_slug: str,
    expect_group_required: bool,
) -> None:
    dispatch = _dispatch_module()
    for k, v in hosts_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    if not expect_group_required:
        _rig_kill_relay(monkeypatch)
    else:
        import importlib

        transport = importlib.import_module("camp.host.transport")

        def _boom(*args, **kwargs):
            raise AssertionError(
                "transport.run_camp must not be called for a --host launch missing --group"
            )

        monkeypatch.setattr(transport, "run_camp", _boom)

    monkeypatch.setattr(
        sys, "argv", ["camp", verb, ref_or_slug, "--host", "andromeda"]
    )

    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()

    err = capsys.readouterr().err
    if expect_group_required:
        assert excinfo.value.code != 0
        assert "requires an explicit" in err and "--group" in err, err
    else:
        assert excinfo.value.code == 0, err
        assert "requires an explicit" not in err, err


def test_kill_host_and_group_collision_takes_attachs_refusal_not_launchs(
    monkeypatch: pytest.MonkeyPatch,
    hosts_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """`camp kill <ref> --host <name> --group <g>` must refuse with the
    one-remote-host-and-one-local-group collision wording that `list` /
    `sessions` / `attach` already take — NOT `launch`'s "requires an
    explicit --group" wording, since a stop never needed --group in the
    first place."""
    dispatch = _dispatch_module()
    for k, v in hosts_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    import importlib

    transport = importlib.import_module("camp.host.transport")

    def _boom(*args, **kwargs):
        raise AssertionError(
            "transport.run_camp must not be called when --host and --group collide on kill"
        )

    monkeypatch.setattr(transport, "run_camp", _boom)
    monkeypatch.setattr(
        sys, "argv", ["camp", "kill", "sess-1", "--host", "andromeda", "--group", "testgrp"]
    )

    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()

    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "--host" in err and "--group" in err, err
    assert "one remote" in err, err
    assert "requires an explicit" not in err, err


@pytest.mark.parametrize("all_hosts_flag", ["--all-hosts", "-a"])
@pytest.mark.parametrize("verb,ref_or_slug", [("kill", "sess-1"), ("launch", "myslug")])
def test_state_changing_all_hosts_wording_applies_to_kill_and_launch_alike(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    verb: str,
    ref_or_slug: str,
    all_hosts_flag: str,
) -> None:
    """The all-hosts refusal for a state-changing verb must name the single-
    host form, never the generic "has no meaning here" — and this now holds
    for BOTH state-changing verbs, not just launch."""
    dispatch = _dispatch_module()
    for k, v in isolated_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    import importlib

    transport = importlib.import_module("camp.host.transport")

    def _boom(*args, **kwargs):
        raise AssertionError(f"transport.run_camp must not be called for a refused -a {verb}")

    monkeypatch.setattr(transport, "run_camp", _boom)
    monkeypatch.setattr(sys, "argv", ["camp", verb, ref_or_slug, all_hosts_flag])

    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()

    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "changes state" in err, err
    assert "has no meaning here" not in err, err
    assert "--host" in err, err


def test_host_kill_with_no_value_reports_missing_value_not_no_meaning(
    isolated_env: dict[str, str], tmp_path: Path
) -> None:
    """`camp kill --host` with no following value must refuse for the missing
    value — kill is now IN `_HOST_VERBS`, so the "has no meaning here" path
    (reserved for a verb outside the set entirely) must not fire instead."""
    result = _run(["kill", "some-ref", "--host"], env=isolated_env, cwd=tmp_path)

    assert result.returncode != 0
    assert "requires a value" in result.stderr, result.stderr
    assert "has no meaning here" not in result.stderr, result.stderr
