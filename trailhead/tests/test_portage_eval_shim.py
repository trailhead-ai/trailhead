"""The pinned ``portage-eval`` shim: built beside ``outpost-gateway`` when portage's CLI is enabled."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from trailhead.install import build_cli_shims, resolve_cli_tools
from trailhead.pathint import PathIntegrationError, resolve_eval_runner, resolve_shim_dir


def _tools() -> dict[str, Path]:
    return resolve_cli_tools({"camp": True, "lore": True, "portage": True})


def _stub_runner(tmp_path: Path, mode: int = 0o755) -> Path:
    bindir = tmp_path / "runner-bin"
    real = tmp_path / "claude-2.1.999"
    if not bindir.exists():
        bindir.mkdir()
        real.write_text("#!/bin/sh\nexit 0\n")
        (bindir / "claude").symlink_to(real)
    real.chmod(mode)
    return bindir


def _env(tmp_path: Path, bindir: Path | None = None) -> dict[str, str]:
    bindir = bindir or _stub_runner(tmp_path)
    return {
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "PATH": f"{bindir}:/usr/bin:/bin",
    }


def _seen_by_interpreter(tmp_path, monkeypatch, shim_name, session_env, args=("start",)):
    dump = tmp_path / f"seen-{shim_name}"
    fake = tmp_path / "fake-python"
    fake.write_text(f'#!/bin/sh\n/usr/bin/env > "{dump}"\n')
    fake.chmod(0o755)
    monkeypatch.setattr("trailhead.pathint.sys.executable", str(fake))
    env = _env(tmp_path)
    build_cli_shims(_tools(), env=env, outpost_enabled=True)
    proc = subprocess.run(
        [str(resolve_shim_dir(env=env) / shim_name), *args],
        capture_output=True, text=True, env=session_env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line)


SESSION = {
    "HOME": "/home/x",
    "PATH": "/usr/bin:/bin",
    "SANDBOX_RUNTIME": "1",
    "LD_PRELOAD": "/nonexistent/evil.so",
    "SOME_SESSION_VARIABLE": "x",
    "XDG_CACHE_HOME": "/home/x/.cache",
    "USER": "tester",
    "LANG": "en_US.UTF-8",
    "TERM": "xterm",
    "LC_ALL": "C",
    "LC_TIME": "C",
}


class TestBuild:
    def test_portage_enabled_writes_a_portage_eval_shim_that_refuses_a_verb_other_than_start_or_wait(
            self, tmp_path):
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env)
        shim = resolve_shim_dir(env=env) / "portage-eval"
        for verb in ("status", "--help", "eval"):
            proc = subprocess.run([str(shim), verb], capture_output=True, text=True,
                                  env={"HOME": env["HOME"], "PATH": "/usr/bin:/bin"}, timeout=60)
            assert proc.returncode == 2, (verb, proc.stdout, proc.stderr)
            assert "start" in proc.stderr and "wait" in proc.stderr
        none = subprocess.run([str(shim)], capture_output=True, text=True,
                              env={"HOME": env["HOME"], "PATH": "/usr/bin:/bin"}, timeout=60)
        assert none.returncode == 2

    def test_the_shim_accepts_start_and_prefixes_eval_itself(self, tmp_path):
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env)
        proc = subprocess.run([str(resolve_shim_dir(env=env) / "portage-eval"), "wait", "bogus"],
                              capture_output=True, text=True,
                              env={"HOME": env["HOME"], "PATH": "/usr/bin:/bin"}, timeout=60)
        assert proc.returncode == 2
        assert "eval wait" in proc.stderr and "not a request identifier" in proc.stderr

    def test_without_portage_enabled_no_portage_eval_shim_is_built(self, tmp_path):
        env = _env(tmp_path)
        build_cli_shims({"camp": _tools()["camp"]}, env=env)
        assert not (resolve_shim_dir(env=env) / "portage-eval").exists()

    def test_the_shim_is_built_whether_or_not_outpost_is_enabled(self, tmp_path):
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env, outpost_enabled=False)
        assert (resolve_shim_dir(env=env) / "portage-eval").exists()


class TestEnvironment:
    def test_the_marker_and_locale_reach_the_command_and_loader_and_cache_and_unrelated_variables_do_not(
            self, tmp_path, monkeypatch):
        seen = _seen_by_interpreter(tmp_path, monkeypatch, "portage-eval", SESSION)
        for var in ("SANDBOX_RUNTIME", "USER", "LANG", "TERM", "LC_ALL", "LC_TIME", "HOME"):
            assert seen[var] == SESSION[var], var
        for var in ("LD_PRELOAD", "SOME_SESSION_VARIABLE", "XDG_CACHE_HOME"):
            assert var not in seen, var
        assert seen["PATH"] == "/usr/bin:/bin"

    def test_a_marker_the_session_did_not_set_is_not_passed_empty(self, tmp_path, monkeypatch):
        seen = _seen_by_interpreter(
            tmp_path, monkeypatch, "portage-eval", {"HOME": "/home/x", "PATH": "/usr/bin:/bin"})
        assert "SANDBOX_RUNTIME" not in seen and "USER" not in seen and "LANG" not in seen

    def test_the_runner_resolved_at_build_time_is_pinned_and_a_session_value_cannot_replace_it(
            self, tmp_path, monkeypatch):
        seen = _seen_by_interpreter(
            tmp_path, monkeypatch, "portage-eval", {**SESSION, "PORTAGE_EVAL_RUNNER": "/tmp/hostile"})
        assert seen["PORTAGE_EVAL_RUNNER"] == str(tmp_path / "claude-2.1.999")

    def test_the_shim_tells_the_command_its_own_absolute_path(self, tmp_path, monkeypatch):
        seen = _seen_by_interpreter(tmp_path, monkeypatch, "portage-eval", SESSION)
        env = _env(tmp_path)
        assert seen["PORTAGE_EVAL_SHIM"] == str(resolve_shim_dir(env=env) / "portage-eval")

    def test_the_extras_reach_only_the_eval_shim_and_the_gateway_shim_gets_what_it_always_did(
            self, tmp_path, monkeypatch):
        gateway = _seen_by_interpreter(tmp_path, monkeypatch, "outpost-gateway", SESSION, args=("run", "list"))
        eval_seen = _seen_by_interpreter(tmp_path, monkeypatch, "portage-eval", SESSION)
        assert set(gateway) - {"PWD"} == {"HOME", "PATH", "TRAILHEAD_ROOT"}
        assert {"SANDBOX_RUNTIME", "USER", "LANG", "TERM", "LC_ALL", "LC_TIME"} <= set(eval_seen)
        assert not {"SANDBOX_RUNTIME", "USER", "LANG", "TERM", "LC_ALL", "PORTAGE_EVAL_RUNNER",
                    "PORTAGE_EVAL_SHIM"} & set(gateway)


class TestRunnerResolution:
    def test_a_runner_that_cannot_be_found_is_refused_naming_why(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(PathIntegrationError, match="claude"):
            resolve_eval_runner({"PATH": str(empty)})

    def test_a_runner_others_can_write_is_refused_naming_why(self, tmp_path):
        bindir = _stub_runner(tmp_path, mode=0o775)
        with pytest.raises(PathIntegrationError, match="writable"):
            resolve_eval_runner({"PATH": str(bindir)})

    def test_a_runner_path_is_resolved_through_its_symlinks(self, tmp_path):
        bindir = _stub_runner(tmp_path)
        assert resolve_eval_runner({"PATH": str(bindir)}) == tmp_path / "claude-2.1.999"

    def test_a_build_that_cannot_resolve_the_runner_writes_no_eval_shim_and_says_why(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()
        env = {**_env(tmp_path), "PATH": str(empty)}
        shim_dir = build_cli_shims(_tools(), env=env)
        assert (shim_dir / "camp").exists()
        assert not (shim_dir / "portage-eval").exists()
        err = capsys.readouterr().err
        assert "portage-eval" in err and "claude" in err
