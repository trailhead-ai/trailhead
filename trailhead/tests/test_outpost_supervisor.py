"""Behavioral tests for trailhead/outpost_supervisor.py — the
``trailhead outpost enable|disable`` verbs.

TDD: written before the implementation. Every test routes config/state through
the per-app override env vars (OUTPOST_CONFIG_DIR / OUTPOST_STATE_DIR) into
tmp_path, and injects platform + a supervisor directory under tmp_path, so
nothing touches a real ``~/Library/LaunchAgents`` or
``~/.config/systemd/user`` (Axiom 6).
"""

from __future__ import annotations

import configparser
import os
import plistlib
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from trailhead import cli, outpost_lifecycle, outpost_supervisor as osup
from trailhead.outpost_lifecycle import OutpostLifecycleError

_REPO_ROOT = Path(outpost_lifecycle.__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def outpost(tmp_path):
    checkout = tmp_path / "outpost-checkout"
    entry = checkout / "dist" / "server" / "index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fake entrypoint\n")

    config_home = tmp_path / "cfg"
    state_home = tmp_path / "state"
    config_home.mkdir()
    (config_home / "config.toml").write_text(f'checkout = "{checkout}"\n')

    env = {
        "OUTPOST_CONFIG_DIR": str(config_home),
        "OUTPOST_STATE_DIR": str(state_home),
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
    }
    ns = SimpleNamespace(
        env=env,
        checkout=checkout,
        entry=entry,
        config_home=config_home,
        state_dir=state_home,
        supervisor_dir=tmp_path / "supervisor",
    )
    yield ns

    # Teardown: never leak a daemon out of the test (mirrors
    # test_outpost_lifecycle.py's fixture).
    pidfile = state_home / "outpost.pid"
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
        except (ValueError, ProcessLookupError, OSError):
            pass


class _RecordingRunner:
    """Records every argv passed to it; returns a canned CompletedProcess."""

    def __init__(self, returncode_by_prefix: dict | None = None):
        self.calls: list = []
        self._returncode_by_prefix = returncode_by_prefix or {}

    def __call__(self, argv: list) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        returncode = 0
        for prefix, code in self._returncode_by_prefix.items():
            if argv[: len(prefix)] == list(prefix):
                returncode = code
        return subprocess.CompletedProcess(argv, returncode, stdout="", stderr="")


def _real_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


# ---------------------------------------------------------------------------
# Rendering — varies with its inputs
# ---------------------------------------------------------------------------


def _entry(**overrides) -> osup.SupervisorEntry:
    base = dict(
        entrypoint=Path("/checkout-a/dist/server/index.js"),
        checkout=Path("/checkout-a"),
        node_bin="/usr/local/bin/node",
        port=7313,
        log_path=Path("/state-a/outpost.log"),
        path_value="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    )
    base.update(overrides)
    return osup.SupervisorEntry(**base)


def test_render_plist_varies_with_its_inputs():
    entry_a = _entry()
    entry_b = _entry(
        entrypoint=Path("/checkout-b/dist/server/index.js"),
        checkout=Path("/checkout-b"),
        node_bin="/opt/homebrew/bin/node",
        port=9999,
        log_path=Path("/state-b/outpost.log"),
        path_value="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    )

    plist_a = plistlib.loads(osup.render_plist(entry_a))
    plist_b = plistlib.loads(osup.render_plist(entry_b))

    assert plist_a["ProgramArguments"] == ["/usr/local/bin/node", "/checkout-a/dist/server/index.js"]
    assert plist_b["ProgramArguments"] == ["/opt/homebrew/bin/node", "/checkout-b/dist/server/index.js"]
    assert plist_a["WorkingDirectory"] == "/checkout-a"
    assert plist_b["WorkingDirectory"] == "/checkout-b"
    assert plist_a["EnvironmentVariables"]["HTTP_PORT"] == "7313"
    assert plist_b["EnvironmentVariables"]["HTTP_PORT"] == "9999"
    assert plist_a["EnvironmentVariables"]["PATH"] != plist_b["EnvironmentVariables"]["PATH"]
    assert plist_a["StandardOutPath"] == "/state-a/outpost.log"
    assert plist_b["StandardOutPath"] == "/state-b/outpost.log"


def _parse_unit(text: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    cp.optionxform = str
    cp.read_string(text)
    return cp


def test_render_systemd_unit_varies_with_its_inputs():
    entry_a = _entry()
    entry_b = _entry(
        entrypoint=Path("/checkout-b/dist/server/index.js"),
        checkout=Path("/checkout-b"),
        node_bin="/opt/homebrew/bin/node",
        port=9999,
        log_path=Path("/state-b/outpost.log"),
        path_value="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    )

    unit_a = _parse_unit(osup.render_systemd_unit(entry_a))
    unit_b = _parse_unit(osup.render_systemd_unit(entry_b))

    assert unit_a["Service"]["WorkingDirectory"] == "/checkout-a"
    assert unit_b["Service"]["WorkingDirectory"] == "/checkout-b"
    assert unit_a["Service"]["Environment"] != unit_b["Service"]["Environment"]
    assert "HTTP_PORT=7313" in unit_a["Service"]["Environment"]
    assert "HTTP_PORT=9999" in unit_b["Service"]["Environment"]
    assert unit_a["Service"]["ExecStart"] != unit_b["Service"]["ExecStart"]


def test_restart_posture_pinned_once():
    """Seam smoke: the restart posture is a fixed decision, checked once."""
    entry = _entry()
    plist = plistlib.loads(osup.render_plist(entry))
    unit = _parse_unit(osup.render_systemd_unit(entry))

    assert plist["KeepAlive"]["SuccessfulExit"] is False
    assert plist["RunAtLoad"] is True
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Install"]["WantedBy"] == "default.target"


# ---------------------------------------------------------------------------
# enable — macOS
# ---------------------------------------------------------------------------


def test_enable_darwin_writes_plist_and_runs_bootout_then_bootstrap(outpost):
    runner = _RecordingRunner()

    rc = osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        which_runner=_real_which,
        uid=501,
    )

    assert rc == 0
    target = outpost.supervisor_dir / f"{osup.LAUNCHD_LABEL}.plist"
    assert target.exists()
    prefixes = [call[:2] for call in runner.calls]
    assert prefixes == [["launchctl", "bootout"], ["launchctl", "bootstrap"]]
    assert runner.calls[1][-1] == str(target)


# ---------------------------------------------------------------------------
# enable — Linux
# ---------------------------------------------------------------------------


def test_enable_linux_writes_unit_and_runs_reload_enable_linger_in_order(outpost):
    runner = _RecordingRunner()

    rc = osup.enable(
        env=outpost.env,
        platform="linux",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        which_runner=_real_which,
        user="alice",
    )

    assert rc == 0
    target = outpost.supervisor_dir / osup.SYSTEMD_UNIT_NAME
    assert target.exists()
    commands = [call[0] for call in runner.calls]
    assert commands == ["systemctl", "systemctl", "loginctl"]
    assert runner.calls[0] == ["systemctl", "--user", "daemon-reload"]
    assert runner.calls[1] == ["systemctl", "--user", "enable", "--now", osup.SYSTEMD_UNIT_NAME]
    assert runner.calls[2] == ["loginctl", "enable-linger", "alice"]


def test_enable_linux_linger_failure_raises_named_error_unit_stays_written(outpost):
    runner = _RecordingRunner(returncode_by_prefix={("loginctl", "enable-linger"): 1})

    with pytest.raises(OutpostLifecycleError, match="loginctl enable-linger bob"):
        osup.enable(
            env=outpost.env,
            platform="linux",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            which_runner=_real_which,
            user="bob",
        )

    target = outpost.supervisor_dir / osup.SYSTEMD_UNIT_NAME
    assert target.exists()


# ---------------------------------------------------------------------------
# enable — validation / refusal paths
# ---------------------------------------------------------------------------


def test_enable_unsupported_platform_raises_named_error_writes_and_runs_nothing(outpost):
    runner = _RecordingRunner()

    with pytest.raises(OutpostLifecycleError, match="win32"):
        osup.enable(
            env=outpost.env,
            platform="win32",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            which_runner=_real_which,
        )

    assert not outpost.supervisor_dir.exists() or list(outpost.supervisor_dir.iterdir()) == []
    assert runner.calls == []


def test_enable_missing_entrypoint_raises_named_error_writes_nothing(outpost):
    outpost.entry.unlink()
    runner = _RecordingRunner()

    with pytest.raises(OutpostLifecycleError):
        osup.enable(
            env=outpost.env,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            which_runner=_real_which,
            uid=501,
        )

    assert not outpost.supervisor_dir.exists() or list(outpost.supervisor_dir.iterdir()) == []
    assert runner.calls == []


def test_enable_missing_lore_on_path_raises_named_error_naming_lore_writes_nothing(outpost):
    runner = _RecordingRunner()

    def which_missing_lore(name: str) -> str | None:
        return {"node": "/usr/bin/node", "git": "/usr/bin/git"}.get(name)

    with pytest.raises(OutpostLifecycleError, match="lore"):
        osup.enable(
            env=outpost.env,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            which_runner=which_missing_lore,
            uid=501,
        )

    assert not outpost.supervisor_dir.exists() or list(outpost.supervisor_dir.iterdir()) == []
    assert runner.calls == []


# ---------------------------------------------------------------------------
# enable / disable — idempotence
# ---------------------------------------------------------------------------


def test_enable_twice_is_idempotent_same_runner_sequence_no_error(outpost):
    runner = _RecordingRunner()

    osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        which_runner=_real_which,
        uid=501,
    )
    osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        which_runner=_real_which,
        uid=501,
    )

    target = outpost.supervisor_dir / f"{osup.LAUNCHD_LABEL}.plist"
    assert target.exists()
    prefixes = [call[:2] for call in runner.calls]
    assert prefixes == [
        ["launchctl", "bootout"],
        ["launchctl", "bootstrap"],
        ["launchctl", "bootout"],
        ["launchctl", "bootstrap"],
    ]


def test_disable_removes_file_and_records_deregister_command(outpost):
    runner = _RecordingRunner()
    osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        which_runner=_real_which,
        uid=501,
    )
    target = outpost.supervisor_dir / f"{osup.LAUNCHD_LABEL}.plist"
    assert target.exists()

    disable_runner = _RecordingRunner()
    rc = osup.disable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=disable_runner,
        uid=501,
    )

    assert rc == 0
    assert not target.exists()
    assert disable_runner.calls == [["launchctl", "bootout", f"gui/501/{osup.LAUNCHD_LABEL}"]]


def test_disable_when_nothing_enabled_prints_not_enabled_runs_nothing(outpost, capsys):
    runner = _RecordingRunner()

    rc = osup.disable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
    )

    assert rc == 0
    assert runner.calls == []
    out = capsys.readouterr().out
    assert "not enabled" in out


# ---------------------------------------------------------------------------
# enable — stops a live detached daemon first
# ---------------------------------------------------------------------------


def _wait_until(pred, timeout: float, interval: float = 0.05) -> bool:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def _health_reachable(port: int) -> bool:
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as r:
            json.loads(r.read())
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _run_lifecycle_verb(o, call: str) -> subprocess.CompletedProcess:
    code = (
        "import sys\n"
        "from trailhead import outpost_lifecycle as ol\n"
        f"sys.exit(ol.{call})\n"
    )
    proc_env = {**o.env, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-c", code],
        env=proc_env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_enable_stops_a_running_detached_daemon_before_registering(outpost):
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    fake_daemon = """\
import http.server, json, os, signal, threading
port = int(os.environ["HTTP_PORT"])
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"ok": True, "contract_version": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass
server = http.server.HTTPServer(("127.0.0.1", port), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *a: stop.set())
stop.wait()
server.shutdown()
"""
    outpost.entry.write_text(fake_daemon)

    result = _run_lifecycle_verb(
        outpost, f"start(node_bin={sys.executable!r}, port={port})"
    )
    assert result.returncode == 0, result.stderr

    pidfile = outpost.state_dir / "outpost.pid"
    assert pidfile.exists()
    old_pid = int(pidfile.read_text().strip())
    assert _wait_until(lambda: _health_reachable(port), timeout=5.0)

    runner = _RecordingRunner()
    rc = osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        port=port,
        runner=runner,
        which_runner=_real_which,
        uid=501,
    )

    assert rc == 0
    assert not pidfile.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(old_pid, 0)
    # bootstrap ran after the daemon was already stopped.
    assert runner.calls[-1][:2] == ["launchctl", "bootstrap"]


# ---------------------------------------------------------------------------
# runner seam — pinned once
# ---------------------------------------------------------------------------


def test_default_runner_invokes_subprocess_run_with_list_argv_captured_text(monkeypatch):
    recorded = {}

    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = osup.default_runner(["echo", "hi"])

    assert recorded["argv"] == ["echo", "hi"]
    assert recorded["kwargs"].get("shell") is None or recorded["kwargs"].get("shell") is False
    assert recorded["kwargs"]["capture_output"] is True
    assert recorded["kwargs"]["text"] is True
    assert result.stdout == "ok"


# ---------------------------------------------------------------------------
# PATH composition
# ---------------------------------------------------------------------------


def test_composed_path_uses_resolved_binaries_dirs_plus_system_dirs_not_shell_path(outpost):
    runner = _RecordingRunner()

    def which_a(name: str) -> str | None:
        return {"node": "/opt/a/node", "git": "/opt/a/git", "lore": "/opt/a/lore"}.get(name)

    osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        which_runner=which_a,
        uid=501,
    )
    plist_a = plistlib.loads((outpost.supervisor_dir / f"{osup.LAUNCHD_LABEL}.plist").read_bytes())
    path_a = plist_a["EnvironmentVariables"]["PATH"]

    def which_b(name: str) -> str | None:
        return {"node": "/opt/b/node", "git": "/opt/b/git", "lore": "/opt/b/lore"}.get(name)

    osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        which_runner=which_b,
        uid=501,
    )
    plist_b = plistlib.loads((outpost.supervisor_dir / f"{osup.LAUNCHD_LABEL}.plist").read_bytes())
    path_b = plist_b["EnvironmentVariables"]["PATH"]

    assert path_a != path_b
    assert "/opt/a" in path_a and "/opt/a" not in path_b
    assert "/opt/b" in path_b and "/opt/b" not in path_a
    # a shell PATH entry that holds none of the three binaries never appears
    assert "/some/unrelated/shell/dir" not in path_a
    assert "/usr/bin" in path_a  # system dirs are still present


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------


def test_is_enabled_true_after_enable_false_after_disable(outpost):
    assert osup.is_enabled(outpost.env, platform="darwin", supervisor_dir=outpost.supervisor_dir) is False

    runner = _RecordingRunner()
    osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        which_runner=_real_which,
        uid=501,
    )
    assert osup.is_enabled(outpost.env, platform="darwin", supervisor_dir=outpost.supervisor_dir) is True

    osup.disable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
    )
    assert osup.is_enabled(outpost.env, platform="darwin", supervisor_dir=outpost.supervisor_dir) is False


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_outpost_enable_disable_verbs_parse():
    parser = cli._build_parser()
    for verb in ("enable", "disable"):
        args = parser.parse_args(["outpost", verb])
        assert args.command == "outpost"
        assert args.outpost_command == verb


def test_cli_dispatches_enable_and_disable_to_the_supervisor_module(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "supervisor_enable", lambda: calls.append("enable") or 0)
    monkeypatch.setattr(cli, "supervisor_disable", lambda: calls.append("disable") or 0)
    monkeypatch.setattr(sys, "argv", ["trailhead", "outpost", "enable"])
    assert cli.main() == 0
    monkeypatch.setattr(sys, "argv", ["trailhead", "outpost", "disable"])
    assert cli.main() == 0

    assert calls == ["enable", "disable"]
