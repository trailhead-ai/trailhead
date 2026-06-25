"""Tests for trailhead/doctor.py — read-only install-state report.

doctor never gates (exit_code is always 0); it reports what's installed,
discovered from on-disk markers. which/python probes are injected.
"""

import os
import subprocess
from pathlib import Path

from trailhead.doctor import run_doctor


def _env(tmp_path: Path) -> dict[str, str]:
    return {**os.environ, "TRAILHEAD_STATE_DIR": str(tmp_path)}


def _make_tree(tmp_path: Path, hname: str, tools: list[str], *, registered=True, mkt="trailhead"):
    root = tmp_path / "composed" / hname
    (root / ".claude-plugin").mkdir(parents=True)
    if mkt is not None:
        import json

        (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"name": mkt}))
    if registered:
        (root / ".trailhead-registered").write_text("{}")
    for t in tools:
        (root / f".trailhead-installed-{t}").write_text("{}")


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
