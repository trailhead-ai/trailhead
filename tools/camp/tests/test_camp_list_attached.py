"""`camp list --json` reports `last_touched` and `attached` on every row of
every entry point.

Drives `camp.cli.dispatch.main()` in-process with a `tmux` stub on PATH (the
real `Tmux()` seam parses its `list-sessions` output) and `run_camp` injected,
so no real tmux server or SSH connection is touched.

Test contract:
- `-ag --json`'s local half carries `last_touched`; every entry point carries
  `attached` and `last_touched` for the same workspace.
- one attached client -> true, detached -> false, no session -> null, tmux not
  answering -> null on every row.
- a relayed row keeps what the remote sent; a relayed ok row missing the keys
  (an older camp) reads null — on `--host` and on `-ag` independently.
- an `ok: false` row keeps its shape on both.
- the human table is unchanged.
"""
from __future__ import annotations

import importlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_MANIFEST_MTIME = 1_600_000_000
_MANIFEST_ISO = "2020-09-13T12:26:40Z"
_TMUX_ACTIVITY = 1_700_000_000
_TMUX_ISO = "2023-11-14T22:13:20Z"

_ENTRY_POINTS = [
    pytest.param(["list", "--group", "testgrp", "--json"], id="list"),
    pytest.param(["list", "-g", "--json"], id="-g"),
    pytest.param(["list", "-a", "--group", "testgrp", "--json"], id="-a"),
    pytest.param(["list", "-ag", "--json"], id="-ag"),
]


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _transport_module():
    return importlib.import_module("camp.host.transport")


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        dispatch.main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def _answered(rows: list[dict]):
    return _transport_module().Answered(stdout=json.dumps(rows), stderr="", exit_code=0)


def _stub_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    bin_dir = tmp_path / "tmux-stub-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "tmux"
    stub.write_text("#!/usr/bin/env python3\nimport sys\n" + body, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def _stub_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sessions: dict[str, tuple[int, int]],
) -> None:
    """tmux answers `list-sessions` with *sessions*: name -> (activity,
    attached-client count), in tmux's `windows|activity|attached|name` shape."""
    lines = "".join(
        f"1|{activity}|{attached}|{name}\n" for name, (activity, attached) in sessions.items()
    )
    _stub_tmux(tmp_path, monkeypatch, f"sys.stdout.write({lines!r})\nsys.exit(0)\n")


def _stub_tmux_outage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_tmux(
        tmp_path,
        monkeypatch,
        'sys.stderr.write("directory /private/tmp/bad has unsafe permissions\\n")\nsys.exit(1)\n',
    )


def _seed(group_name: str, slug: str, *, env: dict) -> None:
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
    os.utime(mpath, (_MANIFEST_MTIME, _MANIFEST_MTIME))


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """One real group ("testgrp") with workspaces `ws-a` and `ws-b`, two
    declared hosts, config/state isolated in tmp_path; remote hosts answer
    an empty list unless a test re-rigs `run_camp`."""
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
    monkeypatch.setattr(
        _transport_module(), "run_camp", lambda host, remote_argv, **kw: _answered([])
    )
    _seed("testgrp", "ws-a", env=os.environ)
    _seed("testgrp", "ws-b", env=os.environ)
    return dict(os.environ)


def _local_rows(out: str) -> dict[str, dict]:
    return {r["slug"]: r for r in json.loads(out) if r.get("ok") and r.get("slug")}


def _session(slug: str) -> str:
    from camp.launch.naming import workspace_session_name

    return workspace_session_name("testgrp", slug)


@pytest.mark.parametrize("argv", _ENTRY_POINTS)
def test_every_entry_point_carries_last_touched_and_attached_for_the_same_workspace(
    argv, env, tmp_path, monkeypatch, capsys
) -> None:
    _stub_sessions(tmp_path, monkeypatch, {_session("ws-a"): (_TMUX_ACTIVITY, 1)})

    assert _run(monkeypatch, argv) == 0

    rows = _local_rows(capsys.readouterr().out)
    assert rows["ws-a"]["last_touched"] == _TMUX_ISO
    assert rows["ws-a"]["attached"] is True
    assert rows["ws-b"]["last_touched"] == _MANIFEST_ISO
    assert rows["ws-b"]["attached"] is None


@pytest.mark.parametrize("argv", _ENTRY_POINTS)
@pytest.mark.parametrize(
    "clients, expected", [(1, True), (3, True), (0, False)], ids=["one", "many", "detached"]
)
def test_attached_reflects_whether_a_client_is_attached(
    argv, clients, expected, env, tmp_path, monkeypatch, capsys
) -> None:
    _stub_sessions(tmp_path, monkeypatch, {_session("ws-a"): (_TMUX_ACTIVITY, clients)})

    assert _run(monkeypatch, argv) == 0

    assert _local_rows(capsys.readouterr().out)["ws-a"]["attached"] is expected


def test_a_workspace_with_no_session_reads_attached_null(
    env, tmp_path, monkeypatch, capsys
) -> None:
    _stub_sessions(tmp_path, monkeypatch, {_session("ws-a"): (_TMUX_ACTIVITY, 1)})

    _run(monkeypatch, ["list", "--group", "testgrp", "--json"])

    rows = _local_rows(capsys.readouterr().out)
    assert rows["ws-a"]["attached"] is True
    assert rows["ws-b"]["attached"] is None


@pytest.mark.parametrize("argv", _ENTRY_POINTS)
def test_tmux_not_answering_reads_attached_null_on_every_local_row(
    argv, env, tmp_path, monkeypatch, capsys
) -> None:
    _stub_tmux_outage(tmp_path, monkeypatch)

    _run(monkeypatch, argv)

    rows = _local_rows(capsys.readouterr().out)
    assert set(rows) == {"ws-a", "ws-b"}
    assert [r["attached"] for r in rows.values()] == [None, None]
    assert [r["last_touched"] for r in rows.values()] == [_MANIFEST_ISO, _MANIFEST_ISO]


def test_a_leftover_session_row_carries_attached(
    env, tmp_path, monkeypatch, capsys
) -> None:
    _stub_sessions(
        tmp_path,
        monkeypatch,
        {"camp-old-a1b2c3d4": (_TMUX_ACTIVITY, 1), "camp-older-b2c3d4e5": (_TMUX_ACTIVITY, 0)},
    )

    _run(monkeypatch, ["list", "-g", "--json"])

    rows = {r["tmux_session"]: r for r in json.loads(capsys.readouterr().out) if r.get("slug") is None}
    assert rows["camp-old-a1b2c3d4"]["attached"] is True
    assert rows["camp-older-b2c3d4e5"]["attached"] is False


# --- relayed rows ----------------------------------------------------------

_REMOTE_FULL = {
    "ok": True, "slug": "r-new", "branch": "", "workspace_path": "/r/new",
    "group": "testgrp", "state": "running", "window_count": 1,
    "tmux_session": "camp-testgrp-r-new", "last_touched": "2024-01-02T03:04:05Z",
    "attached": True,
}
_REMOTE_OLD = {
    "ok": True, "slug": "r-old", "branch": "", "workspace_path": "/r/old",
    "group": "testgrp", "state": "none", "window_count": 0,
    "tmux_session": "camp-testgrp-r-old",
}
_REMOTE_FAILURE = {"ok": False, "group": None, "reason": "bad config"}


def _remote(monkeypatch, rows: list[dict]) -> None:
    monkeypatch.setattr(
        _transport_module(), "run_camp", lambda host, remote_argv, **kw: _answered(rows)
    )


def test_host_json_keeps_what_the_remote_sent_and_nulls_what_an_older_camp_omitted(
    env, monkeypatch, capsys
) -> None:
    _remote(monkeypatch, [_REMOTE_FULL, _REMOTE_OLD])

    assert _run(monkeypatch, ["list", "--host", "andromeda", "--json"]) == 0

    rows = {r["slug"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["r-new"]["attached"] is True
    assert rows["r-new"]["last_touched"] == "2024-01-02T03:04:05Z"
    assert "attached" in rows["r-old"] and rows["r-old"]["attached"] is None
    assert "last_touched" in rows["r-old"] and rows["r-old"]["last_touched"] is None


def test_all_hosts_json_keeps_what_the_remote_sent_and_nulls_what_an_older_camp_omitted(
    env, monkeypatch, capsys
) -> None:
    _remote(monkeypatch, [_REMOTE_FULL, _REMOTE_OLD])

    assert _run(monkeypatch, ["list", "-ag", "--json"]) == 0

    remote = [r for r in json.loads(capsys.readouterr().out) if r.get("slug", "").startswith("r-")]
    by_slug = {}
    for r in remote:
        by_slug.setdefault(r["slug"], r)
    assert by_slug["r-new"]["attached"] is True
    assert by_slug["r-new"]["last_touched"] == "2024-01-02T03:04:05Z"
    assert "attached" in by_slug["r-old"] and by_slug["r-old"]["attached"] is None
    assert "last_touched" in by_slug["r-old"] and by_slug["r-old"]["last_touched"] is None


def test_host_json_relays_a_remote_failure_row_without_the_new_keys(
    env, monkeypatch, capsys
) -> None:
    _remote(monkeypatch, [_REMOTE_FAILURE])

    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    assert json.loads(capsys.readouterr().out)[0].keys() == _REMOTE_FAILURE.keys() | {"host"}


def test_host_json_unreachable_host_keeps_its_failure_shape(
    env, monkeypatch, capsys
) -> None:
    transport = _transport_module()
    monkeypatch.setattr(
        transport, "run_camp", lambda host, remote_argv, **kw: transport.Unreachable(reason="no route")
    )

    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert set(rows[0]) == {"ok", "host", "reason"}


def test_all_hosts_json_unreachable_host_keeps_its_failure_shape(
    env, monkeypatch, capsys
) -> None:
    transport = _transport_module()
    monkeypatch.setattr(
        transport, "run_camp", lambda host, remote_argv, **kw: transport.Unreachable(reason="no route")
    )

    _run(monkeypatch, ["list", "-ag", "--json"])

    failures = [r for r in json.loads(capsys.readouterr().out) if r.get("ok") is False]
    assert len(failures) == 2
    assert all(set(f) == {"ok", "host", "reason"} for f in failures)


# --- the human table -------------------------------------------------------


def test_human_table_does_not_surface_attached(env, tmp_path, monkeypatch, capsys) -> None:
    _stub_sessions(tmp_path, monkeypatch, {_session("ws-a"): (_TMUX_ACTIVITY, 1)})
    _run(monkeypatch, ["list", "--group", "testgrp"])
    attached_out = capsys.readouterr().out

    _stub_sessions(tmp_path, monkeypatch, {_session("ws-a"): (_TMUX_ACTIVITY, 0)})
    _run(monkeypatch, ["list", "--group", "testgrp"])
    detached_out = capsys.readouterr().out

    lines = attached_out.splitlines()
    assert lines[0].split() == ["WORKSPACE", "SESSIONS", "LAST", "TOUCHED"]
    assert attached_out == detached_out
    assert "True" not in attached_out and "attached" not in attached_out.lower()


def test_group_scoped_leftover_count_row_carries_the_attached_key(
    env, tmp_path, monkeypatch, capsys
) -> None:
    _stub_sessions(tmp_path, monkeypatch, {"camp-old-a1b2c3d4": (_TMUX_ACTIVITY, 1)})

    _run(monkeypatch, ["list", "--group", "testgrp", "--json"])

    count_rows = [r for r in json.loads(capsys.readouterr().out) if "unmanaged_count" in r]
    assert len(count_rows) == 1
    assert "attached" in count_rows[0] and count_rows[0]["attached"] is None
