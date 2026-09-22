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
import subprocess
import sys
from pathlib import Path

import pytest

from trailhead import cli, outpost_supervisor as osup
from trailhead.outpost_lifecycle import OutpostLifecycleError

from . import test_outpost_lifecycle as _lifecycle_tests
from .test_outpost_lifecycle import (
    _RecordingRunner,
    _health_reachable,
    _pid,
    _start,
    _wait_until,
    _write_supervisor_entry,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# The fake checkout + isolated config/state/HOME fixture the lifecycle verbs'
# tests use; enable/disable resolve the same entrypoint and state dir.
outpost = _lifecycle_tests.outpost

# The tiny in-process /health stand-in the supervised lifecycle tests use, so
# enable()'s inner stop() (driven through a supervised path here) has
# something real to probe and stop.
health_server = _lifecycle_tests.health_server


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
        which_runner=outpost.which_runner,
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
        which_runner=outpost.which_runner,
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
            which_runner=outpost.which_runner,
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
            which_runner=outpost.which_runner,
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
            which_runner=outpost.which_runner,
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
# enable — checked supervisor return codes
# ---------------------------------------------------------------------------


def test_enable_darwin_bootstrap_failure_raises_named_error_and_removes_the_entry(outpost):
    runner = _RecordingRunner(returncode_by_prefix={("launchctl", "bootstrap"): 1})

    with pytest.raises(OutpostLifecycleError, match="bootstrap"):
        osup.enable(
            env=outpost.env,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            which_runner=outpost.which_runner,
            uid=501,
        )

    # A failed bootstrap must not leave an entry file behind — is_enabled()
    # would otherwise report True for a job that never actually loaded.
    target = outpost.supervisor_dir / f"{osup.LAUNCHD_LABEL}.plist"
    assert not target.exists()
    assert osup.is_enabled(outpost.env, platform="darwin", supervisor_dir=outpost.supervisor_dir) is False


def test_enable_darwin_bootstrap_failure_error_names_start_and_says_daemon_is_down(outpost):
    # enable()'s own inner stop() already ran before this failure — whatever
    # was running (detached or supervised) is now stopped, and bootstrap
    # never registered a replacement. The operator is left with nothing
    # running at all; the error must say so and name the recovery command.
    runner = _RecordingRunner(returncode_by_prefix={("launchctl", "bootstrap"): 1})

    with pytest.raises(OutpostLifecycleError, match="trailhead outpost start") as exc:
        osup.enable(
            env=outpost.env,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            which_runner=outpost.which_runner,
            uid=501,
        )

    assert "stopped" in str(exc.value)
    assert "unregistered" in str(exc.value)


def test_enable_linux_enable_now_failure_raises_named_error_and_removes_the_entry(outpost):
    runner = _RecordingRunner(returncode_by_prefix={("systemctl", "--user", "enable"): 1})

    with pytest.raises(OutpostLifecycleError, match="enable"):
        osup.enable(
            env=outpost.env,
            platform="linux",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            which_runner=outpost.which_runner,
            user="alice",
        )

    target = outpost.supervisor_dir / osup.SYSTEMD_UNIT_NAME
    assert not target.exists()
    assert osup.is_enabled(outpost.env, platform="linux", supervisor_dir=outpost.supervisor_dir) is False


def test_enable_linux_enable_now_failure_deregisters_the_unit_before_removing_the_file(outpost):
    # `enable --now` failing can still leave the unit half-registered with
    # systemd (loaded, just not started) — the file alone being deleted
    # doesn't undo that registration. Clean it up with disable + daemon-reload
    # (best-effort: their own failure must not mask the real error) before
    # the unit file is removed.
    runner = _RecordingRunner(
        returncode_by_prefix={
            ("systemctl", "--user", "enable"): 1,
            ("systemctl", "--user", "disable"): 1,
        }
    )

    with pytest.raises(OutpostLifecycleError, match="enable"):
        osup.enable(
            env=outpost.env,
            platform="linux",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            which_runner=outpost.which_runner,
            user="alice",
        )

    prefixes = [call[:3] for call in runner.calls]
    enable_idx = prefixes.index(["systemctl", "--user", "enable"])
    # daemon-reload runs once before this enable() attempt (registering the
    # freshly written unit) and again as part of the failure cleanup — the
    # SECOND occurrence, after the failed enable, is the one this pin cares
    # about.
    reload_indices = [i for i, p in enumerate(prefixes) if p == ["systemctl", "--user", "daemon-reload"]]
    disable_idx = prefixes.index(["systemctl", "--user", "disable"])
    assert enable_idx < disable_idx
    assert len(reload_indices) == 2
    assert reload_indices[-1] > enable_idx
    target = outpost.supervisor_dir / osup.SYSTEMD_UNIT_NAME
    assert not target.exists()


# ---------------------------------------------------------------------------
# enable / disable — idempotence
# ---------------------------------------------------------------------------


def test_enable_twice_is_idempotent_same_runner_sequence_no_error(outpost):
    runner = _RecordingRunner()

    osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        port=outpost.port,
        runner=runner,
        which_runner=outpost.which_runner,
        uid=501,
    )
    osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        port=outpost.port,
        runner=runner,
        which_runner=outpost.which_runner,
        uid=501,
    )

    target = outpost.supervisor_dir / f"{osup.LAUNCHD_LABEL}.plist"
    assert target.exists()
    prefixes = [call[:2] for call in runner.calls]
    # The first enable() finds nothing registered yet, so its inner stop() is
    # a no-op unsupervised check (no runner calls) — just bootout+bootstrap.
    assert prefixes[:2] == [["launchctl", "bootout"], ["launchctl", "bootstrap"]]
    # The second enable() now finds the first one's entry already registered,
    # so its inner stop() drives the supervised path: a pre-stop probe, a
    # kill, then however many supervisor-state re-probes the settle window
    # takes, before this enable's own bootout+bootstrap.
    assert runner.calls[2][:2] == ["launchctl", "print"]
    assert runner.calls[3] == ["launchctl", "kill", "TERM", osup.launchd_service(501)]
    assert all(c[:2] == ["launchctl", "print"] for c in runner.calls[4:-2])
    assert prefixes[-2:] == [["launchctl", "bootout"], ["launchctl", "bootstrap"]]


def test_disable_removes_file_and_records_deregister_command(outpost):
    runner = _RecordingRunner()
    osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        which_runner=outpost.which_runner,
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


def test_enable_stops_a_running_detached_daemon_before_registering(outpost):
    _start(outpost)
    old_pid = _pid(outpost)
    assert _wait_until(lambda: _health_reachable(outpost.port), timeout=5.0)

    # Check the old pid's liveness AT THE MOMENT bootstrap runs, inside the
    # runner's own callback — not after enable() has already returned, which
    # would also pass if something else (unrelated to this ordering) killed
    # the daemon sometime before the assertion ran.
    observed_dead_at_bootstrap = {}

    def on_call(argv):
        if argv[:2] == ["launchctl", "bootstrap"]:
            try:
                os.kill(old_pid, 0)
            except ProcessLookupError:
                observed_dead_at_bootstrap["value"] = True
            else:
                observed_dead_at_bootstrap["value"] = False

    runner = _RecordingRunner(on_call=on_call)
    rc = osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        port=outpost.port,
        runner=runner,
        which_runner=outpost.which_runner,
        uid=501,
    )

    assert rc == 0
    assert observed_dead_at_bootstrap == {"value": True}
    assert not (outpost.state_dir / "outpost.pid").exists()
    # bootstrap ran after the daemon was already stopped.
    assert runner.calls[-1][:2] == ["launchctl", "bootstrap"]


def test_enable_over_already_registered_running_supervised_daemon_stops_through_injected_runner_first(
    outpost, health_server
):
    # enable()'s own inner stop() must be driven with the SAME platform /
    # supervisor_dir / runner / uid overrides enable() itself received — not
    # bare defaults — or it can't see (and drive) an already-registered
    # supervised daemon at all.
    _write_supervisor_entry(outpost, "darwin")
    health_server.start()

    def on_call(argv):
        if argv == ["launchctl", "kill", "TERM", osup.launchd_service(501)]:
            health_server.stop()

    runner = _RecordingRunner(on_call=on_call)

    rc = osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        port=outpost.port,
        runner=runner,
        which_runner=outpost.which_runner,
        uid=501,
    )

    assert rc == 0
    stop_idx = runner.calls.index(["launchctl", "kill", "TERM", osup.launchd_service(501)])
    bootstrap_idx = next(i for i, c in enumerate(runner.calls) if c[:2] == ["launchctl", "bootstrap"])
    assert stop_idx < bootstrap_idx


def test_enable_over_already_registered_daemon_succeeds_when_old_pid_lingers_past_settle_window(
    outpost, health_server
):
    # The same false-relaunch trap as the bare `stop` verb: `launchctl kill
    # TERM` only sends the signal, and the old process's pid can keep being
    # reported by `launchctl print`, unchanged, past /health going silent —
    # until an open SSE subscriber's own backstop closes it. enable()'s inner
    # stop() must not mistake that lingering old pid for a relaunch and abort
    # re-registration with the daemon left down and unregistered.
    _write_supervisor_entry(outpost, "darwin")
    health_server.start()

    def on_call(argv):
        if argv == ["launchctl", "kill", "TERM", osup.launchd_service(501)]:
            health_server.stop()

    runner = _RecordingRunner(
        on_call=on_call,
        stdout_by_prefix={("launchctl", "print"): "state = running\n\tpid = 9191\n"},
    )

    rc = osup.enable(
        env=outpost.env,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        port=outpost.port,
        runner=runner,
        which_runner=outpost.which_runner,
        uid=501,
    )

    assert rc == 0
    target = outpost.supervisor_dir / f"{osup.LAUNCHD_LABEL}.plist"
    assert target.exists()


def test_enable_linux_over_already_registered_running_daemon_stops_before_enable_now(
    outpost, health_server
):
    # Mirrors the darwin ordering pin: enable()'s inner stop() must actually
    # run (and complete) BEFORE the `enable --now` that (re)registers and
    # starts the unit, or the new unit can race an old running process for
    # the port.
    _write_supervisor_entry(outpost, "linux")
    health_server.start()

    def on_call(argv):
        if argv == ["systemctl", "--user", "stop", osup.SYSTEMD_UNIT_NAME]:
            health_server.stop()

    runner = _RecordingRunner(on_call=on_call)

    rc = osup.enable(
        env=outpost.env,
        platform="linux",
        supervisor_dir=outpost.supervisor_dir,
        port=outpost.port,
        runner=runner,
        which_runner=outpost.which_runner,
        user="alice",
    )

    assert rc == 0
    stop_idx = runner.calls.index(["systemctl", "--user", "stop", osup.SYSTEMD_UNIT_NAME])
    enable_now_idx = runner.calls.index(
        ["systemctl", "--user", "enable", "--now", osup.SYSTEMD_UNIT_NAME]
    )
    assert stop_idx < enable_now_idx


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
        port=outpost.port,
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
        port=outpost.port,
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
        which_runner=outpost.which_runner,
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
