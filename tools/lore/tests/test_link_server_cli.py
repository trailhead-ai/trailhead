"""Slice 2 tests: `lore link-server <install|uninstall|status>`.

Two surfaces under test:

1. The pure / unit-testable module ``scripts/link_server.py``:
   - ``render_plist`` — pure function returning the launchd plist XML.
   - State-file I/O (``write_state`` / ``read_state`` / ``clear_state``) against
     an injected state dir.
   - ``port_in_use`` — bind probe.

2. The CLI wiring (``cmd_link_server`` + the ``link-server`` subparser) in
   ``cli/lore``, exercised in-process with the launchctl subprocess + platform
   checks + ``port_in_use`` monkeypatched so NO writes hit real ``~/Library`` and
   NO real ``launchctl`` runs.

Hermetic: ``--launch-agents-dir`` and ``--state-dir`` are redirected to tmp; the
launchctl side effect is captured, never executed.
"""
from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from conftest import CLI_PATH, load_script


def load_cli():
    """Load the extension-less cli/lore as an importable module."""
    loader = SourceFileLoader("lore_cli", str(CLI_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run_cli(args, env=None, input_text=None):
    """Subprocess invocation — used only for parser-wiring (--help) tests."""
    import os

    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, input=input_text,
    )


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------

def test_link_server_help_lists_actions():
    """`lore link-server --help` exits 0 and lists install/uninstall/status."""
    r = run_cli(["link-server", "--help"])
    assert r.returncode == 0, r.stderr
    out = r.stdout.lower()
    assert "install" in out
    assert "uninstall" in out
    assert "status" in out


# ---------------------------------------------------------------------------
# render_plist — pure function
# ---------------------------------------------------------------------------

def test_render_plist_contains_label_args_env_and_keys():
    ls = load_script("link_server")
    xml = ls.render_plist(
        label="com.lore.link-server",
        python="/usr/bin/python3",
        server_script="/p/bin/lore-link-server",
        vault="/tmp/v",
        port=7777,
        out_log="/tmp/lore-link-server.out.log",
        err_log="/tmp/lore-link-server.err.log",
    )
    assert "com.lore.link-server" in xml
    # Both ProgramArguments present.
    assert "/usr/bin/python3" in xml
    assert "/p/bin/lore-link-server" in xml
    # EnvironmentVariables carry vault + port.
    assert "LORE_VAULT" in xml
    assert "/tmp/v" in xml
    assert "LORE_LINK_PORT" in xml
    assert "7777" in xml
    # Behavioral keys.
    assert "RunAtLoad" in xml
    assert "KeepAlive" in xml
    # Log paths.
    assert "/tmp/lore-link-server.out.log" in xml
    assert "/tmp/lore-link-server.err.log" in xml


def test_render_plist_is_pure_no_side_effects(tmp_path):
    """render_plist returns a string and writes nothing."""
    ls = load_script("link_server")
    before = sorted(p.name for p in tmp_path.iterdir())
    out = ls.render_plist(
        label="com.lore.link-server",
        python="/usr/bin/python3",
        server_script="/p/bin/lore-link-server",
        vault="/tmp/v",
        port=7777,
        out_log="/tmp/a.log",
        err_log="/tmp/b.log",
    )
    assert isinstance(out, str)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_render_plist_escapes_xml_metacharacters():
    """A vault path containing & / < / > yields well-formed XML that parses and
    round-trips the literal value (a raw `&` would break `launchctl load`)."""
    from xml.dom.minidom import parseString

    ls = load_script("link_server")
    xml = ls.render_plist(
        label="com.lore.link-server",
        python="/usr/bin/python3",
        server_script="/p/bin/lore-link-server",
        vault="/tmp/a & b/<v>",
        port=7777,
        out_log="/tmp/a.log",
        err_log="/tmp/b.log",
    )
    # A raw ampersand would make parseString raise ExpatError.
    parsed = parseString(xml)
    # The literal value survives unescaping by the parser.
    found = [
        n.firstChild.data
        for n in parsed.getElementsByTagName("string")
        if n.firstChild and "a & b" in n.firstChild.data
    ]
    assert found == ["/tmp/a & b/<v>"]
    # The escaped form is what's actually in the serialized XML.
    assert "&amp;" in xml
    assert "&lt;v&gt;" in xml


# ---------------------------------------------------------------------------
# State-file I/O
# ---------------------------------------------------------------------------

def test_state_roundtrip_and_schema(tmp_path):
    ls = load_script("link_server")
    state_dir = tmp_path / "config"
    payload = {
        "schema": 1,
        "port": 7777,
        "plist_path": "/x/com.lore.link-server.plist",
        "vault": "/tmp/v",
        "plugin_root": "/p",
    }
    ls.write_state(state_dir, payload)
    on_disk = state_dir / "link-server.json"
    assert on_disk.is_file()
    read_back = ls.read_state(state_dir)
    assert read_back["schema"] == 1
    assert read_back == payload


def test_read_state_missing_returns_none(tmp_path):
    ls = load_script("link_server")
    assert ls.read_state(tmp_path / "nope") is None


def test_read_state_malformed_json_returns_none(tmp_path):
    """A truncated/garbage state file degrades to None, never raises."""
    ls = load_script("link_server")
    state_dir = tmp_path / "config"
    state_dir.mkdir()
    (state_dir / "link-server.json").write_text('{"schema": 1, "port":')  # truncated
    assert ls.read_state(state_dir) is None


def test_read_state_unknown_schema_returns_none(tmp_path):
    """A state file carrying an unrecognized schema is treated as absent."""
    ls = load_script("link_server")
    state_dir = tmp_path / "config"
    ls.write_state(state_dir, {"schema": 999, "port": 7777})
    assert ls.read_state(state_dir) is None


def test_read_state_non_dict_returns_none(tmp_path):
    """A well-formed-but-non-object JSON body degrades to None."""
    ls = load_script("link_server")
    state_dir = tmp_path / "config"
    state_dir.mkdir()
    (state_dir / "link-server.json").write_text("[1, 2, 3]")
    assert ls.read_state(state_dir) is None


def test_clear_state_removes_file(tmp_path):
    ls = load_script("link_server")
    state_dir = tmp_path / "config"
    ls.write_state(state_dir, {"schema": 1, "port": 7777})
    assert (state_dir / "link-server.json").is_file()
    ls.clear_state(state_dir)
    assert not (state_dir / "link-server.json").exists()
    # Idempotent — clearing again is safe.
    ls.clear_state(state_dir)


# ---------------------------------------------------------------------------
# port_in_use
# ---------------------------------------------------------------------------

def test_port_in_use_true_when_bound():
    ls = load_script("link_server")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert ls.port_in_use(port) is True
    finally:
        s.close()


def test_port_in_use_false_when_free():
    ls = load_script("link_server")
    # Grab a free port then release it, so it's (almost certainly) free.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert ls.port_in_use(port) is False


# ---------------------------------------------------------------------------
# CLI: install — happy path + guards (in-process, hermetic)
# ---------------------------------------------------------------------------

def _install_args(cli, tmp_path, *, port=7777, vault="/tmp/v"):
    parser = cli.build_parser()
    return parser.parse_args([
        "link-server", "install",
        "--launch-agents-dir", str(tmp_path / "LaunchAgents"),
        "--state-dir", str(tmp_path / "config"),
    ])


def _patch_launchctl(monkeypatch, cli, record):
    """Capture launchctl invocations instead of running them."""
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        if cmd and cmd[0] == "launchctl":
            record.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)


def test_install_happy_path_writes_plist_and_state(tmp_path, monkeypatch):
    cli = load_cli()
    record = []
    _patch_launchctl(monkeypatch, cli, record)
    monkeypatch.setattr(cli, "_link_server_guards_ok", lambda: (True, ""))
    monkeypatch.setattr(cli.link_server, "port_in_use", lambda port: False)

    args = _install_args(cli, tmp_path, port=7777, vault="/tmp/v")
    monkeypatch.setenv("LORE_VAULT", "/tmp/v")
    monkeypatch.setenv("LORE_LINK_PORT", "7777")

    rc = cli.cmd_link_server(args)
    assert rc == 0

    plist = tmp_path / "LaunchAgents" / "com.lore.link-server.plist"
    assert plist.is_file()
    xml = plist.read_text()
    assert "com.lore.link-server" in xml
    assert "/tmp/v" in xml

    ls = load_script("link_server")
    vault = load_script("vault")
    state = ls.read_state(tmp_path / "config")
    assert state["schema"] == 1
    assert state["port"] == 7777
    # vault is the resolved real path (macOS symlinks /tmp -> /private/tmp).
    assert state["vault"] == vault.resolve_vault()
    assert state["plist_path"] == str(plist)
    assert state["plugin_root"]  # non-empty
    # launchctl load -w was invoked.
    assert any("load" in c for c in record)


def test_install_refuses_non_darwin(tmp_path, monkeypatch):
    cli = load_cli()
    record = []
    _patch_launchctl(monkeypatch, cli, record)
    monkeypatch.setattr(cli.link_server, "port_in_use", lambda port: False)
    monkeypatch.setattr(cli.sys, "platform", "linux", raising=False)

    args = _install_args(cli, tmp_path)
    rc = cli.cmd_link_server(args)
    assert rc != 0
    assert not (tmp_path / "LaunchAgents" / "com.lore.link-server.plist").exists()
    assert not (tmp_path / "config" / "link-server.json").exists()
    # launchctl never invoked.
    assert record == []


def test_install_refuses_when_port_in_use(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    record = []
    _patch_launchctl(monkeypatch, cli, record)
    monkeypatch.setattr(cli, "_link_server_guards_ok", lambda: (True, ""))
    monkeypatch.setattr(cli.link_server, "port_in_use", lambda port: True)

    args = _install_args(cli, tmp_path, port=7777)
    rc = cli.cmd_link_server(args)
    assert rc != 0
    out = capsys.readouterr()
    msg = (out.out + out.err).lower()
    assert "port" in msg and "in use" in msg
    # Actionable recovery guidance is present even without a named culprit.
    assert "launchctl" in msg
    assert not (tmp_path / "LaunchAgents" / "com.lore.link-server.plist").exists()
    assert not (tmp_path / "config" / "link-server.json").exists()
    assert record == []


def test_install_port_in_use_names_old_agent_from_env(tmp_path, monkeypatch, capsys):
    """$LORE_LINK_OLD_AGENT lets the operator inject the legacy agent label so the
    refusal message names the likely culprit + the exact unload command."""
    cli = load_cli()
    record = []
    _patch_launchctl(monkeypatch, cli, record)
    monkeypatch.setattr(cli, "_link_server_guards_ok", lambda: (True, ""))
    monkeypatch.setattr(cli.link_server, "port_in_use", lambda port: True)
    monkeypatch.setenv("LORE_LINK_OLD_AGENT", "com.example.old-link-server")

    rc = cli.cmd_link_server(_install_args(cli, tmp_path, port=7777))
    assert rc != 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "com.example.old-link-server" in combined
    assert "launchctl unload -w" in combined
    assert "com.example.old-link-server.plist" in combined


def test_install_idempotent_same_vault_port(tmp_path, monkeypatch):
    cli = load_cli()
    record = []
    _patch_launchctl(monkeypatch, cli, record)
    monkeypatch.setattr(cli, "_link_server_guards_ok", lambda: (True, ""))
    monkeypatch.setattr(cli.link_server, "port_in_use", lambda port: False)
    monkeypatch.setenv("LORE_VAULT", "/tmp/v")
    monkeypatch.setenv("LORE_LINK_PORT", "7777")

    args = _install_args(cli, tmp_path)
    assert cli.cmd_link_server(args) == 0
    ls = load_script("link_server")
    first = ls.read_state(tmp_path / "config")

    # Re-run with identical vault+port → no-op, state unchanged.
    args2 = _install_args(cli, tmp_path)
    assert cli.cmd_link_server(args2) == 0
    second = ls.read_state(tmp_path / "config")
    assert first == second


def test_install_changed_port_rewrites_state(tmp_path, monkeypatch):
    cli = load_cli()
    record = []
    _patch_launchctl(monkeypatch, cli, record)
    monkeypatch.setattr(cli, "_link_server_guards_ok", lambda: (True, ""))
    monkeypatch.setattr(cli.link_server, "port_in_use", lambda port: False)

    monkeypatch.setenv("LORE_VAULT", "/tmp/v")
    monkeypatch.setenv("LORE_LINK_PORT", "7777")
    assert cli.cmd_link_server(_install_args(cli, tmp_path)) == 0

    monkeypatch.setenv("LORE_LINK_PORT", "8123")
    assert cli.cmd_link_server(_install_args(cli, tmp_path)) == 0

    ls = load_script("link_server")
    state = ls.read_state(tmp_path / "config")
    assert state["port"] == 8123
    plist = (tmp_path / "LaunchAgents" / "com.lore.link-server.plist").read_text()
    assert "8123" in plist
    assert "7777" not in plist  # no stale port baked in


def test_install_changed_vault_rewrites_state(tmp_path, monkeypatch):
    """Re-running install with a different vault (same port) re-renders the plist
    and rewrites the state file — no stale vault left in either."""
    cli = load_cli()
    record = []
    _patch_launchctl(monkeypatch, cli, record)
    monkeypatch.setattr(cli, "_link_server_guards_ok", lambda: (True, ""))
    monkeypatch.setattr(cli.link_server, "port_in_use", lambda port: False)

    (tmp_path / "v1").mkdir()
    (tmp_path / "v2").mkdir()
    monkeypatch.setenv("LORE_LINK_PORT", "7777")

    monkeypatch.setenv("LORE_VAULT", str(tmp_path / "v1"))
    assert cli.cmd_link_server(_install_args(cli, tmp_path)) == 0

    monkeypatch.setenv("LORE_VAULT", str(tmp_path / "v2"))
    assert cli.cmd_link_server(_install_args(cli, tmp_path)) == 0

    ls = load_script("link_server")
    state = ls.read_state(tmp_path / "config")
    assert state["vault"] == str(tmp_path / "v2")
    plist = (tmp_path / "LaunchAgents" / "com.lore.link-server.plist").read_text()
    assert str(tmp_path / "v2") in plist
    assert str(tmp_path / "v1") not in plist  # no stale vault baked in


def test_install_load_failure_writes_no_state(tmp_path, monkeypatch):
    """When `launchctl load` returns non-zero, install exits 1 and leaves no
    state file (the no-partial-install invariant on the load-failure path)."""
    cli = load_cli()
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        if cmd and cmd[0] == "launchctl":
            rc = 1 if "load" in cmd else 0
            return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="load boom")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "_link_server_guards_ok", lambda: (True, ""))
    monkeypatch.setattr(cli.link_server, "port_in_use", lambda port: False)
    monkeypatch.setenv("LORE_VAULT", "/tmp/v")
    monkeypatch.setenv("LORE_LINK_PORT", "7777")

    rc = cli.cmd_link_server(_install_args(cli, tmp_path))
    assert rc == 1
    assert not (tmp_path / "config" / "link-server.json").exists()


# ---------------------------------------------------------------------------
# CLI: uninstall
# ---------------------------------------------------------------------------

def test_uninstall_removes_plist_and_state(tmp_path, monkeypatch):
    cli = load_cli()
    record = []
    _patch_launchctl(monkeypatch, cli, record)
    monkeypatch.setattr(cli, "_link_server_guards_ok", lambda: (True, ""))
    monkeypatch.setattr(cli.link_server, "port_in_use", lambda port: False)
    monkeypatch.setenv("LORE_VAULT", "/tmp/v")
    monkeypatch.setenv("LORE_LINK_PORT", "7777")

    assert cli.cmd_link_server(_install_args(cli, tmp_path)) == 0
    plist = tmp_path / "LaunchAgents" / "com.lore.link-server.plist"
    assert plist.is_file()

    parser = cli.build_parser()
    uninstall = parser.parse_args([
        "link-server", "uninstall",
        "--launch-agents-dir", str(tmp_path / "LaunchAgents"),
        "--state-dir", str(tmp_path / "config"),
    ])
    assert cli.cmd_link_server(uninstall) == 0
    assert not plist.exists()
    assert not (tmp_path / "config" / "link-server.json").exists()
    assert any("unload" in c for c in record)


def test_uninstall_safe_when_nothing_installed(tmp_path, monkeypatch):
    cli = load_cli()
    record = []
    _patch_launchctl(monkeypatch, cli, record)
    parser = cli.build_parser()
    uninstall = parser.parse_args([
        "link-server", "uninstall",
        "--launch-agents-dir", str(tmp_path / "LaunchAgents"),
        "--state-dir", str(tmp_path / "config"),
    ])
    assert cli.cmd_link_server(uninstall) == 0


# ---------------------------------------------------------------------------
# CLI: status truthfulness
# ---------------------------------------------------------------------------

def _status_args(cli, tmp_path):
    parser = cli.build_parser()
    return parser.parse_args([
        "link-server", "status",
        "--launch-agents-dir", str(tmp_path / "LaunchAgents"),
        "--state-dir", str(tmp_path / "config"),
    ])


def _seed_state(tmp_path, *, plugin_root, port=7777, vault="/tmp/v"):
    ls = load_script("link_server")
    plist = tmp_path / "LaunchAgents" / "com.lore.link-server.plist"
    ls.write_state(tmp_path / "config", {
        "schema": 1,
        "port": port,
        "plist_path": str(plist),
        "vault": vault,
        "plugin_root": plugin_root,
    })


def test_status_not_installed(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    rc = cli.cmd_link_server(_status_args(cli, tmp_path))
    assert rc == 0
    out = capsys.readouterr()
    assert "not installed" in (out.out + out.err).lower()


def test_status_running(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    _seed_state(tmp_path, plugin_root=str(cli.PLUGIN_ROOT))
    monkeypatch.setattr(cli, "_agent_running", lambda label: True)

    rc = cli.cmd_link_server(_status_args(cli, tmp_path))
    assert rc == 0
    out = capsys.readouterr()
    assert "running" in (out.out + out.err).lower()


def test_status_state_present_agent_not_running(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    _seed_state(tmp_path, plugin_root=str(cli.PLUGIN_ROOT))
    monkeypatch.setattr(cli, "_agent_running", lambda label: False)

    rc = cli.cmd_link_server(_status_args(cli, tmp_path))
    assert rc != 0
    out = capsys.readouterr()
    msg = (out.out + out.err).lower()
    assert "not running" in msg or "dead" in msg


def test_status_stale_plugin_root(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    _seed_state(tmp_path, plugin_root="/some/old/path/plugins/lore")
    monkeypatch.setattr(cli, "_agent_running", lambda label: True)

    rc = cli.cmd_link_server(_status_args(cli, tmp_path))
    assert rc != 0
    out = capsys.readouterr()
    assert "stale" in (out.out + out.err).lower()


# ---------------------------------------------------------------------------
# _agent_running — actual launchctl list parsing (mocked at the subprocess
# boundary so the substring scan itself is exercised, not stubbed wholesale).
# ---------------------------------------------------------------------------

def test_agent_running_true_when_label_in_list(monkeypatch):
    cli = load_cli()
    stdout = (
        "PID\tStatus\tLabel\n"
        "123\t0\tcom.apple.something\n"
        "456\t0\tcom.lore.link-server\n"
    )

    def fake_run(cmd, *a, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._agent_running("com.lore.link-server") is True


def test_agent_running_false_when_label_absent(monkeypatch):
    cli = load_cli()
    stdout = (
        "PID\tStatus\tLabel\n"
        "123\t0\tcom.apple.something\n"
        "456\t0\tcom.other.agent\n"
    )

    def fake_run(cmd, *a, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._agent_running("com.lore.link-server") is False


def test_agent_running_false_on_nonzero_returncode(monkeypatch):
    cli = load_cli()

    def fake_run(cmd, *a, **kw):
        # Non-zero rc even though the label happens to appear in stderr.
        return subprocess.CompletedProcess(
            cmd, 1, stdout="com.lore.link-server\n", stderr="boom"
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._agent_running("com.lore.link-server") is False
