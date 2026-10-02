"""Tests for trailhead/update_runner.py — `trailhead update --detach --json`.

No test reaches a real ``systemd-run``/``systemctl``/``launchctl``: the
supervised paths run against an injected runner. The unsupervised path spawns a
real child, but the child is a stand-in ``bin/trailhead`` in a tmp checkout
that takes the run lock through ``update_run`` (or deliberately does not).
State, HOME and Outpost config all live under ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import plistlib
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from trailhead import cli, outpost_supervisor, update_run, update_runner
from trailhead.tests.test_update_apply import _env as _base_env
from trailhead.tests.test_update_apply import _install_stamp

REPO_ROOT = Path(update_run.__file__).resolve().parent.parent
HOSTILE = "a$(touch PWNED)`touch PWNED2`;touch PWNED3\nsecond-line"


def _env(tmp_path: Path) -> dict[str, str]:
    env = dict(_base_env(tmp_path))
    env["OUTPOST_STATE_DIR"] = str(tmp_path / "outpost-state")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    env["TH_CALLER_VAR"] = "caller-value"
    return env


def _checkout(tmp_path: Path) -> Path:
    return tmp_path / "home" / "checkout"


def _state(tmp_path: Path) -> Path:
    return tmp_path / "state"


def _log(tmp_path: Path) -> Path:
    return _state(tmp_path) / "update-run.log"


def _sup_dir(tmp_path: Path, *, kind: str | None) -> Path:
    d = tmp_path / "sup"
    d.mkdir(exist_ok=True)
    if kind == "linux":
        (d / "outpost.service").write_text("[Unit]\n")
    elif kind == "darwin":
        (d / "com.trailhead.outpost.plist").write_text("<plist/>")
    return d


class FakeRunner:
    """Records every argv; optionally acts as the started job."""

    def __init__(self, *, rc=0, on_start=None, raises=None):
        self.calls: list[list[str]] = []
        self.envs: list[dict | None] = []
        self.rc = rc
        self.on_start = on_start
        self.raises = raises
        self.locks: list[int] = []

    def __call__(self, argv, env=None):
        self.calls.append(list(argv))
        self.envs.append(env)
        if self.raises:
            raise self.raises
        if self.on_start:
            self.on_start(argv, self)
        return CompletedProcess(argv, self.rc, "", "boom" if self.rc else "")

    def close(self):
        for fd in self.locks:
            update_run.release_run_lock(fd)
        self.locks.clear()


def _run_id_from(argv) -> str:
    return argv[argv.index("--run-id") + 1]


def _takes_lock(env):
    def start(argv, runner):
        runner.locks.append(update_run.acquire_run_lock(_run_id_from(argv), env=env))

    return start


@pytest.fixture(autouse=True)
def _no_real_supervisor_binaries(tmp_path, monkeypatch):
    guard = tmp_path / "guard-bin"
    guard.mkdir()
    for name in ("systemd-run", "systemctl", "launchctl"):
        exe = guard / name
        exe.write_text("#!/bin/sh\nexit 99\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{guard}:{os.environ['PATH']}")


@pytest.fixture
def world(tmp_path):
    env = _env(tmp_path)
    _install_stamp(tmp_path, env)
    return tmp_path, env


# --------------------------------------------------------------- systemd path


def test_systemd_argv_is_fixed_and_carries_user_collect_unit_setenv(world):
    tmp_path, env = world
    env["TH_HOSTILE"] = HOSTILE
    runner = FakeRunner(on_start=_takes_lock(env))
    try:
        out = update_runner.detach(
            env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
            runner=runner, wait_seconds=2, poll_interval=0.01,
        )
    finally:
        runner.close()
    assert out["started"] is True
    (argv,) = runner.calls
    run_id = out["run_id"]
    checkout = _checkout(tmp_path)
    log = _log(tmp_path)
    assert argv[:5] == [
        "systemd-run", "--user", f"--unit=trailhead-update-{run_id}", "--collect",
        f"--working-directory={checkout}",
    ]
    assert f"--property=StandardOutput=append:{log}" in argv
    assert f"--property=StandardError=append:{log}" in argv
    assert argv[argv.index("--") + 1:][:5] == [
        str(checkout / "bin" / "trailhead"), "update", "--yes", "--run-id", run_id,
    ]
    assert argv[-2] == "--start-by" and update_run.validate_start_by(argv[-1])
    setenvs = [a for a in argv if a.startswith("--setenv=")]
    assert setenvs.count("--setenv=TH_CALLER_VAR") == 1
    assert sorted(setenvs) == sorted(f"--setenv={name}" for name in env)
    assert not any(value in a for a in argv for value in ("caller-value", HOSTILE))
    assert runner.envs == [env]
    assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)


def test_systemd_job_gets_every_caller_variable(world):
    tmp_path, env = world
    env["PATH"] = "/opt/node/bin:/usr/bin"
    runner = FakeRunner(on_start=_takes_lock(env))
    try:
        update_runner.detach(
            env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
            runner=runner, wait_seconds=2, poll_interval=0.01,
        )
    finally:
        runner.close()
    (argv,) = runner.calls
    got = {a[len("--setenv="):] for a in argv if a.startswith("--setenv=")}
    assert got == set(env)
    assert "--setenv=PATH" in argv
    assert runner.envs[0]["PATH"] == "/opt/node/bin:/usr/bin"
    assert not any("/opt/node/bin" in a for a in argv)


@pytest.mark.parametrize(
    "bad", ["BASH_FUNC_x%%", "1STARTS_WITH_DIGIT", "HAS-DASH", "HAS SPACE", "\u00c9NV"]
)
def test_an_environment_name_systemd_would_reject_is_not_forwarded_and_the_rest_are(world, bad):
    tmp_path, env = world
    env[bad] = "() { :; }"
    env["_UNDERSCORE_OK"] = "1"
    runner = FakeRunner(on_start=_takes_lock(env))
    try:
        out = update_runner.detach(
            env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
            runner=runner, wait_seconds=2, poll_interval=0.01,
        )
    finally:
        runner.close()
    assert out["started"] is True
    (argv,) = runner.calls
    setenvs = [a for a in argv if a.startswith("--setenv=")]
    assert f"--setenv={bad}" not in setenvs
    assert "--setenv=_UNDERSCORE_OK" in setenvs
    assert "--setenv=TH_CALLER_VAR" in setenvs
    assert len(setenvs) == len(env) - 1


def test_systemd_no_shell_hostile_value_in_no_argv_element_and_env_unchanged(world):
    tmp_path, env = world
    env["TH_HOSTILE"] = HOSTILE
    runner = FakeRunner(on_start=_takes_lock(env))
    try:
        update_runner.detach(
            env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
            runner=runner, wait_seconds=2, poll_interval=0.01,
        )
    finally:
        runner.close()
    (argv,) = runner.calls
    assert "--setenv=TH_HOSTILE" in argv
    for fragment in ("$(", "`", ";", "\n", "PWNED", "second-line"):
        assert not any(fragment in a for a in argv)
    assert runner.envs[0]["TH_HOSTILE"] == HOSTILE


# --------------------------------------------------------------- launchd path


def _launchd_runner(env, plists):
    def start(argv, runner):
        if argv[:2] == ["launchctl", "bootstrap"]:
            plists.append(plistlib.loads(Path(argv[3]).read_bytes()))
            runner.locks.append(update_run.acquire_run_lock(
                _run_id_from(plists[-1]["ProgramArguments"]), env=env))

    return FakeRunner(on_start=start)


def test_launchd_job_definition_built_with_plistlib(world):
    tmp_path, env = world
    env["TH_HOSTILE"] = HOSTILE
    plists: list[dict] = []
    runner = _launchd_runner(env, plists)
    try:
        out = update_runner.detach(
            env=env, platform="darwin", supervisor_dir=_sup_dir(tmp_path, kind="darwin"),
            runner=runner, uid=501, wait_seconds=2, poll_interval=0.01,
        )
    finally:
        runner.close()
    assert out["started"] is True
    (plist,) = plists
    checkout = _checkout(tmp_path)
    assert plist["ProgramArguments"][:5] == [
        str(checkout / "bin" / "trailhead"), "update", "--yes", "--run-id", out["run_id"],
    ]
    assert plist["ProgramArguments"][5] == "--start-by"
    assert update_run.validate_start_by(plist["ProgramArguments"][6])
    assert plist["EnvironmentVariables"] == env
    assert plist["EnvironmentVariables"]["TH_HOSTILE"] == HOSTILE
    assert plist["Label"] != outpost_supervisor.LAUNCHD_LABEL
    assert plist["Label"].startswith("com.trailhead.update")
    assert plist["RunAtLoad"] is True
    assert "KeepAlive" not in plist
    assert plist["WorkingDirectory"] == str(checkout)
    assert plist["StandardOutPath"] == str(_log(tmp_path))
    assert plist["StandardErrorPath"] == str(_log(tmp_path))
    bootstrap = [c for c in runner.calls if c[:2] == ["launchctl", "bootstrap"]]
    assert bootstrap[0][:3] == ["launchctl", "bootstrap", "gui/501"]
    assert not any("kickstart" in c or "submit" in c for c in runner.calls)


def test_an_environment_value_a_plist_cannot_carry_is_not_forwarded_and_the_update_starts(world):
    tmp_path, env = world
    env["TH_ESC"] = "a\x1b[31mb"
    env["TH_NEWLINE"] = "a\nb"
    env["TH_TAB"] = "a\tb"
    plists: list[dict] = []
    runner = _launchd_runner(env, plists)
    try:
        out = update_runner.detach(
            env=env, platform="darwin", supervisor_dir=_sup_dir(tmp_path, kind="darwin"),
            runner=runner, uid=501, wait_seconds=2, poll_interval=0.01,
        )
    finally:
        runner.close()
    assert out["started"] is True
    (plist,) = plists
    assert "TH_ESC" not in plist["EnvironmentVariables"]
    assert plist["EnvironmentVariables"] == {k: v for k, v in env.items() if k != "TH_ESC"}


def test_launchd_plist_is_owner_only(world):
    tmp_path, env = world
    modes = []

    def start(argv, runner):
        if argv[:2] == ["launchctl", "bootstrap"]:
            modes.append(Path(argv[3]).stat().st_mode & 0o777)
            runner.locks.append(update_run.acquire_run_lock(
                _run_id_from(plistlib.loads(Path(argv[3]).read_bytes())["ProgramArguments"]), env=env))

    runner = FakeRunner(on_start=start)
    try:
        update_runner.detach(
            env=env, platform="darwin", supervisor_dir=_sup_dir(tmp_path, kind="darwin"),
            runner=runner, uid=501, wait_seconds=2, poll_interval=0.01,
        )
    finally:
        runner.close()
    assert modes == [0o600]


def _state_files_holding(tmp_path: Path, needle: str) -> list[Path]:
    return [
        f for f in _state(tmp_path).rglob("*")
        if f.is_file() and not f.is_symlink() and needle.encode() in f.read_bytes()
    ]


@pytest.mark.parametrize("bootstrap_rc", [0, 5], ids=["bootstrapped", "bootstrap-failed"])
def test_launchd_job_definition_does_not_outlive_the_bootstrap(world, bootstrap_rc):
    tmp_path, env = world
    env["TH_SECRET"] = "s3cret-token-xyz"
    seen = []

    def start(argv, runner):
        if argv[:2] == ["launchctl", "bootstrap"]:
            seen.append(_state_files_holding(tmp_path, "s3cret-token-xyz"))
            if bootstrap_rc == 0:
                runner.locks.append(update_run.acquire_run_lock(
                    _run_id_from(plistlib.loads(Path(argv[3]).read_bytes())["ProgramArguments"]), env=env))

    runner = FakeRunner(rc=0, on_start=start)
    real_call = runner.__call__

    def call(argv, env=None):
        result = real_call(argv, env=env)
        if argv[:2] == ["launchctl", "bootstrap"] and bootstrap_rc:
            return CompletedProcess(argv, bootstrap_rc, "", "")
        return result

    try:
        update_runner.detach(
            env=env, platform="darwin", supervisor_dir=_sup_dir(tmp_path, kind="darwin"),
            runner=call, uid=501, wait_seconds=1, poll_interval=0.01,
        )
    finally:
        runner.close()
    assert len(seen[0]) == 1, "the definition carries the environment while launchd loads it"
    assert _state_files_holding(tmp_path, "s3cret-token-xyz") == []


def test_launchd_job_definition_is_written_without_following_a_symlink_at_its_path(world):
    tmp_path, env = world
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    _state(tmp_path).mkdir(parents=True, exist_ok=True)
    (_state(tmp_path) / "update-job.plist").symlink_to(victim)
    at_bootstrap = {}

    def start(argv, runner):
        if argv[:2] == ["launchctl", "bootstrap"]:
            path = Path(argv[3])
            at_bootstrap.update(
                symlink=path.is_symlink(), mode=path.stat().st_mode & 0o777,
                plist=plistlib.loads(path.read_bytes()),
            )
            runner.locks.append(update_run.acquire_run_lock(
                _run_id_from(at_bootstrap["plist"]["ProgramArguments"]), env=env))

    runner = FakeRunner(on_start=start)
    try:
        out = update_runner.detach(
            env=env, platform="darwin", supervisor_dir=_sup_dir(tmp_path, kind="darwin"),
            runner=runner, uid=501, wait_seconds=1, poll_interval=0.01,
        )
    finally:
        runner.close()
    assert out["started"] is True
    assert victim.read_text() == "precious"
    assert at_bootstrap["symlink"] is False
    assert at_bootstrap["mode"] == 0o600
    assert at_bootstrap["plist"]["EnvironmentVariables"] == env


def test_launchd_job_definition_replaces_its_path_through_a_temp_file_in_the_same_directory(
    world, monkeypatch
):
    tmp_path, env = world
    replaced = []
    real_replace = os.replace

    def spy(src, dst, *a, **k):
        replaced.append((str(src), str(dst), os.stat(src).st_mode & 0o777))
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(update_runner.os, "replace", spy)
    runner = _launchd_runner(env, [])
    try:
        update_runner.detach(
            env=env, platform="darwin", supervisor_dir=_sup_dir(tmp_path, kind="darwin"),
            runner=runner, uid=501, wait_seconds=1, poll_interval=0.01,
        )
    finally:
        runner.close()
    dest = str(_state(tmp_path) / "update-job.plist")
    ours = [(src, mode) for src, dst, mode in replaced if dst == dest]
    assert len(ours) == 1
    src, mode = ours[0]
    assert Path(src).parent == _state(tmp_path) and src != dest
    assert mode == 0o600


def test_launchd_replaces_the_previous_job_before_bootstrapping(world):
    tmp_path, env = world
    plists: list[dict] = []
    runner = _launchd_runner(env, plists)
    try:
        update_runner.detach(
            env=env, platform="darwin", supervisor_dir=_sup_dir(tmp_path, kind="darwin"),
            runner=runner, uid=501, wait_seconds=2, poll_interval=0.01,
        )
    finally:
        runner.close()
    label = plists[0]["Label"]
    assert runner.calls[0] == ["launchctl", "bootout", f"gui/501/{label}"]
    assert runner.calls[1][:2] == ["launchctl", "bootstrap"]


def test_launchd_bootstrap_failure_is_could_not_start(world):
    tmp_path, env = world

    def start(argv, runner):
        pass

    runner = FakeRunner(on_start=start)

    def failing(argv):
        runner.calls.append(list(argv))
        rc = 5 if argv[:2] == ["launchctl", "bootstrap"] else 0
        return CompletedProcess(argv, rc, "", "")

    t0 = time.monotonic()
    out = update_runner.detach(
        env=env, platform="darwin", supervisor_dir=_sup_dir(tmp_path, kind="darwin"),
        runner=failing, uid=501, wait_seconds=5, poll_interval=0.01,
    )
    assert time.monotonic() - t0 < 2
    assert out == {"started": False, "running": False, "error": "could_not_start"}


# ------------------------------------------------------------ unsupervised path

_STUB = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys, time
    sys.path.insert(0, {repo!r})
    from trailhead import update_run
    mode = os.environ.get("STUB_MODE", "lock")
    print("hello-from-job", flush=True)
    with open(os.environ["STUB_OUT"], "w") as f:
        json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "sid": os.getsid(0),
                    "pid": os.getpid(), "ppid_sid": os.environ.get("STUB_CALLER_SID"),
                    "var": os.environ.get("TH_CALLER_VAR"),
                    "hostile": os.environ.get("TH_HOSTILE")}}, f)
    if mode == "lock":
        update_run.acquire_run_lock(sys.argv[sys.argv.index("--run-id") + 1],
                                    env=dict(os.environ))
        time.sleep(60)
    elif mode == "never":
        time.sleep(60)
    elif mode == "gate":
        while not os.path.exists(os.environ["STUB_OUT"] + ".gate"):
            time.sleep(0.01)
        start_by = int(sys.argv[sys.argv.index("--start-by") + 1])
        update_run.acquire_run_lock(sys.argv[sys.argv.index("--run-id") + 1],
                                    env=dict(os.environ), start_by=start_by)
        open(os.environ["STUB_OUT"] + ".ran", "w").close()
        time.sleep(60)
    elif mode == "at":
        start_by = int(sys.argv[sys.argv.index("--start-by") + 1])
        time.sleep(max(0.0, start_by + float(os.environ["STUB_OFFSET"]) - time.time()))
        try:
            update_run.acquire_run_lock(sys.argv[sys.argv.index("--run-id") + 1],
                                        env=dict(os.environ), start_by=start_by)
        except update_run.RunLockExpired:
            open(os.environ["STUB_OUT"] + ".expired", "w").close()
            sys.exit(1)
        open(os.environ["STUB_OUT"] + ".ran", "w").close()
        time.sleep(60)
    """
)


@pytest.fixture
def stub(world):
    tmp_path, env = world
    bin_dir = _checkout(tmp_path) / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "trailhead"
    exe.write_text(_STUB.format(python=sys.executable, repo=str(REPO_ROOT)))
    exe.chmod(0o755)
    env["STUB_OUT"] = str(tmp_path / "stub-out.json")
    pids: list[int] = []
    yield tmp_path, env, pids
    for pid in pids:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if os.waitpid(-1, os.WNOHANG) == (0, 0):
                break
        except ChildProcessError:
            break
        time.sleep(0.05)


def _stub_out(tmp_path, pids):
    data = json.loads((tmp_path / "stub-out.json").read_text())
    pids.append(data["pid"])
    return data


def test_unsupervised_spawns_a_real_child_in_a_new_session(stub):
    tmp_path, env, pids = stub
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind=None),
        wait_seconds=10, poll_interval=0.02,
    )
    data = _stub_out(tmp_path, pids)
    assert out == {"started": True, "run_id": out["run_id"]}
    assert data["argv"][:4] == ["update", "--yes", "--run-id", out["run_id"]]
    assert data["argv"][4] == "--start-by" and len(data["argv"]) == 6
    assert update_run.validate_start_by(data["argv"][5])
    assert data["sid"] == data["pid"] != os.getsid(0)
    assert data["cwd"] == str(_checkout(tmp_path))
    assert data["var"] == "caller-value"


def test_unsupervised_job_output_goes_only_to_the_log(stub, monkeypatch, capfd):
    tmp_path, env, pids = stub
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "argv", ["trailhead", "update", "--detach", "--json"])
    assert cli.main() == 0
    _stub_out(tmp_path, pids)
    captured = capfd.readouterr()
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["started"] is True
    assert "hello-from-job" not in captured.out + captured.err
    deadline = time.monotonic() + 5
    while "hello-from-job" not in _log(tmp_path).read_text() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "hello-from-job" in _log(tmp_path).read_text()


def test_unsupervised_child_survives_caller_exit_and_sigterm(stub):
    tmp_path, env, pids = stub
    env["PYTHONPATH"] = str(REPO_ROOT)
    code = (
        "import os, sys; from pathlib import Path; from trailhead import update_runner;"
        "print(os.getpid(), flush=True);"
        "r = update_runner.detach(platform='linux', supervisor_dir=Path(sys.argv[1]));"
        "print(r['started'], flush=True); sys.stdin.readline()"
    )
    caller = subprocess.Popen(
        [sys.executable, "-c", code, str(_sup_dir(tmp_path, kind=None))],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        # The caller leads its own process group, as a daemon that runs
        # --detach under a process-group timeout does; SIGTERM goes to that
        # whole group, so only a job in its own session survives it.
        start_new_session=True,
    )
    try:
        caller_pid = int(caller.stdout.readline())
        assert caller.stdout.readline().strip() == "True"
        child = _stub_out(tmp_path, pids)["pid"]
        os.killpg(caller_pid, signal.SIGTERM)
        caller.wait(timeout=10)
        time.sleep(0.2)
        os.kill(child, 0)
        state = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0]
        assert state != "Z"
        assert update_run.current_holder(env=env)["pid"] == child
    finally:
        if caller.poll() is None:
            caller.kill()
        caller.wait()


def test_unsupervised_hostile_env_and_checkout_path_reach_the_child_unexecuted(stub):
    tmp_path, env, pids = stub
    from trailhead import provenance

    odd = tmp_path / "home" / "c h;touch PWNED4 $(touch PWNED5)"
    (odd / "bin").mkdir(parents=True)
    (_checkout(tmp_path) / "bin" / "trailhead").rename(odd / "bin" / "trailhead")
    provenance._atomic_write_json(
        provenance.stamp_path(env=env),
        {"checkout": str(odd), "sha": "0" * 40, "wired_at": "2026-01-01T00:00:00Z",
         "last_check": None},
    )
    env["TH_HOSTILE"] = HOSTILE
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind=None),
        wait_seconds=10, poll_interval=0.02,
    )
    data = _stub_out(tmp_path, pids)
    assert out["started"] is True
    assert data["hostile"] == HOSTILE
    assert data["cwd"] == str(odd)
    assert sorted(p.name for p in odd.iterdir()) == ["bin"]
    assert sorted(p.name for p in odd.parent.iterdir()) == [odd.name, "checkout"]


def test_default_runner_execs_systemd_run_argv_without_a_shell(world, monkeypatch):
    tmp_path, env = world
    env["TH_HOSTILE"] = HOSTILE
    env["TH_ESC"] = "a\x1b[31mb"
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fake = fakebin / "systemd-run"
    fake.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'env': dict(os.environ)}},"
        f" open({str(tmp_path / 'fake-record.json')!r}, 'w'))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fakebin}:{os.environ['PATH']}")
    env["PATH"] = os.environ["PATH"]
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
        wait_seconds=0.2, poll_interval=0.01,
    )
    assert out["error"] == "could_not_start"
    record = json.loads((tmp_path / "fake-record.json").read_text())
    argv = record["argv"]
    assert "--setenv=TH_HOSTILE" in argv
    assert not any(HOSTILE in a or "caller-value" in a for a in argv)
    assert record["env"]["TH_HOSTILE"] == HOSTILE
    assert "--setenv=TH_ESC" in argv
    assert record["env"]["TH_ESC"] == "a\x1b[31mb"
    assert record["env"]["TH_CALLER_VAR"] == "caller-value"
    assert list(cwd.iterdir()) == []


# ---------------------------------------------------------------- the answers


def test_already_running_starts_nothing(world):
    tmp_path, env = world
    holder_id = "c" * 32
    fd = update_run.acquire_run_lock(holder_id, env=env)
    runner = FakeRunner()
    try:
        out = update_runner.detach(
            env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
            runner=runner, wait_seconds=1, poll_interval=0.01,
        )
    finally:
        update_run.release_run_lock(fd)
    assert out == {"started": False, "running": True, "run_id": holder_id}
    assert runner.calls == []


def test_unsupervised_already_running_spawns_nothing(stub):
    tmp_path, env, pids = stub
    fd = update_run.acquire_run_lock("c" * 32, env=env)
    try:
        out = update_runner.detach(
            env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind=None),
            wait_seconds=1, poll_interval=0.01,
        )
    finally:
        update_run.release_run_lock(fd)
    assert out == {"started": False, "running": True, "run_id": "c" * 32}
    assert not _log(tmp_path).exists()


def test_lock_taken_by_another_run_during_the_wait_is_running(world):
    tmp_path, env = world
    other = "d" * 32
    runner = FakeRunner(on_start=lambda argv, r: r.locks.append(
        update_run.acquire_run_lock(other, env=env)))
    try:
        out = update_runner.detach(
            env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
            runner=runner, wait_seconds=2, poll_interval=0.01,
        )
    finally:
        runner.close()
    assert out == {"started": False, "running": True, "run_id": other}
    assert len(runner.calls) == 1


def test_lock_taken_late_during_the_wait_is_still_seen(world):
    tmp_path, env = world
    box = {}

    def start(argv, runner):
        def later():
            time.sleep(0.3)
            runner.locks.append(update_run.acquire_run_lock(_run_id_from(argv), env=env))

        box["t"] = threading.Thread(target=later)
        box["t"].start()

    runner = FakeRunner(on_start=start)
    try:
        out = update_runner.detach(
            env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
            runner=runner, wait_seconds=5, poll_interval=0.02,
        )
    finally:
        box["t"].join()
        runner.close()
    assert out["started"] is True


def test_a_job_that_finished_before_the_poll_still_counts_as_started(world):
    tmp_path, env = world

    def start(argv, runner):
        update_run.release_run_lock(update_run.acquire_run_lock(_run_id_from(argv), env=env))

    runner = FakeRunner(on_start=start)
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
        runner=runner, wait_seconds=1, poll_interval=0.01,
    )
    assert out["started"] is True


def test_failing_runner_is_could_not_start(world):
    tmp_path, env = world
    runner = FakeRunner(rc=1)
    t0 = time.monotonic()
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
        runner=runner, wait_seconds=5, poll_interval=0.01,
    )
    assert out == {"started": False, "running": False, "error": "could_not_start"}
    assert time.monotonic() - t0 < 2


def test_runner_that_cannot_exec_is_could_not_start(world):
    tmp_path, env = world
    runner = FakeRunner(raises=FileNotFoundError("systemd-run"))
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
        runner=runner, wait_seconds=5, poll_interval=0.01,
    )
    assert out == {"started": False, "running": False, "error": "could_not_start"}


def test_job_that_never_takes_the_lock_is_could_not_start_after_the_wait(world):
    tmp_path, env = world
    runner = FakeRunner()
    t0 = time.monotonic()
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
        runner=runner, wait_seconds=0.4, poll_interval=0.02,
    )
    elapsed = time.monotonic() - t0
    assert out == {"started": False, "running": False, "error": "could_not_start"}
    assert 0.4 <= elapsed < 3
    assert len(runner.calls) == 1


def _start_by(argv) -> int:
    return int(argv[argv.index("--start-by") + 1])


def test_the_job_is_told_to_start_by_a_whole_second_just_after_the_wait(world):
    tmp_path, env = world
    runner = FakeRunner(on_start=_takes_lock(env))
    before = time.time()
    try:
        update_runner.detach(
            env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
            runner=runner, wait_seconds=3, poll_interval=0.01,
        )
    finally:
        runner.close()
    start_by = _start_by(runner.calls[0])
    assert before + 3 <= start_by <= before + 4.5


def test_could_not_start_is_never_answered_before_the_start_by_has_passed(world):
    tmp_path, env = world
    runner = FakeRunner()
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
        runner=runner, wait_seconds=0.2, poll_interval=0.02,
    )
    assert out == {"started": False, "running": False, "error": "could_not_start"}
    assert time.time() >= _start_by(runner.calls[0])


def _wait_for(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return False


def test_a_job_that_takes_the_lock_after_the_start_by_does_not_run(stub):
    tmp_path, env, pids = stub
    env["STUB_MODE"] = "at"
    env["STUB_OFFSET"] = "0.3"
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind=None),
        wait_seconds=0.3, poll_interval=0.02,
    )
    assert _wait_for(tmp_path / "stub-out.json")
    _stub_out(tmp_path, pids)
    assert out == {"started": False, "running": False, "error": "could_not_start"}
    assert _wait_for(tmp_path / "stub-out.json.expired"), "the late job never exited"
    assert not (tmp_path / "stub-out.json.ran").exists()
    assert update_run.current_holder(env=env) is None
    assert update_run.read_lock_record(env=env) is None


class _DeadlineClock:
    """update_runner's clock: real time until the job has started, then the
    first reading of it jumps past the start-by deadline, and only after the job
    has taken the lock (holding it from before that deadline), so the outcome
    cannot depend on how fast the job process starts."""

    def __init__(self, tmp_path: Path):
        self._out = tmp_path / "stub-out.json"
        self._fired = False

    def __getattr__(self, name):
        return getattr(time, name)

    def time(self) -> float:
        if self._fired or not self._out.exists():
            return time.time()
        self._fired = True
        start_by = int(json.loads(self._out.read_text())["argv"][-1])
        Path(str(self._out) + ".gate").touch()
        assert _wait_for(Path(str(self._out) + ".ran")), "the job never took the lock"
        return start_by + 1.0


def test_a_job_that_takes_the_lock_before_the_start_by_is_seen_as_started_even_if_the_deadline_passes_while_it_does(
    stub, monkeypatch
):
    tmp_path, env, pids = stub
    env["STUB_MODE"] = "gate"
    monkeypatch.setattr(update_runner, "time", _DeadlineClock(tmp_path))
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind=None),
        wait_seconds=10, poll_interval=0.02,
    )
    _stub_out(tmp_path, pids)
    assert out.get("started") is True, out
    assert (tmp_path / "stub-out.json.ran").exists()


def test_no_install_stamp_is_could_not_start_and_starts_nothing(tmp_path):
    env = _env(tmp_path)
    runner = FakeRunner()
    out = update_runner.detach(
        env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
        runner=runner, wait_seconds=0.2, poll_interval=0.01,
    )
    assert out == {"started": False, "running": False, "error": "could_not_start"}
    assert runner.calls == []


def test_each_detach_uses_a_fresh_valid_run_id(world):
    tmp_path, env = world
    ids = []
    for _ in range(2):
        runner = FakeRunner(on_start=_takes_lock(env))
        try:
            out = update_runner.detach(
                env=env, platform="linux", supervisor_dir=_sup_dir(tmp_path, kind="linux"),
                runner=runner, wait_seconds=2, poll_interval=0.01,
            )
        finally:
            runner.close()
        ids.append(out["run_id"])
    assert ids[0] != ids[1]
    assert all(update_run.validate_run_id(i) for i in ids)


# ------------------------------------------------------------------------ CLI


def _cli(monkeypatch, capsys, args, answer=None):
    if answer is not None:
        monkeypatch.setattr(update_runner, "detach", lambda **kw: answer)
    monkeypatch.setattr(sys, "argv", ["trailhead", "update", *args])
    rc = cli.main()
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


@pytest.mark.parametrize(
    "answer",
    [
        {"started": True, "run_id": "a" * 32},
        {"started": False, "running": True, "run_id": "b" * 32},
        {"started": False, "running": False, "error": "could_not_start"},
    ],
)
def test_cli_prints_exactly_one_json_line_and_exits_zero(monkeypatch, capsys, answer):
    rc, out, _ = _cli(monkeypatch, capsys, ["--detach", "--json"], answer)
    assert rc == 0
    assert out == json.dumps(answer) + "\n"


@pytest.mark.parametrize(
    "args",
    [
        ["--detach"],
        ["--detach", "--json", "--check"],
        ["--detach", "--json", "--status"],
        ["--detach", "--json", "--run-id", "a" * 32],
        ["--detach", "--json", "--dry-run"],
    ],
)
def test_cli_refuses_detach_without_json_or_with_other_modes(monkeypatch, capsys, args):
    called = []
    monkeypatch.setattr(update_runner, "detach", lambda **kw: called.append(1))
    rc, out, err = _cli(monkeypatch, capsys, args)
    assert rc == 1 and out == "" and err and called == []
