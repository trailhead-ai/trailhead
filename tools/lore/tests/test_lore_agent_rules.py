"""Tests for lore agent-interface Slice 3: user-level ruleset install via the seam.

``lore init`` installs lore's static ruleset into every detected harness through
the trailhead ``Harness`` seam (for Claude Code: ``~/.claude/rules/trailhead-lore.md``).
There is NO ``CLAUDE.md`` block injection, NO ``lore:agent-rules`` markers, and NO
``--local`` mode — the old S5 marker-delimited injection machinery is gone.

This file owns the ``lore init`` / ``lore status`` integration coverage:
  - ``lore init`` writes ``~/.claude/rules/trailhead-lore.md`` byte-exact AND the
    PreToolUse guardrail into ``~/.claude/settings.json``; it writes NO ``CLAUDE.md``
    and leaves zero ``lore:agent-rules`` markers on disk; re-run is a no-op; it emits
    a per-harness ``installed …``/``up to date`` confirmation line.
  - ``lore init --local`` is an unknown-flag error (``SystemExit(2)``), writing nothing.
  - ``lore status`` reports ``current`` on a clean install, ``stale`` after the file
    is mutated, ``missing`` after it is removed.

Seam-unit coverage (``install_user_ruleset`` / ``user_ruleset_status`` /
``UNSUPPORTED_RULESET_NOTICE`` degrade-visibly) lives in
``trailhead/tests/test_harness.py`` (Slice 2).

All tests isolate via a tmp ``HOME`` + ``TRAILHEAD_CLAUDE_DIR`` so they NEVER touch
the real ``~/.claude`` (Axiom 6).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _run(args, *, state, config, home, cwd=None, extra=None):
    """Run lore CLI with isolated XDG dirs, a fake HOME, and an isolated Claude dir.

    ``TRAILHEAD_CLAUDE_DIR`` is set to ``$HOME/.claude`` so the trailhead harness
    (a) detects Claude Code as present and (b) writes the ruleset into the SAME
    isolated tree the guardrail's ``settings.json`` lands in — never the real
    ``~/.claude`` (Axiom 6).
    """
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env["HOME"] = str(home)
    env["TRAILHEAD_CLAUDE_DIR"] = str(home / ".claude")
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
    # The Claude dir must already exist for detect() to find the harness — this
    # mirrors a machine that has Claude Code installed.
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    config.mkdir(parents=True, exist_ok=True)
    return state, config, home


def _ruleset_path(home):
    return home / ".claude" / "rules" / "trailhead-lore.md"


def _ruleset_content():
    return load_script("agent_ruleset").RULESET_CONTENT


# ---------------------------------------------------------------------------
# 1. lore init writes the user-level ruleset byte-exact
# ---------------------------------------------------------------------------

class TestInitWritesRuleset:
    def test_init_writes_ruleset_file(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        res = _run(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr
        assert _ruleset_path(home).is_file(), (
            "lore init must write ~/.claude/rules/trailhead-lore.md"
        )

    def test_ruleset_content_is_byte_exact(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        assert _ruleset_path(home).read_text() == _ruleset_content(), (
            "the installed ruleset must be byte-exact RULESET_CONTENT"
        )

    def test_init_emits_per_harness_confirmation_line(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        res = _run(["init"], state=state, config=config, home=home)
        combined = res.stdout + res.stderr
        assert "trailhead-lore.md" in combined and "installed" in combined.lower(), (
            f"lore init must emit a per-harness 'installed …' line:\n{combined}"
        )

    def test_rerun_emits_up_to_date_line(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        res = _run(["init"], state=state, config=config, home=home)
        combined = res.stdout + res.stderr
        assert "up to date" in combined.lower(), (
            f"a re-run must report the ruleset is 'up to date':\n{combined}"
        )


# ---------------------------------------------------------------------------
# 2. lore init also installs the PreToolUse guardrail (must SURVIVE the rewire)
# ---------------------------------------------------------------------------

class TestInitInstallsGuardrail:
    def test_init_writes_pretooluse_guard(self, tmp_path):
        import json

        state, config, home = _dirs(tmp_path)
        res = _run(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        settings = home / ".claude" / "settings.json"
        assert settings.is_file(), "lore init must write ~/.claude/settings.json"
        data = json.loads(settings.read_text())
        cmds = [
            h.get("command", "")
            for e in data.get("hooks", {}).get("PreToolUse", [])
            for h in e.get("hooks", [])
        ]
        assert any("vault-guard" in c for c in cmds), (
            f"lore init must install the PreToolUse vault-guard; got {cmds!r}"
        )


# ---------------------------------------------------------------------------
# 3. No CLAUDE.md block, no markers, no project files
# ---------------------------------------------------------------------------

class TestNoBlockInjection:
    def test_init_writes_no_claude_md(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        assert not (home / "CLAUDE.md").exists(), (
            "lore init must not write a ~/CLAUDE.md (block injection is gone)"
        )

    def test_no_agent_rules_markers_anywhere_on_disk(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        for root in (home, state, config):
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        text = p.read_text()
                    except (UnicodeDecodeError, OSError):
                        continue
                    assert "lore:agent-rules" not in text, (
                        f"found a stale lore:agent-rules marker in {p}"
                    )

    def test_init_creates_no_project_files(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        # Run from a git repo dir to prove init does NOT touch project files.
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        _run(["init"], state=state, config=config, home=home, cwd=repo)
        assert not (repo / "CLAUDE.md").exists()
        assert not (repo / ".cursorrules").exists()
        assert not (repo / ".claude" / "settings.local.json").exists()


# ---------------------------------------------------------------------------
# 4. Idempotency: re-run leaves the ruleset byte-for-byte unchanged
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_rerun_leaves_ruleset_byte_for_byte_unchanged(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        first = _ruleset_path(home).read_bytes()
        _run(["init"], state=state, config=config, home=home)
        second = _ruleset_path(home).read_bytes()
        assert second == first, "ruleset file must be byte-for-byte stable on re-run"


# ---------------------------------------------------------------------------
# 5. --local is gone (unknown flag → SystemExit(2), writes nothing)
# ---------------------------------------------------------------------------

class TestLocalFlagRemoved:
    def test_local_flag_is_unknown_flag_error(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        res = _run(["init", "--local"], state=state, config=config, home=home)
        assert res.returncode == 2, (
            f"lore init --local must be an argparse usage error (exit 2); "
            f"got {res.returncode}; stderr={res.stderr!r}"
        )

    def test_local_flag_writes_no_ruleset(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run(["init", "--local"], state=state, config=config, home=home)
        assert not _ruleset_path(home).exists(), (
            "the rejected --local run must not have installed the ruleset"
        )


# ---------------------------------------------------------------------------
# 6. lore status: current → stale → missing
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_reports_current_after_clean_install(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        res = _run(["status"], state=state, config=config, home=home)
        combined = res.stdout + res.stderr
        assert "current" in combined.lower(), (
            f"lore status must report 'current' on a clean install:\n{combined}"
        )

    def test_status_reports_stale_after_mutation(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        ruleset = _ruleset_path(home)
        ruleset.write_text(ruleset.read_text() + "\nmutated\n")
        res = _run(["status"], state=state, config=config, home=home)
        combined = res.stdout + res.stderr
        assert "stale" in combined.lower(), (
            f"lore status must report 'stale' after the ruleset is mutated:\n{combined}"
        )

    def test_status_reports_missing_after_removal(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        _ruleset_path(home).unlink()
        res = _run(["status"], state=state, config=config, home=home)
        combined = res.stdout + res.stderr
        assert "missing" in combined.lower(), (
            f"lore status must report 'missing' after the ruleset is removed:\n{combined}"
        )

    def test_status_offers_rerun_remedy_when_drifted(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run(["init"], state=state, config=config, home=home)
        _ruleset_path(home).unlink()
        res = _run(["status"], state=state, config=config, home=home)
        combined = res.stdout + res.stderr
        assert "lore init" in combined, (
            f"a drifted lore status must offer a 're-run lore init' remedy:\n{combined}"
        )
