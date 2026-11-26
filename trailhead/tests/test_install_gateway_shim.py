"""The pinned shims, `outpost-gateway` and `trailhead-gateway-register`: built beside `ranger`
when the Outpost tool is enabled."""

from __future__ import annotations

import os
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from trailhead.install import build_cli_shims, resolve_cli_tools
from trailhead.install_config import ResolvedHarness, ResolvedPlugin
from trailhead.pathint import PathIntegrationError, resolve_shim_dir
from trailhead.tests._trusted_checkout import build_gateway_shims


def _env(tmp_path: Path) -> dict[str, str]:
    return {"HOME": str(tmp_path / "home"), "XDG_STATE_HOME": str(tmp_path / "state")}


def _tools() -> dict[str, Path]:
    return resolve_cli_tools({"camp": True, "lore": True, "portage": True})


class TestShimBuild:
    def test_outpost_enabled_writes_a_shim_that_runs_help(self, trusted_checkout, tmp_path):
        env = _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        shim = resolve_shim_dir(env=env) / "outpost-gateway"
        assert shim.exists()
        run_env = {
            **os.environ,
            "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
        }
        proc = subprocess.run([str(shim), "--help"], capture_output=True, text=True, env=run_env, timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert "outpost-gateway" in proc.stdout and "run list" in proc.stdout

    def test_the_pinned_script_follows_the_root_the_shims_are_built_from(self, tmp_path):
        env = _env(tmp_path)
        root = tmp_path / "other-root"
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "_outpost_gateway.py").write_text("print('from other root')\n")
        build_gateway_shims(_tools(), env=env, checkout=root)
        shim = resolve_shim_dir(env=env) / "outpost-gateway"
        assert str(root / "bin" / "_outpost_gateway.py") in shim.read_text()

    def test_outpost_enabled_writes_a_register_shim_that_runs_the_registration_step(self, trusted_checkout, tmp_path):
        state = tmp_path / "trailhead-state"
        env = {**_env(tmp_path), "TRAILHEAD_STATE_DIR": str(state)}
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        proc = subprocess.run(
            [str(resolve_shim_dir(env=env) / "trailhead-gateway-register"), "--hook"],
            capture_output=True,
            text=True,
            env={"HOME": env["HOME"], "PATH": "/usr/bin:/bin"},
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        (line,) = (state / "gateway" / "register.log").read_text().splitlines()
        assert line.split()[2] == "no_harness"

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("outpost-gateway", "run list"),
            ("trailhead-gateway-register", "Register this session"),
            ("trailhead-gateway-auth-header", "{}"),
        ],
    )
    def test_the_shim_runs_its_pinned_interpreter_not_what_the_session_puts_on_path(
        self, trusted_checkout, tmp_path, name, expected
    ):
        env = _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        shim = resolve_shim_dir(env=env) / name
        marker = tmp_path / "ran-hostile-code"
        hostile_bin = tmp_path / "venv-bin"
        hostile_bin.mkdir()
        fake_python = hostile_bin / "python3"
        fake_python.write_text(f'#!/bin/sh\necho python3 >> "{marker}"\nexit 0\n')
        fake_python.chmod(0o755)
        planted = tmp_path / "planted"
        planted.mkdir()
        for module in ("sitecustomize", "json"):
            (planted / f"{module}.py").write_text(f'open("{marker}", "a").write("{module}\\n")\n')
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        (cwd / "json.py").write_text(f'open("{marker}", "a").write("cwd-json\\n")\n')
        run_env = {
            "HOME": env["HOME"],
            "PATH": os.pathsep.join([str(hostile_bin), "/usr/bin", "/bin"]),
            "PYTHONPATH": str(planted),
        }
        proc = subprocess.run(
            [str(shim), "--help"], capture_output=True, text=True, env=run_env, cwd=cwd, timeout=60
        )
        assert proc.returncode == 0, proc.stderr
        assert expected in proc.stdout
        assert not marker.exists(), marker.read_text()

    def test_the_header_shim_prints_an_empty_header_and_exits_0_when_the_session_has_no_credential(
        self, trusted_checkout, tmp_path
    ):
        env = _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        proc = subprocess.run(
            [str(resolve_shim_dir(env=env) / "trailhead-gateway-auth-header")],
            capture_output=True, text=True, env={"HOME": env["HOME"], "PATH": "/usr/bin:/bin"}, timeout=60,
        )
        assert (proc.returncode, proc.stdout) == (0, "{}\n")

    def test_the_shim_runs_no_shell_startup_code_the_session_puts_in_its_environment(self, trusted_checkout, tmp_path):
        env = _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        marker = tmp_path / "ran-hostile-code"
        startup = tmp_path / "bash-env"
        startup.write_text(f'echo bash-env >> "{marker}"\n')
        run_env = {
            "HOME": env["HOME"],
            "PATH": "/usr/bin:/bin",
            "SHELLOPTS": "xtrace",
            "PS4": f'$(echo ps4 >> "{marker}")',
            "BASH_ENV": str(startup),
            "ENV": str(startup),
        }
        proc = subprocess.run(
            [str(resolve_shim_dir(env=env) / "outpost-gateway"), "--help"],
            capture_output=True, text=True, env=run_env, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "run list" in proc.stdout
        assert not marker.exists(), marker.read_text()

    def test_git_and_python_variables_the_session_sets_do_not_reach_run_start(
        self, trusted_checkout, tmp_path, hermetic_git_env
    ):
        env = _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        repo = tmp_path / "elsewhere-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        plain = tmp_path / "plain"
        plain.mkdir()
        marker = tmp_path / "ran-hostile-code"
        planted = tmp_path / "planted"
        planted.mkdir()
        (planted / "sitecustomize.py").write_text(f'open("{marker}", "a").write("sitecustomize\\n")\n')
        hostile_bin = tmp_path / "hostile-bin"
        hostile_bin.mkdir()
        (hostile_bin / "git").write_text(f'#!/bin/sh\necho git >> "{marker}"\n')
        (hostile_bin / "git").chmod(0o755)
        run_env = {
            "HOME": env["HOME"],
            "PATH": f"{hostile_bin}:/usr/bin:/bin",
            "PYTHONPATH": str(planted),
            "GIT_DIR": str(repo / ".git"),
            "GIT_WORK_TREE": str(repo),
        }
        proc = subprocess.run(
            [str(resolve_shim_dir(env=env) / "outpost-gateway"), "run", "start", "build"],
            capture_output=True, text=True, env=run_env, cwd=plain, timeout=60,
        )
        assert (proc.returncode, proc.stdout) == (2, "")
        assert proc.stderr == "outpost-gateway: this folder is not inside a git work tree\n"
        assert not marker.exists(), marker.read_text()

    def test_an_installing_venv_that_is_a_copy_does_not_put_its_site_packages_in_front(
        self, trusted_checkout, tmp_path, monkeypatch
    ):
        venv = tmp_path / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--copies", "--without-pip", str(venv)], check=True)
        marker = tmp_path / "ran-pth"
        (next((venv / "lib").glob("python*/site-packages"))).joinpath("evil.pth").write_text(
            f'import os; open("{marker}", "a").write("pth")\n'
        )
        monkeypatch.setattr("trailhead.pathint.sys.executable", str(venv / "bin" / "python"))
        env = _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        proc = subprocess.run(
            [str(resolve_shim_dir(env=env) / "outpost-gateway"), "--help"],
            capture_output=True,
            text=True,
            env={"HOME": env["HOME"], "PATH": "/usr/bin:/bin"},
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "run list" in proc.stdout
        assert not marker.exists()

    def _env_seen_by_interpreter(self, trusted_checkout, tmp_path, monkeypatch, session_env, install_env=None):
        """Run the real shim against an interpreter that records the environment it is started with."""
        dump = tmp_path / "interpreter-env"
        fake = tmp_path / "fake-python"
        fake.write_text(f'#!/bin/sh\n/usr/bin/env > "{dump}"\n')
        fake.chmod(0o755)
        monkeypatch.setattr("trailhead.pathint.sys.executable", str(fake))
        env = install_env if install_env is not None else _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        proc = subprocess.run(
            [str(resolve_shim_dir(env=env) / "outpost-gateway"), "run", "list"],
            capture_output=True,
            text=True,
            env=session_env,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return dict(line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line)

    def test_the_interpreter_is_started_without_the_dynamic_loader_variables(
        self, trusted_checkout, tmp_path, monkeypatch
    ):
        seen = self._env_seen_by_interpreter(
            trusted_checkout, tmp_path,
            monkeypatch,
            {
                "HOME": str(tmp_path / "home"),
                "PATH": "/usr/bin:/bin",
                "LD_PRELOAD": "/nonexistent/evil.so",
                "LD_LIBRARY_PATH": "/nonexistent",
                "LD_AUDIT": "/nonexistent/audit.so",
                "DYLD_INSERT_LIBRARIES": "/nonexistent/evil.dylib",
                "DYLD_LIBRARY_PATH": "/nonexistent",
            },
        )
        assert not [name for name in seen if name.startswith(("LD_", "DYLD_"))], seen

    def test_the_interpreter_gets_the_state_folders_fixed_at_install_and_a_fixed_path(
        self, trusted_checkout, tmp_path, monkeypatch
    ):
        spaced = tmp_path / "state dir"
        install_env = {
            "HOME": str(tmp_path / "home dir"),
            "TRAILHEAD_STATE_DIR": str(spaced),
            "OUTPOST_STATE_DIR": str(spaced / "outpost"),
            "XDG_STATE_HOME": str(spaced / "xdg"),
        }
        hostile = tmp_path / "hostile"
        seen = self._env_seen_by_interpreter(
            trusted_checkout,
            tmp_path,
            monkeypatch,
            {
                "HOME": str(hostile / "home"),
                "PATH": f"{hostile}/bin:/usr/bin:/bin",
                "TRAILHEAD_STATE_DIR": str(hostile / "trailhead"),
                "OUTPOST_STATE_DIR": str(hostile / "outpost"),
                "XDG_STATE_HOME": str(hostile / "xdg"),
                "SOME_SESSION_VARIABLE": "x",
            },
            install_env,
        )
        assert {name: seen[name] for name in install_env} == install_env
        assert seen["PATH"] == "/usr/bin:/bin"
        assert "SOME_SESSION_VARIABLE" not in seen

    def test_a_state_variable_not_set_at_install_is_not_passed_when_the_session_sets_it(
        self, trusted_checkout, tmp_path, monkeypatch
    ):
        install_env = {"HOME": str(tmp_path / "home"), "XDG_STATE_HOME": str(tmp_path / "state")}
        seen = self._env_seen_by_interpreter(
            trusted_checkout,
            tmp_path,
            monkeypatch,
            {
                "HOME": str(tmp_path / "other-home"),
                "PATH": "/usr/bin:/bin",
                "TRAILHEAD_STATE_DIR": str(tmp_path / "planted"),
                "OUTPOST_STATE_DIR": str(tmp_path / "planted"),
            },
            install_env,
        )
        assert "TRAILHEAD_STATE_DIR" not in seen and "OUTPOST_STATE_DIR" not in seen
        assert seen["HOME"] == install_env["HOME"] and seen["XDG_STATE_HOME"] == install_env["XDG_STATE_HOME"]

    def test_outpost_disabled_writes_no_shim(self, tmp_path):
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env, outpost_enabled=False)
        shim_dir = resolve_shim_dir(env=env)
        assert (shim_dir / "ranger").exists()
        assert not (shim_dir / "outpost-gateway").exists()
        assert not (shim_dir / "trailhead-gateway-register").exists()

    def test_default_is_no_gateway_shim(self, tmp_path):
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env)
        assert not (resolve_shim_dir(env=env) / "outpost-gateway").exists()


class TestInstallPipeline:
    def _cfg(self, plugin_names):
        return type(
            "Cfg",
            (),
            {
                "harnesses": [
                    ResolvedHarness(
                        name="claude_code",
                        plugins=[ResolvedPlugin(name=n, subagents={}, skills={}) for n in plugin_names],
                    )
                ]
            },
        )()

    def test_outpost_enabled_reads_the_resolved_plugin_selection(self):
        from trailhead.install import outpost_enabled

        assert outpost_enabled(self._cfg(["lore", "outpost"])) is True
        assert outpost_enabled(self._cfg(["lore", "camp"])) is False


def _shim_names():
    return ("outpost-gateway", "trailhead-gateway-register", "trailhead-gateway-auth-header")


def _no_gateway_shims(env) -> bool:
    shim_dir = resolve_shim_dir(env=env)
    return not any((shim_dir / name).exists() for name in _shim_names())


class TestInstallRefusesAnUntrustedPin:
    """What `trailhead install` and `update` will pin a shim to: nothing a session can write or set."""

    def test_a_checkout_under_a_sandbox_writable_path_is_refused_naming_the_path_and_no_shim_is_written(
        self, trusted_checkout, tmp_path
    ):
        env = _env(tmp_path)
        with pytest.raises(PathIntegrationError, match=str(trusted_checkout)):
            build_gateway_shims(_tools(), env=env, checkout=trusted_checkout, sandbox_write_roots=[tmp_path])
        assert _no_gateway_shims(env)

    def test_the_default_writable_paths_are_the_ones_a_launched_session_may_write(self, trusted_checkout, tmp_path):
        from trailhead.paths import state_dir

        env = _env(tmp_path)
        camp_worktree = state_dir("camp", env=env) / "grp" / "worktrees" / "ws"
        camp_worktree.parent.mkdir(parents=True)
        trusted_checkout.rename(camp_worktree)
        with pytest.raises(PathIntegrationError, match=rf"{camp_worktree} is under {state_dir('camp', env=env)}"):
            build_cli_shims(_tools(), env=env, root=camp_worktree, outpost_enabled=True)
        assert _no_gateway_shims(env)

    def test_the_default_writable_paths_include_the_cache_folder(self, trusted_checkout, tmp_path):
        env = {**_env(tmp_path), "XDG_CACHE_HOME": str(tmp_path / "cache")}
        cached = tmp_path / "cache" / "checkout"
        cached.parent.mkdir()
        trusted_checkout.rename(cached)
        with pytest.raises(PathIntegrationError, match=rf"{cached} is under {tmp_path / 'cache'}"):
            build_cli_shims(_tools(), env=env, root=cached, outpost_enabled=True)

    def test_an_interpreter_under_a_writable_cache_folder_is_refused_naming_it(
        self, trusted_checkout, tmp_path, monkeypatch
    ):
        cache = tmp_path / "cache"
        cache.mkdir()
        interpreter = cache / "python"
        interpreter.write_text("#!/bin/sh\nexit 0\n")
        interpreter.chmod(0o755)
        monkeypatch.setattr("trailhead.pathint.sys.executable", str(interpreter))
        env = _env(tmp_path)
        with pytest.raises(PathIntegrationError, match=str(interpreter)):
            build_gateway_shims(_tools(), env=env, checkout=trusted_checkout, sandbox_write_roots=[cache])
        assert _no_gateway_shims(env)

    @pytest.mark.parametrize(
        "relative",
        ["bin/_outpost_gateway.py", "bin/_gateway_register.py", "trailhead/gateway/command.py", "trailhead/gateway"],
    )
    def test_a_group_writable_script_module_or_package_folder_is_refused_naming_it(
        self, trusted_checkout, tmp_path, relative
    ):
        target = trusted_checkout / relative
        target.chmod(target.stat().st_mode | 0o020)
        env = _env(tmp_path)
        with pytest.raises(PathIntegrationError, match=str(target)):
            build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        assert _no_gateway_shims(env)

    def test_a_world_writable_checkout_root_is_refused_naming_it(self, trusted_checkout, tmp_path):
        trusted_checkout.chmod(0o757)
        env = _env(tmp_path)
        with pytest.raises(PathIntegrationError, match=str(trusted_checkout)):
            build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)

    def test_a_group_writable_folder_on_the_path_to_the_checkout_is_refused_naming_it(self, tmp_path):
        from trailhead.tests._trusted_checkout import make_trusted_checkout

        parent = tmp_path / "shared"
        parent.mkdir()
        checkout = make_trusted_checkout(parent / "checkout")
        parent.chmod(0o775)
        env = _env(tmp_path)
        with pytest.raises(PathIntegrationError, match=str(parent)):
            build_gateway_shims(_tools(), env=env, checkout=checkout)

    def test_a_checkout_the_operator_does_not_own_is_refused_naming_the_path(self, trusted_checkout, tmp_path):
        env = _env(tmp_path)
        with pytest.raises(PathIntegrationError, match=r"owned by another user"):
            build_gateway_shims(_tools(), env=env, checkout=trusted_checkout, euid=os.geteuid() + 1)
        assert _no_gateway_shims(env)

    def test_a_read_only_checkout_is_accepted_and_the_shims_are_written(self, trusted_checkout, tmp_path):
        env = _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        assert not _no_gateway_shims(env)
        assert all((resolve_shim_dir(env=env) / name).exists() for name in _shim_names())


class TestRefusedInstallRemovesTheStaleShim:
    @pytest.mark.parametrize("name", ["outpost-gateway", "trailhead-gateway-register", "trailhead-gateway-auth-header"])
    def test_a_refused_rebuild_removes_the_earlier_pinned_shim_and_says_so(self, trusted_checkout, tmp_path, name):
        env = _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        shim = resolve_shim_dir(env=env) / name
        assert shim.exists()
        with pytest.raises(PathIntegrationError, match=r"removed the existing .*shim") as refused:
            build_gateway_shims(_tools(), env=env, checkout=trusted_checkout, sandbox_write_roots=[tmp_path])
        assert str(trusted_checkout) in str(refused.value)
        assert _no_gateway_shims(env)

    def test_a_refusal_with_no_earlier_shim_does_not_claim_to_remove_one(self, trusted_checkout, tmp_path):
        env = _env(tmp_path)
        with pytest.raises(PathIntegrationError) as refused:
            build_gateway_shims(_tools(), env=env, checkout=trusted_checkout, sandbox_write_roots=[tmp_path])
        assert "removed" not in str(refused.value)

    def test_a_refused_rebuild_leaves_other_shims_in_place(self, trusted_checkout, tmp_path):
        env = _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        other = resolve_shim_dir(env=env) / "trailhead"
        assert other.exists()
        with pytest.raises(PathIntegrationError):
            build_gateway_shims(_tools(), env=env, checkout=trusted_checkout, sandbox_write_roots=[tmp_path])
        assert other.exists()


class TestShimRechecksAtExec:
    def _installed(self, trusted_checkout, tmp_path, name="outpost-gateway"):
        env = _env(tmp_path)
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        return resolve_shim_dir(env=env) / name

    def _run(self, shim, *args):
        return subprocess.run(
            [str(shim), *args], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, timeout=60
        )

    @pytest.mark.parametrize(
        ("name", "script_name"),
        [
            ("outpost-gateway", "_outpost_gateway.py"),
            ("trailhead-gateway-register", "_gateway_register.py"),
            ("trailhead-gateway-auth-header", "_gateway_auth_header.py"),
        ],
    )
    def test_a_script_swapped_after_install_is_refused_at_exec_and_not_run(
        self, trusted_checkout, tmp_path, name, script_name
    ):
        shim = self._installed(trusted_checkout, tmp_path, name)
        script = trusted_checkout / "bin" / script_name
        marker = tmp_path / "ran-swapped-script"
        script.write_text(f'open("{marker}", "w").write("ran")\n')
        proc = self._run(shim, "--help")
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert str(script) in proc.stderr and "changed" in proc.stderr
        assert not marker.exists()

    @pytest.mark.parametrize("name", ["outpost-gateway", "trailhead-gateway-auth-header"])
    def test_an_interpreter_swapped_after_install_is_refused_at_exec_and_not_run(
        self, trusted_checkout, tmp_path, monkeypatch, name
    ):
        marker = tmp_path / "ran-swapped-interpreter"
        interpreter = tmp_path / "python"
        interpreter.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        interpreter.chmod(0o755)
        monkeypatch.setattr("trailhead.pathint.sys.executable", str(interpreter))
        shim = self._installed(trusted_checkout, tmp_path, name)
        interpreter.write_text(f'#!/bin/sh\necho ran > "{marker}"\n')
        proc = self._run(shim, "--help")
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert str(interpreter) in proc.stderr and "changed" in proc.stderr
        assert not marker.exists()

    def test_a_checkout_root_replaced_by_a_link_after_install_is_refused_at_exec(self, trusted_checkout, tmp_path):
        from trailhead.tests._trusted_checkout import make_trusted_checkout

        shim = self._installed(trusted_checkout, tmp_path)
        elsewhere = make_trusted_checkout(tmp_path / "elsewhere")
        trusted_checkout.rename(tmp_path / "moved")
        trusted_checkout.symlink_to(elsewhere)
        proc = self._run(shim, "--help")
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert str(trusted_checkout) in proc.stderr and "changed" in proc.stderr

    def test_a_shim_whose_pinned_files_are_unchanged_runs(self, trusted_checkout, tmp_path):
        proc = self._run(self._installed(trusted_checkout, tmp_path), "--help")
        assert proc.returncode == 0, proc.stderr
        assert "run list" in proc.stdout


class TestInstalledShimOnItsOwnState:
    """The installed shims against a stub Outpost, run as a child of a fake harness with nothing but what the session
    sets."""

    def _install(self, trusted_checkout, tmp_path, monkeypatch, port):
        from trailhead.tests._gateway_support import register_port_interpreter

        outpost_state = tmp_path / "baked-outpost-state"
        env = {
            "HOME": str(tmp_path / "home"),
            "TRAILHEAD_STATE_DIR": str(tmp_path / "baked-trailhead-state"),
            "OUTPOST_STATE_DIR": str(outpost_state),
        }
        monkeypatch.setattr(
            "trailhead.pathint.sys.executable", str(register_port_interpreter(tmp_path / "interp", port))
        )
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        return env, outpost_state

    def _run_under_harness(self, tmp_path, argv, session_env, on_start):
        from trailhead.tests._gateway_support import run_under_harness
        from trailhead.tests._harness_process_support import make_fake_harness

        return run_under_harness(
            make_fake_harness(tmp_path / "fake"),
            shlex.join(argv),
            env=session_env,
            on_start=on_start,
        )

    def test_a_credential_planted_as_a_link_under_a_folder_the_session_names_is_never_read(
        self, trusted_checkout, tmp_path, monkeypatch
    ):
        # AC82: no session can read another session's credential.
        from trailhead.harness.process import format_session_key
        from trailhead.tests._gateway_support import StubOutpost, await_harness, write_credential

        with StubOutpost(runs=[]) as stub:
            env, outpost_state = self._install(trusted_checkout, tmp_path, monkeypatch, stub.port)
            planted = tmp_path / "planted-outpost-state"

            def seed(pid):
                key = format_session_key(*await_harness(pid))
                write_credential(outpost_state, key)
                other = write_credential(
                    tmp_path / "other-session-state", "ws-other", client_id="other-id", client_secret="other-secret"
                )
                link = planted / "sessions" / key / "gateway-client.json"
                link.parent.mkdir(parents=True)
                link.symlink_to(other)

            proc = self._run_under_harness(
                tmp_path,
                [str(resolve_shim_dir(env=env) / "outpost-gateway"), "run", "list"],
                {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "session-home"), "OUTPOST_STATE_DIR": str(planted)},
                seed,
            )
            tokens = [q for q in stub.requests if q.path == "/broker/token"]
        assert proc.returncode == 0, proc.stderr
        from trailhead.gateway.outpost_http import basic_auth
        from trailhead.tests._gateway_support import CLIENT_ID, CLIENT_SECRET

        assert [q.headers["authorization"] for q in tokens] == [basic_auth(CLIENT_ID, CLIENT_SECRET)]

    def test_a_session_with_no_credential_registers_on_demand_through_the_installed_shim_with_a_scrubbed_env(
        self, trusted_checkout, tmp_path, monkeypatch
    ):
        # AC28: on-demand registration, proven through the real installed shim.
        from trailhead.tests._gateway_support import (
            StubOutpost,
            REGISTRATION_AUTHORITY,
            write_authority,
            write_credential,
        )

        holder = []
        with StubOutpost(runs=[], on_register=lambda key: write_credential(holder[0], key)) as stub:
            env, outpost_state = self._install(trusted_checkout, tmp_path, monkeypatch, stub.port)
            holder.append(outpost_state)
            write_authority(outpost_state)
            proc = self._run_under_harness(
                tmp_path,
                [str(resolve_shim_dir(env=env) / "outpost-gateway"), "run", "list"],
                {"PATH": "/usr/bin:/bin"},
                lambda pid: None,
            )
            seen = [(q.method, q.path) for q in stub.requests]
            registration = next(q for q in stub.requests if q.path == "/gateway/sessions")
        assert proc.returncode == 0, proc.stderr
        assert registration.headers["authorization"] == f"Bearer {REGISTRATION_AUTHORITY}"
        assert seen[:2] == [("POST", "/gateway/sessions"), ("POST", "/broker/token")]

    def test_the_register_shim_writes_its_log_only_under_the_folder_baked_at_install(self, trusted_checkout, tmp_path):
        state = tmp_path / "baked-state"
        planted = tmp_path / "planted-state"
        planted.mkdir()
        env = {**_env(tmp_path), "TRAILHEAD_STATE_DIR": str(state)}
        build_gateway_shims(_tools(), env=env, checkout=trusted_checkout)
        proc = subprocess.run(
            [str(resolve_shim_dir(env=env) / "trailhead-gateway-register"), "--hook"],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "TRAILHEAD_STATE_DIR": str(planted), "XDG_STATE_HOME": str(planted)},
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert (state / "gateway" / "register.log").read_text().split()[2] == "no_harness"
        assert list(planted.rglob("*")) == []
