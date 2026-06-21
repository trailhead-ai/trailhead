"""KU-1 assumption probe — ephemeral, delete after Slice 3 is built.

Proves or disproves KU-1 (plan: 2026-06-20-lore-agent-interface-fully-pull-hook-cleanup-and-user-level-rules-install.md).

Two claims, both must hold for VALIDATED:

(a) detect_harnesses() resolves at lore init runtime and returns ClaudeCodeHarness in
    the subprocess. The bootstrap (ensure_trailhead_importable) puts the repo root on
    sys.path, allowing `from trailhead.harness import detect_harnesses` to import, and
    detect_harnesses(env) returns a ClaudeCodeHarness when TRAILHEAD_CLAUDE_DIR points
    at a real directory.

(b) No caller passes --local to `lore init`, and no install-flow test asserts a
    CLAUDE.md agent-rules block is present post-install (which would break when Slice 3
    deletes that S5 path). Specifically:
      - install.py calls `lore init` without --local (verified by reading install.py:80-86)
      - test_trailhead_install_lore.py asserts CLAUDE.md block exists (BREAKER — will
        break when S5 injection is deleted)
      - test_lore_guardrail.py TestInitInstallsGuardrail tests use --local (BREAKER)
      - test_lore_init.py has --local tests (BREAKER)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
BOOTSTRAP = SCRIPTS_DIR / "_bootstrap.py"

# Repo root — walk up from here; the bootstrap does the same walk at runtime.
_HERE = Path(__file__).resolve()
_REPO_ROOT: Path | None = None
for _p in (_HERE, *_HERE.parents):
    if (_p / "trailhead" / "paths.py").exists():
        _REPO_ROOT = _p
        break


# =============================================================================
# Part (a): detect_harnesses() resolves inside a subprocess the way lore init runs
# =============================================================================

class TestDetectHarnessesResolvesInSubprocess:
    """KU-1(a): bootstrap + detect_harnesses work in the lore init subprocess."""

    def test_bootstrap_puts_repo_root_on_sys_path(self, tmp_path):
        """ensure_trailhead_importable() succeeds in a cold subprocess (no pip install).

        Simulates the exact environment that trailhead install creates when it
        invokes `lore init`: a fresh Python process with only the repo on disk,
        no editable install, but TRAILHEAD_ROOT set (Tier 3 fallback) or the
        walk-first Tier 2 anchor resolves via __file__.
        """
        assert _REPO_ROOT is not None, "could not locate repo root from test file"

        probe = f"""
import sys
sys.path.insert(0, {str(SCRIPTS_DIR)!r})
import _bootstrap
_bootstrap.ensure_trailhead_importable()
import trailhead.paths  # must not raise
print("bootstrap-ok")
"""
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            env={**os.environ},  # inherit real env; walk-first Tier 2 resolves via __file__
        )
        assert result.returncode == 0, (
            f"bootstrap failed in subprocess.\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        assert "bootstrap-ok" in result.stdout

    def test_detect_harnesses_returns_claude_code_when_claude_dir_present(self, tmp_path):
        """detect_harnesses(env) returns a ClaudeCodeHarness when TRAILHEAD_CLAUDE_DIR
        points at an existing directory.

        This mirrors what happens at lore init time when the user has ~/.claude.
        Uses TRAILHEAD_CLAUDE_DIR (the test override) so the real ~/.claude is
        never consulted (Axiom 6).
        """
        assert _REPO_ROOT is not None, "could not locate repo root from test file"

        fake_claude = tmp_path / "fake_claude"
        fake_claude.mkdir()

        probe = f"""
import sys, os
sys.path.insert(0, {str(SCRIPTS_DIR)!r})
import _bootstrap
_bootstrap.ensure_trailhead_importable()

from trailhead.harness import detect_harnesses, ClaudeCodeHarness

env = dict(os.environ)
env["TRAILHEAD_CLAUDE_DIR"] = {str(fake_claude)!r}

harnesses = detect_harnesses(env)
names = [h.name for h in harnesses]
has_claude = any(isinstance(h, ClaudeCodeHarness) for h in harnesses)
print(f"harnesses={{names}}")
print(f"has_claude_code={{has_claude}}")
"""
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            env={**os.environ},
        )
        assert result.returncode == 0, (
            f"subprocess probe failed.\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        assert "has_claude_code=True" in result.stdout, (
            f"detect_harnesses did not return ClaudeCodeHarness.\nstdout={result.stdout!r}"
        )

    def test_detect_harnesses_returns_empty_when_claude_dir_absent(self, tmp_path):
        """detect_harnesses(env) returns [] when TRAILHEAD_CLAUDE_DIR points nowhere."""
        assert _REPO_ROOT is not None, "could not locate repo root from test file"

        nonexistent = tmp_path / "no_claude_here"
        # deliberately NOT creating nonexistent

        probe = f"""
import sys, os
sys.path.insert(0, {str(SCRIPTS_DIR)!r})
import _bootstrap
_bootstrap.ensure_trailhead_importable()

from trailhead.harness import detect_harnesses

env = dict(os.environ)
env["TRAILHEAD_CLAUDE_DIR"] = {str(nonexistent)!r}
env.pop("HOME", None)  # eliminate HOME-based fallback

harnesses = detect_harnesses(env)
print(f"count={{len(harnesses)}}")
"""
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            env={**os.environ},
        )
        assert result.returncode == 0, (
            f"subprocess probe failed.\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        assert "count=0" in result.stdout, (
            f"detect_harnesses returned non-empty list when dir absent.\nstdout={result.stdout!r}"
        )


# =============================================================================
# Part (b): No caller passes --local, and install-flow tests assert CLAUDE.md block
# =============================================================================

class TestNoLocalCallerAndBreakingTests:
    """KU-1(b): verify --local usage and CLAUDE.md-block assertions in install-flow tests.

    These tests prove the CURRENT STATE of the codebase — specifically which
    things will BREAK when Slice 3 deletes the S5 injection path and --local.
    """

    def test_install_py_does_not_pass_local_to_lore_init(self):
        """trailhead/install.py invokes `lore init` without --local (line 80-86).

        run_lore_init() calls: runner([str(lore_bin), "init"], ...) — no --local.
        This is a static code assertion. If this fails, install.py grew a --local caller.
        """
        install_py = _REPO_ROOT / "trailhead" / "install.py"
        assert install_py.exists(), f"install.py not found at {install_py}"

        content = install_py.read_text()
        # The runner call must be `[str(lore_bin), "init"]` — no --local appended.
        assert '"init"' in content or "'init'" in content, "lore init call not found in install.py"

        # The critical negative: --local must NOT appear in run_lore_init's args list.
        # Find the run_lore_init function and confirm its runner call has no --local.
        import re
        # Extract the run_lore_init function body
        match = re.search(
            r"def run_lore_init.*?(?=\ndef |\Z)",
            content,
            re.DOTALL,
        )
        assert match is not None, "run_lore_init function not found in install.py"
        func_body = match.group(0)
        assert "--local" not in func_body, (
            f"run_lore_init passes --local to `lore init` — this will conflict with "
            f"Slice 3's removal of the flag.\nFunction body:\n{func_body[:500]}"
        )

    def test_install_flow_test_asserts_claude_md_block_BREAKER(self):
        """BREAKER: test_trailhead_install_lore.py asserts CLAUDE.md agent-rules block.

        test_injected_block_documents_rules_file_divergence (line 127-135) reads
        home/CLAUDE.md and asserts "re-run" and "lore init" are in the text.
        test_second_init_leaves_rules_byte_for_byte_unchanged (line 153-161)
        reads home/CLAUDE.md and asserts byte stability.

        These tests WILL FAIL when Slice 3 deletes the S5 injection path.
        This test confirms those assertions exist TODAY (expected to pass = BREAKER confirmed).
        """
        install_lore_test = TESTS_DIR / "test_trailhead_install_lore.py"
        assert install_lore_test.exists(), f"missing: {install_lore_test}"

        content = install_lore_test.read_text()
        # Confirm both breaker tests exist
        assert "test_injected_block_documents_rules_file_divergence" in content, (
            "CLAUDE.md block assertion test missing — may already be cleaned up"
        )
        assert "test_second_init_leaves_rules_byte_for_byte_unchanged" in content, (
            "CLAUDE.md idempotency test missing — may already be cleaned up"
        )
        # Confirm they actually check home/CLAUDE.md
        assert "_rules_path" in content or "CLAUDE.md" in content, (
            "CLAUDE.md reference missing from test_trailhead_install_lore.py"
        )

    def test_guardrail_test_uses_local_flag_BREAKER(self):
        """BREAKER: test_lore_guardrail.py TestInitInstallsGuardrail uses --local.

        Lines 505-673 contain 8+ tests that call `lore init --local`.
        These WILL FAIL when Slice 3 removes the --local flag (SystemExit(2)).
        This test confirms those usages exist TODAY.
        """
        guardrail_test = TESTS_DIR / "test_lore_guardrail.py"
        assert guardrail_test.exists(), f"missing: {guardrail_test}"

        content = guardrail_test.read_text()
        assert '"init", "--local"' in content or "'init', '--local'" in content or \
               '["init", "--local"]' in content, (
            "test_lore_guardrail.py has no `lore init --local` calls — "
            "may already be cleaned up"
        )

    def test_lore_init_test_has_local_tests_BREAKER(self):
        """BREAKER: test_lore_init.py has test_resolve_targets_local_returns_project_paths
        and test_local_outside_git_repo_fails_cleanly (plan lines 189-190).

        These WILL FAIL when Slice 3 removes --local and simplifies resolve_targets.
        This test confirms those tests exist TODAY.
        """
        init_test = TESTS_DIR / "test_lore_init.py"
        assert init_test.exists(), f"missing: {init_test}"

        content = init_test.read_text()
        assert "test_resolve_targets_local_returns_project_paths" in content, (
            "test_lore_init.py missing local resolve_targets test — may already cleaned up"
        )
        assert "test_local_outside_git_repo_fails_cleanly" in content, (
            "test_lore_init.py missing --local-outside-git test — may already cleaned up"
        )
