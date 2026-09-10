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
# Human rendering — grouped by machine, local first, declared order.
# ---------------------------------------------------------------------------


def test_human_output_grouped_by_machine_local_first_then_declared_order(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
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
    assert lines[1] == "andromeda"
    assert lines[2] == "  remoteA /r/a"
    assert lines[3] == "lookout"
    assert lines[4].strip() != ""  # the failure line lookout owes
    assert "remoteA" not in lines[4]


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
# Backward compatibility — the plain single-machine surface is unchanged.
# ---------------------------------------------------------------------------


def test_plain_list_json_still_carries_no_host_key(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    def _boom(*args, **kwargs):
        raise AssertionError("a plain camp list must never contact the transport")

    monkeypatch.setattr(transport, "run_camp", _boom)

    code = _run(monkeypatch, ["list", "--group", "testgrp", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    assert json.loads(out) == []


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


def test_sessions_human_output_renders_answered_rows_under_their_machine(
    hosts_and_group_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

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
