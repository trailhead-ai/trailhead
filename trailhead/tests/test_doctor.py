"""Tests for trailhead/doctor.py — read-only install-state report.

doctor never gates (exit_code is always 0); it reports what's installed,
discovered from on-disk markers. which/python probes are injected.
"""

import os
import subprocess
from pathlib import Path

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
        assert set(r.data["clis"]) == {"camp", "lore", "portage", "ranger"}
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
