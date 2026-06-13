"""Tests for trailhead doctor rollup (Slice 5).

TDD: tests written BEFORE implementation. Each test must fail before the
implementation exists, then pass after.

Test contract (plan §Slice 5 + amendments R-2, R-3, U-2):
  - doctor --json aggregates per-tool checks with any_failed.
  - A tool with no doctor verb → "no doctor" entry, rollup succeeds.
  - A wired tool's subprocess that hangs → 5-second timeout → named entry,
    rollup still exits cleanly (R-3).
  - A wired tool's subprocess that returns malformed JSON → named parse-error
    entry, rollup still exits (R-3).
  - A wired tool's subprocess that exits nonzero → named entry, rollup
    continues (R-3).
  - R-2 drift: config declaring a cap absent from the wired dest → doctor
    reports drift.  A present-but-undeclared skill → also drift.
  - U-2 python check: doctor warns when python3 < 3.10 on PATH.
  - A-9: --json for machine reads; human output groups by tool.
  - overall any_failed reflects any tool failure.

Hermeticity:
  - The per-tool doctor subprocess runner is injectable (doctor_runner kwarg).
  - Tests use tmp_path for config/state dirs; never touch real ~/.claude/,
    real state_dir, or invoke real `camp doctor`.
"""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent


def _hermetic_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "TRAILHEAD_CONFIG_DIR": str(tmp_path / "config"),
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
    }


def _stub_doctor_runner_camp_pass():
    """Returns a runner stub that makes camp doctor --json succeed with a passing check."""
    camp_result = {
        "pass": True,
        "checks": [
            {"check": "asdf", "description": "asdf installed", "pass": True, "details": "ok"},
        ],
    }

    def runner(args, *, timeout=5):
        if "camp" in args[0] or (len(args) > 0 and "camp" in " ".join(args)):
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps(camp_result)
            return result
        raise ValueError(f"unexpected args: {args}")

    return runner


def _stub_doctor_runner_camp_fail():
    """Returns a runner stub that makes camp doctor --json report a failure."""
    camp_result = {
        "pass": False,
        "checks": [
            {"check": "asdf", "description": "asdf installed", "pass": False, "details": "asdf not found"},
        ],
    }

    def runner(args, *, timeout=5):
        result = MagicMock()
        result.returncode = 1
        result.stdout = json.dumps(camp_result)
        return result

    return runner


# ---------------------------------------------------------------------------
# T-D1: doctor --json aggregates tool checks
# ---------------------------------------------------------------------------


class TestDoctorJsonAggregation:
    def test_json_output_parseable(self, tmp_path):
        """doctor --json emits parseable JSON."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        runner = _stub_doctor_runner_camp_pass()
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=runner,
            env=env,
        )
        assert isinstance(result.data, dict)

    def test_json_has_any_failed_key(self, tmp_path):
        """doctor --json output has top-level any_failed key."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        runner = _stub_doctor_runner_camp_pass()
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=runner,
            env=env,
        )
        assert "any_failed" in result.data

    def test_json_has_tools_key(self, tmp_path):
        """doctor --json output has tools dict."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        runner = _stub_doctor_runner_camp_pass()
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=runner,
            env=env,
        )
        assert "tools" in result.data

    def test_json_camp_checks_nested_under_camp_key(self, tmp_path):
        """camp's checks appear nested under the 'camp' key in the aggregate."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        runner = _stub_doctor_runner_camp_pass()
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=runner,
            env=env,
        )
        assert "camp" in result.data["tools"]
        assert "checks" in result.data["tools"]["camp"]

    def test_json_any_failed_false_when_all_pass(self, tmp_path):
        """any_failed is False when all tools pass."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        runner = _stub_doctor_runner_camp_pass()
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=runner,
            which_runner=lambda name: f"/fake/bin/{name}",
            env=env,
        )
        assert result.data["any_failed"] is False

    def test_json_any_failed_true_when_camp_fails(self, tmp_path):
        """any_failed is True when camp doctor reports failure."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        runner = _stub_doctor_runner_camp_fail()
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=runner,
            env=env,
        )
        assert result.data["any_failed"] is True

    def test_exit_code_zero_when_all_pass(self, tmp_path):
        """exit_code is 0 when no failures."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        runner = _stub_doctor_runner_camp_pass()
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=runner,
            which_runner=lambda name: f"/fake/bin/{name}",
            env=env,
        )
        assert result.exit_code == 0

    def test_exit_code_nonzero_when_any_fail(self, tmp_path):
        """exit_code is nonzero when any tool fails."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        runner = _stub_doctor_runner_camp_fail()
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=runner,
            env=env,
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# T-D2: tool with no doctor → "no doctor" entry, rollup still succeeds
# ---------------------------------------------------------------------------


class TestNoDoctorTool:
    def test_lore_no_doctor_entry_in_json(self, tmp_path):
        """lore has no doctor verb → appears as 'no doctor' entry in JSON output."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)

        # lore has no doctor; camp has one
        def runner(args, *, timeout=5):
            # Only called for tools with doctor verb; lore should be handled separately
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps({"pass": True, "checks": []})
            return result

        result = run_doctor(
            as_json=True,
            wired_tools={"lore": {"capture", "recall"}},
            doctor_runner=runner,
            env=env,
        )
        assert "lore" in result.data["tools"]
        lore_entry = result.data["tools"]["lore"]
        # Should indicate no doctor available
        assert lore_entry.get("status") == "no_doctor" or "no_doctor" in str(lore_entry) or "n/a" in str(lore_entry).lower()

    def test_no_doctor_does_not_crash_rollup(self, tmp_path):
        """A tool with no doctor still allows the rollup to complete without crash."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"lore": {"capture"}},
            doctor_runner=lambda args, **kw: (_ for _ in ()).throw(FileNotFoundError("no lore doctor")),
            env=env,
        )
        # rollup should still produce a result, not raise
        assert isinstance(result.data, dict)

    def test_no_doctor_tool_does_not_set_any_failed(self, tmp_path):
        """A tool with no doctor does not set any_failed to True."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"lore": {"capture"}},
            which_runner=lambda name: f"/fake/bin/{name}",
            env=env,
        )
        assert result.data["any_failed"] is False


# ---------------------------------------------------------------------------
# T-D3: R-3 — subprocess timeout → named entry, rollup still exits
# ---------------------------------------------------------------------------


class TestSubprocessTimeout:
    def test_hang_produces_named_timeout_entry(self, tmp_path):
        """A tool's doctor that hangs → times out at 5s → named timeout entry (R-3)."""
        from trailhead.doctor import run_doctor

        def hanging_runner(args, *, timeout=5):
            raise subprocess.TimeoutExpired(args, timeout)

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=hanging_runner,
            env=env,
        )
        camp_entry = result.data["tools"]["camp"]
        # Should have a timeout error entry
        assert (
            camp_entry.get("status") == "timeout"
            or "timeout" in str(camp_entry).lower()
        )

    def test_hang_rollup_still_completes(self, tmp_path):
        """A hanging tool doctor doesn't hang the rollup — rollup completes."""
        from trailhead.doctor import run_doctor

        def hanging_runner(args, *, timeout=5):
            raise subprocess.TimeoutExpired(args, timeout)

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=hanging_runner,
            env=env,
        )
        assert isinstance(result.data, dict)
        assert "any_failed" in result.data

    def test_hang_sets_any_failed_true(self, tmp_path):
        """A timeout sets any_failed to True (it's an error condition)."""
        from trailhead.doctor import run_doctor

        def hanging_runner(args, *, timeout=5):
            raise subprocess.TimeoutExpired(args, timeout)

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=hanging_runner,
            env=env,
        )
        assert result.data["any_failed"] is True


# ---------------------------------------------------------------------------
# T-D4: R-3 — malformed JSON → named parse-error entry
# ---------------------------------------------------------------------------


class TestMalformedJson:
    def test_malformed_json_produces_named_error_entry(self, tmp_path):
        """A tool doctor returning malformed JSON → named parse-error entry (R-3)."""
        from trailhead.doctor import run_doctor

        def malformed_runner(args, *, timeout=5):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "this is not json {"
            return result

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=malformed_runner,
            env=env,
        )
        camp_entry = result.data["tools"]["camp"]
        assert (
            camp_entry.get("status") == "parse_error"
            or "parse" in str(camp_entry).lower()
            or "json" in str(camp_entry).lower()
        )

    def test_malformed_json_rollup_still_completes(self, tmp_path):
        """Malformed JSON does not crash the rollup."""
        from trailhead.doctor import run_doctor

        def malformed_runner(args, *, timeout=5):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "{"
            return result

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=malformed_runner,
            env=env,
        )
        assert isinstance(result.data, dict)

    def test_malformed_json_sets_any_failed_true(self, tmp_path):
        """Malformed JSON from a tool doctor sets any_failed."""
        from trailhead.doctor import run_doctor

        def malformed_runner(args, *, timeout=5):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "not json"
            return result

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=malformed_runner,
            env=env,
        )
        assert result.data["any_failed"] is True


# ---------------------------------------------------------------------------
# T-D5: R-3 — nonzero exit → named entry (but if JSON still parseable, use it)
# ---------------------------------------------------------------------------


class TestNonzeroExit:
    def test_nonzero_exit_with_valid_json_captured_as_tool_fail(self, tmp_path):
        """Nonzero exit with valid JSON → captured as a tool failure entry (R-3)."""
        from trailhead.doctor import run_doctor

        camp_result = {
            "pass": False,
            "checks": [
                {"check": "asdf", "description": "asdf check", "pass": False, "details": "missing"},
            ],
        }

        def fail_runner(args, *, timeout=5):
            result = MagicMock()
            result.returncode = 1
            result.stdout = json.dumps(camp_result)
            return result

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=fail_runner,
            env=env,
        )
        # Rollup should still complete
        assert isinstance(result.data, dict)
        assert result.data["any_failed"] is True

    def test_nonzero_exit_no_output_produces_error_entry(self, tmp_path):
        """Nonzero exit with no/empty output → named error entry, rollup continues."""
        from trailhead.doctor import run_doctor

        def silent_fail_runner(args, *, timeout=5):
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            return result

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=silent_fail_runner,
            env=env,
        )
        assert isinstance(result.data, dict)
        assert result.data["any_failed"] is True


# ---------------------------------------------------------------------------
# T-D6: R-2 drift — config↔filesystem coherence check
# ---------------------------------------------------------------------------


class TestDriftCheck:
    def _setup_composed_dest(self, tmp_path: Path, tool: str, caps: list[str]) -> Path:
        """Create a minimal composed dest structure with given cap subdirs.

        Consolidated layout (Slice 3): composed/plugins/<tool>/skills/<cap>.
        """
        composed_root = tmp_path / "state" / "composed"
        dest = composed_root / "plugins" / tool
        dest.mkdir(parents=True, exist_ok=True)
        for cap in caps:
            (dest / "skills" / cap).mkdir(parents=True, exist_ok=True)
        return dest

    def test_declared_but_absent_cap_reported_as_drift(self, tmp_path):
        """Config declares 'recall' for lore, but dest has no skills/recall → drift."""
        from trailhead.doctor import run_doctor
        from trailhead.config import TrailheadConfig, save_config

        env = _hermetic_env(tmp_path)

        # Config says lore has recall
        cfg = TrailheadConfig(
            capabilities={"lore": ["recall"]},
        )
        save_config(cfg, env=env)

        # But the composed dest only has 'capture', not 'recall'
        self._setup_composed_dest(tmp_path, "lore", ["capture"])

        result = run_doctor(
            as_json=True,
            wired_tools={"lore": {"recall"}},
            env=env,
        )
        # Drift should be detected
        drift_checks = result.data.get("drift_checks", [])
        tools_data = result.data.get("tools", {})
        # Either a top-level drift_checks list or embedded in the tool entry
        has_drift = (
            len(drift_checks) > 0
            or any("drift" in str(v).lower() or "absent" in str(v).lower()
                   for v in tools_data.values())
            or result.data.get("any_failed") is True
        )
        assert has_drift, f"Expected drift to be detected, got: {result.data}"

    def test_present_but_undeclared_cap_reported_as_drift(self, tmp_path):
        """Dest has skills/bonus, but config doesn't declare 'bonus' → drift."""
        from trailhead.doctor import run_doctor
        from trailhead.config import TrailheadConfig, save_config

        env = _hermetic_env(tmp_path)

        # Config declares only capture
        cfg = TrailheadConfig(capabilities={"lore": ["capture"]})
        save_config(cfg, env=env)

        # But dest also has 'bonus' skill dir
        self._setup_composed_dest(tmp_path, "lore", ["capture", "bonus"])

        result = run_doctor(
            as_json=True,
            wired_tools={"lore": {"capture"}},
            env=env,
        )
        # Drift should be detected (present-but-undeclared)
        has_drift = (
            len(result.data.get("drift_checks", [])) > 0
            or result.data.get("any_failed") is True
            or any("drift" in str(v).lower() or "undeclared" in str(v).lower()
                   for v in result.data.get("tools", {}).values())
        )
        assert has_drift, f"Expected drift, got: {result.data}"

    def test_no_drift_when_config_matches_dest(self, tmp_path):
        """No drift when config capabilities match what's in the composed dest."""
        from trailhead.doctor import run_doctor
        from trailhead.config import TrailheadConfig, save_config

        env = _hermetic_env(tmp_path)

        cfg = TrailheadConfig(capabilities={"lore": ["capture"]})
        save_config(cfg, env=env)
        self._setup_composed_dest(tmp_path, "lore", ["capture"])

        result = run_doctor(
            as_json=True,
            wired_tools={"lore": {"capture"}},
            env=env,
        )
        drift_checks = result.data.get("drift_checks", [])
        assert all(c.get("pass", True) for c in drift_checks), \
            f"Expected no drift failures, got: {drift_checks}"


# ---------------------------------------------------------------------------
# T-D7: U-2 — python3 ≥ 3.10 check
# ---------------------------------------------------------------------------


class TestPythonVersionCheck:
    def test_python_below_310_flags_warning(self, tmp_path):
        """doctor warns when python3 < 3.10 is on PATH (U-2)."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)

        def python_version_runner(cmd):
            """Stub: returns 3.9.6 for python3 --version."""
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Python 3.9.6"
            return result

        result = run_doctor(
            as_json=True,
            wired_tools={},
            env=env,
            python_version_runner=python_version_runner,
        )
        # Should have a check that warns about old python
        python_check = None
        for check in result.data.get("checks", []):
            if "python" in check.get("check", "").lower():
                python_check = check
                break
        assert python_check is not None, f"No python check found in: {result.data}"
        assert python_check.get("pass") is False, f"Expected python check to fail: {python_check}"

    def test_python_310_or_above_passes(self, tmp_path):
        """doctor passes the python check when python3 ≥ 3.10."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)

        def python_version_runner(cmd):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Python 3.11.4"
            return result

        result = run_doctor(
            as_json=True,
            wired_tools={},
            env=env,
            python_version_runner=python_version_runner,
        )
        python_check = None
        for check in result.data.get("checks", []):
            if "python" in check.get("check", "").lower():
                python_check = check
                break
        assert python_check is not None, f"No python check found in: {result.data}"
        assert python_check.get("pass") is True, f"Expected python check to pass: {python_check}"

    def test_python_not_on_path_flags_failure(self, tmp_path):
        """doctor fails the python check when python3 is not on PATH."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)

        def missing_python_runner(cmd):
            raise FileNotFoundError("python3 not found")

        result = run_doctor(
            as_json=True,
            wired_tools={},
            env=env,
            python_version_runner=missing_python_runner,
        )
        python_check = None
        for check in result.data.get("checks", []):
            if "python" in check.get("check", "").lower():
                python_check = check
                break
        assert python_check is not None
        assert python_check.get("pass") is False


# ---------------------------------------------------------------------------
# T-D7b: PATH integration — front-door CLI (camp/lore) resolvable on PATH
# ---------------------------------------------------------------------------


class TestPathIntegrationCheck:
    def _path_check(self, data, tool):
        for c in data.get("checks", []):
            if c.get("check") == f"path:{tool}":
                return c
        return None

    def test_camp_not_on_path_flags_failure(self, tmp_path):
        """The forcing case (gap 3): camp wired but not on PATH → doctor FAILS."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=lambda args, **kw: (_ for _ in ()).throw(FileNotFoundError("no camp")),
            which_runner=lambda name: None,  # nothing resolves on PATH
            env=env,
        )
        check = self._path_check(result.data, "camp")
        assert check is not None, f"no path:camp check found in {result.data}"
        assert check["pass"] is False
        assert result.data["any_failed"] is True

    def test_camp_not_on_path_message_is_actionable(self, tmp_path):
        """The failure must tell the user how to fix it."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=lambda args, **kw: (_ for _ in ()).throw(FileNotFoundError("no camp")),
            which_runner=lambda name: None,
            env=env,
        )
        details = self._path_check(result.data, "camp")["details"].lower()
        assert "path" in details
        assert "path_integration" in details or "restart" in details or "shell" in details

    def test_camp_on_path_passes(self, tmp_path):
        """camp resolvable on PATH → path check passes."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=_stub_doctor_runner_camp_pass(),
            which_runner=lambda name: f"/fake/bin/{name}",
            env=env,
        )
        check = self._path_check(result.data, "camp")
        assert check is not None
        assert check["pass"] is True

    def test_path_check_skipped_when_integration_disabled(self, tmp_path):
        """When path_integration is off in config, a missing shim is intentional."""
        from trailhead.doctor import run_doctor
        from trailhead.config import TrailheadConfig, save_config

        env = _hermetic_env(tmp_path)
        save_config(TrailheadConfig(path_integration=False, capabilities={"camp": []}), env=env)

        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=lambda args, **kw: (_ for _ in ()).throw(FileNotFoundError("no camp")),
            which_runner=lambda name: None,
            env=env,
        )
        # No per-tool path:camp failure, and PATH didn't drag any_failed true.
        assert self._path_check(result.data, "camp") is None
        assert result.data["any_failed"] is False

    def test_not_on_path_probe_status_does_not_double_count(self, tmp_path):
        """The probe's not_on_path status is informational; the PATH check owns the fail."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"camp": set()},
            doctor_runner=lambda args, **kw: (_ for _ in ()).throw(FileNotFoundError("no camp")),
            which_runner=lambda name: None,
            env=env,
        )
        # The camp probe entry reflects not_on_path, not a hard error/no_doctor.
        assert result.data["tools"]["camp"]["status"] == "not_on_path"

    def test_discovers_registered_tools_without_config(self, tmp_path):
        """Config-less but camp registered (marker) → PATH check still fires.

        Regression: doctor used to key 'what's wired' purely off config.toml, so
        a directly-wired machine (no config) reported nothing and passed blind.
        """
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        camp = tmp_path / "state" / "composed" / "camp"
        (camp / "plugins" / "camp").mkdir(parents=True)
        (camp / ".trailhead-registered").write_text("{}")

        # wired_tools=None → default discovery (config ∪ registration markers)
        result = run_doctor(
            as_json=True,
            doctor_runner=lambda args, **kw: (_ for _ in ()).throw(FileNotFoundError("no camp")),
            which_runner=lambda name: None,
            env=env,
        )
        check = self._path_check(result.data, "camp")
        assert check is not None, f"camp not discovered from marker: {result.data}"
        assert check["pass"] is False
        assert result.data["any_failed"] is True

    def test_registered_marker_tool_does_not_trigger_false_drift(self, tmp_path):
        """A marker-discovered tool absent from config must not produce drift noise.

        Drift is config↔filesystem; a tool with no config entry has nothing to
        drift against, so the present skills must NOT read as 'undeclared'.
        """
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        camp = tmp_path / "state" / "composed" / "camp"
        (camp / "plugins" / "camp" / "skills" / "worktree").mkdir(parents=True)
        (camp / ".trailhead-registered").write_text("{}")

        result = run_doctor(
            as_json=True,
            doctor_runner=lambda args, **kw: (_ for _ in ()).throw(FileNotFoundError("no camp")),
            which_runner=lambda name: f"/fake/bin/{name}",  # on PATH → PATH check passes
            env=env,
        )
        # No drift failures should appear for camp.
        drift_failures = [d for d in result.data.get("drift_checks", []) if not d.get("pass", True)]
        assert drift_failures == [], f"unexpected drift: {drift_failures}"

    def test_craft_has_no_path_check(self, tmp_path):
        """craft ships no CLI wrapper → no path check generated for it."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=True,
            wired_tools={"craft": {"planning"}},
            which_runner=lambda name: None,
            env=env,
        )
        assert self._path_check(result.data, "craft") is None


# ---------------------------------------------------------------------------
# T-D8: human output groups by tool
# ---------------------------------------------------------------------------


class TestConsolidatedMarketplaceCheck:
    """T-D9: doctor asserts the consolidated 'trailhead' marketplace signal.

    A half-migrated machine (old per-tool 'trailhead-<tool>' marketplace.json
    still live at composed/.claude-plugin/) must be visible: the check fails
    when name != 'trailhead'.  When marketplace.json is absent, the check is a
    no-op pass (not yet wired).
    """

    def _composed_root(self, tmp_path: Path) -> Path:
        return tmp_path / "state" / "composed"

    def _write_marketplace(self, tmp_path: Path, name: str) -> None:
        composed_root = self._composed_root(tmp_path)
        cp_dir = composed_root / ".claude-plugin"
        cp_dir.mkdir(parents=True, exist_ok=True)
        (cp_dir / "marketplace.json").write_text(
            json.dumps({"name": name, "owner": {"name": "trailhead"}, "plugins": []})
        )

    def _marketplace_check(self, data: dict) -> dict | None:
        for check in data.get("checks", []):
            if "marketplace" in check.get("check", "").lower():
                return check
        return None

    def test_marketplace_check_passes_when_name_is_trailhead(self, tmp_path):
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        self._write_marketplace(tmp_path, "trailhead")

        result = run_doctor(as_json=True, wired_tools={}, env=env)
        check = self._marketplace_check(result.data)
        assert check is not None, f"no marketplace check found in: {result.data}"
        assert check.get("pass") is True, f"expected pass, got: {check}"

    def test_marketplace_check_fails_on_half_migrated_name(self, tmp_path):
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        # A stale per-tool marketplace still live at the consolidated path.
        self._write_marketplace(tmp_path, "trailhead-lore")

        result = run_doctor(as_json=True, wired_tools={}, env=env)
        check = self._marketplace_check(result.data)
        assert check is not None, f"no marketplace check found in: {result.data}"
        assert check.get("pass") is False, (
            f"half-migrated marketplace name must fail the check: {check}"
        )
        assert result.data["any_failed"] is True

    def test_marketplace_check_passes_when_absent(self, tmp_path):
        """No marketplace.json yet (fresh machine) → check is a no-op pass."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        result = run_doctor(as_json=True, wired_tools={}, env=env)
        check = self._marketplace_check(result.data)
        assert check is not None, f"no marketplace check found in: {result.data}"
        assert check.get("pass") is True, (
            f"absent marketplace.json should not fail the check: {check}"
        )


class TestHumanOutput:
    def test_human_output_names_camp_section(self, tmp_path):
        """Human output has a camp section."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        runner = _stub_doctor_runner_camp_pass()
        result = run_doctor(
            as_json=False,
            wired_tools={"camp": set()},
            doctor_runner=runner,
            env=env,
        )
        assert "camp" in result.human_output.lower()

    def test_human_output_no_doctor_tool_says_na(self, tmp_path):
        """Human output says n/a for tools with no doctor."""
        from trailhead.doctor import run_doctor

        env = _hermetic_env(tmp_path)
        result = run_doctor(
            as_json=False,
            wired_tools={"lore": {"capture"}},
            env=env,
        )
        assert "n/a" in result.human_output.lower() or "no doctor" in result.human_output.lower()
