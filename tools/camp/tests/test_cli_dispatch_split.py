"""Test contract: the per-command-group CLI split preserved dispatch wiring.

`cli/camp` was split from a ~1173-line monolith into `camp.cli.{dispatch,status,
group,lifecycle,workspace,inject}`, with `dispatch.main` routing verb strings to
handlers now living in sibling modules. Verb routing — alias resolution, the
disabled/legacy tables, the bare-slug refusal — is camp's own, decided before
any verb's parser runs, so it is not covered by the per-verb flag declarations.
These tests exercise the REAL `cli/camp` binary end-to-end and assert:

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
    ["record-conversation"],
    ["restock"],
    ["sweep"],
    ["code"],
    ["fire"],
    ["open"],
    ["break"],
    ["init"],
    ["ai"],
    ["enter"],
    ["launch"],
    ["sessions"],
    ["kill"],
    ["stop"],
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


#: `sessions` used to be parametrized here alongside `list` — both reached
#: `_dispatch_host_command`. `sessions` is a retired verb now
#: (LEGACY_REDIRECTS: sessions -> list), so it redirects before main() ever
#: reaches the `--host` block this handler-swap drives against; `list` is
#: the only member still exercised this way.
@pytest.mark.parametrize("verb", ["list"])
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
    canonical, host, host_name, rest, _connect_timeout = calls[0]
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
        sys, "argv", ["camp", "list", "--host", "andromeda", "--group", "testgrp"]
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
# A retired verb (`launch`, `resume`) is classified and handled ahead of ALL
# host routing — both the --all-hosts branch and the --host branch — so under
# either flag it prints the same redirect line it prints with no flag,
# contacts no host, loads no hosts.toml, and never reaches a live-host-verb
# applicability check.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb,rest,expect_redirect",
    [
        ("launch", ["myslug"], True),
        ("resume", ["some-ref"], True),
        ("list", [], False),
    ],
    ids=["launch-retired", "resume-retired", "list-live"],
)
def test_retired_verbs_under_host_redirect_locally_while_a_live_host_verb_reaches_the_stub(
    monkeypatch: pytest.MonkeyPatch,
    hosts_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    verb: str,
    rest: list[str],
    expect_redirect: bool,
) -> None:
    """Vary the retired verb typed under --host (launch, resume): each
    redirects locally with no forward. `list`, a live host verb, is the
    control — it still reaches the transport stub, proving the retired-verb
    check does not swallow --host routing for a verb that still uses it.
    (`sessions` used to be the control here; it is a retired verb itself now
    — LEGACY_REDIRECTS: sessions -> list — so it no longer reaches the
    transport under `--host` either; its own redirect coverage lives in
    `test_sessions_and_kill_host_redirect_before_reaching_the_relay` in
    `test_cli_all_hosts.py`.)"""
    dispatch = _dispatch_module()
    for k, v in hosts_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    import importlib

    transport = importlib.import_module("camp.host.transport")
    calls: list = []

    def fake_run_camp(host, remote_argv, **kw):
        calls.append(remote_argv)
        return transport.Answered(stdout="[]", stderr="", exit_code=0)

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)
    monkeypatch.setattr(sys, "argv", ["camp", verb, *rest, "--host", "andromeda"])

    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()

    err = capsys.readouterr().err
    if expect_redirect:
        assert excinfo.value.code == 1
        assert f"camp {verb}: this command has been replaced" in err, err
        assert calls == [], f"transport must never be called for retired {verb!r}: {calls}"
    else:
        assert excinfo.value.code == 0, err
        assert calls, f"{verb!r} must still reach the transport under --host"


# ---------------------------------------------------------------------------
# `kill` is a retired verb now (LEGACY_REDIRECTS: kill -> stop). It used to
# be a STATE-CHANGING host verb, reaching a real routed handler under
# `--host` with no --group required, and taking the same --host/--group
# collision refusal `list`/`sessions`/`attach` take. The legacy check in
# main() runs ahead of every host route, so none of that machinery is
# reachable through the real CLI any more — both tests below now pin the
# redirect firing first instead, in each of those two shapes.
# ---------------------------------------------------------------------------


def test_host_kill_redirects_locally_with_no_group_required(
    monkeypatch: pytest.MonkeyPatch,
    hosts_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """`camp kill <ref> --host <name>` — with NO --group — never reaches a
    routed handler any more: the legacy check answers locally before host
    resolution, exactly as it does with no --host at all."""
    dispatch = _dispatch_module()
    for k, v in hosts_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    import importlib

    transport = importlib.import_module("camp.host.transport")

    def _boom(*args, **kwargs):
        raise AssertionError("transport.run_camp must not be reached for retired kill")

    monkeypatch.setattr(transport, "run_camp", _boom)
    monkeypatch.setattr(sys, "argv", ["camp", "kill", "sess-1", "--host", "andromeda"])

    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().err == (
        "camp kill: this command has been replaced — use 'camp stop' instead.\n"
    )


def test_kill_host_and_group_collision_redirects_before_the_collision_check(
    monkeypatch: pytest.MonkeyPatch,
    hosts_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """`camp kill <ref> --host <name> --group <g>` used to refuse with the
    one-remote-host-and-one-local-group collision wording `list`/`sessions`/
    `attach` take. It redirects instead now, before that check ever runs —
    neither flag changes the answer."""
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

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert err == "camp kill: this command has been replaced — use 'camp stop' instead.\n"
    assert "--group" not in err, err
    assert "one remote" not in err, err


@pytest.mark.parametrize("all_hosts_flag", ["--all-hosts", "-a"])
@pytest.mark.parametrize(
    "verb,ref_or_slug,replacement",
    [("kill", "sess-1", "stop"), ("launch", "myslug", "attach")],
    ids=["kill-redirects", "launch-redirects"],
)
def test_all_hosts_redirects_kill_and_launch_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    verb: str,
    ref_or_slug: str,
    replacement: str,
    all_hosts_flag: str,
) -> None:
    """`kill` used to be a live, state-changing host verb, so `camp kill
    --all-hosts` carried its own "changes state" refusal, distinct from the
    generic redirect `camp launch --all-hosts` already got as a retired
    verb. `kill` is retired now too (LEGACY_REDIRECTS: kill -> stop), so
    both verbs answer the SAME way under either spelling of the flag — the
    "changes state" wording (and the `_STATE_CHANGING_HOST_VERBS` table that
    drove it) is gone, along with the divergence this test used to pin."""
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

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert err == f"camp {verb}: this command has been replaced — use 'camp {replacement}' instead.\n"
    assert "changes state" not in err, err
    assert "has no meaning here" not in err, err


def test_host_kill_with_no_value_still_redirects_rather_than_reporting_missing_value(
    isolated_env: dict[str, str], tmp_path: Path
) -> None:
    """`camp kill --host` with no following value used to refuse for the
    missing value specifically (kill IN `_HOST_VERBS`, so never the generic
    "has no meaning here" a verb outside the set gets). `kill` is retired
    now, so the legacy check answers before the value is ever read — the
    malformed flag never gets its own refusal at all."""
    result = _run(["kill", "some-ref", "--host"], env=isolated_env, cwd=tmp_path)

    assert result.returncode == 1
    assert result.stderr == (
        "camp kill: this command has been replaced — use 'camp stop' instead.\n"
    )
    assert "requires a value" not in result.stderr, result.stderr
    assert "has no meaning here" not in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# sessions/kill under every route (bare, --host, -a, -g) never reach the
# engine behind them — a tmux double whose `kill_session` raises is wired in
# so an accidental fall-through to a live handler would blow up loudly
# instead of quietly answering wrong.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,replacement",
    [
        (["sessions"], "list"),
        (["sessions", "--all-hosts"], "list"),
        (["sessions", "-a"], "list"),
        (["sessions", "--all-groups"], "list"),
        (["sessions", "-g"], "list"),
        (["kill", "some-ref"], "stop"),
        (["kill", "some-ref", "--all-hosts"], "stop"),
        (["kill", "some-ref", "-a"], "stop"),
        (["kill", "some-ref", "--all-groups"], "stop"),
        (["kill", "some-ref", "-g"], "stop"),
    ],
    ids=[
        "sessions-bare", "sessions-all-hosts", "sessions-a",
        "sessions-all-groups", "sessions-g",
        "kill-bare", "kill-all-hosts", "kill-a", "kill-all-groups", "kill-g",
    ],
)
def test_sessions_and_kill_never_reach_a_raising_tmux_double(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    argv: list[str],
    replacement: str,
) -> None:
    """Every form of the retired verbs — bare and each widening flag — must
    redirect without ever calling the tmux-kill engine behind it. Wiring it
    to raise turns an accidental fall-through into a loud failure instead of
    a quietly wrong answer."""
    for k, v in isolated_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    import importlib

    tmux_module = importlib.import_module("camp.launch.tmux")

    def _boom_kill(*args, **kwargs):
        raise AssertionError("tmux kill_session must not be reached for a retired verb")

    monkeypatch.setattr(tmux_module.Tmux, "kill_session", _boom_kill)
    monkeypatch.setattr(sys, "argv", ["camp", *argv])

    dispatch = _dispatch_module()
    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()

    verb = argv[0]
    assert excinfo.value.code == 1
    assert capsys.readouterr().err == (
        f"camp {verb}: this command has been replaced — use 'camp {replacement}' instead.\n"
    )
