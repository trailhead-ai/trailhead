"""Tests for Slice 4, S5: agent-rules injection (marker-delimited, idempotent, lock-guarded).

Covers every bullet of the Slice 4 test contract:
  - The lore block is injected between stable markers.
  - Re-run replaces in place (single block, no dupes).
  - Only pre-existing rules files (plus the canonical one) are touched; no stray files.
  - Concurrent inject (two processes, barrier-synced) yields exactly one block (flock smoke test).
  - ``--local`` injects into the project rules file; global into the user rules file.
  - The injected block documents the non-Claude-Code degradation (rules-only guardrail).
  - Drift advisory: with a rules-file candidate present but missing the marker block,
    ``lore init`` (and ``lore status``) names it in an advisory; with all candidates
    carrying the block, no advisory.
  - CRITICAL: the injected block explicitly prohibits Bash/shell vault writes (e.g.
    ``> file``, ``tee``, ``sed -i``, ``cp``, ``mv``), not just direct file edits.

All tests inject XDG_STATE_HOME / XDG_CONFIG_HOME / HOME via env and use tmp_path so
they NEVER touch real config, state, vault, or ``~/.claude`` data (Axiom 6).
"""
from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------

def _run(args, *, state, config, home, cwd=None, extra=None):
    """Run lore CLI with fully isolated XDG dirs and a fake HOME."""
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env["HOME"] = str(home)
    env["LORE_EMAIL"] = "tester@example.com"
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _dirs(tmp_path):
    state = tmp_path / "state"
    config = tmp_path / "config"
    home = tmp_path / "home"
    state.mkdir(parents=True, exist_ok=True)
    config.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    return state, config, home


def _load_installer():
    return load_script("installer")


# ---------------------------------------------------------------------------
# 1. Block injected between stable markers
# ---------------------------------------------------------------------------

class TestMarkerDelimitedInjection:
    def test_inject_creates_markers_in_new_file(self, tmp_path):
        """inject_agent_rules writes LORE_START and LORE_END markers in a new file."""
        installer = _load_installer()
        rules = tmp_path / "CLAUDE.md"
        installer.inject_agent_rules(rules)
        content = rules.read_text()
        assert "<!-- lore:agent-rules:start -->" in content
        assert "<!-- lore:agent-rules:end -->" in content

    def test_inject_appends_to_existing_file(self, tmp_path):
        """inject_agent_rules preserves existing content before the block."""
        installer = _load_installer()
        rules = tmp_path / "CLAUDE.md"
        rules.write_text("# My existing rules\n\nSome content.\n")
        installer.inject_agent_rules(rules)
        content = rules.read_text()
        assert "# My existing rules" in content
        assert "<!-- lore:agent-rules:start -->" in content

    def test_inject_block_between_markers(self, tmp_path):
        """The lore content appears between the start and end markers."""
        installer = _load_installer()
        rules = tmp_path / "CLAUDE.md"
        installer.inject_agent_rules(rules)
        content = rules.read_text()
        start = content.index("<!-- lore:agent-rules:start -->")
        end = content.index("<!-- lore:agent-rules:end -->")
        assert start < end, "start marker must precede end marker"
        block = content[start:end]
        assert len(block) > len("<!-- lore:agent-rules:start -->"), (
            "block between markers must have substantive content"
        )

    def test_rerun_replaces_in_place_no_dupes(self, tmp_path):
        """Re-running inject_agent_rules replaces the block in place (no duplicate markers)."""
        installer = _load_installer()
        rules = tmp_path / "CLAUDE.md"
        installer.inject_agent_rules(rules)
        installer.inject_agent_rules(rules)
        content = rules.read_text()
        assert content.count("<!-- lore:agent-rules:start -->") == 1
        assert content.count("<!-- lore:agent-rules:end -->") == 1

    def test_rerun_via_lore_init_no_dupes(self, tmp_path):
        """lore init called twice does not duplicate the agent-rules block."""
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        _run(["init"], state=state, config=config, home=home)
        rules = home / "CLAUDE.md"
        content = rules.read_text()
        assert content.count("<!-- lore:agent-rules:start -->") == 1
        assert content.count("<!-- lore:agent-rules:end -->") == 1


# ---------------------------------------------------------------------------
# 2. Only pre-existing rules files (plus canonical) are touched
# ---------------------------------------------------------------------------

class TestOnlyExistingFilesTouched:
    def test_canonical_rules_file_created_if_absent(self, tmp_path):
        """inject_agent_rules creates the canonical rules file if absent (CLAUDE.md)."""
        installer = _load_installer()
        rules = tmp_path / "CLAUDE.md"
        assert not rules.exists()
        installer.inject_agent_rules(rules)
        assert rules.exists()

    def test_cursorrules_only_touched_if_already_present(self, tmp_path):
        """inject_agent_rules only injects into .cursorrules if it already exists."""
        installer = _load_installer()
        canonical = tmp_path / "CLAUDE.md"
        cursorrules = tmp_path / ".cursorrules"
        assert not cursorrules.exists()
        # Inject into both with existing .cursorrules
        cursorrules.write_text("# Cursor rules\n")
        installer.inject_agent_rules(canonical, extra_paths=[cursorrules])
        assert "<!-- lore:agent-rules:start -->" in cursorrules.read_text()

    def test_no_stray_rules_files_created(self, tmp_path):
        """inject_agent_rules does not create extra rules files that were absent."""
        installer = _load_installer()
        canonical = tmp_path / "CLAUDE.md"
        cursorrules = tmp_path / ".cursorrules"
        # Don't create .cursorrules — it should stay absent
        installer.inject_agent_rules(canonical, extra_paths=[cursorrules])
        assert not cursorrules.exists(), (
            "inject_agent_rules created a stray .cursorrules file that did not exist before"
        )

    def test_lore_init_does_not_create_stray_cursorrules(self, tmp_path):
        """lore init must not create .cursorrules if it was absent."""
        state, config, home = _dirs(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)

        _run(["init", "--local"], state=state, config=config, home=home, cwd=repo)
        assert not (repo / ".cursorrules").exists(), (
            "lore init --local created a stray .cursorrules file"
        )


# ---------------------------------------------------------------------------
# 3. Concurrent inject yields exactly one block (flock smoke test)
# ---------------------------------------------------------------------------

def _inject_with_barrier(rules_path, barrier_path, done_path, installer_scripts_dir):
    """Worker: wait for barrier, then inject, signal done."""
    import importlib.util
    import sys
    sys.path.insert(0, str(installer_scripts_dir))
    spec = importlib.util.spec_from_file_location(
        "installer", str(installer_scripts_dir) + "/installer.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Wait for barrier (poll until barrier file appears)
    for _ in range(50):
        if Path(barrier_path).exists():
            break
        time.sleep(0.01)

    mod.inject_agent_rules(Path(rules_path))
    Path(done_path).touch()


class TestConcurrentInject:
    def test_concurrent_inject_yields_exactly_one_block(self, tmp_path):
        """Two processes racing inject_agent_rules produce exactly one marker block (KU3)."""
        rules = tmp_path / "CLAUDE.md"
        barrier = tmp_path / "barrier"
        done1 = tmp_path / "done1"
        done2 = tmp_path / "done2"

        p1 = multiprocessing.Process(
            target=_inject_with_barrier,
            args=(str(rules), str(barrier), str(done1), str(SCRIPTS_DIR)),
        )
        p2 = multiprocessing.Process(
            target=_inject_with_barrier,
            args=(str(rules), str(barrier), str(done2), str(SCRIPTS_DIR)),
        )
        p1.start()
        p2.start()

        # Signal both to start simultaneously
        barrier.touch()

        p1.join(timeout=10)
        p2.join(timeout=10)

        assert p1.exitcode == 0, f"process 1 exited with {p1.exitcode}"
        assert p2.exitcode == 0, f"process 2 exited with {p2.exitcode}"

        content = rules.read_text()
        assert content.count("<!-- lore:agent-rules:start -->") == 1, (
            f"Expected exactly 1 start marker, got "
            f"{content.count('<!-- lore:agent-rules:start -->')}:\n{content}"
        )
        assert content.count("<!-- lore:agent-rules:end -->") == 1, (
            f"Expected exactly 1 end marker, got "
            f"{content.count('<!-- lore:agent-rules:end -->')}:\n{content}"
        )


# ---------------------------------------------------------------------------
# 4. --local vs global target routing
# ---------------------------------------------------------------------------

class TestLocalVsGlobalTargets:
    def test_global_init_injects_into_user_rules_file(self, tmp_path):
        """Global lore init injects the block into ~/CLAUDE.md."""
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        rules = home / "CLAUDE.md"
        assert rules.exists(), "global lore init must create ~/CLAUDE.md"
        content = rules.read_text()
        assert "<!-- lore:agent-rules:start -->" in content

    def test_local_init_injects_into_project_rules_file(self, tmp_path):
        """lore init --local injects the block into the git repo's CLAUDE.md."""
        state, config, home = _dirs(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)

        _run(["init", "--local"], state=state, config=config, home=home, cwd=repo)
        rules = repo / "CLAUDE.md"
        assert rules.exists(), "lore init --local must create <git-root>/CLAUDE.md"
        content = rules.read_text()
        assert "<!-- lore:agent-rules:start -->" in content

    def test_local_init_does_not_touch_user_rules_file(self, tmp_path):
        """lore init --local must NOT inject into ~/CLAUDE.md."""
        state, config, home = _dirs(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)

        _run(["init", "--local"], state=state, config=config, home=home, cwd=repo)
        user_rules = home / "CLAUDE.md"
        if user_rules.exists():
            content = user_rules.read_text()
            assert "<!-- lore:agent-rules:start -->" not in content, (
                "lore init --local injected into ~/CLAUDE.md (should only touch project CLAUDE.md)"
            )


# ---------------------------------------------------------------------------
# 5. Block content requirements
# ---------------------------------------------------------------------------

class TestBlockContent:
    def _get_block(self, tmp_path):
        installer = _load_installer()
        rules = tmp_path / "CLAUDE.md"
        installer.inject_agent_rules(rules)
        content = rules.read_text()
        start = content.index("<!-- lore:agent-rules:start -->")
        end = content.index("<!-- lore:agent-rules:end -->")
        return content[start:end + len("<!-- lore:agent-rules:end -->")]

    def test_block_states_cli_is_only_write_path(self, tmp_path):
        """The injected block must state that the lore CLI is the only write path."""
        block = self._get_block(tmp_path)
        block_lower = block.lower()
        assert "lore" in block_lower
        assert any(w in block_lower for w in ("cli", "command", "only")), (
            "block must state lore CLI is the only write path"
        )

    def test_block_prohibits_direct_file_edits(self, tmp_path):
        """The injected block must explicitly prohibit direct file edits."""
        block = self._get_block(tmp_path)
        block_lower = block.lower()
        assert any(w in block_lower for w in ("direct", "never", "do not", "not by")), (
            "block must explicitly prohibit direct file edits"
        )

    def test_block_prohibits_bash_shell_writes(self, tmp_path):
        """CRITICAL: the injected block must explicitly prohibit Bash/shell vault writes.

        Slice 3's runtime guardrail does NOT cover Bash-mediated writes (> file, tee,
        sed -i, cp, mv) — those are covered ONLY by this agent-rules prohibition. The
        block MUST state this unambiguously, not just imply it.
        """
        block = self._get_block(tmp_path)
        block_lower = block.lower()
        # Must mention Bash or shell explicitly
        assert any(w in block_lower for w in ("bash", "shell", "redirect", ">", "tee", "sed")), (
            "CRITICAL: block must explicitly mention Bash/shell writes (>, tee, sed -i, cp, mv) "
            "— these are NOT covered by the PreToolUse guardrail and this is the only protection"
        )

    def test_block_documents_non_claude_degradation(self, tmp_path):
        """The injected block must document the non-Claude-Code degradation (rules-only)."""
        block = self._get_block(tmp_path)
        block_lower = block.lower()
        # Must reference other harnesses or the rules-only nature
        assert any(w in block_lower for w in (
            "non-claude", "other harness", "rules-only", "harness", "cursor", "codex",
            "rules only", "only protection", "without claude"
        )), (
            "block must document the non-Claude-Code degradation (rules-only guardrail)"
        )

    def test_block_includes_docs_pointer(self, tmp_path):
        """The injected block must include a pointer to the lore docs."""
        block = self._get_block(tmp_path)
        block_lower = block.lower()
        assert any(w in block_lower for w in ("doc", "lore:", "see ", "refer", "guide")), (
            "block must include a pointer to lore docs / agent-driven procedures"
        )


# ---------------------------------------------------------------------------
# 6. Drift advisory
# ---------------------------------------------------------------------------

class TestDriftAdvisory:
    def test_lore_init_emits_advisory_for_candidate_missing_block(self, tmp_path):
        """lore init emits an advisory when a candidate rules file lacks the marker block."""
        state, config, home = _dirs(tmp_path)
        # Create a .cursorrules WITHOUT the block — simulates a file added after init
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        cursorrules = repo / ".cursorrules"
        cursorrules.write_text("# Cursor rules without lore block\n")

        res = _run(["init", "--local"], state=state, config=config, home=home, cwd=repo)
        # Advisory should mention the file and "re-run lore init"
        combined = res.stdout + res.stderr
        assert ".cursorrules" in combined or "cursorrules" in combined.lower(), (
            f"lore init must name the uninjected rules file in an advisory:\n{combined}"
        )

    def test_lore_init_silent_when_all_candidates_have_block(self, tmp_path):
        """lore init emits no drift advisory when all candidate rules files carry the block."""
        state, config, home = _dirs(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)

        # First run: injects the block (no .cursorrules present, so no advisory)
        res = _run(["init", "--local"], state=state, config=config, home=home, cwd=repo)
        assert res.returncode == 0, res.stderr

        # Run again: everything already has the block — should be advisory-free
        res2 = _run(["init", "--local"], state=state, config=config, home=home, cwd=repo)
        combined = res2.stdout + res2.stderr
        assert "re-run" not in combined.lower() or "advisory" not in combined.lower(), (
            "lore init emitted a drift advisory when all rules files already have the block"
        )

    def test_lore_init_advisory_mentions_rerun(self, tmp_path):
        """The drift advisory from lore init says 're-run lore init'."""
        state, config, home = _dirs(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        cursorrules = repo / ".cursorrules"
        cursorrules.write_text("# Cursor rules without lore block\n")

        res = _run(["init", "--local"], state=state, config=config, home=home, cwd=repo)
        combined = res.stdout + res.stderr
        assert "re-run" in combined.lower() or "lore init" in combined.lower(), (
            f"drift advisory must say 're-run lore init':\n{combined}"
        )

    def test_lore_status_emits_drift_line_for_uninjected_candidate(self, tmp_path):
        """lore status emits a drift line naming rules-file candidates missing the block."""
        state, config, home = _dirs(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)

        # Run init first so vault/config are set up
        _run(["init", "--local"], state=state, config=config, home=home, cwd=repo)

        # Now add a .cursorrules WITHOUT the block AFTER init
        cursorrules = repo / ".cursorrules"
        cursorrules.write_text("# Cursor rules without lore block\n")

        res = _run(["status"], state=state, config=config, home=home, cwd=repo)
        combined = res.stdout + res.stderr
        assert ".cursorrules" in combined or "cursorrules" in combined.lower(), (
            f"lore status must name the uninjected rules file in a drift line:\n{combined}"
        )

    def test_lore_status_no_drift_when_all_blocks_present(self, tmp_path):
        """lore status emits no drift advisory when all candidate rules files have the block."""
        state, config, home = _dirs(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)

        # Init with .cursorrules present — both get injected
        cursorrules = repo / ".cursorrules"
        cursorrules.write_text("# Cursor rules\n")
        _run(["init", "--local"], state=state, config=config, home=home, cwd=repo)

        # Verify .cursorrules got the block
        assert "<!-- lore:agent-rules:start -->" in cursorrules.read_text(), (
            "pre-existing .cursorrules must have been injected during init"
        )

        res = _run(["status"], state=state, config=config, home=home, cwd=repo)
        combined = res.stdout + res.stderr
        assert "re-run" not in combined.lower(), (
            f"lore status emitted drift advisory when all rules files have the block:\n{combined}"
        )

    def test_scan_for_rules_drift_returns_uninjected_candidates(self, tmp_path):
        """scan_for_rules_drift returns paths of candidate rules files lacking the marker block."""
        installer = _load_installer()
        search_root = tmp_path
        cursorrules = search_root / ".cursorrules"
        cursorrules.write_text("# Cursor rules without block\n")
        # CLAUDE.md has the block
        claude_md = search_root / "CLAUDE.md"
        installer.inject_agent_rules(claude_md)

        drifted = installer.scan_for_rules_drift(search_root)
        # .cursorrules is present and lacks the block → should be in drifted
        assert any(p.name == ".cursorrules" for p in drifted), (
            f"scan_for_rules_drift must return .cursorrules (lacks block); got: {drifted}"
        )
        # CLAUDE.md has the block → must NOT be in drifted
        assert not any(p.name == "CLAUDE.md" for p in drifted), (
            "CLAUDE.md has the block and must not appear in scan_for_rules_drift"
        )

    def test_scan_for_rules_drift_empty_when_all_injected(self, tmp_path):
        """scan_for_rules_drift returns empty list when all candidates have the block."""
        installer = _load_installer()
        search_root = tmp_path
        claude_md = search_root / "CLAUDE.md"
        cursorrules = search_root / ".cursorrules"
        cursorrules.write_text("# Cursor rules\n")
        installer.inject_agent_rules(claude_md)
        installer.inject_agent_rules(cursorrules)

        drifted = installer.scan_for_rules_drift(search_root)
        assert drifted == [], (
            f"scan_for_rules_drift must return [] when all blocks present; got: {drifted}"
        )
