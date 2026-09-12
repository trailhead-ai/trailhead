"""Tests for `camp list -a` / `--all-hosts` and `camp sessions -a` —
the operator-facing wiring of the two-axis merged answer per
`docs/design/the-all-hosts-answer-merges-every-declared-machine.md` and
`task/wire-the-all-hosts-option-and-render-the-merged-answer`'s test
contract.

Drives `camp.cli.dispatch.main()` in-process (sys.argv/env monkeypatched,
the same style `test_cli_dispatch_split.py` and `test_camp_list_host.py`
already use), with `camp.host.transport.run_camp` injected so no real SSH
connection is ever made.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _transport_module():
    return importlib.import_module("camp.host.transport")


@pytest.fixture()
def hosts_and_group_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAMP_CONFIG_DIR with one real, empty group ("testgrp") and hosts.toml
    declaring two hosts, "andromeda" then "lookout" — declaration order
    matters for the ordering assertions below."""
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    (tmp_path / "hosts.toml").write_text(
        "[hosts.andromeda]\n[hosts.lookout]\n", encoding="utf-8"
    )
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))


def _seed_local_workspace(group_name: str, slug: str, *, env: dict) -> None:
    """Seed a real local workspace + manifest, no git operations, so the
    local answer this file merges actually carries a row (rather than the
    empty local answer every other fixture in this file exercises)."""
    from camp.group.manifest import manifest_path_for, workspace_dir, write_central_manifest

    ws = workspace_dir(group_name, slug, env=env)
    ws.mkdir(parents=True, exist_ok=True)
    mpath = manifest_path_for(group_name, slug, env=env)
    write_central_manifest(
        mpath,
        {
            "schema_version": 1,
            "group": group_name,
            "slug": slug,
            "branch": f"worktree-{slug}",
            "members": [
                {
                    "name": "repo_a",
                    "repo_root": "/nonexistent/repo_a",
                    "worktree_path": str(ws / "repo_a"),
                    "provision_state": "pending",
                }
            ],
        },
    )


@pytest.fixture()
def hosts_and_group_env_with_local_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hosts_and_group_env`, plus a real, provisioned local workspace in
    "testgrp" — so a merged answer's local block carries an actual row
    rather than only a header, for tests that must observe the local row's
    shape and position, not just its header."""
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    (tmp_path / "hosts.toml").write_text(
        "[hosts.andromeda]\n[hosts.lookout]\n", encoding="utf-8"
    )
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    _seed_local_workspace("testgrp", "local-ws", env=os.environ)


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        dispatch.main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def _answered(rows: list[dict]):
    transport = _transport_module()
    return transport.Answered(stdout=json.dumps(rows), stderr="", exit_code=0)


# ---------------------------------------------------------------------------
# Equivalence — the two spellings, and the two ways to bundle -a with -g.
# ---------------------------------------------------------------------------


def test_a_and_all_hosts_flag_produce_the_same_answer(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    remote_rows = [
        {"ok": True, "slug": "remote1", "workspace_path": "/remote/1", "branch": "", "group": "testgrp"}
    ]
    monkeypatch.setattr(transport, "run_camp", lambda host, remote_argv, **kw: _answered(remote_rows))

    code_a = _run(monkeypatch, ["list", "-a", "--group", "testgrp", "--json"])
    out_a = capsys.readouterr().out

    code_b = _run(monkeypatch, ["list", "--all-hosts", "--group", "testgrp", "--json"])
    out_b = capsys.readouterr().out

    assert (code_a, out_a) == (code_b, out_b)
    assert code_a == 0
    rows = json.loads(out_a)
    assert {r["host"] for r in rows} == {"andromeda", "lookout"}


@pytest.mark.parametrize(
    "argv",
    [["-ag"], ["-a", "-g"], ["-ga"], ["-g", "-a"]],
    ids=["ag-bundled", "a-then-g", "ga-bundled", "g-then-a"],
)
def test_bundled_and_split_forms_produce_the_same_answer(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, argv: list[str]
) -> None:
    transport = _transport_module()
    remote_rows = [
        {"ok": True, "slug": "remote1", "workspace_path": "/remote/1", "branch": "", "group": "testgrp"}
    ]
    monkeypatch.setattr(transport, "run_camp", lambda host, remote_argv, **kw: _answered(remote_rows))

    code = _run(monkeypatch, ["list", *argv, "--json"])
    out = capsys.readouterr().out
    assert code == 0
    rows = json.loads(out)
    assert {r["host"] for r in rows} == {"andromeda", "lookout"}


def test_bundled_and_split_forms_are_byte_identical_to_each_other(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    remote_rows = [
        {"ok": True, "slug": "remote1", "workspace_path": "/remote/1", "branch": "", "group": "testgrp"}
    ]
    monkeypatch.setattr(transport, "run_camp", lambda host, remote_argv, **kw: _answered(remote_rows))

    outputs = {}
    for label, argv in [("ag", ["-ag"]), ("a_g", ["-a", "-g"]), ("ga", ["-ga"])]:
        code = _run(monkeypatch, ["list", *argv, "--json"])
        outputs[label] = (code, capsys.readouterr().out)

    assert outputs["ag"] == outputs["a_g"] == outputs["ga"]


# ---------------------------------------------------------------------------
# The short-option splitter — conservative by construction.
# ---------------------------------------------------------------------------


def test_split_bundled_short_flags_passes_through_a_token_with_a_foreign_character() -> None:
    dispatch = _dispatch_module()
    # 'l' is not one of camp's own short flags — the whole token must be
    # left completely untouched, not partially split.
    assert dispatch.split_bundled_short_flags(["-la", "-ag", "--group"]) == [
        "-la",
        "-a",
        "-g",
        "--group",
    ]


def test_a_short_token_not_wholly_camp_flags_is_never_read_as_all_hosts(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`-la` is not wholly `a`/`g` and must pass through unsplit — it must
    never trigger the all-hosts (or all-groups) dispatch path at all."""
    dispatch = _dispatch_module()

    def _boom(*args, **kwargs):
        raise AssertionError("-la must never be read as --all-hosts/--all-groups")

    monkeypatch.setattr(dispatch, "_dispatch_all_hosts_command", _boom)
    monkeypatch.setattr(dispatch, "_dispatch_all_groups_command", _boom)

    code = _run(monkeypatch, ["list", "-la", "--group", "testgrp", "--json"])
    assert code == 0
    # The plain single-group list answer — no traceback, no host key.
    rows = json.loads(capsys.readouterr().out)
    assert rows == []


def test_foreachs_opaque_payload_is_never_scanned_for_bundled_short_flags(
    hosts_and_group_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch = _dispatch_module()
    calls: list[list[str]] = []
    original = dispatch.split_bundled_short_flags

    def _spy(args):
        calls.append(list(args))
        return original(args)

    monkeypatch.setattr(dispatch, "split_bundled_short_flags", _spy)
    # foreach falls through to the standalone no-group registry when no
    # workspace resolves, which reads $WORKSPACE_ROOT (default $HOME/code)
    # — a real HOME must never be touched by a test (see conftest.py's
    # Path.home() guard).
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setattr(sys, "argv", ["camp", "foreach", "-ag", "git", "log", "-g"])

    try:
        dispatch.main()
    except SystemExit:
        pass

    assert calls == [], "foreach's own argv must never reach the splitter"


# ---------------------------------------------------------------------------
# Refusals.
# ---------------------------------------------------------------------------


def test_all_hosts_on_an_inapplicable_verb_refuses(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    code = _run(monkeypatch, ["status", "-a"])
    err = capsys.readouterr().err
    assert code != 0
    assert "--all-hosts has no meaning here" in err


def test_all_hosts_and_host_together_refuses(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    def _boom(*args, **kwargs):
        raise AssertionError("transport.run_camp must not be called when -a and --host collide")

    monkeypatch.setattr(transport, "run_camp", _boom)

    code = _run(monkeypatch, ["list", "-a", "--host", "andromeda"])
    err = capsys.readouterr().err
    assert code != 0
    assert "--all-hosts" in err and "--host" in err


def test_all_hosts_without_a_resolvable_group_refuses_naming_both_ways_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)

    code = _run(monkeypatch, ["list", "-a"])
    err = capsys.readouterr().err
    assert code != 0
    assert "-ag" in err
    assert "--group" in err


def test_all_hosts_with_group_flag_composes_rather_than_refusing(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`-a --group <name>` must not merely fail to refuse — it must actually
    reach the declared hosts and narrow to the named group, distinguishing
    this from a plain (un-widened) `camp list --group <name>`."""
    transport = _transport_module()
    calls: list[str] = []

    def fake_run_camp(host, remote_argv, **kw):
        calls.append(host.ssh)
        return _answered(
            [{"ok": True, "slug": "remote1", "workspace_path": "/r/1", "branch": "", "group": "testgrp"}]
        )

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    rows = json.loads(out)
    assert sorted(calls) == ["andromeda", "lookout"], "every declared host must be contacted"
    assert {r["host"] for r in rows} == {"andromeda", "lookout"}


# ---------------------------------------------------------------------------
# CRITICAL 1 — an unparsable hosts.toml must still yield the local answer
# plus one ok:false row naming the file, never a bare exit-1 refusal.
# ---------------------------------------------------------------------------


@pytest.fixture()
def broken_hosts_and_group_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAMP_CONFIG_DIR with one real group ("testgrp") and a hosts.toml that
    cannot parse as TOML at all."""
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    (tmp_path / "hosts.toml").write_text("this is not = = toml [[[", encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))


def test_unparsable_hosts_toml_still_yields_the_local_answer(
    broken_hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The recovery branch `merge_all_hosts_answer` already renders for an
    unparsable hosts.toml must actually be reachable: the local answer
    still prints, exit is the local answer's own (0 for an existing empty
    group), and the parse failure is named on stderr — never a bare exit 1
    with empty stdout."""
    transport = _transport_module()

    def _boom(*args, **kwargs):
        raise AssertionError("an unparsable hosts.toml has no hosts to contact")

    monkeypatch.setattr(transport, "run_camp", _boom)

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp", "--json"])
    captured = capsys.readouterr()
    assert code == 0
    rows = json.loads(captured.out)
    assert any(r.get("ok") is False and "hosts.toml" in r.get("reason", "") for r in rows)
    assert any("hosts.toml" in n for n in captured.err.splitlines())


# ---------------------------------------------------------------------------
# Human rendering — grouped by machine, local first, declared order.
# ---------------------------------------------------------------------------


def test_human_output_grouped_by_machine_local_first_then_declared_order(
    hosts_and_group_env_with_local_workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kw):
        if host.ssh == "andromeda":
            return _answered(
                [{"ok": True, "slug": "remoteA", "workspace_path": "/r/a", "branch": "", "group": "testgrp"}]
            )
        return transport.Unreachable(reason="connect timed out")

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp"])
    out = capsys.readouterr().out
    assert code == 0

    lines = out.splitlines()
    assert lines[0] == "this machine"
    assert lines[1].startswith("  ") and lines[1].split()[0] == "local-ws"
    assert lines[2] == "andromeda"
    assert lines[3] == "  remoteA /r/a"
    assert lines[4] == "lookout"
    assert lines[5].strip() != ""  # the failure line lookout owes
    assert "remoteA" not in lines[5]


# ---------------------------------------------------------------------------
# 8 — self_name equal to a declared host name must not double the machine.
# ---------------------------------------------------------------------------


@pytest.fixture()
def self_name_collides_with_declared_host_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hosts.toml declares self_name == a declared host's own name — nothing
    rejects this (host/config.py explicitly declines to check uniqueness)."""
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    (tmp_path / "hosts.toml").write_text(
        'self_name = "andromeda"\n[hosts.andromeda]\n[hosts.lookout]\n', encoding="utf-8"
    )
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))


def test_self_name_colliding_with_a_declared_host_prints_the_machine_once(
    self_name_collides_with_declared_host_env,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: _answered(
            [{"ok": True, "slug": "remote1", "workspace_path": "/r/1", "branch": "", "group": "testgrp"}]
        ),
    )

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp"])
    out = capsys.readouterr().out
    assert code == 0

    lines = out.splitlines()
    assert lines.count("andromeda") == 1
    assert lines[0] == "andromeda"
    assert lines[1] == "  remote1 /r/1"
    assert lines[2] == "lookout"
    assert lines[3] == "  remote1 /r/1"


def test_zero_state_prints_every_machines_header_with_nothing_beneath(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    monkeypatch.setattr(transport, "run_camp", lambda host, remote_argv, **kw: _answered([]))

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp"])
    out = capsys.readouterr().out
    assert code == 0
    assert out == "this machine\nandromeda\nlookout\n"


def test_a_failed_machine_shows_its_failure_line_under_its_own_header(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kw):
        return transport.Unreachable(reason="connect timed out")

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp"])
    out = capsys.readouterr().out
    assert code == 0

    lines = out.splitlines()
    assert lines[0] == "this machine"
    assert lines[1] == "andromeda"
    assert lines[2].startswith("  ") and lines[2].strip()
    assert lines[3] == "lookout"
    assert lines[4].startswith("  ") and lines[4].strip()


# ---------------------------------------------------------------------------
# Machine-readable — one flat array, same order.
# ---------------------------------------------------------------------------


def test_machine_readable_output_is_one_flat_array_in_declared_order(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kw):
        return _answered(
            [{"ok": True, "slug": f"s-{host.ssh}", "workspace_path": f"/{host.ssh}", "branch": "", "group": "testgrp"}]
        )

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    rows = json.loads(out)
    assert isinstance(rows, list)
    assert [r["host"] for r in rows] == ["andromeda", "lookout"]


# ---------------------------------------------------------------------------
# IMPORTANT 5 — `-a --json` must not drop notices; stdout stays one array.
# ---------------------------------------------------------------------------


def test_ag_json_still_prints_the_unparsable_group_notice_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`camp list -ag --json` (every group, every machine) must still name
    an unparsable group config on stderr — the same notice `-g --json`
    (without `-a`) already prints. This notice comes from the LOCAL
    all-groups answer, independent of hosts.toml, so it isolates the JSON
    branch's own notice-dropping defect from the hosts_error path."""
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "broken.toml").write_text("this is not = = toml [[[", encoding="utf-8")
    (groups_dir / "ok.toml").write_text(
        '[group]\nname = "ok"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    (tmp_path / "hosts.toml").write_text("[hosts.andromeda]\n", encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))

    transport = _transport_module()
    monkeypatch.setattr(transport, "run_camp", lambda host, remote_argv, **kw: _answered([]))

    code = _run(monkeypatch, ["list", "-ag", "--json"])
    captured = capsys.readouterr()
    assert code == 0
    rows = json.loads(captured.out)
    assert any(r.get("ok") is False and "broken.toml" in r.get("reason", "") for r in rows)
    assert "skipping" in captured.err
    assert "broken.toml" in captured.err


# ---------------------------------------------------------------------------
# Backward compatibility — the plain single-machine surface is unchanged.
# ---------------------------------------------------------------------------


def test_plain_list_json_still_carries_no_host_key(
    hosts_and_group_env_with_local_workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    def _boom(*args, **kwargs):
        raise AssertionError("a plain camp list must never contact the transport")

    monkeypatch.setattr(transport, "run_camp", _boom)

    code = _run(monkeypatch, ["list", "--group", "testgrp", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    rows = json.loads(out)
    assert len(rows) == 1
    assert rows[0]["slug"] == "local-ws"
    assert "host" not in rows[0]


def test_plain_host_flag_behaves_exactly_as_before(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    monkeypatch.setattr(transport, "run_camp", lambda host, remote_argv, **kw: _answered([]))

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    assert json.loads(out) == []


# ---------------------------------------------------------------------------
# `camp sessions -a` — the second verb this wiring must cover; a different
# local value function (`_sessions_live_answer`) and a different row shape
# from `list` above.
# ---------------------------------------------------------------------------


def test_sessions_a_and_all_hosts_flag_produce_the_same_answer(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    import camp.launch.session as launch_session

    monkeypatch.setattr(launch_session, "enumerate_records", lambda *a, **k: [])
    remote_rows = [
        {
            "ok": True,
            "session_id": "sess-1",
            "cwd": "/remote/cwd",
            "kind": "claude",
            "controllable": True,
            "name": None,
            "pid": 123,
            "started_at": None,
            "group": "testgrp",
            "account": None,
        }
    ]
    monkeypatch.setattr(transport, "run_camp", lambda host, remote_argv, **kw: _answered(remote_rows))

    code_a = _run(monkeypatch, ["sessions", "-a", "--group", "testgrp", "--json"])
    out_a = capsys.readouterr().out

    code_b = _run(monkeypatch, ["sessions", "--all-hosts", "--group", "testgrp", "--json"])
    out_b = capsys.readouterr().out

    assert (code_a, out_a) == (code_b, out_b)
    assert code_a == 0
    rows = json.loads(out_a)
    assert {r["host"] for r in rows} == {"andromeda", "lookout"}


# ---------------------------------------------------------------------------
# CRITICAL 2 — `sessions -a` must refuse the same five local-only options
# `_cmd_sessions_host_cli` already refuses for `--host`, rather than
# silently answering the live listing.
# ---------------------------------------------------------------------------


def test_sessions_a_and_recoverable_refuses(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    def _boom(*args, **kwargs):
        raise AssertionError("a refused sessions -a must never contact the transport")

    monkeypatch.setattr(transport, "run_camp", _boom)

    code = _run(monkeypatch, ["sessions", "-a", "--group", "testgrp", "--recoverable"])
    err = capsys.readouterr().err
    assert code != 0
    assert "own live sessions" in err


def test_sessions_a_and_all_refuses(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    monkeypatch.setattr(transport, "run_camp", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("a refused sessions -a must never contact the transport")
    ))

    code = _run(monkeypatch, ["sessions", "-a", "--group", "testgrp", "--all"])
    err = capsys.readouterr().err
    assert code != 0
    assert "only widens --recoverable" in err


def test_sessions_a_and_dir_refuses(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    monkeypatch.setattr(transport, "run_camp", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("a refused sessions -a must never contact the transport")
    ))

    code = _run(monkeypatch, ["sessions", "-a", "--group", "testgrp", "--dir", "/tmp/somewhere"])
    err = capsys.readouterr().err
    assert code != 0
    assert "not a local directory" in err


def test_sessions_a_and_limit_refuses(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    monkeypatch.setattr(transport, "run_camp", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("a refused sessions -a must never contact the transport")
    ))

    code = _run(monkeypatch, ["sessions", "-a", "--group", "testgrp", "--limit", "5"])
    err = capsys.readouterr().err
    assert code != 0
    assert "only widens --recoverable" in err


def test_sessions_a_and_positional_slug_refuses(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    monkeypatch.setattr(transport, "run_camp", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("a refused sessions -a must never contact the transport")
    ))

    code = _run(monkeypatch, ["sessions", "-a", "--group", "testgrp", "feat-x"])
    err = capsys.readouterr().err
    assert code != 0
    assert "feat-x" in err
    assert "workspace slug" in err


def test_sessions_human_output_renders_answered_rows_under_their_machine(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    import camp.launch.session as launch_session

    monkeypatch.setattr(launch_session, "enumerate_records", lambda *a, **k: [])

    def fake_run_camp(host, remote_argv, **kw):
        if host.ssh == "andromeda":
            return _answered(
                [
                    {
                        "ok": True,
                        "session_id": "sess-remote",
                        "cwd": "/remote/cwd",
                        "kind": "claude",
                        "controllable": True,
                        "name": None,
                        "pid": 42,
                        "started_at": None,
                        "group": "testgrp",
                        "account": None,
                    }
                ]
            )
        return _answered([])

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["sessions", "-a", "--group", "testgrp"])
    out = capsys.readouterr().out
    assert code == 0

    lines = out.splitlines()
    assert lines[0] == "this machine"
    assert lines[1] == "andromeda"
    assert "sess-remote" in lines[2]
    assert lines[3] == "lookout"


def test_sessions_human_output_session_id_control_sequence_cannot_forge_a_second_line(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`render_session_row_human` (`cli/session.py`) escapes only `cwd`
    today; `session_id`, `kind`, and the `name` label reach this f-string
    raw. Any of them can carry a relayed embedded newline and must not
    split one row into a forged second one — the same argument
    `printable_path` already makes for `root` and for `cwd` here."""
    transport = _transport_module()
    import camp.launch.session as launch_session

    monkeypatch.setattr(launch_session, "enumerate_records", lambda *a, **k: [])

    forged_id = "sess-real\nandromeda    FORGED-999  claude  /evil (forged-name)"

    def fake_run_camp(host, remote_argv, **kw):
        if host.ssh == "andromeda":
            return _answered(
                [
                    {
                        "ok": True,
                        "session_id": forged_id,
                        "cwd": "/remote/cwd",
                        "kind": "claude",
                        "controllable": True,
                        "name": None,
                        "pid": 42,
                        "started_at": None,
                        "group": "testgrp",
                        "account": None,
                    }
                ]
            )
        return _answered([])

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["sessions", "-a", "--group", "testgrp"])
    out = capsys.readouterr().out
    assert code == 0

    lines = [ln for ln in out.splitlines() if ln]
    # "this machine" / "andromeda" / the one row / "lookout" — never a fifth
    # line forged out of the embedded newline in session_id.
    assert len(lines) == 4
    assert "\\x0a" in lines[2]
    assert "FORGED-999" in lines[2]


# ---------------------------------------------------------------------------
# Per-row failure isolation — a malformed row from one machine must not take
# down the merged listing. `_render_all_hosts_human` calls `render_row`
# directly on every row; the two `--host` siblings already wrap this same
# per-row access in `try/except KeyError` (`workspace.py`'s
# `_cmd_ls_host_cli` and `session.py`'s `_cmd_sessions_host_cli`), and this
# pins the merged renderer to the same isolation guarantee.
# ---------------------------------------------------------------------------


def test_a_malformed_remote_row_is_skipped_with_a_notice_other_rows_still_render(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kw):
        if host.ssh == "andromeda":
            return _answered(
                [
                    {"ok": True, "workspace_path": "/ws/feat-x", "group": "testgrp"},  # missing slug
                    {"ok": True, "slug": "alpha", "workspace_path": "/ws/alpha", "group": "testgrp"},
                ]
            )
        return _answered([])

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp"])
    captured = capsys.readouterr()
    assert code == 0
    assert "Traceback" not in captured.err
    assert "alpha" in captured.out
    assert "andromeda" in captured.err
    assert "slug" in captured.err
    assert "skipping" in captured.err


def test_isolation_a_malformed_row_on_one_host_does_not_affect_another_hosts_rows(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kw):
        if host.ssh == "andromeda":
            return _answered([{"ok": True, "workspace_path": "/ws/feat-x"}])  # missing slug
        return _answered(
            [{"ok": True, "slug": "beta", "workspace_path": "/ws/beta", "branch": "", "group": "testgrp"}]
        )

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp"])
    captured = capsys.readouterr()
    assert code == 0
    assert "Traceback" not in captured.err
    assert "beta" in captured.out
    lines = captured.out.splitlines()
    assert lines[0] == "this machine"
    assert lines[1] == "andromeda"
    assert lines[2] == "lookout"
    assert lines[3] == "  beta /ws/beta"


def test_exit_code_is_unaffected_by_a_malformed_remote_row(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: _answered([{"ok": True, "workspace_path": "/ws/feat-x"}]),
    )

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp"])
    capsys.readouterr()
    assert code == 0


def test_a_malformed_local_row_is_skipped_consistently_with_remote_rows(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    workspace = importlib.import_module("camp.cli.workspace")
    transport = _transport_module()

    def fake_local_list_answer(group, *, all_groups):
        return (
            [
                {"ok": True, "workspace_path": "/ws/local-broken"},  # missing slug
                {"ok": True, "slug": "local-ok", "workspace_path": "/ws/local-ok", "branch": "", "group": "testgrp"},
            ],
            [],
            0,
        )

    monkeypatch.setattr(workspace, "local_list_answer", fake_local_list_answer)
    monkeypatch.setattr(transport, "run_camp", lambda host, remote_argv, **kw: _answered([]))

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp"])
    captured = capsys.readouterr()
    assert code == 0
    assert "Traceback" not in captured.err
    assert "local-ok" in captured.out
    assert "slug" in captured.err
    assert "skipping" in captured.err


# ---------------------------------------------------------------------------
# connect_timeout — the top-level hosts.toml scalar threads to every
# transport call this option's two routes make.
# ---------------------------------------------------------------------------


def _hosts_and_group_env_with_connect_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connect_timeout_line: str
) -> None:
    """`hosts_and_group_env`'s same two declared hosts and one group, but
    with the caller's own top-level ``connect_timeout`` line (or none)
    prepended to hosts.toml — the fixture the two declared-hosts fixtures
    above already establish, extended with the one line these tests vary."""
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    (tmp_path / "hosts.toml").write_text(
        f"{connect_timeout_line}[hosts.andromeda]\n[hosts.lookout]\n", encoding="utf-8"
    )
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))


def test_all_hosts_fanout_threads_declared_connect_timeout_to_every_transport_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _hosts_and_group_env_with_connect_timeout(tmp_path, monkeypatch, "connect_timeout = 3\n")
    transport = _transport_module()
    seen: list[float] = []

    def fake_run_camp(host, remote_argv, **kw):
        seen.append(kw.get("connect_timeout"))
        return _answered([])

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp", "--json"])
    capsys.readouterr()
    assert code == 0
    assert seen == [3.0, 3.0]


def test_all_hosts_fanout_uses_a_different_declared_connect_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The second of the two points that change the answer — a different
    declared value causes the transport to be invoked with THAT value,
    proving this is not just "any nonzero value passes through"."""
    _hosts_and_group_env_with_connect_timeout(tmp_path, monkeypatch, "connect_timeout = 42\n")
    transport = _transport_module()
    seen: list[float] = []

    def fake_run_camp(host, remote_argv, **kw):
        seen.append(kw.get("connect_timeout"))
        return _answered([])

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["list", "-a", "--group", "testgrp", "--json"])
    capsys.readouterr()
    assert code == 0
    assert seen == [42.0, 42.0]


def test_all_hosts_fanout_uses_the_transports_default_when_connect_timeout_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _hosts_and_group_env_with_connect_timeout(tmp_path, monkeypatch, "")
    import camp.launch.session as launch_session

    monkeypatch.setattr(launch_session, "enumerate_records", lambda *a, **k: [])
    transport = _transport_module()
    seen: list[float] = []

    def fake_run_camp(host, remote_argv, **kw):
        seen.append(kw.get("connect_timeout"))
        return _answered([])

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["sessions", "-a", "--group", "testgrp", "--json"])
    capsys.readouterr()
    assert code == 0
    assert seen == [
        transport.DEFAULT_CONNECT_TIMEOUT_SECONDS,
        transport.DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ]


def test_single_host_list_threads_declared_connect_timeout_to_the_relay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _hosts_and_group_env_with_connect_timeout(tmp_path, monkeypatch, "connect_timeout = 7\n")
    transport = _transport_module()
    seen: list[float] = []

    def fake_run_camp(host, remote_argv, **kw):
        seen.append(kw.get("connect_timeout"))
        return _answered([])

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    capsys.readouterr()
    assert code == 0
    assert seen == [7.0]


def test_relay_route_verb_set_is_every_host_verb_except_attach():  # inert-gate: allow closed-vocabulary smoke — compares `_HOST_VERBS` against a hand-copied literal, not the parametrize table below, so it cannot detect the table drifting from the source; kept as a wiring reminder that a verb added to `_HOST_VERBS` needs a matching case there
    """Pins the enumeration this parametrized test below drives against —
    `_HOST_VERBS` minus `attach` (which hands off interactively, with no
    transport seam to assert at) is exactly {list, sessions, launch, kill}.
    A verb added to `_HOST_VERBS` without a matching case below breaks this
    test, not silently ships uncovered."""
    dispatch = _dispatch_module()
    assert dispatch._HOST_VERBS - {"attach"} == {"list", "sessions", "launch", "kill"}


@pytest.mark.parametrize(
    "argv",
    [
        ["list", "--host", "andromeda", "--json"],
        ["sessions", "--host", "andromeda", "--json"],
        ["launch", "somews", "--host", "andromeda", "--group", "testgrp", "--json"],
        ["kill", "ref1", "--host", "andromeda", "--json"],
    ],
    ids=["list", "sessions", "launch", "kill"],
)
def test_every_relay_host_verb_threads_the_declared_connect_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, argv: list[str]
) -> None:
    """Drives every member of `_HOST_VERBS` reachable through the single-
    host `--host` relay (list, sessions, launch, kill — attach's `--host`
    path hands off interactively and is covered separately, in
    test_attach_cli.py's cross-host probe route). A verb left reading the
    old constant instead of the resolved value fails this test rather than
    shipping — the exact regression a sample of two verbs would let through."""
    _hosts_and_group_env_with_connect_timeout(tmp_path, monkeypatch, "connect_timeout = 9\n")
    transport = _transport_module()
    seen: list[float] = []

    def fake_run_camp(host, remote_argv, **kw):
        seen.append(kw.get("connect_timeout"))
        return transport.Unreachable(reason="Connection timed out")

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    _run(monkeypatch, argv)
    capsys.readouterr()

    assert seen == [9.0]


# ---------------------------------------------------------------------------
# camp doctor -a — the per-host probe section
# ---------------------------------------------------------------------------


def _doctor_hosts_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hosts_toml: str
) -> None:
    """A hermetic env for `camp doctor -a`: an isolated config dir carrying
    *hosts_toml*, isolated workspace/canonical roots (so the local checks
    never touch a real registry), and deterministic local-check seams."""
    (tmp_path / "hosts.toml").write_text(hosts_toml, encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("CAMP_CANONICAL_ROOT", str(tmp_path / "canonical"))
    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    (tmp_path / "workspace").mkdir(exist_ok=True)
    (tmp_path / "canonical").mkdir(exist_ok=True)


def _probe_answered(report: dict) -> "object":
    transport = _transport_module()
    return transport.Answered(stdout=json.dumps(report), stderr="", exit_code=0)


def test_doctor_no_remote_hosts_declared_renders_this_machine_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """State — no remote hosts are declared: the host section still renders,
    carrying this machine's row alone, and no network is contacted."""
    _doctor_hosts_env(tmp_path, monkeypatch, "")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()

    def _boom(*args, **kwargs):
        raise AssertionError("no hosts are declared — the transport must never be called")

    monkeypatch.setattr(transport, "run_camp", _boom)

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(report["hosts"]) == 1
    assert report["hosts"][0]["verdict"] == "PASS"


def test_doctor_hosts_file_read_error_is_rendered_in_the_host_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """When hosts.toml itself fails to parse, the declared hosts can never
    be enumerated — the section renders only this machine today, with
    nothing saying why. The all-hosts path for other verbs already surfaces
    this failure (`merge_all_hosts_answer`'s own `ok: false` row); the
    doctor host section must say so too, in its own grammar.

    Valid TOML with a malformed `[hosts]` table, rather than a TOML syntax
    error, so the pre-existing self-declared-host-name check (which reads
    only the unrelated `self_name` key) stays unaffected — isolating this
    assertion to the host section's own exit-status contract rather than
    the local check that already fails on a genuine parse error."""
    _doctor_hosts_env(tmp_path, monkeypatch, 'hosts = "not a table"\n')
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()

    def _boom(*args, **kwargs):
        raise AssertionError("hosts.toml never parsed — the transport must never be called")

    monkeypatch.setattr(transport, "run_camp", _boom)

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0, "a hosts.toml read failure must never change the exit status"
    assert len(report["hosts"]) == 2, "this machine's row, plus a row for the read failure"
    error_row = next(h for h in report["hosts"] if h["host"] != report["hosts"][0]["host"])
    assert "[hosts]" in error_row["detail"]


def test_doctor_one_host_answers_fully_resolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """State — one declared host answers and its camp and multiplexer both
    resolve: rendered as the fully-answerable verdict, naming the machine."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: _probe_answered(
            {"pass": True, "checks": [], "probe": True, "multiplexer_present": True}
        ),
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "PASS"


def test_doctor_several_hosts_each_rendered_as_its_own_block_in_declared_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """State — several declared hosts answer, each rendered as its own
    block, in declared order — even when the second-declared host answers
    first."""
    _doctor_hosts_env(
        tmp_path, monkeypatch, "[hosts.andromeda]\n[hosts.lookout]\n"
    )
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()

    import time

    def fake_run_camp(host, remote_argv, **kw):
        if host.ssh == "andromeda":
            time.sleep(0.05)  # declared first, completes last
        return _probe_answered(
            {"pass": True, "checks": [], "probe": True, "multiplexer_present": True}
        )

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    remote_names = [h["host"] for h in report["hosts"] if h["host"] != report["hosts"][0]["host"]]
    assert remote_names == ["andromeda", "lookout"], (
        "declared order must be preserved even though lookout answers first"
    )


def test_doctor_host_unreachable_within_connect_timeout_renders_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """State — a declared host does not answer within the connection
    timeout: rendered with the transport's own reason, and the "down"
    verdict — never the "warn" verdict a resolved-but-flawed connection
    gets."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()
    monkeypatch.setattr(
        transport, "run_camp", lambda host, remote_argv, **kw: transport.Unreachable(reason="x")
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "DOWN"
    assert "unreachable" in row["detail"]


def test_doctor_host_answers_but_camp_does_not_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """State — a declared host answers but camp does not resolve there:
    reported as its own finding, distinct from unreachability, naming the
    camp location that failed."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()
    monkeypatch.setattr(
        transport, "run_camp", lambda host, remote_argv, **kw: transport.CampNotResolvable()
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "WARN"
    assert "camp_bin" in row["detail"]


def test_doctor_host_answers_but_no_multiplexer_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """State — a declared host answers but no terminal multiplexer is
    present: a finding, distinct from the probe being unavailable — the two
    must never render alike."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: _probe_answered(
            {"pass": True, "checks": [], "probe": True, "multiplexer_present": False}
        ),
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "WARN"
    assert "multiplexer" in row["detail"]
    assert "unavailable" not in row["detail"]


def test_doctor_without_a_option_is_unchanged_and_never_touches_the_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """State — the health check is run without asking for host probing: the
    host section is absent entirely, and the transport is never invoked —
    asserted at the transport seam, never on printed output alone."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()

    def _boom(*args, **kwargs):
        raise AssertionError("plain `camp doctor` must never open a socket")

    monkeypatch.setattr(transport, "run_camp", _boom)

    code = _run(monkeypatch, ["doctor", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert "hosts" not in report
    assert set(report.keys()) == {"pass", "checks"}


def test_doctor_unreachable_host_never_changes_the_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The host section never contributes to the health check's exit
    status: the same local checks, run once with the host answering and
    once unreachable, produce the same exit code both times."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()

    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: _probe_answered(
            {"pass": True, "checks": [], "probe": True, "multiplexer_present": True}
        ),
    )
    code_answering = _run(monkeypatch, ["doctor", "-a", "--json"])
    capsys.readouterr()

    monkeypatch.setattr(
        transport, "run_camp", lambda host, remote_argv, **kw: transport.Unreachable(reason="x")
    )
    code_unreachable = _run(monkeypatch, ["doctor", "-a", "--json"])
    capsys.readouterr()

    assert code_answering == code_unreachable == 0


def test_doctor_json_composes_and_carries_host_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`camp doctor -a --json` composes rather than being refused, and the
    host findings are carried in the structured answer."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: _probe_answered(
            {"pass": True, "checks": [], "probe": True, "multiplexer_present": True}
        ),
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert isinstance(report["hosts"], list)
    assert any(h["host"] == "andromeda" for h in report["hosts"])
    assert isinstance(report["checks"], list)


def test_doctor_all_hosts_reachable_through_real_cli_short_and_long_form(
    tmp_path: Path,
) -> None:
    """`doctor` is reachable through the real `camp` CLI entry point under
    both spellings of the widen-to-every-machine option — the bundled short
    form `-a` and the long form `--all-hosts`."""
    import subprocess

    cli = _PLUGIN_DIR / "cli" / "camp"
    (tmp_path / "hosts.toml").write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "CAMP_CONFIG_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
            "WORKSPACE_ROOT": str(tmp_path / "workspace"),
            "CAMP_CANONICAL_ROOT": str(tmp_path / "canonical"),
            "CAMP_TEST_ASDF_PRESENT": "1",
            "CAMP_TEST_TMUX_PRESENT": "1",
        }
    )
    (tmp_path / "workspace").mkdir()
    (tmp_path / "canonical").mkdir()

    reports = []
    for flag in ("-a", "--all-hosts"):
        result = subprocess.run(
            [sys.executable, str(cli), "doctor", flag, "--json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        reports.append(json.loads(result.stdout))

    for report in reports:
        assert len(report["hosts"]) == 1
        assert report["hosts"][0]["verdict"] == "PASS"


def test_doctor_probe_decode_failure_renders_unavailable_and_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A probe answer that arrives but does not parse — truncated or
    corrupt — renders as the probe being unavailable, exactly as a rejected
    flag does, and never crashes the command or costs the operator the rest
    of the report."""
    _doctor_hosts_env(
        tmp_path, monkeypatch, "[hosts.andromeda]\n[hosts.lookout]\n"
    )
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kw):
        if host.ssh == "andromeda":
            return transport.Answered(stdout="not json{{{", stderr="", exit_code=0)
        return _probe_answered(
            {"pass": True, "checks": [], "probe": True, "multiplexer_present": True}
        )

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    corrupt_row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    fine_row = next(h for h in report["hosts"] if h["host"] == "lookout")
    assert corrupt_row["verdict"] == "WARN"
    assert "unavailable" in corrupt_row["detail"]
    assert fine_row["verdict"] == "PASS", "a decode failure on one host must not cost the rest"


def test_doctor_probe_unavailable_never_renders_as_no_multiplexer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A far camp that answers normally but carries no probe field (the
    shape an un-upgraded machine actually produces) renders as the probe
    being unavailable — never as a multiplexer being absent. These are
    distinct findings."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()
    # The exact shape an older camp gives: well-formed, successful, no
    # probe key at all — not even multiplexer_present.
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: _probe_answered({"pass": True, "checks": []}),
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "WARN"
    assert "unavailable" in row["detail"]
    assert "no multiplexer" not in row["detail"], (
        "an un-upgraded machine's silence must never be misread as a negative finding"
    )


def test_doctor_probe_row_shape_matches_the_far_sides_own_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The per-host row is built from the far side's own probe payload —
    proven by running `cmd_doctor`'s real `--probe` producer once to get its
    actual JSON, then feeding that exact string back through the `-a`
    consumer, rather than a second, hand-guessed shape."""
    import importlib

    spine = importlib.import_module("camp.spine")

    producer_env = {
        "HOME": str(tmp_path / "producer-home"),
        "CAMP_CONFIG_DIR": str(tmp_path / "producer-config"),
    }
    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            spine.cmd_doctor(["--json", "--probe"], env=producer_env)
        except SystemExit:
            pass
    producer_stdout = buf.getvalue()
    produced = json.loads(producer_stdout)
    assert produced[spine.DOCTOR_PROBE_KEY] is True
    assert produced[spine.DOCTOR_PROBE_MULTIPLEXER_KEY] is True

    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=producer_stdout, stderr="", exit_code=0
        ),
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# Defect A — a row reaching the doctor renderer from a path that did not
# build it (the fan-out's own internal-fault row) must be converted at the
# boundary, in both render paths, and the fault must be surfaced rather
# than discarded.
# ---------------------------------------------------------------------------


def test_doctor_fanout_internal_fault_renders_as_host_row_not_foreign_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A worker that raises (e.g. `ssh` absent from PATH) is caught by
    `camp.host.merge`'s fan-out and turned into its own `{"ok", "host",
    "reason"}` row — a shape the doctor host section never built. It must
    be converted to this section's `{"host", "verdict", "detail"}` shape,
    never passed through, and never cost the rest of the report."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n[hosts.lookout]\n")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kw):
        if host.ssh == "andromeda":
            raise FileNotFoundError(2, "No such file or directory: 'ssh'")
        return _probe_answered(
            {"pass": True, "checks": [], "probe": True, "multiplexer_present": True}
        )

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    out, err = capsys.readouterr()
    report = json.loads(out)
    assert code == 0
    fault_row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert set(fault_row) == {"host", "verdict", "detail"}, (
        "the foreign {'ok', 'reason'} shape must never reach the JSON output"
    )
    fine_row = next(h for h in report["hosts"] if h["host"] == "lookout")
    assert fine_row["verdict"] == "PASS", "one host's internal fault must not cost the rest"
    assert "andromeda" in err, "the internal fault must be surfaced, never silently discarded"


def test_doctor_fanout_internal_fault_never_crashes_the_human_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The same fault, through the human render path — this is where the
    real defect actually crashed: `camp doctor -a` died part-way through
    printing rather than finishing the report."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kw):
        raise FileNotFoundError(2, "No such file or directory: 'ssh'")

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["doctor", "-a"])
    out = capsys.readouterr().out
    assert code == 0
    assert "camp doctor — hosts:" in out
    assert "andromeda" in out


# ---------------------------------------------------------------------------
# Defect B — the outcome-to-verdict mapping, made total and explicit.
# ---------------------------------------------------------------------------


def test_doctor_stopped_responding_renders_warn_never_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A machine that connected and then stopped responding must never be
    reported with the "could not be reached at all" verdict — its own
    detail says it connected."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.StoppedResponding(execution_timeout=60.0),
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "WARN"
    assert "connected" in row["detail"]


def test_doctor_remote_refusal_with_unparseable_stdout_renders_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """ssh's own exit-255 catch-all — an unrecognized transport failure the
    transport classifies as `RemoteRefusal` — must never be read as an
    answer just because its stdout does not parse. Rendering it as "the
    probe is unavailable" would claim the machine answered when it did
    not, the collapse this whole slice exists to prevent."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.RemoteRefusal(
            stdout="", stderr="some unrecognized ssh failure", exit_code=255
        ),
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "DOWN"
    assert "unreachable" in row["detail"]


def test_doctor_remote_refusal_with_nonzero_exit_and_unparseable_stdout_renders_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A `RemoteRefusal` whose exit code is NOT ssh's own 255 catch-all means
    the remote command actually ran — a login shell writing a banner to
    stdout that will not parse as JSON is still an answering machine, never
    "could not be reached at all". Only an unrecognized 255 gets the DOWN
    verdict; every other exit code means camp (or the far shell) ran and
    the probe is simply unavailable on that machine."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.RemoteRefusal(
            stdout="a login banner, not JSON", stderr="", exit_code=17
        ),
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "WARN", (
        "exit code 17 means the remote command ran; rendering it as "
        "unreachable claims the machine never answered when it did"
    )
    assert "unavailable" in row["detail"]


def test_doctor_remote_refusal_with_parseable_stdout_reads_probe_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A `RemoteRefusal` whose stdout DOES parse — camp actually ran and
    exited nonzero from one of its own failed local checks — is read for
    probe availability exactly as an `Answered` outcome is: the exit code
    has nothing to do with whether the probe itself answered."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.RemoteRefusal(
            stdout=json.dumps({"pass": False, "checks": []}), stderr="", exit_code=1
        ),
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "WARN"
    assert "unavailable" in row["detail"]


def test_doctor_camp_not_resolvable_names_the_camp_bin_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The row names the actual configured camp_bin location that failed
    to run — not only the generic remedy — so the correction needs no
    construction."""
    _doctor_hosts_env(
        tmp_path, monkeypatch, '[hosts.andromeda]\ncamp_bin = "/opt/weird/camp"\n'
    )
    transport = _transport_module()
    monkeypatch.setattr(
        transport, "run_camp", lambda host, remote_argv, **kw: transport.CampNotResolvable()
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    row = next(h for h in report["hosts"] if h["host"] == "andromeda")
    assert row["verdict"] == "WARN"
    assert "/opt/weird/camp" in row["detail"]


# ---------------------------------------------------------------------------
# Defect C — a malformed connect_timeout must render as a finding, never a
# hard exit that starves the checks.
# ---------------------------------------------------------------------------


def test_doctor_malformed_connect_timeout_still_prints_every_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Doctor's own contract: a broken declaration is one failed check row
    among others, never a reason to exit before the rest run — the
    precedent the malformed self-name path already sets, extended to a
    malformed connect_timeout."""
    _doctor_hosts_env(tmp_path, monkeypatch, 'connect_timeout = "abc"\n[hosts.andromeda]\n')
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: _probe_answered(
            {"pass": True, "checks": [], "probe": True, "multiplexer_present": True}
        ),
    )

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1, "a malformed declaration is a failed check, not a refusal"
    check_names = [c["check"] for c in report["checks"]]
    assert "asdf" in check_names and "consistency" in check_names
    timeout_check = next(c for c in report["checks"] if c["check"] == "connect_timeout")
    assert timeout_check["pass"] is False
    assert len(report["hosts"]) == 2, "the host section still runs, using the transport's default"


# ---------------------------------------------------------------------------
# Defect D — the human render path, exercised at least once per verdict.
# ---------------------------------------------------------------------------


def test_doctor_human_render_grammar_pass_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The host section reuses the local check rows' own grammar: a
    bracketed verdict, the name, and an indented detail line beneath —
    this machine's own row labeled distinctly from a probed host's."""
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: _probe_answered(
            {"pass": True, "checks": [], "probe": True, "multiplexer_present": True}
        ),
    )

    code = _run(monkeypatch, ["doctor", "-a"])
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert "camp doctor — hosts:" in lines
    assert "  [PASS] (this machine)" in lines
    assert "  [PASS] andromeda" in lines
    assert "         camp resolves; multiplexer present" in lines


def test_doctor_human_render_grammar_warn_verdict_names_camp_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _doctor_hosts_env(
        tmp_path, monkeypatch, '[hosts.andromeda]\ncamp_bin = "/opt/weird/camp"\n'
    )
    transport = _transport_module()
    monkeypatch.setattr(
        transport, "run_camp", lambda host, remote_argv, **kw: transport.CampNotResolvable()
    )

    code = _run(monkeypatch, ["doctor", "-a"])
    out = capsys.readouterr().out
    assert code == 0
    assert "  [WARN] andromeda" in out.splitlines()
    assert "/opt/weird/camp" in out


def test_doctor_human_render_grammar_down_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _doctor_hosts_env(tmp_path, monkeypatch, "[hosts.andromeda]\n")
    transport = _transport_module()
    monkeypatch.setattr(
        transport, "run_camp", lambda host, remote_argv, **kw: transport.Unreachable(reason="x")
    )

    code = _run(monkeypatch, ["doctor", "-a"])
    out = capsys.readouterr().out
    assert code == 0
    assert "  [DOWN] andromeda" in out.splitlines()


# ---------------------------------------------------------------------------
# Defect E — the operator's declared connect_timeout actually reaches the
# probe's transport call, proven across two different declared values.
# ---------------------------------------------------------------------------


def test_doctor_all_hosts_threads_declared_connect_timeout_to_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _doctor_hosts_env(tmp_path, monkeypatch, "connect_timeout = 4\n[hosts.andromeda]\n")
    transport = _transport_module()
    seen: list[float] = []

    def fake_run_camp(host, remote_argv, **kw):
        seen.append(kw.get("connect_timeout"))
        return transport.Unreachable(reason="x")

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    capsys.readouterr()
    assert code == 0
    assert seen == [4.0]


def test_doctor_all_hosts_uses_a_different_declared_connect_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The second of the two points that change the answer — a different
    declared value causes the transport to be invoked with THAT value."""
    _doctor_hosts_env(tmp_path, monkeypatch, "connect_timeout = 8\n[hosts.andromeda]\n")
    transport = _transport_module()
    seen: list[float] = []

    def fake_run_camp(host, remote_argv, **kw):
        seen.append(kw.get("connect_timeout"))
        return transport.Unreachable(reason="x")

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    capsys.readouterr()
    assert code == 0
    assert seen == [8.0]


# ---------------------------------------------------------------------------
# Defect F — the shared connect_timeout refusal branch, pinned at the CLI
# level on at least one route.
# ---------------------------------------------------------------------------


def test_single_host_list_malformed_connect_timeout_refuses_naming_the_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The `--host` single-machine route (list/sessions/launch/kill's
    shared `_resolve_connect_timeout` refusal) names the verb and exits
    nonzero — pinned at the CLI level, not just at the underlying reader."""
    (tmp_path / "hosts.toml").write_text(
        'connect_timeout = "nope"\n[hosts.andromeda]\n', encoding="utf-8"
    )
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    err = capsys.readouterr().err
    assert code == 1
    assert "camp list:" in err
    assert "connect_timeout" in err


# ---------------------------------------------------------------------------
# Defect H2 — the doctor fan-out excludes this machine's own declared name.
# ---------------------------------------------------------------------------


def test_doctor_excludes_host_declared_under_this_machines_own_self_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A host file declaring this machine's own self_name must never
    produce two rows bearing the same name — one local, one probed over
    ssh — in a section whose whole purpose is telling machines apart."""
    _doctor_hosts_env(
        tmp_path, monkeypatch, 'self_name = "andromeda"\n[hosts.andromeda]\n'
    )
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    transport = _transport_module()

    def _boom(*args, **kwargs):
        raise AssertionError("this machine's own declared name must never be probed over ssh")

    monkeypatch.setattr(transport, "run_camp", _boom)

    code = _run(monkeypatch, ["doctor", "-a", "--json"])
    out, err = capsys.readouterr()
    report = json.loads(out)
    assert code == 0
    names = [h["host"] for h in report["hosts"]]
    assert names.count("andromeda") == 1
