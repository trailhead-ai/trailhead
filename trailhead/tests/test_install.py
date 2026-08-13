"""Tests for trailhead/install.py — config-driven, multi-harness install.

wire / create_shims / detect_harnesses are patched for hermeticity:
these tests never compose real trees or touch the user's harness/PATH.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


from trailhead.capabilities import load_manifest
from trailhead.harness import ClaudeCodeHarness
from trailhead.install import run_install
from trailhead.pathint import ShimDirResult

_REPO_ROOT = Path(__file__).parent.parent.parent


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        **os.environ,
        "TRAILHEAD_STATE_DIR": str(tmp_path),
        "HOME": str(home),
        # Pinned, not inherited: TRAILHEAD_CLAUDE_DIR outranks CLAUDE_CONFIG_DIR,
        # so a developer with a relocated Claude dir still can't be written to.
        "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude-dir"),
    }


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
        assert set(selection) == {"camp", "lore", "craft", "portage", "outpost", "ranger"}

    def test_clis_installed(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), quiet=True)
        cli_tools = m["pathint"].call_args[0][0]
        assert set(cli_tools) == {"camp", "lore", "portage", "ranger"}

    def test_summary_prints_shellenv_hint(self, tmp_path, capsys):
        with _patched(detected=True):
            run_install(env=_env(tmp_path))
        out = capsys.readouterr().out
        assert "shellenv" in out

    def test_summary_names_trailhead_when_bin_is_executable(self, tmp_path, capsys):
        with _patched(detected=True), patch(
            "trailhead.install.trailhead_bin_executable", return_value=True
        ):
            run_install(env=_env(tmp_path))
        out = capsys.readouterr().out
        path_line = next((ln for ln in out.splitlines() if "on your PATH" in ln), "")
        assert path_line, "PATH guidance line should print"
        assert "trailhead" in path_line


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
        assert "portage" in cli_tools

    def test_no_lore_skips_lore_cli(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), no_lore=True, quiet=True)
        cli_tools = m["pathint"].call_args[0][0]
        assert "lore" not in cli_tools
        assert "camp" in cli_tools
        assert "portage" in cli_tools

    def test_no_portage_skips_portage_cli(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), no_portage=True, quiet=True)
        cli_tools = m["pathint"].call_args[0][0]
        assert "portage" not in cli_tools
        assert "camp" in cli_tools
        assert "lore" in cli_tools

    def test_config_can_drop_a_cli_with_no_matching_flag(self, tmp_path):
        # ranger ships no --no-ranger flag; its install_<name>_cli key is
        # resolved generically, so the config file is the only way to drop it.
        cfg_path = tmp_path / "no-ranger.toml"
        cfg_path.write_text("install_ranger_cli = false\n")
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), config_arg=str(cfg_path), quiet=True)
        cli_tools = m["pathint"].call_args[0][0]
        assert "ranger" not in cli_tools
        assert "camp" in cli_tools

    def test_every_cli_disabled_skips_pathint_entirely(self, tmp_path):
        cfg_path = tmp_path / "no-clis.toml"
        cfg_path.write_text("install_ranger_cli = false\n")
        with _patched(detected=True) as m:
            run_install(
                env=_env(tmp_path),
                config_arg=str(cfg_path),
                no_camp=True,
                no_lore=True,
                no_portage=True,
                quiet=True,
            )
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
        assert data["cli_flags"] == {
            "camp": True,
            "lore": True,
            "portage": True,
            "ranger": True,
        }

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


# ---------------------------------------------------------------------------
# shellenv PATH-guidance block — prints whenever path integration applies
# ---------------------------------------------------------------------------


class TestShellenvGuidance:
    def test_prints_and_names_trailhead_when_all_clis_disabled(self, tmp_path, capsys):
        cfg_path = tmp_path / "no-clis.toml"
        cfg_path.write_text("install_ranger_cli = false\n")
        with _patched(detected=True) as m, patch(
            "trailhead.install.trailhead_bin_executable", return_value=True
        ):
            rc = run_install(
                env=_env(tmp_path),
                config_arg=str(cfg_path),
                no_camp=True,
                no_lore=True,
                no_portage=True,
                quiet=True,
            )
        m["pathint"].assert_not_called()
        assert rc == 0
        out = capsys.readouterr().out
        assert "shellenv" in out
        path_line = next((ln for ln in out.splitlines() if "on your PATH" in ln), "")
        assert path_line, "PATH guidance line should print"
        assert "trailhead" in path_line

    def test_omits_trailhead_when_bin_not_executable(self, tmp_path, capsys):
        with _patched(detected=True), patch(
            "trailhead.install.trailhead_bin_executable", return_value=False
        ):
            run_install(env=_env(tmp_path), quiet=True)
        out = capsys.readouterr().out
        path_line = next((ln for ln in out.splitlines() if "on your PATH" in ln), "")
        assert path_line, "PATH guidance line should still print for the plugin CLIs"
        assert "trailhead" not in path_line

    def test_shim_dir_failure_stays_exit_zero_and_carries_full_path_fallback(
        self, tmp_path, capsys
    ):
        with _patched(detected=True) as m, patch(
            "trailhead.install.trailhead_bin_executable", return_value=True
        ):
            m["pathint"].side_effect = OSError("disk full")
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "shellenv" in out
        assert "bin/trailhead" in out
        # the explicit full-path fallback for each unshimmed CLI, not just prose
        assert "tools/camp/plugins/camp/bin/camp" in out
        assert "tools/lore/plugins/lore/bin/lore" in out
        # the failed plugin CLIs are dropped from the "on your PATH" promise —
        # the eval line does not provide them when the shim build failed
        path_line = next((ln for ln in out.splitlines() if "on your PATH" in ln), "")
        assert path_line, "PATH guidance line should still print"
        assert "camp" not in path_line
        assert "lore" not in path_line
        assert "trailhead" in path_line

    def test_no_cli_binaries_resolved_skips_shim_build_without_failure_copy(
        self, tmp_path, capsys
    ):
        # cli_flags enabled but _resolve_cli_tools drops every CLI (bins missing
        # on disk) — no shim build is attempted, so the "could not build the
        # shim dir (see warning above)" copy (which implies an attempt/warning)
        # must not appear.
        with _patched(detected=True) as m, patch(
            "trailhead.install._resolve_cli_tools", return_value={}
        ):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        m["pathint"].assert_not_called()
        out = capsys.readouterr().out
        assert "could not build the shim dir" not in out


# ---------------------------------------------------------------------------
# Plugin-declared rulesets
# ---------------------------------------------------------------------------


def _outpost_ruleset() -> tuple[str, str]:
    """The ruleset name + content outpost's manifest declares, read from disk."""
    manifest = load_manifest(_REPO_ROOT / "tools" / "outpost" / "capabilities.toml")
    # utf-8 explicitly: install pins it, and the ruleset carries non-ASCII prose
    # that a locale-default codec would fail to read (or read differently).
    content = (manifest.plugin_root / manifest.ruleset).read_text(encoding="utf-8")
    return f"trailhead-{manifest.tool_name}", content


class _RecordingHarness(ClaudeCodeHarness):
    """Claude Code harness that records the ruleset installs asked of it."""

    def __init__(self):
        self.ruleset_calls = []

    def install_user_ruleset(self, name, content, *, env=None):
        self.ruleset_calls.append((name, content))


class _RulesetlessHarness(ClaudeCodeHarness):
    """Stands in for a harness with no user-level ruleset support."""

    def user_ruleset_path(self, name, *, env=None):
        return None

    def user_ruleset_status(self, name, content, *, env=None):
        return "unsupported"

    def install_user_ruleset(self, name, content, *, env=None):
        raise AssertionError("an unsupported harness must never be asked to install")


class _RecorderA(ClaudeCodeHarness):
    """Registry-constructed recorder; get_harness builds a fresh instance per call,
    so installs are recorded on the CLASS."""

    name = "claude_code"
    installs: list[tuple[str, str]] = []

    def install_user_ruleset(self, name, content, *, env=None):
        type(self).installs.append((self.name, name))


class _RecorderB(_RecorderA):
    name = "codex"
    installs: list[tuple[str, str]] = []


class TestRulesetInstall:
    def test_declared_ruleset_installed_into_every_resolved_harness(self, tmp_path):
        """Two resolved harnesses each get the ruleset — not just the first."""
        _RecorderA.installs = []
        _RecorderB.installs = []
        name, _ = _outpost_ruleset()
        registry = {"claude_code": _RecorderA, "codex": _RecorderB}
        with _patched(detected=True), patch.dict(
            "trailhead.harness._HARNESSES", registry, clear=True
        ), patch(
            "trailhead.install.detect_harnesses",
            return_value=[_RecorderA(), _RecorderB()],
        ):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        assert _RecorderA.installs == [("claude_code", name)]
        assert _RecorderB.installs == [("codex", name)]

    def test_declared_ruleset_installed_once_per_harness(self, tmp_path):
        harness = _RecordingHarness()
        with _patched(detected=True), patch(
            "trailhead.install.get_harness", return_value=harness
        ):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        assert harness.ruleset_calls == [_outpost_ruleset()]

    def test_unwired_plugin_contributes_no_ruleset(self, tmp_path):
        harness = _RecordingHarness()
        with _patched(detected=True), patch(
            "trailhead.install.get_harness", return_value=harness
        ):
            run_install(env=_env(tmp_path), plugins=["lore"], quiet=True)
        assert harness.ruleset_calls == []

    def test_ruleset_written_to_the_injected_claude_dir(self, tmp_path):
        env = {**_env(tmp_path), "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude")}
        name, content = _outpost_ruleset()
        with _patched(detected=True):
            run_install(env=env, quiet=True)
        installed = tmp_path / "claude" / "rules" / f"{name}.md"
        assert installed.read_text(encoding="utf-8") == content

    def test_reinstall_is_a_no_op_and_reports_up_to_date(self, tmp_path, capsys):
        env = {**_env(tmp_path), "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude")}
        name, _ = _outpost_ruleset()
        target = tmp_path / "claude" / "rules" / f"{name}.md"
        with _patched(detected=True):
            run_install(env=env, quiet=True)
            first_mtime = target.stat().st_mtime_ns
            capsys.readouterr()
            run_install(env=env)
        out = capsys.readouterr().out
        assert target.stat().st_mtime_ns == first_mtime  # no write, no swap
        assert "up to date" in out

    def test_unsupported_harness_says_so_and_writes_nothing(self, tmp_path, capsys):
        env = {**_env(tmp_path), "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude")}
        with _patched(detected=True), patch(
            "trailhead.install.get_harness", return_value=_RulesetlessHarness()
        ):
            rc = run_install(env=env, quiet=True)
        assert rc == 0
        assert "no user-level ruleset support" in capsys.readouterr().err
        assert not (tmp_path / "claude" / "rules").exists()

    def test_unwritable_ruleset_surface_warns_without_failing_the_install(
        self, tmp_path, capsys
    ):
        harness = _RecordingHarness()
        harness.install_user_ruleset = lambda *a, **kw: (_ for _ in ()).throw(
            OSError("read-only file system")
        )
        with _patched(detected=True), patch(
            "trailhead.install.get_harness", return_value=harness
        ):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        assert "could not install the outpost ruleset" in capsys.readouterr().err

    def _manifest_with_ruleset(self, plugin_root: Path):
        from trailhead.capabilities import Manifest

        return Manifest(
            tool_name="outpost",
            plugin_root=plugin_root,
            base=[],
            hooks_json=None,
            cli_bin=None,
            ruleset="rules.md",
            validate=False,
            subagents={},
            skills={},
        )

    def test_unreadable_declared_ruleset_warns_without_failing_the_install(
        self, tmp_path, capsys
    ):
        """A declared ruleset that cannot be READ is a clean warning, not a traceback."""
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        rules = plugin_root / "rules.md"
        rules.write_text("rules\n", encoding="utf-8")
        harness = _RecordingHarness()
        # The denial is injected rather than expressed as a permission bit:
        # chmod(0o000) does not deny root, so a suite running as root in a
        # container would silently lose the failure this test exists to check.
        real_read_text = Path.read_text

        def denied(self, *args, **kwargs):
            if self.resolve() == rules.resolve():
                raise PermissionError(13, "Permission denied")
            return real_read_text(self, *args, **kwargs)

        with _patched(detected=True), patch(
            "trailhead.install.get_harness", return_value=harness
        ), patch(
            "trailhead.install.ruleset_bearing_manifests",
            return_value={"outpost": self._manifest_with_ruleset(plugin_root)},
        ), patch.object(Path, "read_text", denied):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        assert harness.ruleset_calls == []
        err = capsys.readouterr().err
        assert "trailhead: could not install the outpost ruleset" in err
        assert "Traceback" not in err

    def test_non_utf8_file_on_the_ruleset_surface_warns_and_install_continues(
        self, tmp_path, capsys
    ):
        """A pre-existing rules file with invalid utf-8 bytes degrades cleanly.

        The drift compare decodes the file already on disk as strict utf-8, so a
        hand-edited or legacy file there can raise a decode error. That must warn
        and leave the rest of the install — shim build, CLI bootstrap — running.
        """
        env = {**_env(tmp_path), "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude")}
        name, _ = _outpost_ruleset()
        target = tmp_path / "claude" / "rules" / f"{name}.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"legacy rules \xff\xfe not utf-8\n")
        with _patched(detected=True) as m:
            rc = run_install(env=env, quiet=True)
        assert rc == 0
        err = capsys.readouterr().err
        assert "trailhead: could not install the outpost ruleset" in err
        assert "Traceback" not in err
        # The steps that follow the ruleset install still ran.
        m["pathint"].assert_called_once()
        m["lore_init"].assert_called_once()

    def test_missing_declared_ruleset_warns_without_failing_the_install(
        self, tmp_path, capsys
    ):
        """A manifest that declares a ruleset file it doesn't ship degrades cleanly."""
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        harness = _RecordingHarness()
        with _patched(detected=True), patch(
            "trailhead.install.get_harness", return_value=harness
        ), patch(
            "trailhead.install.ruleset_bearing_manifests",
            return_value={"outpost": self._manifest_with_ruleset(plugin_root)},
        ):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        assert harness.ruleset_calls == []
        assert "could not install the outpost ruleset" in capsys.readouterr().err
