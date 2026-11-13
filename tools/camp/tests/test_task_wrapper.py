"""Test contract: camp runs every per-member setup step through a wrapper the caller supplies.

`CAMP_TASK_WRAPPER` (a JSON array of strings; `{worktree}` and `{member}` are
substituted per member) is read when a workspace is created and recorded in the
workspace manifest as `task_wrapper`. Every later run of that workspace's steps
(`camp setup`, `camp activate`, reconcile) uses the RECORDED wrapper, whatever
the shell's environment says.

Lifecycle cases run camp's own code in this process (`run_camp`, and the
seed / reconcile functions) against a scratch group under scratch
CAMP_CONFIG_DIR / CAMP_STATE_DIR / HOME: `camp setup --background` and
`camp activate --background` are the bodies the detached provisioner runs, and
they complete before they return. The one seam where the process boundary is
the subject, `camp new` handing the workspace to a detached provisioner, spawns
the real CLI once. The wrapper is a script that itself writes a marker (the
member's substituted worktree and the argv it wrapped) and then execs the
wrapped argv, so a marker proves the step really ran *through* the wrapper.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ._helpers import CLI_CAMP, init_git_repo, run_camp

VAR = "CAMP_TASK_WRAPPER"

_WRAP_PY = """\
import json, os, sys
args = sys.argv[1:]
i = args.index("--")
opts, cmd = args[:i], args[i + 1:]
policy = json.load(open(opts[opts.index("--policy") + 1]))
worktree = opts[opts.index("--worktree") + 1]
member = opts[opts.index("--member") + 1]
with open(os.path.join(policy["marker_dir"], "wrapper.log"), "a") as f:
    f.write(json.dumps({"member": member, "worktree": worktree, "cmd": cmd}) + "\\n")
os.execvp(cmd[0], cmd)
"""

_STEP_PY = """\
import os, sys
marker_dir, worktree = sys.argv[1], sys.argv[2]
if os.path.exists(os.path.join(marker_dir, "require-gate")) and not os.path.exists(
    os.path.join(marker_dir, "gate")
):
    sys.exit(1)
name = os.path.basename(worktree)
open(os.path.join(marker_dir, "ran-" + name), "a").write("x")
"""


class Scenario:
    def __init__(self, tmp_path: Path, *, members=("m1", "m2")):
        self.tmp = tmp_path
        self.cfg = tmp_path / "cfg"
        self.state = tmp_path / "state"
        self.home = tmp_path / "home"
        self.marks = tmp_path / "marks"
        for d in (self.cfg / "groups", self.state, self.home, self.marks):
            d.mkdir(parents=True)
        self.base_env = {
            **{k: v for k, v in os.environ.items() if k != VAR},
            "CAMP_CONFIG_DIR": str(self.cfg),
            "CAMP_STATE_DIR": str(self.state),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".state"),
            "XDG_DATA_HOME": str(self.home / ".data"),
        }
        self.members = list(members)
        self.wrap = tmp_path / "wrap.py"
        self.wrap.write_text(_WRAP_PY)
        self.step = tmp_path / "step.py"
        self.step.write_text(_STEP_PY)
        self.policy = tmp_path / "policy.json"
        self.policy.write_text(json.dumps({"marker_dir": str(self.marks)}))
        args = []
        for name in self.members:
            repo = tmp_path / f"repo-{name}"
            init_git_repo(repo, origin=True)
            args += ["--member", f"{name}={repo}"]
        r = self.camp("group", "g", *args)
        assert r.returncode == 0, r.stderr

    def camp(self, *argv, env=None):
        return run_camp(list(argv), env=env if env is not None else self.base_env)

    def camp_cli(self, *argv, env=None):
        return subprocess.run(
            [sys.executable, str(CLI_CAMP), *argv],
            capture_output=True,
            text=True,
            env=env if env is not None else self.base_env,
            timeout=120,
        )

    def wrapper_json(self, **overrides) -> str:
        argv = [
            sys.executable,
            str(self.wrap),
            "--policy",
            str(self.policy),
            "--worktree",
            "{worktree}",
            "--member",
            "{member}",
            "--",
        ]
        return json.dumps(overrides.get("argv", argv))

    def define_task(self, *, phase="provision", required=True, cleanup=None, name="t"):
        toml = self.cfg / "groups" / "g.toml"
        body = toml.read_text()
        for m in self.members:
            body = body.replace(f'name = "{m}"', f'name = "{m}"\ntasks = ["{name}"]', 1)
        step_cmd = [sys.executable, str(self.step), str(self.marks), "{worktree}"]
        body += (
            f'\n[tasks.{name}]\nphase = "{phase}"\nrequired = {str(required).lower()}\n'
        )
        if cleanup is not None:
            body += f"cleanup = {json.dumps(cleanup)}\n"
        body += (
            f'\n[[tasks.{name}.steps]]\nname = "s"\ncmd = {json.dumps(step_cmd)}\n'
        )
        toml.write_text(body)

    def env_with(self, wrapper: str | None) -> dict[str, str]:
        env = dict(self.base_env)
        if wrapper is not None:
            env[VAR] = wrapper
        return env

    def new(self, wrapper: str | None) -> None:
        """What `camp new` does before the harness launches, run in-process:
        seed the workspace (recording the wrapper) and run the provisioner's body."""
        from camp.group.config import load_group
        from camp.provision.provision import seed_pending_workspace

        env = self.env_with(wrapper)
        seed_pending_workspace(load_group(self.cfg / "groups" / "g.toml"), "s1", env=env)
        r = self.camp("setup", "--background", "s1", "--group", "g", env=env)
        assert r.returncode == 0, r.stderr

    def new_via_cli(self, wrapper: str | None) -> None:
        r = self.camp_cli(
            "new", "s1", "--group", "g", "--no-session", "--json", env=self.env_with(wrapper)
        )
        assert r.returncode == 0, r.stderr

    def status(self) -> int:
        return self.camp("status", "--name", "s1", "--group", "g").returncode

    def wait_settled(self) -> subprocess.CompletedProcess:
        deadline = time.monotonic() + 90
        while True:
            st = self.camp_cli("status", "--name", "s1", "--group", "g")
            if st.returncode != 2 or time.monotonic() > deadline:
                return st
            time.sleep(0.2)  # poll interval on the observable exit code

    def wrapper_log(self) -> list[dict]:
        path = self.marks / "wrapper.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def manifest(self) -> dict:
        found = list(self.state.rglob("manifest.json"))
        assert len(found) == 1, found
        return json.loads(found[0].read_text())

    def ws_dir(self) -> Path:
        return Path(self.manifest()["members"][0]["worktree_path"]).parent


@pytest.fixture
def sc(tmp_path):
    return Scenario(tmp_path)


def test_each_member_step_runs_through_the_wrapper_with_its_own_worktree(sc):
    # The one real-process seam: `camp new` hands the workspace to a detached
    # provisioner. The wait is a bounded poll on `camp status`'s exit code.
    sc.define_task()
    wrapper = sc.wrapper_json()
    sc.new_via_cli(wrapper)
    assert sc.wait_settled().returncode == 0

    ws = sc.ws_dir()
    by_member = {e["member"]: e for e in sc.wrapper_log()}
    assert set(by_member) == {"m1", "m2"}
    assert by_member["m1"]["worktree"] == str(ws / "m1")
    assert by_member["m2"]["worktree"] == str(ws / "m2")
    assert by_member["m1"]["cmd"][0] == sys.executable
    assert (sc.marks / "ran-m1").exists() and (sc.marks / "ran-m2").exists()
    assert sc.manifest()["task_wrapper"] == wrapper


def test_each_member_step_is_wrapped_with_its_own_worktree_in_process(sc):
    sc.define_task()
    wrapper = sc.wrapper_json()
    sc.new(wrapper)
    assert sc.status() == 0

    ws = sc.ws_dir()
    by_member = {e["member"]: e for e in sc.wrapper_log()}
    assert {m: e["worktree"] for m, e in by_member.items()} == {
        "m1": str(ws / "m1"),
        "m2": str(ws / "m2"),
    }
    assert (sc.marks / "ran-m1").exists() and (sc.marks / "ran-m2").exists()
    assert sc.manifest()["task_wrapper"] == wrapper


def test_setup_rerun_with_var_unset_applies_the_recorded_wrapper(sc):
    sc.define_task(required=False)
    (sc.marks / "require-gate").write_text("")
    sc.new(sc.wrapper_json())
    assert sc.status() == 0
    assert not (sc.marks / "ran-m1").exists()
    first_runs = len(sc.wrapper_log())
    assert first_runs == 2

    (sc.marks / "gate").write_text("")
    r = sc.camp("setup", "s1", "--group", "g")
    assert r.returncode == 0, r.stderr

    assert (sc.marks / "ran-m1").exists() and (sc.marks / "ran-m2").exists()
    assert len(sc.wrapper_log()) == first_runs + 2


def test_recorded_wrapper_wins_over_a_different_wrapper_in_the_rerun_env(sc):
    sc.define_task(required=False)
    (sc.marks / "require-gate").write_text("")
    sc.new(sc.wrapper_json())
    assert sc.status() == 0
    (sc.marks / "gate").write_text("")

    env = {**sc.base_env, VAR: json.dumps([str(sc.tmp / "no-such-wrapper")])}
    r = sc.camp("setup", "s1", "--group", "g", env=env)
    assert r.returncode == 0, r.stderr
    assert (sc.marks / "ran-m1").exists()
    assert len(sc.wrapper_log()) == 4


def test_activate_with_var_unset_applies_the_recorded_wrapper(sc):
    sc.define_task(phase="activate")
    sc.new(sc.wrapper_json())
    assert sc.status() == 0
    assert sc.wrapper_log() == []

    r = sc.camp("activate", "--background", "m1", "--name", "s1", "--group", "g")
    assert r.returncode == 0, r.stderr
    assert (sc.marks / "ran-m1").exists()
    log = sc.wrapper_log()
    assert [e["member"] for e in log] == ["m1"]
    assert log[0]["worktree"] == str(sc.ws_dir() / "m1")


@pytest.mark.parametrize("unset", [None, "", "   "], ids=["absent", "empty", "blank"])
def test_unwrapped_workspace_runs_steps_bare(sc, unset):
    sc.define_task()
    sc.new(unset)
    assert sc.status() == 0
    assert (sc.marks / "ran-m1").exists() and (sc.marks / "ran-m2").exists()
    assert sc.wrapper_log() == []
    assert "task_wrapper" not in sc.manifest()


def test_missing_recorded_policy_file_fails_the_step_closed(sc):
    sc.define_task()
    (sc.marks / "require-gate").write_text("")
    sc.new(sc.wrapper_json())
    assert sc.status() == 3
    (sc.marks / "gate").write_text("")
    sc.policy.unlink()

    sc.camp("setup", "s1", "--group", "g")
    assert sc.status() == 3
    assert not (sc.marks / "ran-m1").exists()
    assert not (sc.marks / "ran-m2").exists()
    reasons = " ".join(m.get("reason", "") for m in sc.manifest()["members"])
    assert f"policy file {sc.policy} is missing" in reasons
    assert "task wrapper" in reasons


def test_missing_recorded_wrapper_executable_fails_the_step_closed(sc):
    exe = sc.tmp / "wrapper-exe"
    exe.write_text("#!/bin/sh\nexec \"$@\"\n")
    exe.chmod(0o755)
    sc.define_task()
    (sc.marks / "require-gate").write_text("")
    sc.new(json.dumps([str(exe), "--"]))
    assert sc.status() == 3
    (sc.marks / "gate").write_text("")
    exe.unlink()

    sc.camp("setup", "s1", "--group", "g")
    assert sc.status() == 3
    assert not (sc.marks / "ran-m1").exists()
    reasons = " ".join(m.get("reason", "") for m in sc.manifest()["members"])
    assert f"executable {exe} is not found" in reasons


@pytest.mark.parametrize(
    "raw, reason_part",
    [
        ("not json at all", "is not valid JSON"),
        ('{"a": 1}', "must be a JSON array of non-empty strings"),
        ('["ok", ""]', "must be a JSON array of non-empty strings"),
        ('["ok", 3]', "must be a JSON array of non-empty strings"),
        ("[]", "must be a JSON array of non-empty strings"),
        ('["ok", "{bogus}"]', "unknown placeholder {bogus}"),
    ],
)
def test_malformed_wrapper_fails_each_step_closed_and_status_exits_3(sc, raw, reason_part):
    sc.define_task()
    sc.new(raw)
    assert sc.status() == 3
    assert not (sc.marks / "ran-m1").exists()
    assert not (sc.marks / "ran-m2").exists()
    reasons = [m.get("reason", "") for m in sc.manifest()["members"]]
    assert all("task wrapper (CAMP_TASK_WRAPPER)" in r and reason_part in r for r in reasons), reasons


def test_stray_wrapper_variable_on_an_unwrapped_workspace_fails_setup_closed(sc):
    sc.define_task()
    (sc.marks / "require-gate").write_text("")
    sc.new(None)
    assert sc.status() == 3
    (sc.marks / "gate").write_text("")

    env = {**sc.base_env, VAR: sc.wrapper_json()}
    sc.camp("setup", "s1", "--group", "g", env=env)
    assert sc.status() == 3
    assert not (sc.marks / "ran-m1").exists()
    assert sc.wrapper_log() == []
    reasons = " ".join(m.get("reason", "") for m in sc.manifest()["members"])
    assert "was created without a task wrapper" in reasons


def test_retry_cleanup_runs_through_the_wrapper_too(sc):
    marker = sc.marks / "cleaned"
    cleanup = [
        sys.executable,
        "-c",
        "import sys; open(sys.argv[1], 'w').write('c')",
        str(marker),
    ]
    sc.define_task(required=False, cleanup=cleanup)
    (sc.marks / "require-gate").write_text("")
    sc.new(sc.wrapper_json())
    assert sc.status() == 0
    (sc.marks / "gate").write_text("")

    sc.camp("setup", "s1", "--group", "g")
    assert marker.exists()
    assert any(e["cmd"] == cleanup for e in sc.wrapper_log())


def test_capabilities_json_lists_task_wrapper(tmp_path):
    r = run_camp(["capabilities", "--json"], env={**os.environ, "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    assert "task-wrapper" in json.loads(r.stdout)["capabilities"]


# ---------------------------------------------------------------------------
# The wrapper module itself: parsing, substitution, and the policy-file rule.
# ---------------------------------------------------------------------------

from camp.provision.wrapper import WrapperError, wrapped_argv  # noqa: E402


def _wrap(raw, step=("echo", "hi"), worktree="/w/m1", member="m1"):
    return wrapped_argv(raw, list(step), worktree=worktree, member=member)


def test_wrapped_argv_prefixes_the_substituted_wrapper(tmp_path):
    exe = tmp_path / "w"
    exe.write_text("")
    exe.chmod(0o755)
    raw = json.dumps([str(exe), "--wt={worktree}", "{member}", "--"])
    assert _wrap(raw) == [str(exe), "--wt=/w/m1", "m1", "--", "echo", "hi"]


def test_wrapped_argv_follows_the_member_it_is_given(tmp_path):
    exe = tmp_path / "w"
    exe.write_text("")
    exe.chmod(0o755)
    raw = json.dumps([str(exe), "{member}"])
    assert _wrap(raw, member="a")[1] == "a"
    assert _wrap(raw, member="b")[1] == "b"


def test_every_absolute_path_argument_must_still_exist(tmp_path):
    exe = tmp_path / "w"
    exe.write_text("")
    exe.chmod(0o755)
    policy = tmp_path / "policy.toml"
    raw = json.dumps([str(exe), "--policy", str(policy), "--"])
    with pytest.raises(WrapperError, match="policy file .*policy.toml is missing"):
        _wrap(raw)
    policy.write_text("")
    assert _wrap(raw)[:3] == [str(exe), "--policy", str(policy)]


def test_a_relative_argument_is_not_treated_as_a_policy_file(tmp_path):
    exe = tmp_path / "w"
    exe.write_text("")
    exe.chmod(0o755)
    raw = json.dumps([str(exe), "--policy", "relative/nope.toml", "--"])
    assert _wrap(raw)[2] == "relative/nope.toml"


def test_an_absolute_path_built_from_a_placeholder_is_not_checked(tmp_path):
    exe = tmp_path / "w"
    exe.write_text("")
    exe.chmod(0o755)
    raw = json.dumps([str(exe), "/not/yet/there/{member}", "--"])
    assert _wrap(raw)[1] == "/not/yet/there/m1"


def test_the_executable_resolves_on_path_when_not_absolute(tmp_path, monkeypatch):
    tool = tmp_path / "wraptool"
    tool.write_text("")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert _wrap(json.dumps(["wraptool", "--"]))[0] == "wraptool"
    with pytest.raises(WrapperError, match="executable othertool is not found"):
        _wrap(json.dumps(["othertool", "--"]))


# ---------------------------------------------------------------------------
# Reconcile (the SessionStart path) creates and reruns steps in-process.
# ---------------------------------------------------------------------------


def test_reconcile_records_the_wrapper_on_creation_and_reuses_it_when_the_var_is_unset(sc):
    from camp.group.config import load_group
    from camp.provision.reconcile import reconcile_worktree

    sc.define_task(required=False)
    (sc.marks / "require-gate").write_text("")
    group = load_group(sc.cfg / "groups" / "g.toml")

    reconcile_worktree(group, "s1", env={**sc.base_env, VAR: sc.wrapper_json()})
    assert sc.manifest()["task_wrapper"] == sc.wrapper_json()
    assert len(sc.wrapper_log()) == 2
    assert not (sc.marks / "ran-m1").exists()

    (sc.marks / "gate").write_text("")
    reconcile_worktree(group, "s1", env=sc.base_env)
    assert (sc.marks / "ran-m1").exists() and (sc.marks / "ran-m2").exists()
    assert len(sc.wrapper_log()) == 4
    assert sc.manifest()["task_wrapper"] == sc.wrapper_json()


def test_reseeding_an_existing_workspace_keeps_what_it_recorded(sc):
    from camp.group.config import load_group
    from camp.provision.provision import seed_pending_workspace

    group = load_group(sc.cfg / "groups" / "g.toml")
    wrapped_env = {**sc.base_env, VAR: sc.wrapper_json()}

    seed_pending_workspace(group, "wrapped", env=wrapped_env)
    seed_pending_workspace(group, "wrapped", env={**sc.base_env, VAR: json.dumps(["other"])})
    seed_pending_workspace(group, "bare", env=sc.base_env)
    seed_pending_workspace(group, "bare", env=wrapped_env)

    manifests = {
        json.loads(p.read_text())["slug"]: json.loads(p.read_text())
        for p in sc.state.rglob("manifest.json")
    }
    assert manifests["wrapped"]["task_wrapper"] == sc.wrapper_json()
    assert "task_wrapper" not in manifests["bare"]


# ---------------------------------------------------------------------------
# A workspace that records a wrapper also writes it to <slug>.task-wrapper.json,
# a sibling of the workspace dir beside the slug lock, out of its session's reach.
# A readable sidecar is the recorded wrapper whatever the manifest says; a
# manifest that cannot be read is rebuilt from it; an unreadable sidecar refuses.
# ---------------------------------------------------------------------------

_BAD_SIDECARS = ["{not json", "[]", "", '{"task_wrapper": 3}']


def _manifest_path(sc):
    (path,) = list(sc.state.rglob("manifest.json"))
    return path


def _sidecar_path(sc):
    ws = _manifest_path(sc).parent
    return ws.parent / f"{ws.name}.task-wrapper.json"


def _seed(sc, wrapper):
    from camp.group.config import load_group
    from camp.provision.provision import seed_pending_workspace

    group = load_group(sc.cfg / "groups" / "g.toml")
    seed_pending_workspace(group, "s1", env=sc.env_with(wrapper))
    return group


def test_creation_writes_the_wrapper_to_a_private_sidecar_beside_the_workspace_dir(sc):
    _seed(sc, sc.wrapper_json())
    sidecar = _sidecar_path(sc)
    assert json.loads(sidecar.read_text()) == {"task_wrapper": sc.wrapper_json()}
    assert sidecar.stat().st_mode & 0o777 == 0o600


def test_an_unwrapped_creation_writes_no_sidecar(sc):
    _seed(sc, None)
    assert not (_sidecar_path(sc)).exists()


def test_reseeding_over_a_truncated_manifest_carries_the_wrapper_from_the_sidecar(sc):
    group = _seed(sc, sc.wrapper_json())
    path = _manifest_path(sc)
    path.write_text("{ truncated")

    from camp.provision.provision import seed_pending_workspace

    seed_pending_workspace(group, "s1", env=sc.base_env)
    assert json.loads(path.read_text())["task_wrapper"] == sc.wrapper_json()


def test_reseeding_over_a_truncated_manifest_with_no_sidecar_records_no_wrapper(sc):
    group = _seed(sc, None)
    path = _manifest_path(sc)
    path.write_text("{ truncated")

    from camp.provision.provision import seed_pending_workspace

    seed_pending_workspace(group, "s1", env=sc.base_env)
    assert "task_wrapper" not in json.loads(path.read_text())


def test_reconcile_over_a_truncated_manifest_runs_its_steps_through_the_sidecar_wrapper(sc):
    from camp.provision.reconcile import reconcile_worktree

    sc.define_task()
    group = _seed(sc, sc.wrapper_json())
    path = _manifest_path(sc)
    path.write_text("{ truncated")

    reconcile_worktree(group, "s1", env=sc.base_env)
    assert len(sc.wrapper_log()) == 2
    assert (sc.marks / "ran-m1").exists() and (sc.marks / "ran-m2").exists()
    assert json.loads(path.read_text())["task_wrapper"] == sc.wrapper_json()


@pytest.mark.parametrize("text", _BAD_SIDECARS)
def test_reseeding_refuses_a_truncated_manifest_whose_sidecar_is_unreadable(sc, text):
    from camp.group.manifest import ManifestError
    from camp.provision.provision import seed_pending_workspace

    group = _seed(sc, sc.wrapper_json())
    path = _manifest_path(sc)
    sidecar = _sidecar_path(sc)
    path.write_text("{ truncated")
    sidecar.write_text(text)

    with pytest.raises(ManifestError) as info:
        seed_pending_workspace(group, "s1", env=sc.base_env)
    assert str(path) in str(info.value) and str(sidecar) in str(info.value)
    assert path.read_text() == "{ truncated"


@pytest.mark.parametrize("text", _BAD_SIDECARS)
def test_reconcile_refuses_a_truncated_manifest_whose_sidecar_is_unreadable_and_runs_no_step(sc, text):
    from camp.group.manifest import ManifestError
    from camp.provision.reconcile import reconcile_worktree

    sc.define_task()
    group = _seed(sc, sc.wrapper_json())
    path = _manifest_path(sc)
    sidecar = _sidecar_path(sc)
    path.write_text("{ truncated")
    sidecar.write_text(text)

    with pytest.raises(ManifestError) as info:
        reconcile_worktree(group, "s1", env=sc.base_env)
    assert str(path) in str(info.value) and str(sidecar) in str(info.value)
    assert path.read_text() == "{ truncated"
    assert not (sc.marks / "ran-m1").exists() and not (sc.marks / "ran-m2").exists()


def test_reconcile_creating_a_workspace_writes_the_wrapper_sidecar_that_survives_a_truncated_manifest(sc):
    from camp.group.config import load_group
    from camp.provision.reconcile import reconcile_worktree

    sc.define_task(required=False)
    (sc.marks / "require-gate").write_text("")
    group = load_group(sc.cfg / "groups" / "g.toml")
    reconcile_worktree(group, "s1", env={**sc.base_env, VAR: sc.wrapper_json()})
    path = _manifest_path(sc)
    assert json.loads((_sidecar_path(sc)).read_text()) == {"task_wrapper": sc.wrapper_json()}

    path.write_text("{ truncated")
    (sc.marks / "gate").write_text("")
    reconcile_worktree(group, "s1", env=sc.base_env)
    assert len(sc.wrapper_log()) == 4


def test_the_sidecar_sits_beside_the_slug_lock_outside_the_workspace_dir(sc):
    from camp.group.manifest import lock_path_for

    _seed(sc, sc.wrapper_json())
    ws = _manifest_path(sc).parent
    sidecar = _sidecar_path(sc)
    assert sidecar.parent == lock_path_for(ws).parent
    assert ws not in sidecar.parents


def _tamper(sc, how):
    path = _manifest_path(sc)
    data = json.loads(path.read_text())
    if how == "deleted":
        del data["task_wrapper"]
    else:
        data["task_wrapper"] = json.dumps(["/usr/bin/env"])
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize("how", ["deleted", "swapped"])
def test_setup_over_a_tampered_manifest_runs_through_the_sidecar_wrapper_and_rewrites_it(sc, how):
    sc.define_task(required=False)
    (sc.marks / "require-gate").write_text("")
    sc.new(sc.wrapper_json())
    assert len(sc.wrapper_log()) == 2
    path = _tamper(sc, how)
    (sc.marks / "gate").write_text("")

    r = sc.camp("setup", "s1", "--group", "g")
    assert r.returncode == 0, r.stderr

    assert (sc.marks / "ran-m1").exists() and (sc.marks / "ran-m2").exists()
    assert len(sc.wrapper_log()) == 4
    assert json.loads(path.read_text())["task_wrapper"] == sc.wrapper_json()


@pytest.mark.parametrize("how", ["deleted", "swapped"])
def test_reconcile_over_a_tampered_manifest_runs_through_the_sidecar_wrapper_and_rewrites_it(sc, how):
    from camp.group.config import load_group
    from camp.provision.reconcile import reconcile_worktree

    sc.define_task(required=False)
    (sc.marks / "require-gate").write_text("")
    group = load_group(sc.cfg / "groups" / "g.toml")
    reconcile_worktree(group, "s1", env=sc.env_with(sc.wrapper_json()))
    path = _tamper(sc, how)
    (sc.marks / "gate").write_text("")

    reconcile_worktree(group, "s1", env=sc.base_env)

    assert (sc.marks / "ran-m1").exists() and (sc.marks / "ran-m2").exists()
    assert len(sc.wrapper_log()) == 4
    assert json.loads(path.read_text())["task_wrapper"] == sc.wrapper_json()


@pytest.mark.parametrize("how", ["deleted", "swapped"])
def test_activate_runs_through_the_sidecar_wrapper_over_a_tampered_manifest(sc, how):
    sc.define_task(phase="activate")
    sc.new(sc.wrapper_json())
    _tamper(sc, how)

    r = sc.camp("activate", "--background", "m1", "--name", "s1", "--group", "g")
    assert r.returncode == 0, r.stderr
    assert [e["member"] for e in sc.wrapper_log()] == ["m1"]


@pytest.mark.parametrize("text", _BAD_SIDECARS)
def test_a_corrupt_sidecar_refuses_even_beside_a_readable_manifest(sc, text):
    from camp.group.manifest import ManifestError
    from camp.provision.provision import seed_pending_workspace

    group = _seed(sc, sc.wrapper_json())
    sidecar = _sidecar_path(sc)
    sidecar.write_text(text)
    before = _manifest_path(sc).read_text()

    with pytest.raises(ManifestError) as info:
        seed_pending_workspace(group, "s1", env=sc.base_env)
    assert str(sidecar) in str(info.value)
    assert _manifest_path(sc).read_text() == before


def test_removing_a_workspace_clears_its_sidecar_so_a_new_one_starts_clean(sc):
    from camp.provision.reconcile import reconcile_break

    group = _seed(sc, sc.wrapper_json())
    sidecar = _sidecar_path(sc)
    assert sidecar.exists()

    reconcile_break(group, "s1", env=sc.base_env, force=True)

    from camp.provision.provision import seed_pending_workspace

    seed_pending_workspace(group, "s1", env=sc.base_env)
    assert "task_wrapper" not in _manifest_path(sc).read_text()
    assert not sidecar.exists()


def _with_unwritable_worktrees_root(sc, group):
    """A slug whose lock and workspace dir exist but whose worktrees root is read-only.

    The sidecar lives in that root, so writing it fails while the workspace dir
    stays writable.
    """
    import contextlib
    import stat

    from camp.group.manifest import manifest_path_for
    from camp.provision.provision import seed_pending_workspace

    seed_pending_workspace(group, "s1", env=sc.base_env)
    mpath = manifest_path_for("g", "s1", env=sc.base_env)
    mpath.unlink()
    root = mpath.parent.parent

    @contextlib.contextmanager
    def locked():
        root.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            yield mpath
        finally:
            root.chmod(stat.S_IRWXU)

    return locked()


def test_a_failed_sidecar_write_leaves_no_wrapper_bearing_manifest(sc):
    from camp.group.config import load_group
    from camp.provision.provision import seed_pending_workspace

    group = load_group(sc.cfg / "groups" / "g.toml")
    with _with_unwritable_worktrees_root(sc, group) as mpath:
        with pytest.raises(OSError):
            seed_pending_workspace(group, "s1", env=sc.env_with(sc.wrapper_json()))
        assert not mpath.exists()


def test_a_failed_sidecar_write_in_reconcile_leaves_no_wrapper_bearing_manifest(sc):
    from camp.group.config import load_group
    from camp.provision.reconcile import reconcile_worktree

    group = load_group(sc.cfg / "groups" / "g.toml")
    with _with_unwritable_worktrees_root(sc, group) as mpath:
        with pytest.raises(OSError):
            reconcile_worktree(group, "s1", env=sc.env_with(sc.wrapper_json()))
        assert not mpath.exists()
