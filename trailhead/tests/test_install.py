"""Tests for trailhead/install.py — config-driven, multi-harness install.

wire / create_shims / detect_harnesses are patched for hermeticity:
these tests never compose real trees or touch the user's harness/PATH.
"""

import importlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from trailhead.capabilities import load_manifest
from trailhead.core_vault import CoreVaultOutcome, install_core_vault
from trailhead.harness import ClaudeCodeHarness
from trailhead.install import run_install
from trailhead.pathint import ShimDirResult
from trailhead.tests.test_core_vault import FakeLore, _entry

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
def _patched(*, detected=None, lore_init_rc=0, lore_init_stderr="", stamp_warning=None, real_core=False):
    """Patch wire, create_shims, detect_harnesses, run_lore_init, write_stamp.

    ``run_lore_init`` is ALWAYS patched (Axiom 6): these tests must never
    invoke the real ``lore init`` against the user's vault/state. The default
    stub reports success; tests that exercise the failure path pass a non-zero
    ``lore_init_rc`` + ``lore_init_stderr``.

    ``write_stamp`` is likewise ALWAYS patched: it otherwise shells out to the
    real git checkout this test suite lives in. The default stub reports
    success (no warning); pass ``stamp_warning`` to exercise the unresolved-
    HEAD path.

    ``install_core_vault`` is patched too (it would shell out to lore) unless
    ``real_core`` is set, in which case the test supplies a scripted ``runner=``.
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
        patch("trailhead.install.write_stamp", return_value=stamp_warning) as stamp_mock,
        patch(
            "trailhead.install.install_core_vault",
            **(
                {"wraps": install_core_vault} if real_core else {"return_value": CoreVaultOutcome("up_to_date", "stub")}
            ),
        ) as core_mock,
    ):
        yield {
            "core_vault": core_mock,
            "wire": wire_mock,
            "pathint": pathint_mock,
            "detect": detect_mock,
            "lore_init": lore_mock,
            "write_stamp": stamp_mock,
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
        assert set(selection) == {
            "camp",
            "lore",
            "craft",
            "portage",
            "outpost",
            "trailhead",
        }

    def test_clis_installed(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), quiet=True)
        cli_tools = m["pathint"].call_args[0][0]
        pinned = m["pathint"].call_args.kwargs["pinned_scripts"]
        assert set(cli_tools) | set(pinned) == {
            "camp",
            "lore",
            "portage",
            "ranger",
            "trailhead",
            "outpost-gateway",
            "trailhead-gateway-register",
        }

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
        # Every CLI-bearing tool's install_<name>_cli key is resolved
        # generically off its manifest, so a config file can drop a CLI
        # without going through that tool's dedicated --no-<name> flag.
        cfg_path = tmp_path / "no-portage.toml"
        cfg_path.write_text("install_portage_cli = false\n")
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), config_arg=str(cfg_path), quiet=True)
        cli_tools = m["pathint"].call_args[0][0]
        assert "portage" not in cli_tools
        assert "camp" in cli_tools

    def test_every_cli_disabled_skips_pathint_entirely(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(
                env=_env(tmp_path),
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


class TestTrailheadJoinsTheShimSet:
    def test_build_cli_shims_writes_a_trailhead_shim_that_execs_bin_trailhead(self, tmp_path):
        from trailhead import install

        env = _env(tmp_path)
        fake = tmp_path / "camp-bin"
        fake.write_text("#!/bin/sh\n")

        shim_dir = install.build_cli_shims({"camp": fake}, env=env)

        assert shim_dir is not None
        content = (shim_dir / "trailhead").read_text()
        assert str(install._TRAILHEAD_BIN) in content
        assert str(install._REPO_ROOT) in content

    def test_build_cli_shims_builds_no_shim_dir_when_every_cli_is_disabled(self, tmp_path):
        from trailhead import install

        env = _env(tmp_path)

        assert install.build_cli_shims({}, env=env) is None
        assert not (Path(env["TRAILHEAD_STATE_DIR"]) / "bin" / "trailhead").exists()

    def test_install_hands_trailhead_to_the_shim_builder(self, tmp_path):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), quiet=True)
        assert "trailhead" in m["pathint"].call_args[0][0]


class TestShellenvGuidance:
    def test_prints_and_names_trailhead_when_all_clis_disabled(self, tmp_path, capsys):
        with _patched(detected=True) as m, patch(
            "trailhead.install.trailhead_bin_executable", return_value=True
        ):
            rc = run_install(
                env=_env(tmp_path),
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

    def test_path_line_names_ranger_when_its_shim_is_built(self, tmp_path, capsys):
        with _patched(detected=True) as m:
            run_install(env=_env(tmp_path), quiet=True)
        assert "ranger" in m["pathint"].call_args[0][0]
        path_line = next((ln for ln in capsys.readouterr().out.splitlines() if "on your PATH" in ln), "")
        assert "ranger" in path_line

    def test_path_line_omits_ranger_when_no_shim_is_built(self, tmp_path, capsys):
        with _patched(detected=True) as m, patch(
            "trailhead.install.trailhead_bin_executable", return_value=True
        ):
            run_install(
                env=_env(tmp_path), no_camp=True, no_lore=True, no_portage=True, quiet=True
            )
        m["pathint"].assert_not_called()
        path_line = next((ln for ln in capsys.readouterr().out.splitlines() if "on your PATH" in ln), "")
        assert path_line and "ranger" not in path_line

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
        # cli_flags enabled but resolve_cli_tools drops every CLI (bins missing
        # on disk) — no shim build is attempted, so the "could not build the
        # shim dir (see warning above)" copy (which implies an attempt/warning)
        # must not appear.
        with _patched(detected=True) as m, patch(
            "trailhead.install.resolve_cli_tools", return_value={}
        ):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        m["pathint"].assert_not_called()
        out = capsys.readouterr().out
        assert "could not build the shim dir" not in out


# ---------------------------------------------------------------------------
# Plugin-declared rulesets
# ---------------------------------------------------------------------------


def _declared_ruleset(tool: str) -> tuple[str, str]:
    """The ruleset name + content *tool*'s manifest declares, read from disk."""
    manifest = load_manifest(_REPO_ROOT / "tools" / tool / "capabilities.toml")
    # utf-8 explicitly: install pins it, and the ruleset carries non-ASCII prose
    # that a locale-default codec would fail to read (or read differently).
    content = (manifest.plugin_root / manifest.ruleset).read_text(encoding="utf-8")
    return f"trailhead-{manifest.tool_name}", content


def _outpost_ruleset() -> tuple[str, str]:
    return _declared_ruleset("outpost")


def _record_link_tokens() -> tuple[str, str]:
    """The record-URL contract's own emitted tokens, derived by importing the
    module rather than retyping them.

    Loading ``record_url.py`` directly by path fails: it carries a
    package-relative import, so ``exec_module`` on a bare file-location spec
    raises ``ImportError: attempted relative import with no known parent
    package``. Putting the package root on ``sys.path`` and importing it by
    dotted name works; ``sys.path`` is restored afterwards since the suite
    runs under pytest-xdist.

    ``build_record_url`` is called with an explicit ``env`` override pinning
    the base to :data:`DEFAULT_BASE`. Leaving ``env`` at its default reads the
    real process environment (``LORE_RECORD_URL_BASE``, then
    ``~/.config/lore/config.json``), so on a developer machine with either set
    the resolved base would differ from ``base`` below and ``built[len(base):]``
    would mis-slice — a failure caused by the test's own environment leakage,
    not by anything the rule under test does.
    """
    lore_plugin_root = str(_REPO_ROOT / "tools" / "lore" / "plugins" / "lore")
    sys.path.insert(0, lore_plugin_root)
    try:
        record_url = importlib.import_module("lore.record_url")
        base = record_url.DEFAULT_BASE
        built = record_url.build_record_url(
            "vaultseg", "kindseg", "slugseg", env={"LORE_RECORD_URL_BASE": base}
        )
    finally:
        sys.path.remove(lore_plugin_root)
    records_segment = built[len(base):].split("vaultseg")[0]
    return base, records_segment


def _ruleset_names_the_record_link_tokens(text: str) -> bool:
    """The predicate under test: does *text* carry the record-URL contract's
    own base and path-form tokens?"""
    base, records_segment = _record_link_tokens()
    return base in text and records_segment in text


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
        # Every harness gets the same set — the subject is per-harness parity,
        # not which plugins happen to declare a ruleset today.
        assert {n for _, n in _RecorderA.installs} == {n for _, n in _RecorderB.installs}
        assert ("claude_code", name) in _RecorderA.installs
        assert ("codex", name) in _RecorderB.installs

    def test_declared_ruleset_installed_once_per_harness(self, tmp_path):
        harness = _RecordingHarness()
        with _patched(detected=True), patch(
            "trailhead.install.get_harness", return_value=harness
        ):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        # Once per harness: no ruleset name is installed twice in a single run.
        names = [n for n, _ in harness.ruleset_calls]
        assert len(names) == len(set(names)), names
        assert harness.ruleset_calls.count(_outpost_ruleset()) == 1

    def test_ruleset_name_comes_from_the_plugin_key_not_the_manifest(
        self, tmp_path, capsys
    ):
        """The installed filename is derived from the plugin key install already
        trusts (the same key that decides whether the plugin is selected at all),
        never from the ``[tool] name`` the plugin file declares about itself —
        which is why a manifest cannot aim the write at a path of its choosing,
        nor collide with another plugin's ruleset by declaring its name.
        """
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "rules.md").write_text("rules\n", encoding="utf-8")
        manifest = self._manifest_with_ruleset(plugin_root, tool_name="/../../CLAUDE")
        harness = _RecordingHarness()
        with _patched(detected=True), patch(
            "trailhead.install.get_harness", return_value=harness
        ), patch(
            "trailhead.install.ruleset_bearing_manifests",
            return_value={"outpost": manifest},
        ):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        assert harness.ruleset_calls == [("trailhead-outpost", "rules\n")]
        assert "Traceback" not in capsys.readouterr().err

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

    def test_craft_ruleset_written_to_the_injected_claude_dir(self, tmp_path):
        """Craft owns the TDD rituals, so the testing posture ships with craft."""
        env = {**_env(tmp_path), "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude")}
        source = _REPO_ROOT / "tools" / "craft" / "plugins" / "craft" / "rules.md"
        with _patched(detected=True):
            run_install(env=env, quiet=True)
        installed = tmp_path / "claude" / "rules" / "trailhead-craft.md"
        assert installed.read_text(encoding="utf-8") == source.read_text(
            encoding="utf-8"
        )

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

    def _manifest_with_ruleset(self, plugin_root: Path, *, tool_name: str = "outpost"):
        from trailhead.capabilities import Manifest

        return Manifest(
            tool_name=tool_name,
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


# ---------------------------------------------------------------------------
# Record-link rule pinned at the install seam
# ---------------------------------------------------------------------------


class TestRecordLinkRuleInstalled:
    def test_installed_outpost_ruleset_names_the_record_url_contract_tokens(
        self, tmp_path
    ):
        env = {**_env(tmp_path), "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude")}
        name, _ = _outpost_ruleset()
        with _patched(detected=True):
            run_install(env=env, quiet=True)
        installed = tmp_path / "claude" / "rules" / f"{name}.md"
        installed_text = installed.read_text(encoding="utf-8")

        assert _ruleset_names_the_record_link_tokens(installed_text) is True

        # A genuinely different real input, not a synthetic string built by
        # deleting the very substrings the predicate searches for: the craft
        # plugin's ruleset, installed by this same real run_install call,
        # carries no record-link rule at all. Deriving the negative case by
        # stripping the two tokens out of installed_text would make the
        # assertion true by construction — the predicate can only ever answer
        # False against a string it was just built to lack those tokens in.
        craft_name, _ = _declared_ruleset("craft")
        craft_installed = tmp_path / "claude" / "rules" / f"{craft_name}.md"
        craft_text = craft_installed.read_text(encoding="utf-8")
        assert _ruleset_names_the_record_link_tokens(craft_text) is False


# ---------------------------------------------------------------------------
# Install provenance stamp
# ---------------------------------------------------------------------------


class TestProvenanceStampIntegration:
    def test_install_writes_a_provenance_stamp_after_wiring(self, tmp_path):
        with _patched(detected=True) as m:
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        m["write_stamp"].assert_called_once()
        args, kwargs = m["write_stamp"].call_args
        assert Path(args[0]).is_dir()  # the checkout path (repo root)

    def test_unresolvable_head_prints_a_warning_but_install_still_succeeds(
        self, tmp_path, capsys
    ):
        with _patched(detected=True, stamp_warning="could not record install provenance: boom"):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        err = capsys.readouterr().err
        assert "could not record install provenance" in err

    def test_no_harness_skips_the_provenance_stamp(self, tmp_path):
        with _patched(detected=False) as m:
            run_install(env=_env(tmp_path), quiet=True)
        m["write_stamp"].assert_not_called()


# ---------------------------------------------------------------------------
# core vault step
# ---------------------------------------------------------------------------


class TestCoreVaultStep:
    def test_runs_after_lore_init_and_with_the_checkout_source(self, tmp_path):
        order = []
        runner = object()
        with _patched(detected=True) as m:
            m["lore_init"].side_effect = lambda *a, **k: order.append("init") or (0, "")
            m["core_vault"].side_effect = lambda **k: order.append("core") or CoreVaultOutcome("up_to_date", "x")
            assert run_install(env=_env(tmp_path), quiet=True, runner=runner) == 0
        assert order == ["init", "core"]
        kw = m["core_vault"].call_args.kwargs
        assert kw["runner"] is runner
        assert kw["source"] == _REPO_ROOT / "core-vault"
        assert kw["lore_bin"].name == "lore"

    def test_lore_init_failure_skips_core_step_and_returns_one(self, tmp_path):
        with _patched(detected=True, lore_init_rc=1, lore_init_stderr="lore: boom") as m:
            assert run_install(env=_env(tmp_path), quiet=True) == 1
        m["core_vault"].assert_not_called()

    def test_no_lore_prints_turned_off_skip_and_calls_nothing(self, tmp_path, capsys):
        lore = FakeLore()
        with _patched(detected=True, real_core=True):
            assert run_install(env=_env(tmp_path), no_lore=True, quiet=True, runner=lore) == 0
        out = capsys.readouterr().out
        assert "skipped the core vault" in out and "turned off" in out
        assert lore.calls == []
        assert not (tmp_path / "core-vault").exists()

    def test_unavailable_cli_prints_a_different_skip_cause(self, tmp_path, capsys):
        with _patched(detected=True), patch("trailhead.install.resolve_cli_tools", return_value={}):
            assert run_install(env=_env(tmp_path), quiet=True) == 0
        out = capsys.readouterr().out
        assert "skipped the core vault" in out and "unavailable" in out and "turned off" not in out

    @pytest.mark.parametrize(
        ("vaults", "ls_rc", "outcome"),
        [([], 0, "registered"), (["decoy"], 0, "declined"), ([], 3, "failed")],
    )
    def test_json_summary_carries_core_vault_outcome(self, tmp_path, capsys, vaults, ls_rc, outcome):
        import json as _json

        lore = FakeLore([_entry("core", str(tmp_path / "x"), scope="team") for _ in vaults], ls_rc=ls_rc)
        with _patched(detected=True, real_core=True):
            assert run_install(env=_env(tmp_path), as_json=True, runner=lore) == 0
        captured = capsys.readouterr()
        data = _json.loads(captured.out)
        assert data["core_vault"]["outcome"] == outcome
        assert data["core_vault"]["reason"]
        assert data["no_harness"] is False

    def test_failed_core_step_keeps_exit_zero_and_summary(self, tmp_path, capsys):
        lore = FakeLore(ls_rc=1)
        with _patched(detected=True, real_core=True):
            assert run_install(env=_env(tmp_path), runner=lore) == 0
        captured = capsys.readouterr()
        assert "installed plugins" in captured.out
        assert "core vault" in captured.err

    def test_missing_lore_binary_in_core_step_keeps_exit_zero_and_summary(self, tmp_path, capsys):
        def runner(args, **kw):
            raise FileNotFoundError(2, "No such file or directory", args[0])

        with _patched(detected=True, real_core=True):
            assert run_install(env=_env(tmp_path), runner=runner) == 0
        captured = capsys.readouterr()
        assert "installed plugins" in captured.out
        assert "core vault" in captured.err

    def test_json_mode_sends_the_core_line_to_stderr(self, tmp_path, capsys):
        lore = FakeLore([])
        with _patched(detected=True, real_core=True):
            run_install(env=_env(tmp_path), as_json=True, runner=lore)
        captured = capsys.readouterr()
        assert "core vault registered" in captured.err
        assert "core vault" not in captured.out.replace('"core_vault"', "")


# ---------------------------------------------------------------------------
# core vault step under the wire lock
# ---------------------------------------------------------------------------


def _lock_path(env: dict[str, str]) -> Path:
    from trailhead.paths import state_dir

    return state_dir("trailhead", env=env) / "trailhead.lock"


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


def _seed_copy(env: dict[str, str]) -> Path:
    from trailhead.core_vault import core_copy_dir

    copy = core_copy_dir(env)
    (copy / "sub").mkdir(parents=True)
    (copy / "sub" / "doc.md").write_text("pre-existing copy content\n")
    return copy


class TestCoreVaultStepUnderWireLock:
    """The lock is taken at install's call site; a held lock degrades the step, never the install."""

    def _held_after_lore_init(self, m, env):
        """Create the lock once wiring (which takes and releases it) is done."""
        lock = _lock_path(env)

        def hold(*a, **k):
            lock.write_text("held by someone else\n")
            return (0, "")

        m["lore_init"].side_effect = hold
        return lock

    def test_held_lock_writes_nothing_and_install_exits_zero(self, tmp_path, capsys):
        import json as _json

        env = _env(tmp_path)
        copy = _seed_copy(env)
        before = _snapshot(copy)
        lore = FakeLore([])
        with _patched(detected=True, real_core=True) as m:
            self._held_after_lore_init(m, env)
            rc = run_install(env=env, as_json=True, runner=lore)
        assert rc == 0
        data = _json.loads(capsys.readouterr().out)
        assert data["core_vault"]["outcome"] == "failed"
        assert lore.calls == []
        assert _snapshot(copy) == before
        assert not [p for p in copy.parent.iterdir() if p.name.startswith(".core-vault.")]

    def test_human_stderr_names_lock_path_rm_recovery_and_rerun(self, tmp_path, capsys):
        env = _env(tmp_path)
        lore = FakeLore([])
        with _patched(detected=True, real_core=True) as m:
            lock = self._held_after_lore_init(m, env)
            assert run_install(env=env, runner=lore) == 0
        captured = capsys.readouterr()
        assert "installed plugins" in captured.out
        assert str(lock) in captured.err
        assert "another trailhead install or update may be running" in captured.err
        assert f"rm {lock}" in captured.err
        assert "re-run `trailhead install`" in captured.err

    def test_json_mode_sends_same_line_to_stderr_and_reason_names_the_lock(self, tmp_path, capsys):
        import json as _json

        env = _env(tmp_path)
        with _patched(detected=True, real_core=True) as m:
            lock = self._held_after_lore_init(m, env)
            run_install(env=env, as_json=True, runner=FakeLore([]))
        captured = capsys.readouterr()
        data = _json.loads(captured.out)
        assert data["core_vault"]["outcome"] == "failed"
        assert str(lock) in data["core_vault"]["reason"]
        assert f"rm {lock}" in captured.err and "re-run `trailhead install`" in captured.err
        assert "re-run" not in captured.out

    def test_no_harness_config_still_takes_the_lock(self, tmp_path, capsys):
        import json as _json

        env = _env(tmp_path)
        lock = _lock_path(env)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("held by someone else\n")
        lore = FakeLore([])
        with _patched(detected=False, real_core=True):
            run_install(env=env, as_json=True, runner=lore)
        data = _json.loads(capsys.readouterr().out)
        assert data["core_vault"]["outcome"] == "failed"
        assert lore.calls == []

    def test_unheld_lock_leaves_normal_behaviour_and_no_lock_file(self, tmp_path, capsys):
        import json as _json

        env = _env(tmp_path)
        lore = FakeLore([])
        with _patched(detected=True, real_core=True):
            assert run_install(env=env, as_json=True, runner=lore) == 0
        assert _json.loads(capsys.readouterr().out)["core_vault"]["outcome"] == "registered"
        assert not _lock_path(env).exists()

    def test_lock_is_released_and_exception_propagates_when_the_step_raises(self, tmp_path):
        env = _env(tmp_path)

        def runner(args, **kw):
            raise RuntimeError("lore exploded")

        with _patched(detected=True, real_core=True), pytest.raises(RuntimeError, match="lore exploded"):
            run_install(env=env, runner=runner)
        assert not _lock_path(env).exists()

    def test_install_core_vault_called_directly_leaves_a_caller_held_lock_alone(self, tmp_path):
        from trailhead.wire import wire_lock

        env = _env(tmp_path)
        lore = FakeLore([])
        with wire_lock(env=env) as lock:
            outcome = install_core_vault(
                source=_REPO_ROOT / "core-vault", env=env, lore_bin=Path("/x/lore"), runner=lore
            )
            assert lock.exists()
        assert outcome.outcome == "registered"


class TestCoreVaultStepWhenTheLockCannotBeTaken:
    """A lock that cannot be acquired for a reason other than being held degrades the step, never the install."""

    def test_unusable_state_dir_is_a_failed_outcome_and_install_still_summarises(self, tmp_path, capsys):
        import json as _json

        env = _env(tmp_path)
        blocker = tmp_path / "state-is-a-file"
        blocker.write_text("not a directory\n")
        env["TRAILHEAD_STATE_DIR"] = str(blocker)
        lore = FakeLore([])
        with _patched(detected=False, real_core=True):
            run_install(env=env, as_json=True, runner=lore)
        captured = capsys.readouterr()
        data = _json.loads(captured.out)
        assert data["core_vault"]["outcome"] == "failed"
        assert data["core_vault"]["reason"]
        assert "core vault" in captured.err
        assert lore.calls == []

    def test_unresolvable_state_dir_is_a_failed_outcome_not_a_raise(self, tmp_path, capsys):
        import json as _json

        env = _env(tmp_path)
        env["TRAILHEAD_STATE_DIR"] = "relative/state"
        lore = FakeLore([])
        with _patched(detected=False, real_core=True):
            run_install(env=env, as_json=True, runner=lore)
        data = _json.loads(capsys.readouterr().out)
        assert data["core_vault"]["outcome"] == "failed"
        assert lore.calls == []
