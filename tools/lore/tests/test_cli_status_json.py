"""``lore status`` readiness items: the ``--json`` report and the plain report.

Both outputs render from one list built by ``lore.cli.readiness``. Tests inject
the environment, the signing check, and the probe runner, and never touch the
network or a real ssh.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

import pytest

from conftest import write_vault_config


def _readiness():
    from lore.cli import readiness
    return readiness


def _init():
    from lore.cli import init
    return init


def _git(cwd, *args):
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"},
    )


def _git_vault(path, *, remote="https://example.invalid/team/vault.git"):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "note.md").write_text("x\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    return path


def _config(tmp_path, vaults):
    write_vault_config(tmp_path / "config", vaults)


def _env(tmp_path, *, makes_vault_content=True):
    """The env handed to the builder, mirroring the fenced ambient env."""
    env = dict(os.environ)
    cfg = tmp_path / "config" / "lore" / "config.json"
    if makes_vault_content is not None:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        data["makes_vault_content"] = makes_vault_content
        cfg.write_text(json.dumps(data), encoding="utf-8")
    return env


@pytest.fixture
def signing_ok(monkeypatch):
    """This host signs unattended: signing_mod.describe_status says so."""
    from lore.vault import signing
    monkeypatch.setattr(signing, "git_requires_signing", lambda: True)
    monkeypatch.setattr(
        signing, "describe_status", lambda env=None: (True, "this host signs unattended (key SHA256:abc)")
    )


def _ok_runner(argv, env, timeout):
    return 0, ""


def _build(tmp_path, runner=_ok_runner, **kw):
    if "env" not in kw:
        kw["env"] = _env(tmp_path)
    return _readiness().build_readiness_items(probe_runner=runner, **kw)


def _by_id(items):
    return {i.id: i for i in items}


@pytest.fixture
def healthy(tmp_path, signing_ok):
    vault = _git_vault(tmp_path / "vaults" / "default")
    _config(tmp_path, [("default", "default", vault)])
    return vault


# --- healthy fixture ---------------------------------------------------------


def test_healthy_fixture_every_item_ok_with_documented_json(tmp_path, healthy):
    items = _build(tmp_path)
    payload = json.loads(_readiness().render_json(items))

    assert payload["schema"] == 1
    assert [i["id"] for i in payload["items"]] == [
        "signing", "vault:default", "forge:default", "author",
    ]
    assert {i["state"] for i in payload["items"]} == {"ok"}
    for item in payload["items"]:
        assert set(item) <= {"id", "state", "summary", "detail"}
        assert isinstance(item["summary"], str) and item["summary"]


def test_status_json_prints_the_report_and_exits_zero(tmp_path, healthy, monkeypatch, capsys):
    import argparse
    monkeypatch.setattr(_readiness(), "_run_probe", _ok_runner)
    _env(tmp_path)
    rc = _init().cmd_status(argparse.Namespace(json=True))
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["schema"] == 1
    assert [i["id"] for i in payload["items"]][-1] == "author"


def test_status_json_exits_zero_even_when_items_are_missing(tmp_path, signing_ok, monkeypatch, capsys):
    import argparse
    _config(tmp_path, [("default", "default", tmp_path / "absent")])
    monkeypatch.setattr(_readiness(), "_run_probe", _ok_runner)
    rc = _init().cmd_status(argparse.Namespace(json=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert _by_id_json(payload)["vault:default"]["state"] == "missing"


def _by_id_json(payload):
    return {i["id"]: i for i in payload["items"]}


# --- signing -----------------------------------------------------------------


def _signing(monkeypatch, *, requires, describe):
    from lore.vault import signing
    monkeypatch.setattr(signing, "git_requires_signing", lambda: requires)
    monkeypatch.setattr(signing, "describe_status", describe)


def test_signing_not_configured_and_not_required_is_ok(tmp_path, healthy, monkeypatch):
    def boom(env=None):
        raise AssertionError("describe_status must not run for an unconfigured, unrequired host")
    _signing(monkeypatch, requires=False, describe=boom)
    assert _by_id(_build(tmp_path))["signing"].state == "ok"


def test_signing_not_configured_but_required_is_missing(tmp_path, healthy, monkeypatch):
    _signing(monkeypatch, requires=True,
             describe=lambda env=None: (False, "no signing key is configured — run `lore signing enable`"))
    item = _by_id(_build(tmp_path))["signing"]
    assert item.state == "missing"


def test_signing_failing_check_is_missing_without_a_fix_command(tmp_path, healthy, monkeypatch):
    _signing(monkeypatch, requires=True, describe=lambda env=None: (
        False, "the configured key /k is readable by others — run `chmod 600 /k`"))
    payload = json.loads(_readiness().render_json(_build(tmp_path)))
    item = _by_id_json(payload)["signing"]
    assert item["state"] == "missing"
    assert "readable by others" in item["summary"]
    assert "chmod" not in json.dumps(item)
    assert "lore signing enable" not in json.dumps(item)


def test_signing_check_that_errors_is_could_not_check(tmp_path, healthy, monkeypatch):
    def hang(env=None):
        raise subprocess.TimeoutExpired("ssh-keygen", 10)
    _signing(monkeypatch, requires=True, describe=hang)
    assert _by_id(_build(tmp_path))["signing"].state == "could-not-check"


# --- vault -------------------------------------------------------------------


def test_vault_directory_missing_is_missing_and_not_probed(tmp_path, signing_ok):
    _config(tmp_path, [("default", "default", tmp_path / "absent")])
    calls = []
    items = _by_id(_build(tmp_path, runner=lambda a, e, t: calls.append(a) or (0, "")))
    assert items["vault:default"].state == "missing"
    assert "forge:default" not in items
    assert calls == []


def test_vault_not_a_git_repo_is_missing_and_not_probed(tmp_path, signing_ok):
    plain = tmp_path / "plain"
    plain.mkdir()
    _config(tmp_path, [("default", "default", plain)])
    calls = []
    items = _by_id(_build(tmp_path, runner=lambda a, e, t: calls.append(a) or (0, "")))
    assert items["vault:default"].state == "missing"
    assert calls == []


def test_vault_without_remote_is_missing_and_not_probed(tmp_path, signing_ok):
    vault = _git_vault(tmp_path / "v", remote=None)
    _config(tmp_path, [("default", "default", vault)])
    calls = []
    items = _by_id(_build(tmp_path, runner=lambda a, e, t: calls.append(a) or (0, "")))
    assert items["vault:default"].state == "missing"
    assert "forge:default" not in items
    assert calls == []


def test_every_configured_vault_gets_its_own_items(tmp_path, signing_ok):
    a = _git_vault(tmp_path / "a")
    b = _git_vault(tmp_path / "b", remote=None)
    _config(tmp_path, [("default", "default", a), ("extra", "team", b)])
    items = _build(tmp_path)
    assert [i.id for i in items if i.id.startswith(("vault:", "forge:"))] == [
        "vault:default", "vault:extra", "forge:default",
    ]


def test_unreadable_vault_config_is_a_could_not_check_item(tmp_path, signing_ok):
    cfg = tmp_path / "config" / "lore"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text("[]", encoding="utf-8")
    items = _by_id(_readiness().build_readiness_items(env=dict(os.environ), probe_runner=_ok_runner))
    assert items["vault:default"].state == "could-not-check"
    assert "forge:default" not in items


# --- forge probe: outcomes ---------------------------------------------------


def _forge_state(tmp_path, runner):
    return _by_id(_build(tmp_path, runner=runner))["forge:default"]


def _stderr_runner(rc, stderr):
    return lambda argv, env, timeout: (rc, stderr)


@pytest.mark.parametrize("rc,stderr", [
    (0, ""),
    (2, ""),
])
def test_probe_success_and_empty_remote_are_ok(tmp_path, healthy, rc, stderr):
    assert _forge_state(tmp_path, _stderr_runner(rc, stderr)).state == "ok"


@pytest.mark.parametrize("stderr", [
    "git@host: Permission denied (publickey).\nfatal: Could not read from remote repository.",
    "fatal: could not read Username for 'https://h': terminal prompts disabled",
    "remote: Authentication failed for 'https://h/x'",
    "fatal: unable to access 'https://h/x': The requested URL returned error: 401",
    "fatal: unable to access 'https://h/x': The requested URL returned error: 403",
    "ERROR: Repository not found.\nfatal: Could not read from remote repository.",
    "fatal: repository 'https://h/x/' not found",
])
def test_probe_credential_refusal_is_missing(tmp_path, healthy, stderr):
    assert _forge_state(tmp_path, _stderr_runner(128, stderr)).state == "missing"


@pytest.mark.parametrize("stderr", [
    "ssh: Could not resolve hostname h: Name or service not known",
    "fatal: unable to access 'https://h/': Failed to connect to h port 443",
    "ssh: connect to host h port 22: Connection refused",
    "ssh: connect to host h port 22: Connection timed out",
    "Connection closed: banner exchange: timeout",
    "ssh: connect to host h port 22: Network is unreachable",
    "fatal: unable to access 'https://h/': SSL certificate problem: certificate verification failed",
    "fatal: unable to access 'https://h/': The requested URL returned error: 503",
    "fatal: transport 'file' not allowed",
    "something nobody has ever seen",
    "sh: 1: ssh: not found",
    "fatal: cannot run ssh: No such file or directory\nsh: ssh: not found",
])
def test_probe_unreachable_or_unrecognised_is_could_not_check(tmp_path, healthy, stderr):
    assert _forge_state(tmp_path, _stderr_runner(128, stderr)).state == "could-not-check"


def test_probe_unexpected_exit_code_is_could_not_check_never_ok(tmp_path, healthy):
    assert _forge_state(tmp_path, _stderr_runner(1, "")).state == "could-not-check"


def test_probe_timeout_is_could_not_check_and_says_to_retry(tmp_path, healthy):
    def runner(argv, env, timeout):
        raise subprocess.TimeoutExpired(argv, timeout)
    item = _forge_state(tmp_path, runner)
    assert item.state == "could-not-check"
    assert "retry when online" in item.summary


@pytest.mark.parametrize("stderr", [
    "Host key verification failed.",
    "@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@",
])
def test_probe_host_key_is_could_not_check_with_its_own_summary(tmp_path, healthy, stderr):
    item = _forge_state(tmp_path, _stderr_runner(128, stderr))
    unreachable = _forge_state(tmp_path, _stderr_runner(128, "Connection refused"))
    assert item.state == "could-not-check"
    assert "host key" in item.summary
    assert item.summary != unreachable.summary


def test_probe_ok_summary_names_the_shell_credential_scope(tmp_path, healthy):
    assert "reachable with the credential in this shell" in _forge_state(tmp_path, _ok_runner).summary


# --- forge probe: argv and env -----------------------------------------------


def _capture(tmp_path):
    seen = {}

    def runner(argv, env, timeout):
        seen["argv"], seen["env"], seen["timeout"] = argv, env, timeout
        return 0, ""

    _build(tmp_path, runner=runner)
    return seen


def test_probe_argv_probes_by_remote_name_after_double_dash(tmp_path, healthy):
    argv = _capture(tmp_path)["argv"]
    assert argv == ["git", "-C", str(healthy), "ls-remote", "--exit-code", "--", "origin", "HEAD"]


@pytest.mark.parametrize("name,expected", [
    ("GIT_TERMINAL_PROMPT", "0"),
    ("GIT_ASKPASS", ""),
    ("SSH_ASKPASS", ""),
    ("GIT_ALLOW_PROTOCOL", "https:ssh:git"),
    ("GCM_INTERACTIVE", "never"),
])
def test_probe_env_sets_each_hardening_variable(tmp_path, healthy, name, expected):
    env = _capture(tmp_path)["env"]
    assert env[name] == expected


@pytest.mark.parametrize("option", ["-o BatchMode=yes", "-o ConnectTimeout=5"])
def test_probe_env_ssh_command_carries_each_option(tmp_path, healthy, option):
    assert option in _capture(tmp_path)["env"]["GIT_SSH_COMMAND"]


def test_probe_env_overrides_a_hostile_inherited_env(tmp_path, healthy):
    env = _env(tmp_path)
    env.update({"GIT_TERMINAL_PROMPT": "1", "GIT_ASKPASS": "/bin/evil", "GCM_INTERACTIVE": "always"})
    seen = {}
    _readiness().build_readiness_items(
        env=env, probe_runner=lambda a, e, t: seen.update(env=e) or (0, ""))
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["env"]["GIT_ASKPASS"] == ""
    assert seen["env"]["GCM_INTERACTIVE"] == "never"


def test_probe_timeout_stays_within_the_total_cap(tmp_path, healthy):
    assert _capture(tmp_path)["timeout"] <= 20


# --- leak --------------------------------------------------------------------

_LEAK = "fatal: unable to access 'https://user:tok3n@host/x': The requested URL returned error: 401"


def test_probe_stderr_secret_never_reaches_the_json(tmp_path, healthy):
    items = _build(tmp_path, runner=_stderr_runner(128, _LEAK))
    rendered = _readiness().render_json(items)
    assert _by_id(items)["forge:default"].state == "missing"
    assert "tok3n" not in rendered
    assert "user:" not in rendered


def test_remote_credential_never_reaches_the_plain_output(tmp_path, signing_ok, monkeypatch, capsys):
    import argparse
    vault = _git_vault(tmp_path / "v", remote="https://user:tok3n@host/x.git")
    _config(tmp_path, [("default", "default", vault)])
    monkeypatch.setattr(_readiness(), "_run_probe", _stderr_runner(128, _LEAK))
    monkeypatch.setattr(_init(), "_detect_harnesses", lambda: [])
    _init().cmd_status(argparse.Namespace(json=False))
    captured = capsys.readouterr()
    assert "tok3n" not in captured.out
    assert "tok3n" not in captured.err


# --- total cap ---------------------------------------------------------------


def test_three_hanging_probes_return_within_the_cap_all_could_not_check(tmp_path, signing_ok):
    vaults = [(n, "default" if n == "a" else "team", _git_vault(tmp_path / n)) for n in "abc"]
    _config(tmp_path, vaults)
    release = threading.Event()
    try:
        started = time.monotonic()
        items = _readiness().build_readiness_items(
            env=_env(tmp_path),
            probe_runner=lambda argv, env, timeout: release.wait(30) and (0, ""),
            probe_timeout=30.0,
            total_cap=0.5,
        )
        elapsed = time.monotonic() - started
    finally:
        release.set()
    forge = [i for i in items if i.id.startswith("forge:")]
    assert len(forge) == 3
    assert {i.state for i in forge} == {"could-not-check"}
    assert elapsed < 3


def _hanging_git_on_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    real = shutil.which("git")
    git.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"remote get-url"*|*"config --get core.sshCommand"*) exec sleep 30;;\n'
        "esac\n"
        f'exec {real} "$@"\n'
    )
    git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_hanging_pre_probe_git_and_hanging_runner_return_within_the_cap(tmp_path, signing_ok, monkeypatch):
    vaults = [(n, "default" if n == "a" else "team", _git_vault(tmp_path / n)) for n in "ab"]
    _config(tmp_path, vaults)
    env = _env(tmp_path)
    env.pop("GIT_SSH_COMMAND", None)
    real_collect = _readiness().collect_vaults

    def collect_then_hang_git():
        result = real_collect()
        _hanging_git_on_path(tmp_path, monkeypatch)
        return result

    monkeypatch.setattr(_readiness(), "collect_vaults", collect_then_hang_git)
    release = threading.Event()
    try:
        started = time.monotonic()
        items = _readiness().build_readiness_items(
            env=env,
            probe_runner=lambda argv, env, timeout: release.wait(30) and (0, ""),
            probe_timeout=30.0,
            total_cap=1.0,
        )
        elapsed = time.monotonic() - started
    finally:
        release.set()
    assert {i.state for i in items if i.id.startswith("forge:")} == {"could-not-check"}
    assert elapsed < 2.5


def test_probe_timeout_is_the_budget_remaining_at_spawn(tmp_path, healthy, monkeypatch):
    real = _readiness()._git_output

    def slow_git_output(*args, **kwargs):
        time.sleep(0.4)
        return real(*args, **kwargs)

    monkeypatch.setattr(_readiness(), "_git_output", slow_git_output)
    seen = {}

    def runner(argv, env, timeout):
        seen["timeout"] = timeout
        return 0, ""

    _build(tmp_path, runner=runner, probe_timeout=15.0, total_cap=2.0)
    assert seen["timeout"] <= 2.0 - 0.4


def _pid_gone(pid, wait=3.0):
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_no_probe_process_outlives_the_call_when_the_cap_expires(tmp_path, healthy):
    pidfile = tmp_path / "probe.pid"
    release = threading.Event()
    spawned = []

    def runner(argv, env, timeout):
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        spawned.append(proc)
        pidfile.write_text(str(proc.pid))
        release.wait(30)
        return 0, ""

    try:
        items = _build(tmp_path, runner=runner, probe_timeout=30.0, total_cap=0.5)
        pid = int(pidfile.read_text())
        assert _by_id(items)["forge:default"].state == "could-not-check"
        assert _pid_gone(pid)
    finally:
        release.set()
        for proc in spawned:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def test_probes_run_concurrently(tmp_path, signing_ok):
    vaults = [(n, "default" if n == "a" else "team", _git_vault(tmp_path / n)) for n in "abc"]
    _config(tmp_path, vaults)
    barrier = threading.Barrier(3, timeout=5)

    def runner(argv, env, timeout):
        barrier.wait()
        return 0, ""

    items = _readiness().build_readiness_items(env=_env(tmp_path), probe_runner=runner)
    assert {i.state for i in items if i.id.startswith("forge:")} == {"ok"}


# --- default runner ----------------------------------------------------------


def test_default_runner_kills_the_whole_process_group_on_timeout(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time\n"
        f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
        "time.sleep(8)\n"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _readiness()._run_probe([sys.executable, "-c", script], dict(os.environ), 1.5)
    assert time.monotonic() - started < 5
    child = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    alive = True
    while time.monotonic() < deadline and alive:
        try:
            os.kill(child, 0)
            time.sleep(0.05)
        except ProcessLookupError:
            alive = False
    assert not alive


def test_default_runner_returns_exit_code_and_stderr():
    rc, err = _readiness()._run_probe(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(128)"],
        dict(os.environ), 5)
    assert (rc, err) == (128, "boom")


def test_real_git_against_a_local_remote_is_refused_by_the_protocol_allow_list(tmp_path, signing_ok):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    vault = _git_vault(tmp_path / "v", remote=str(bare))
    _config(tmp_path, [("default", "default", vault)])
    items = _by_id(_readiness().build_readiness_items(env=_env(tmp_path)))
    assert items["forge:default"].state == "could-not-check"


def _seen_probe(tmp_path, *, env=None):
    seen = {}

    def runner(argv, env, timeout):
        seen["env"] = env
        return 0, ""

    kw = {} if env is None else {"env": env}
    _build(tmp_path, runner=runner, **kw)
    return seen["env"]


_BATCH = "-o BatchMode=yes -o ConnectTimeout=5"


def test_probe_ssh_command_is_plain_ssh_when_nothing_else_is_set(tmp_path, healthy):
    assert _seen_probe(tmp_path, env=_env_from(tmp_path, {}))["GIT_SSH_COMMAND"] == f"ssh {_BATCH}"


def _env_from(tmp_path, base):
    env = _env(tmp_path)
    env.pop("GIT_SSH_COMMAND", None)
    env.update({k: v for k, v in base.items() if k == "GIT_SSH_COMMAND"})
    return env


def test_probe_ssh_command_uses_the_vault_core_ssh_command(tmp_path, healthy):
    _git(healthy, "config", "core.sshCommand", "ssh -i /k")
    assert _seen_probe(tmp_path, env=_env_from(tmp_path, {}))["GIT_SSH_COMMAND"] == f"ssh -i /k {_BATCH}"


def test_probe_ssh_command_prefers_the_inherited_env_over_the_vault(tmp_path, healthy):
    _git(healthy, "config", "core.sshCommand", "ssh -i /k")
    env = _env_from(tmp_path, {"GIT_SSH_COMMAND": "ssh -F /cfg"})
    assert _seen_probe(tmp_path, env=env)["GIT_SSH_COMMAND"] == f"ssh -F /cfg {_BATCH}"


def test_probe_ssh_command_ignores_an_empty_inherited_env(tmp_path, healthy):
    _git(healthy, "config", "core.sshCommand", "ssh -i /k")
    env = _env_from(tmp_path, {"GIT_SSH_COMMAND": ""})
    assert _seen_probe(tmp_path, env=env)["GIT_SSH_COMMAND"] == f"ssh -i /k {_BATCH}"


def test_probe_ssh_command_is_chosen_per_vault(tmp_path, signing_ok):
    a = _git_vault(tmp_path / "vaults" / "default")
    b = _git_vault(tmp_path / "vaults" / "extra")
    _git(b, "config", "core.sshCommand", "ssh -i /extra")
    _config(tmp_path, [("default", "default", a), ("extra", "team", b)])
    seen = {}

    def runner(argv, env, timeout):
        seen[argv[2]] = env["GIT_SSH_COMMAND"]
        return 0, ""

    _build(tmp_path, runner=runner, env=_env_from(tmp_path, {}))
    assert seen == {str(a): f"ssh {_BATCH}", str(b): f"ssh -i /extra {_BATCH}"}


@pytest.mark.parametrize("remote,phrase", [
    ("git@example.invalid:team/vault.git", "refused this shell's ssh key"),
    ("ssh://git@example.invalid/team/vault.git", "refused this shell's ssh key"),
    ("https://example.invalid/team/vault.git", "refused this shell's https credential"),
])
def test_forge_missing_summary_names_the_transport(tmp_path, signing_ok, remote, phrase):
    vault = _git_vault(tmp_path / "vaults" / "default", remote=remote)
    _config(tmp_path, [("default", "default", vault)])
    item = _forge_state(tmp_path, _stderr_runner(128, "fatal: Authentication failed"))
    assert item.state == "missing"
    assert phrase in item.summary


# --- author ------------------------------------------------------------------


def test_author_absent_is_undeclared_missing_and_plain_output_names_it(tmp_path, healthy, monkeypatch, capsys):
    import argparse
    items = _by_id(_build(tmp_path, env=_env(tmp_path, makes_vault_content=None)))
    assert items["author"].state == "missing"
    assert items["author"].detail == "undeclared"

    monkeypatch.setattr(_init(), "_detect_harnesses", lambda: [])
    _init().cmd_status(argparse.Namespace(json=False))
    assert "lore: author: " in capsys.readouterr().out


def test_author_true_is_ok_and_plain_output_has_no_author_line(tmp_path, healthy, monkeypatch, capsys):
    import argparse
    items = _by_id(_build(tmp_path, env=_env(tmp_path, makes_vault_content=True)))
    assert items["author"].state == "ok"
    assert items["author"].detail == "yes"

    monkeypatch.setattr(_init(), "_detect_harnesses", lambda: [])
    _init().cmd_status(argparse.Namespace(json=False))
    assert "author" not in capsys.readouterr().out


def test_author_false_is_ok_with_detail_no(tmp_path, healthy):
    items = _by_id(_build(tmp_path, env=_env(tmp_path, makes_vault_content=False)))
    assert items["author"].state == "ok"
    assert items["author"].detail == "no"


def test_author_maybe_is_malformed_missing_and_plain_output_names_it(tmp_path, healthy, monkeypatch, capsys):
    import argparse
    items = _by_id(_build(tmp_path, env=_env(tmp_path, makes_vault_content="maybe")))
    assert items["author"].state == "missing"
    assert items["author"].detail == "malformed"

    monkeypatch.setattr(_init(), "_detect_harnesses", lambda: [])
    _init().cmd_status(argparse.Namespace(json=False))
    out = capsys.readouterr().out
    assert "lore: author: malformed" in out
    assert "lore vault config --makes-vault-content yes" in out


def _break_config(tmp_path, text):
    (tmp_path / "config" / "lore" / "config.json").write_text(text, encoding="utf-8")


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_author_with_an_invalid_config_is_could_not_check(tmp_path, healthy, text):
    env = _env(tmp_path)
    _break_config(tmp_path, text)
    item = _by_id(_build(tmp_path, env=env))["author"]
    assert item.state == "could-not-check"
    assert item.detail is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_author_with_an_unreadable_config_is_could_not_check(tmp_path, healthy):
    env = _env(tmp_path)
    cfg = tmp_path / "config" / "lore" / "config.json"
    cfg.chmod(0)
    try:
        item = _by_id(_build(tmp_path, env=env))["author"]
    finally:
        cfg.chmod(0o600)
    assert item.state == "could-not-check"


# --- plain output unchanged --------------------------------------------------


class _FakeHarness:
    name = "fake"

    def user_ruleset_status(self, name, content):
        return "stale"


def test_plain_output_is_byte_identical_for_a_declared_author(tmp_path, monkeypatch, capsys):
    import argparse
    from lore.vault import signing
    good = _git_vault(tmp_path / "good")
    _config(tmp_path, [("default", "default", good), ("gone", "team", tmp_path / "absent")])
    _env(tmp_path, makes_vault_content=True)
    monkeypatch.setattr(signing, "git_requires_signing", lambda: False)
    monkeypatch.setattr(_init(), "_detect_harnesses", lambda: [_FakeHarness()])

    probes = []
    monkeypatch.setattr(_readiness(), "_run_probe", lambda *a, **k: probes.append(a) or (0, ""))

    rc = _init().cmd_status(argparse.Namespace(json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert probes == []
    assert out == (
        "lore: fake: ruleset stale — re-run `lore init` to install it\n"
        "lore: signing: not configured — vault commits follow your git settings, "
        "which do not require signing\n"
        "lore: vault default: never pushed — no upstream branch set"
        " — run `lore sync --vault default`\n"
        "lore: vault gone: directory does not exist"
        " — create the directory, or correct its path in config.json\n"
    )
