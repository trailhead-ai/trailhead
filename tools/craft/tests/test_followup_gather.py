"""followup_gather.py entry half: spec resolution across configured vaults and
the verdict on whether the spec's slice loop has ended.

Failure modes drive the script's functions with an injected `lore` runner; the
seam tests run the script as a subprocess, exactly as a skill invokes it,
against the real `lore` CLI reading a throwaway vault. The child's environment
is built from scratch (never inherited) so it cannot reach the live install.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_LORE_TESTS = Path(__file__).resolve().parents[2] / "lore" / "tests"
_SCRIPT = (
    Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts" / "followup_gather.py"
)

_conftest_spec = importlib.util.spec_from_file_location(
    "lore_tests_conftest_for_followup_gather", _LORE_TESTS / "conftest.py"
)
_lore_conftest = importlib.util.module_from_spec(_conftest_spec)
sys.modules["lore_tests_conftest_for_followup_gather"] = _lore_conftest
_conftest_spec.loader.exec_module(_lore_conftest)

from lore_tests_conftest_for_followup_gather import (  # noqa: E402,F401
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _publish_spawn_guard,
    make_vault,
)

_script_spec = importlib.util.spec_from_file_location("followup_gather", _SCRIPT)
gather = importlib.util.module_from_spec(_script_spec)
sys.modules["followup_gather"] = gather
_script_spec.loader.exec_module(gather)


class FakeLore:
    """A runner that answers `vault ls` and `record show --json --vault V spec/N`."""

    def __init__(self, vaults, records=None, vault_ls_rc=0):
        self.vaults = vaults
        self.records = records or {}
        self.vault_ls_rc = vault_ls_rc
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["vault", "ls"]:
            out = "".join(f"{v}\tteam\t/x/{v}\t[(all kinds)]\n" for v in self.vaults)
            return subprocess.CompletedProcess(argv, self.vault_ls_rc, out, "")
        if argv[:3] == ["record", "show", "--json"]:
            vault = argv[argv.index("--vault") + 1]
            rid = argv[-1]
            doc = self.records.get((vault, rid))
            if doc is None:
                return subprocess.CompletedProcess(argv, 1, "", f"lore: record not found: {rid}\n")
            body = doc if isinstance(doc, str) else json.dumps(doc)
            return subprocess.CompletedProcess(argv, 0, body, "")
        raise AssertionError(f"unexpected lore call {argv}")


def spec_doc(status="ready", labels=None, kind="spec"):
    sidecar = {"kind": kind, "status": status}
    if labels is not None:
        sidecar["labels"] = labels
    return {"record_id": "spec/s", "kind": kind, "sidecar": sidecar, "body": "# s"}


def run_main(runner, name="s", capsys=None):
    code = gather.main([name], runner=runner)
    out = capsys.readouterr()
    return code, out.out, out.err


def reason_of(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("reason-code: ")]
    assert len(lines) == 1, err
    return lines[0].removeprefix("reason-code: ")


@pytest.mark.parametrize("name", ["has space", "a$(touch x)", "semi;colon", "", "a\nb"])
def test_a_malformed_spec_name_is_refused_before_any_lore_call(name, capsys):
    runner = FakeLore(["v"])
    code, out, err = run_main(runner, name, capsys)
    assert code == 2
    assert reason_of(err) == "invalid-spec-name"
    assert runner.calls == []


@pytest.mark.parametrize(
    "status,labels,entry",
    [
        ("ready", {"craft/slice-loop": "complete"}, "complete-marker"),
        ("ready", {"craft/slice-loop": "stopped"}, "stopped-marker"),
        ("complete", {}, "complete-status"),
        ("complete", {"craft/slice-loop": "other"}, "complete-status"),
    ],
)
def test_an_ended_slice_loop_exits_zero_naming_how_it_ended(status, labels, entry, capsys):
    runner = FakeLore(["v"], {("v", "spec/s"): spec_doc(status, labels)})
    code, out, err = run_main(runner, "s", capsys)
    assert code == 0, err
    result = json.loads(out)
    assert result["spec"] == "s"
    assert result["vault"] == "v"
    assert result["entry"] == entry


@pytest.mark.parametrize(
    "labels",
    [{}, {"craft/slice-loop": "in-progress"}, {"craft/slice-loop": ""}, {"unrelated": "complete"}],
)
def test_a_ready_spec_without_an_ended_marker_is_refused(labels, capsys):
    runner = FakeLore(["v"], {("v", "spec/s"): spec_doc("ready", labels)})
    code, out, err = run_main(runner, "s", capsys)
    assert code == 1
    assert out == ""
    assert reason_of(err) == "slice-loop-not-ended"


def test_a_spec_in_no_vault_is_not_found(capsys):
    runner = FakeLore(["a", "b"])
    code, _, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "spec-not-found"


def test_a_spec_in_two_vaults_is_ambiguous(capsys):
    doc = spec_doc("complete")
    runner = FakeLore(["a", "b"], {("a", "spec/s"): doc, ("b", "spec/s"): doc})
    code, _, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "spec-ambiguous"


def test_a_record_that_is_not_a_spec_is_refused(capsys):
    runner = FakeLore(["v"], {("v", "spec/s"): spec_doc("complete", kind="task")})
    code, _, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "not-a-spec"


def test_resolve_spec_returns_the_vault_and_sidecar():
    doc = spec_doc("ready", {"craft/slice-loop": "stopped"})
    runner = FakeLore(["a", "b"], {("b", "spec/s"): doc})
    vault, sidecar = gather.resolve_spec(runner, "s")
    assert vault == "b"
    assert sidecar == doc["sidecar"]


def test_every_lore_call_is_an_argv_list_of_strings():
    runner = FakeLore(["a", "b"], {("b", "spec/s"): spec_doc("complete")})
    gather.main(["s"], runner=runner)
    assert runner.calls
    for argv in runner.calls:
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)


VAULT_LS = "v\tteam\t/x/v\t[(all kinds)]\n"


def _failing(exc=None, rc=None, stdout="", stderr="", on="record"):
    def runner(argv):
        if argv[:2] == ["vault", "ls"] and on == "record":
            return subprocess.CompletedProcess(argv, 0, VAULT_LS, "")
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(argv, rc, stdout, stderr)

    return runner


@pytest.mark.parametrize(
    "runner,reason",
    [
        (_failing(exc=FileNotFoundError("lore")), "lore-missing"),
        (_failing(exc=subprocess.TimeoutExpired(["lore"], 1)), "lore-timeout"),
        (_failing(rc=3, stderr="SECRET-vault-text"), "lore-failed"),
        (_failing(rc=0, stdout="not json SECRET-vault-text"), "lore-output-not-json"),
        (_failing(rc=0, stdout="{}"), "empty-sidecar"),
        (_failing(rc=0, stdout=json.dumps({"kind": "spec", "sidecar": {}})), "empty-sidecar"),
        (_failing(rc=0, stdout="[1, 2]"), "lore-output-not-json"),
        (_failing(exc=FileNotFoundError("lore"), on="ls"), "lore-missing"),
        (_failing(exc=subprocess.TimeoutExpired(["lore"], 1), on="ls"), "lore-timeout"),
        (_failing(rc=3, stderr="SECRET-vault-text", on="ls"), "lore-failed"),
    ],
)
def test_each_runner_failure_has_its_own_reason_code_and_echoes_nothing_from_lore(
    runner, reason, capsys
):
    code, out, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == reason
    assert "SECRET" not in out + err
    assert "Traceback" not in err


def test_a_vault_listing_with_no_vaults_cannot_certify(capsys):
    code, _, err = run_main(FakeLore([]), "s", capsys)
    assert code == 2
    assert reason_of(err) == "no-vaults"


@pytest.mark.parametrize("sidecar", [{"kind": "spec"}, {"status": "ready"}])
def test_a_sidecar_missing_kind_or_status_is_a_failed_lookup(sidecar, capsys):
    doc = {"record_id": "spec/s", "kind": "spec", "sidecar": sidecar}
    runner = FakeLore(["v"], {("v", "spec/s"): doc})
    code, _, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "malformed-sidecar"


def test_an_unexpected_exception_exits_two_not_the_refusal_code(capsys):
    def runner(argv):
        raise RuntimeError("boom SECRET-vault-text")

    code, out, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "internal-error"
    assert "SECRET" not in out + err
    assert "Traceback" not in err


def test_the_default_runner_reports_a_missing_binary_as_lore_missing(monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    code = gather.main(["s"])
    err = capsys.readouterr().err
    assert code == 2
    assert reason_of(err) == "lore-missing"


# --- seam: the script as a subprocess against the real lore CLI ---------------


def _build_env(tmp_path, vaults):
    """A from-scratch child environment whose `lore` sees only `vaults`
    (a list of (name, scope, path)); nothing is inherited from os.environ
    except PATH, behind a shim that runs this checkout's lore."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg = tmp_path / "xdg_config"
    _lore_conftest.write_vault_config(cfg, vaults)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "lore"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_lore_conftest.CLI_PATH}" "$@"\n')
    shim.chmod(0o755)
    return {
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(cfg),
        "LORE_GROUPS_DIR": str(tmp_path / "groups"),
        "LORE_PUBLISH_DISABLE": "1",
        "LORE_EMAIL": "tester@example.com",
    }


def _lore(env, *args, stdin=None):
    r = subprocess.run(["lore", *args], capture_output=True, text=True, env=env, input=stdin)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def lore_env(tmp_path):
    """(env, vault_name): a child environment seeing exactly one throwaway vault."""
    vault, _ = make_vault(tmp_path)
    return _build_env(tmp_path, [("default", "default", vault)]), "default"


@pytest.fixture
def two_vault_lore_env(tmp_path):
    first, _ = make_vault(tmp_path)
    second = tmp_path / "second"
    second.mkdir()
    env = _build_env(tmp_path, [("default", "default", first), ("other", "team", second)])
    return env, ("default", "other")


def make_spec(env, vault, title, status="ready", labels=()):
    args = ["record", "create", "--kind", "spec", "--title", title, "--status", status,
            "--vault", vault]
    for label in labels:
        args += ["--label", label]
    return _lore(env, *args, stdin="# body\n").split("/", 1)[1]


def run_script(env, *args):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args], capture_output=True, text=True, env=env
    )


def test_the_script_run_as_a_subprocess_certifies_a_stopped_spec(  # inert-gate: allow seam smoke
    lore_env,
):
    env, vault = lore_env
    name = make_spec(env, vault, "Seam stopped", labels=["craft/slice-loop=stopped"])
    r = run_script(env, name)
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["spec"] == name
    assert result["vault"] == vault
    assert result["entry"] == "stopped-marker"


def test_a_ready_spec_with_no_marker_is_refused_through_real_lore(lore_env):
    env, vault = lore_env
    name = make_spec(env, vault, "Seam ready")
    r = run_script(env, name)
    assert r.returncode == 1
    assert "reason-code: slice-loop-not-ended" in r.stderr


def test_a_completed_spec_is_certified_by_status_through_real_lore(lore_env):
    env, vault = lore_env
    name = make_spec(env, vault, "Seam complete", status="complete")
    r = run_script(env, name)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["entry"] == "complete-status"


def test_an_unknown_spec_is_not_found_through_real_lore(lore_env):
    env, _ = lore_env
    r = run_script(env, "no-such-spec")
    assert r.returncode == 2
    assert "reason-code: spec-not-found" in r.stderr


def test_a_spec_named_in_two_configured_vaults_is_ambiguous_through_real_lore(
    two_vault_lore_env,
):
    env, (first, second) = two_vault_lore_env
    name = make_spec(env, first, "Seam twin", status="complete")
    make_spec(env, second, "Seam twin", status="complete")
    r = run_script(env, name)
    assert r.returncode == 2
    assert "reason-code: spec-ambiguous" in r.stderr
