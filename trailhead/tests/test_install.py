"""Tests for trailhead/install.py — config-driven, multi-harness install.

wire / create_shims / detect_harnesses are patched for hermeticity:
these tests never compose real trees or touch the user's harness/PATH.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


from trailhead.harness import ClaudeCodeHarness
from trailhead.install import run_install
from trailhead.pathint import ShimDirResult


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {**os.environ, "TRAILHEAD_STATE_DIR": str(tmp_path), "HOME": str(home)}


@contextmanager
def _patched(*, detected=None, lore_init_rc=0, lore_init_stderr=""):
    """Patch wire, create_shims, detect_harnesses, run_lore_init.

    ``run_lore_init`` is ALWAYS patched (Axiom 6): these tests must never
    invoke the real ``lore init`` against the user's vault/state. The default
    stub reports success; tests that exercise the failure path pass a non-zero
    ``lore_init_rc`` + ``lore_init_stderr``.
    """
    sdr = ShimDirResult(shim_dir=Path("/shim/bin"), shims={})
    lore_result = (lore_init_rc, lore_init_stderr)
    with (
        patch("trailhead.install.wire") as wire_mock,
        patch("trailhead.install.create_shims", return_value=sdr) as pathint_mock,
        patch(
            "trailhead.install.detect_harnesses",
            return_value=([ClaudeCodeHarness()] if detected else []),
        ) as detect_mock,
        patch("trailhead.install.run_lore_init", return_value=lore_result) as lore_mock,
    ):
        yield {
            "wire": wire_mock,
            "pathint": pathint_mock,
            "detect": detect_mock,
            "lore_init": lore_mock,
        }


# ---------------------------------------------------------------------------
# Happy path — detection drives the harness
# ---------------------------------------------------------------------------


class TestDetectionDrivenInstall:
    def test_returns_zero_when_harness_detected(self, tmp_path):
        with _patched(detected=True):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0

    def test_wire_called_once_per_detected_harness(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), quiet=True)
        assert m["wire"].call_count == 1
        _, kwargs = m["wire"].call_args
        assert isinstance(kwargs["harness"], ClaudeCodeHarness)

    def test_wire_selection_contains_all_plugins(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), quiet=True)
        selection = m["wire"].call_args[0][0]
        assert set(selection) == {"camp", "lore", "craft", "portage"}

    def test_clis_installed(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), quiet=True)
        cli_tools = m["pathint"].call_args[0][0]
        assert set(cli_tools) == {"camp", "lore"}

    def test_summary_prints_shellenv_hint(self, tmp_path, capsys):
        with _patched(detected=True):
            run_install(env=_env(tmp_path))
        out = capsys.readouterr().out
        assert "shellenv" in out


# ---------------------------------------------------------------------------
# CLI overrides
# ---------------------------------------------------------------------------


class TestCliOverrides:
    def test_plugin_replaces_selection(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), plugins=["lore"], quiet=True)
        selection = m["wire"].call_args[0][0]
        assert set(selection) == {"lore"}

    def test_harness_flag_used_without_detection(self, tmp_path):
        with _patched(detected=False) as m:
            rc = run_install(env=_env(tmp_path), harnesses=["claude"], quiet=True)
        assert rc == 0
        assert m["wire"].call_count == 1

    def test_no_camp_skips_camp_cli(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), no_camp=True, quiet=True)
        cli_tools = m["pathint"].call_args[0][0]
        assert "camp" not in cli_tools
        assert "lore" in cli_tools

    def test_no_lore_skips_lore_cli(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), no_lore=True, quiet=True)
        cli_tools = m["pathint"].call_args[0][0]
        assert "lore" not in cli_tools
        assert "camp" in cli_tools

    def test_no_camp_and_no_lore_skips_pathint_entirely(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), no_camp=True, no_lore=True, quiet=True)
        m["pathint"].assert_not_called()


# ---------------------------------------------------------------------------
# No-harness case
# ---------------------------------------------------------------------------


class TestNoHarness:
    def test_no_harness_exits_nonzero(self, tmp_path, capsys):
        with _patched(detected=False):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 1
        assert "no code harness detected" in capsys.readouterr().err

    def test_no_harness_still_installs_clis(self, tmp_path):
        with _patched(detected=False) as m:
            run_install(env=_env(tmp_path), quiet=True)
        m["pathint"].assert_called_once()
        m["wire"].assert_not_called()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_unknown_plugin_exits_nonzero(self, tmp_path, capsys):
        with _patched(detected=True):
            rc = run_install(env=_env(tmp_path), plugins=["bogus"], quiet=True)
        assert rc == 1
        assert "bogus" in capsys.readouterr().err

    def test_wire_error_exits_nonzero(self, tmp_path, capsys):
        from trailhead.wire import WireError

        with _patched(detected=True) as m:
            m["wire"].side_effect = WireError(
                tool="lore", stage="compose", cause=RuntimeError("boom")
            )
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 1
        assert "lore" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class TestJsonOutput:
    def test_json_summary_shape(self, tmp_path, capsys):
        import json as _json

        with _patched(detected=True):
            run_install(env=_env(tmp_path), as_json=True)
        data = _json.loads(capsys.readouterr().out)
        assert data["no_harness"] is False
        assert set(data["harnesses"]) == {"claude_code"}
        assert data["install_camp_cli"] is True

    def test_json_no_harness_flag(self, tmp_path, capsys):
        import json as _json

        with _patched(detected=False):
            run_install(env=_env(tmp_path), as_json=True)
        data = _json.loads(capsys.readouterr().out)
        assert data["no_harness"] is True
        assert data["harnesses"] == {}


# ---------------------------------------------------------------------------
# Config override disclosure
# ---------------------------------------------------------------------------


def _write_override_config(tmp_path: Path) -> Path:
    path = tmp_path / "override.toml"
    path.write_text(
        '[[harness]]\nname="claude_code"\n'
        '  [[harness.plugins]]\n  name="portage"\n'
        '    [[harness.plugins.subagents]]\n    name="updater"\n'
        '    file_path="/custom/updater.md"\n'
    )
    return path


class TestOverrideDisclosure:
    def test_human_summary_lists_active_override(self, tmp_path, capsys):
        cfg_path = _write_override_config(tmp_path)
        with _patched(detected=True):
            run_install(env=_env(tmp_path), config_arg=str(cfg_path), quiet=True)
        out = capsys.readouterr().out
        assert "/custom/updater.md" in out
        assert "overrides" in out.lower()

    def test_json_summary_lists_active_override(self, tmp_path, capsys):
        import json as _json

        cfg_path = _write_override_config(tmp_path)
        with _patched(detected=True):
            run_install(env=_env(tmp_path), config_arg=str(cfg_path), as_json=True, quiet=True)
        data = _json.loads(capsys.readouterr().out)
        assert any(o["file_path"] == "/custom/updater.md" for o in data["overrides"])
        entry = next(o for o in data["overrides"] if o["file_path"] == "/custom/updater.md")
        assert entry["harness"] == "claude_code"
        assert entry["plugin"] == "portage"
        assert entry["kind"] == "subagent"
        assert entry["name"] == "updater"

    def test_human_summary_omits_overrides_section_when_none_active(self, tmp_path, capsys):
        with _patched(detected=True):
            run_install(env=_env(tmp_path), quiet=True)
        out = capsys.readouterr().out
        assert "overrides" not in out.lower()

    def test_json_summary_omits_overrides_key_when_none_active(self, tmp_path, capsys):
        import json as _json

        with _patched(detected=True):
            run_install(env=_env(tmp_path), as_json=True, quiet=True)
        data = _json.loads(capsys.readouterr().out)
        assert "overrides" not in data


# ---------------------------------------------------------------------------
# lore init integration
# ---------------------------------------------------------------------------


class TestLoreInitIntegration:
    def test_install_invokes_lore_init(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), quiet=True)
        m["lore_init"].assert_called_once()

    def test_lore_init_failure_propagates_nonzero(self, tmp_path):
        with _patched(detected=True, lore_init_rc=1, lore_init_stderr="lore: boom"):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 1

    def test_lore_init_failure_surfaces_stderr(self, tmp_path, capsys):
        with _patched(detected=True, lore_init_rc=3, lore_init_stderr="lore: vault create failed"):
            run_install(env=_env(tmp_path), quiet=True)
        err = capsys.readouterr().err
        assert "lore: vault create failed" in err

    def test_lore_init_success_keeps_install_zero(self, tmp_path):
        with _patched(detected=True):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0

    def test_no_lore_skips_lore_init(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), no_lore=True, quiet=True)
        m["lore_init"].assert_not_called()
