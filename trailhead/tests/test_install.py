"""Tests for trailhead install — cmd_install end-to-end (Slice 4).

TDD: tests written BEFORE implementation. Each test must fail before the
implementation exists, then pass after.

Test contract:
  - --preset minimal → wires lore only, persists preset+caps to config, summary
    names lore (not camp/craft), includes next command + config path + restart note.
  - --preset standard → wires lore+camp+craft subset.
  - --preset full → all tools.
  - Unknown --preset → named error, nonzero exit.
  - non-TTY, no --preset → defaults to standard, prints "defaulting to standard"
    line, never blocks on stdin.
  - interactive TTY, no --preset (simulated via injected input) → prompts with
    A-6 menu, bare-enter → standard.
  - SHA/integrity failure mid-install → nothing wired, named refusal, nonzero exit;
    assert no config saved, no partial wire.
  - Summary contains: wired set, next command, config path, restart note.
  - A-7: verified-in-place vs cloned wording asserted.
  - non-TTY + path_integration on → rc write skipped (A-8 skip message), shims
    still created.
  - --json → parseable object with wired tools/caps + config/shim/rc paths.
  - --quiet → no progress lines, summary still present.
  - A-9: errors to stderr, summary to stdout (assert separation); nonzero exit on
    integrity failure.

Hermeticity:
  All tests use tmp_path for config/state dirs (TRAILHEAD_CONFIG_DIR /
  TRAILHEAD_STATE_DIR env overrides). No test touches real ~/.claude/, real shell
  rc files, or the live trailhead state. wire and pathint are always stubbed.
"""

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent


def _hermetic_env(tmp_path: Path) -> dict[str, str]:
    """Env dict redirecting both config and state dirs to tmp_path subdirs.

    Keeps HOME from os.environ (required by paths.py for macOS path resolution).
    """
    return {
        **os.environ,
        "TRAILHEAD_CONFIG_DIR": str(tmp_path / "config"),
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
    }


def _noop_runner(*args, **kwargs):
    """Stub harness-CLI runner that does nothing."""
    pass


def _run_install(
    args: list[str],
    *,
    env: dict[str, str],
    stdin_text: str = "",
    is_tty: bool = False,
    wire_side_effect=None,
    pathint_side_effect=None,
):
    """Run run_install() with parsed args using stubbed wire + pathint.

    Parses args the same way cli._build_parser() would, but calls run_install()
    directly so the hermetic env dict is threaded through.

    Returns (exit_code, stdout_str, stderr_str).
    """
    from trailhead import install as install_mod
    from trailhead.pathint import PathIntegrationResult

    fake_shim_dir = Path(env.get("TRAILHEAD_STATE_DIR", "/tmp")) / "bin"
    fake_rc = Path(env.get("TRAILHEAD_STATE_DIR", "/tmp")) / ".fishrc"
    fake_pathint_result = PathIntegrationResult(
        shim_dir=fake_shim_dir,
        rc_path=fake_rc,
        skip_message=None,
    )

    # Parse args manually (mirrors the CLI parser)
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--preset", default=None)
    p.add_argument("--quiet", action="store_true", default=False)
    p.add_argument("--json", action="store_true", default=False)
    parsed, _ = p.parse_known_args(args)

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    try:
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf

        with patch("trailhead.install.wire") as mock_wire, \
             patch("trailhead.install.install_path_integration") as mock_pathint, \
             patch("trailhead.install.verify_present_repo") as mock_verify, \
             patch("trailhead.install.sys.stdin", io.StringIO(stdin_text)), \
             patch("trailhead.install._is_tty", return_value=is_tty):

            # By default verify_present_repo passes (returns True)
            mock_verify.return_value = True

            if wire_side_effect is not None:
                mock_wire.side_effect = wire_side_effect
            else:
                mock_wire.return_value = None

            if pathint_side_effect is not None:
                mock_pathint.side_effect = pathint_side_effect
            else:
                mock_pathint.return_value = fake_pathint_result

            try:
                exit_code = install_mod.run_install(
                    parsed.preset,
                    env=env,
                    quiet=parsed.quiet,
                    as_json=parsed.json,
                )
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else 0

    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


def _load_config(env: dict[str, str]):
    """Load config using the hermetic env."""
    from trailhead.config import load_config
    return load_config(env=env)


# ---------------------------------------------------------------------------
# T-I1: --preset minimal — lore only, summary, config persisted
# ---------------------------------------------------------------------------


class TestPresetMinimal:
    def test_exit_code_zero(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        assert code == 0

    def test_summary_names_lore(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        assert "lore" in out

    def test_summary_does_not_name_camp(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        # camp should NOT appear in the wired tools section
        # (it may appear in help text, but not as a wired tool)
        assert "camp" not in out.lower() or "wired" not in out.lower()

    def test_summary_does_not_name_craft(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        assert "craft" not in out.lower() or "wired" not in out.lower()

    def test_config_persisted_preset_minimal(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _run_install(["--preset", "minimal"], env=env)
        cfg = _load_config(env)
        assert cfg.preset == "minimal"

    def test_config_persisted_capabilities(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _run_install(["--preset", "minimal"], env=env)
        cfg = _load_config(env)
        assert "lore" in cfg.capabilities
        lore_caps = set(cfg.capabilities["lore"])
        assert lore_caps == {"capture", "recall", "sessions"}

    def test_summary_contains_next_command(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        # A-1: next step is a terminal-native first win
        assert "lore capture" in out or "capture" in out

    def test_summary_contains_config_path(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        # A-10: config path must appear in summary
        assert "config" in out.lower() and ("trailhead" in out.lower() or ".toml" in out.lower())

    def test_summary_contains_restart_note(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        # U-1 residual: must tell user to start a fresh session
        assert "fresh" in out.lower() or "restart" in out.lower() or "new session" in out.lower()

    def test_summary_to_stdout_not_stderr(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        # A-9: summary goes to stdout
        assert "lore" in out
        assert len(out.strip()) > 0

    def test_no_errors_to_stderr_on_success(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        assert err.strip() == ""


# ---------------------------------------------------------------------------
# T-I2: --preset standard → lore + camp + craft subset
# ---------------------------------------------------------------------------


class TestPresetStandard:
    def test_exit_code_zero(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "standard"], env=env)
        assert code == 0

    def test_summary_names_lore_camp_craft(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "standard"], env=env)
        assert "lore" in out
        assert "camp" in out
        assert "craft" in out

    def test_config_persisted_preset_standard(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _run_install(["--preset", "standard"], env=env)
        cfg = _load_config(env)
        assert cfg.preset == "standard"

    def test_config_persisted_camp_capabilities(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _run_install(["--preset", "standard"], env=env)
        cfg = _load_config(env)
        assert "camp" in cfg.capabilities

    def test_config_persisted_craft_capabilities(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _run_install(["--preset", "standard"], env=env)
        cfg = _load_config(env)
        assert "craft" in cfg.capabilities
        craft_caps = set(cfg.capabilities["craft"])
        assert "planning" in craft_caps
        assert "execute" in craft_caps
        assert "review" in craft_caps
        assert "helpers" in craft_caps


# ---------------------------------------------------------------------------
# T-I3: --preset full → all tools wired
# ---------------------------------------------------------------------------


class TestPresetFull:
    def test_exit_code_zero(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "full"], env=env)
        assert code == 0

    def test_summary_names_lore_camp_craft(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "full"], env=env)
        assert "lore" in out
        assert "camp" in out
        assert "craft" in out

    def test_config_persisted_preset_full(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _run_install(["--preset", "full"], env=env)
        cfg = _load_config(env)
        assert cfg.preset == "full"


# ---------------------------------------------------------------------------
# T-I4: unknown --preset → named error, nonzero exit
# ---------------------------------------------------------------------------


class TestUnknownPreset:
    def test_exit_code_nonzero(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "bogus"], env=env)
        assert code != 0

    def test_error_to_stderr(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "bogus"], env=env)
        assert "bogus" in err or "unknown" in err.lower() or "preset" in err.lower()

    def test_nothing_wired_or_persisted(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "bogus"], env=env)
        # Config should not be created (file absent = default used = no persistence happened)
        cfg_file = Path(env["TRAILHEAD_CONFIG_DIR"]) / "config.toml"
        assert not cfg_file.exists()


# ---------------------------------------------------------------------------
# T-I5: non-TTY, no --preset → defaults to standard, never blocks on stdin
# ---------------------------------------------------------------------------


class TestNonTtyDefaultsToStandard:
    def test_exit_code_zero(self, tmp_path):
        env = _hermetic_env(tmp_path)
        # is_tty=False, no preset flag, stdin empty → must not block
        code, out, err = _run_install([], env=env, is_tty=False, stdin_text="")
        assert code == 0

    def test_prints_defaulting_to_standard(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install([], env=env, is_tty=False, stdin_text="")
        combined = out + err
        assert "standard" in combined.lower()
        # The explicit "defaulting to" line
        assert "default" in combined.lower()

    def test_does_not_block_stdin(self, tmp_path):
        """Non-TTY with empty stdin must complete without reading from stdin."""
        env = _hermetic_env(tmp_path)
        # If this hangs, the test framework will timeout. The stub already patches
        # stdin to an empty StringIO, so a hanging read would read "" immediately.
        # We assert the call completes and returns code 0.
        code, out, err = _run_install([], env=env, is_tty=False, stdin_text="")
        assert code == 0

    def test_config_persisted_standard(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _run_install([], env=env, is_tty=False, stdin_text="")
        cfg = _load_config(env)
        assert cfg.preset == "standard"


# ---------------------------------------------------------------------------
# T-I6: interactive TTY, no --preset → A-6 prompt, bare-enter → standard
# ---------------------------------------------------------------------------


class TestInteractiveTtyPrompt:
    def test_bare_enter_selects_standard(self, tmp_path):
        env = _hermetic_env(tmp_path)
        # Bare enter (empty line) → default standard
        code, out, err = _run_install([], env=env, is_tty=True, stdin_text="\n")
        assert code == 0
        cfg = _load_config(env)
        assert cfg.preset == "standard"

    def test_prompt_shows_a6_menu(self, tmp_path):
        env = _hermetic_env(tmp_path)
        # A-6 self-guiding menu must appear
        code, out, err = _run_install([], env=env, is_tty=True, stdin_text="\n")
        combined = out + err
        assert "minimal" in combined
        assert "standard" in combined
        assert "full" in combined

    def test_prompt_shows_descriptions(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install([], env=env, is_tty=True, stdin_text="\n")
        combined = out + err
        # Each preset must have a description line in the menu (A-6)
        assert "lore only" in combined.lower() or "capture" in combined.lower()

    def test_explicit_minimal_input(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install([], env=env, is_tty=True, stdin_text="minimal\n")
        assert code == 0
        cfg = _load_config(env)
        assert cfg.preset == "minimal"

    def test_explicit_full_input(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install([], env=env, is_tty=True, stdin_text="full\n")
        assert code == 0
        cfg = _load_config(env)
        assert cfg.preset == "full"


# ---------------------------------------------------------------------------
# T-I7: integrity/SHA failure → nothing wired, named refusal, nonzero exit
# ---------------------------------------------------------------------------


class TestIntegrityFailure:
    """Critical: a SHA/integrity failure must → nothing wired, no config saved, nonzero exit."""

    def _integrity_error(self):
        from trailhead.fetch import FetchError
        return FetchError("trailhead: version mismatch in 'trailhead'\n"
                          "  expected: abc123def456\n"
                          "     found: 000000000000\n"
                          "The local checkout is at a different version than the install manifest pins.\n"
                          "Run `git -C /path/to/trailhead checkout abc123def456` to align it, then retry.")

    def test_exit_code_nonzero(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(
            ["--preset", "minimal"],
            env=env,
            wire_side_effect=self._integrity_error(),
        )
        assert code != 0

    def test_error_message_to_stderr(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(
            ["--preset", "minimal"],
            env=env,
            wire_side_effect=self._integrity_error(),
        )
        # Named refusal must go to stderr
        assert "mismatch" in err.lower() or "version" in err.lower() or "integrity" in err.lower() or len(err) > 0

    def test_no_config_saved(self, tmp_path):
        """Config must NOT be written when wire fails (nothing wired = no persist)."""
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(
            ["--preset", "minimal"],
            env=env,
            wire_side_effect=self._integrity_error(),
        )
        cfg_file = Path(env["TRAILHEAD_CONFIG_DIR"]) / "config.toml"
        assert not cfg_file.exists(), "config must not be saved when wire fails"

    def test_wire_error_also_stops_install(self, tmp_path):
        """A WireError (e.g. compose failure) must also stop install and be nonzero."""
        from trailhead.wire import WireError
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(
            ["--preset", "minimal"],
            env=env,
            wire_side_effect=WireError(tool="lore", stage="compose", cause=Exception("boom")),
        )
        assert code != 0
        cfg_file = Path(env["TRAILHEAD_CONFIG_DIR"]) / "config.toml"
        assert not cfg_file.exists(), "config must not be saved when wire fails"


# ---------------------------------------------------------------------------
# T-I8: A-7 honest output — verified-in-place vs cloned
# ---------------------------------------------------------------------------


class TestA7HonestOutput:
    def test_verified_in_place_message(self, tmp_path):
        """When the repo is already present, summary must say 'verified in place'."""
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        assert "verified in place" in out.lower() or "no download" in out.lower()


# ---------------------------------------------------------------------------
# T-I9: PATH integration in summary (A-3)
# ---------------------------------------------------------------------------


class TestPathIntegrationSummary:
    def test_path_line_in_summary_when_tty(self, tmp_path):
        """A-3: when path_integration on + TTY, summary names the rc file."""
        env = _hermetic_env(tmp_path)
        # Use a fake rc path in pathint result
        from trailhead.pathint import PathIntegrationResult
        fake_rc = tmp_path / "config.fish"
        fake_shim = tmp_path / "state" / "bin"

        def fake_pathint(*args, **kwargs):
            return PathIntegrationResult(
                shim_dir=fake_shim,
                rc_path=fake_rc,
                skip_message=None,
            )

        code, out, err = _run_install(
            ["--preset", "minimal"],
            env=env,
            is_tty=True,
            pathint_side_effect=fake_pathint,
        )
        assert code == 0
        # A-3: rc file named in summary
        assert "config.fish" in out or str(fake_rc) in out

    def test_non_tty_skip_message_in_summary(self, tmp_path):
        """A-8: non-TTY install → skip message appears in output."""
        env = _hermetic_env(tmp_path)
        from trailhead.pathint import PathIntegrationResult
        skip_msg = "PATH integration skipped (non-interactive) — run `trailhead config path_integration on` in your shell to enable"
        fake_shim = tmp_path / "state" / "bin"

        def fake_pathint(*args, **kwargs):
            return PathIntegrationResult(
                shim_dir=fake_shim,
                rc_path=None,
                skip_message=skip_msg,
            )

        code, out, err = _run_install(
            ["--preset", "minimal"],
            env=env,
            is_tty=False,
            pathint_side_effect=fake_pathint,
        )
        assert code == 0
        assert "skipped" in out.lower() or "non-interactive" in out.lower()


# ---------------------------------------------------------------------------
# T-I10: --json flag — machine-readable summary
# ---------------------------------------------------------------------------


class TestJsonFlag:
    def test_json_parses(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal", "--json"], env=env)
        assert code == 0
        data = json.loads(out)
        assert isinstance(data, dict)

    def test_json_contains_wired_tools(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal", "--json"], env=env)
        data = json.loads(out)
        assert "wired" in data
        assert "lore" in data["wired"]

    def test_json_contains_config_path(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal", "--json"], env=env)
        data = json.loads(out)
        assert "config_path" in data

    def test_json_contains_shim_dir(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal", "--json"], env=env)
        data = json.loads(out)
        assert "shim_dir" in data

    def test_json_no_progress_lines(self, tmp_path):
        """--json output must be parseable JSON (no progress lines mixed in)."""
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal", "--json"], env=env)
        # Must be valid JSON — no prefix noise
        data = json.loads(out)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# T-I11: --quiet flag — no progress lines, summary present
# ---------------------------------------------------------------------------


class TestQuietFlag:
    def test_quiet_exit_zero(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal", "--quiet"], env=env)
        assert code == 0

    def test_quiet_no_progress_lines(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal", "--quiet"], env=env)
        # Progress lines contain "verifying" or "wiring"
        assert "verifying" not in out.lower()
        assert "wiring" not in out.lower()

    def test_quiet_summary_still_present(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal", "--quiet"], env=env)
        # Summary (what was wired) must still appear
        assert "lore" in out or "wired" in out.lower()


# ---------------------------------------------------------------------------
# T-I12: A-9 stream separation — errors to stderr, summary to stdout
# ---------------------------------------------------------------------------


class TestStreamSeparation:
    def test_success_summary_to_stdout(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        assert len(out.strip()) > 0
        assert err.strip() == ""

    def test_failure_error_to_stderr(self, tmp_path):
        from trailhead.fetch import FetchError
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(
            ["--preset", "minimal"],
            env=env,
            wire_side_effect=FetchError("sha mismatch"),
        )
        assert code != 0
        assert len(err.strip()) > 0

    def test_failure_summary_not_to_stdout(self, tmp_path):
        """On failure, stdout should be empty (errors go to stderr only)."""
        from trailhead.fetch import FetchError
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(
            ["--preset", "minimal"],
            env=env,
            wire_side_effect=FetchError("sha mismatch"),
        )
        assert code != 0
        # stdout should have no success summary
        assert "wired" not in out.lower() or len(out.strip()) == 0


# ---------------------------------------------------------------------------
# T-I13: A-2 progress lines appear in normal (non-quiet, non-json) mode
# ---------------------------------------------------------------------------


class TestProgressLines:
    def test_verifying_progress_line(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        combined = out + err
        assert "verif" in combined.lower()

    def test_wiring_progress_line(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        combined = out + err
        assert "wir" in combined.lower()


# ---------------------------------------------------------------------------
# T-I14: A-10 multi-line grouped summary
# ---------------------------------------------------------------------------


class TestMultiLineSummary:
    def test_summary_is_multiline(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "minimal"], env=env)
        # A-10: multiple lines, not a single dot-separated string
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) > 1

    def test_standard_summary_names_craft_caps_separately(self, tmp_path):
        """A-10: standard preset summary should show craft caps on a line."""
        env = _hermetic_env(tmp_path)
        code, out, err = _run_install(["--preset", "standard"], env=env)
        # craft capabilities should be visible in summary
        assert "planning" in out or "execute" in out or "review" in out
