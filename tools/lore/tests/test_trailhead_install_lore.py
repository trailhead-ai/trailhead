"""Lore-side tests for Slice 5, S5 — ``trailhead install`` integration prep.

Covers the lore half of the Slice 5 contract:
  - ``lore init`` with an UNSET git identity (no ``$LORE_EMAIL``, empty
    ``git config --global user.email``) still exits 0 (bootstrap succeeds) AND
    prints a one-line advisory **to stderr** (so it survives even if
    ``trailhead install`` filters lore stdout).
  - With a git identity set, no advisory is printed.
  - The installed user-level ruleset (``~/.claude/rules/trailhead-lore.md``)
    carries the write-prohibition rules byte-exact.
  - **End-to-end byte-for-byte idempotency:** a second full ``lore init``
    (global) leaves ``settings.json`` AND the user-level ruleset file
    byte-for-byte unchanged. This exercises every writer delivered across the
    init flow (config seed, settings_writer hook/deny/env, seam ruleset install)
    for true idempotency.

All tests inject XDG_STATE_HOME / XDG_CONFIG_HOME / HOME via env and use
tmp_path so they NEVER touch real config, state, or vault (Axiom 6). The git
identity is isolated via ``GIT_CONFIG_GLOBAL`` so the test never reads the
developer's real ``~/.gitconfig``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _dirs(tmp_path):
    state = tmp_path / "state"
    config = tmp_path / "config"
    home = tmp_path / "home"
    for d in (state, config, home):
        d.mkdir(parents=True, exist_ok=True)
    # Claude Code must appear present for the harness to install the ruleset.
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    return state, config, home


def _run(args, *, state, config, home, identity: str | None):
    """Run lore CLI with isolated XDG dirs and a controlled git identity.

    ``identity`` is the email to set as ``$LORE_EMAIL``; when ``None``, no
    ``$LORE_EMAIL`` is set and ``git config --global user.email`` is forced
    empty via an isolated, empty ``GIT_CONFIG_GLOBAL`` file — modelling the
    first-run "git identity unset" case.
    """
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env["HOME"] = str(home)
    # Point the trailhead harness at the isolated Claude dir so it detects Claude
    # Code and installs the ruleset there — never the real ~/.claude (Axiom 6).
    env["TRAILHEAD_CLAUDE_DIR"] = str(home / ".claude")
    # Isolate git's global config so the developer's real ~/.gitconfig is never
    # consulted (deterministic identity for the unset-case test).
    gitconfig = home / "isolated.gitconfig"
    if identity is None:
        env.pop("LORE_EMAIL", None)
        gitconfig.write_text("")  # empty → user.email unset
    else:
        env["LORE_EMAIL"] = identity
        gitconfig.write_text("")
    env["GIT_CONFIG_GLOBAL"] = str(gitconfig)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _settings_path(home):
    return home / ".claude" / "settings.json"


def _rules_path(home):
    return home / ".claude" / "rules" / "trailhead-lore.md"


# ---------------------------------------------------------------------------
# 1. Unset git identity: init still succeeds, advisory goes to stderr
# ---------------------------------------------------------------------------

def test_init_unset_identity_still_exits_zero(tmp_path):
    state, config, home = _dirs(tmp_path)
    res = _run(["init"], state=state, config=config, home=home, identity=None)
    assert res.returncode == 0, res.stderr


def test_init_unset_identity_emits_advisory_to_stderr(tmp_path):
    state, config, home = _dirs(tmp_path)
    res = _run(["init"], state=state, config=config, home=home, identity=None)
    # Advisory must land on STDERR (survives stdout filtering by trailhead).
    assert "git" in res.stderr.lower()
    assert "user.email" in res.stderr or "identity" in res.stderr.lower()


def test_init_unset_identity_advisory_not_on_stdout(tmp_path):
    state, config, home = _dirs(tmp_path)
    res = _run(["init"], state=state, config=config, home=home, identity=None)
    # The identity advisory must NOT be on stdout (council Advocate/Security):
    # trailhead may filter lore stdout, so the warning belongs on stderr only.
    assert "user.email" not in res.stdout


def test_init_with_identity_emits_no_advisory(tmp_path):
    state, config, home = _dirs(tmp_path)
    res = _run(
        ["init"], state=state, config=config, home=home,
        identity="dev@example.com",
    )
    assert res.returncode == 0, res.stderr
    # With identity set, no git-identity advisory at all.
    assert "user.email" not in res.stderr
    assert "git identity" not in res.stderr.lower()


# ---------------------------------------------------------------------------
# 2. The installed user-level ruleset carries the write-prohibition rules
# ---------------------------------------------------------------------------

def test_installed_ruleset_carries_write_prohibition(tmp_path):
    state, config, home = _dirs(tmp_path)
    _run(["init"], state=state, config=config, home=home,
         identity="dev@example.com")
    rules = _rules_path(home).read_text()
    # The write-prohibition (Bash/shell gap) must be present in the ruleset.
    assert "Lore vault — mandatory write rules" in rules
    assert "**only** via the `lore` CLI" in rules


# ---------------------------------------------------------------------------
# 3. End-to-end byte-for-byte idempotency (every init-flow writer)
# ---------------------------------------------------------------------------

def test_second_init_leaves_settings_byte_for_byte_unchanged(tmp_path):
    state, config, home = _dirs(tmp_path)
    _run(["init"], state=state, config=config, home=home,
         identity="dev@example.com")
    first = _settings_path(home).read_bytes()
    _run(["init"], state=state, config=config, home=home,
         identity="dev@example.com")
    second = _settings_path(home).read_bytes()
    assert second == first, "settings.json must be byte-for-byte stable on re-run"


def test_second_init_leaves_ruleset_byte_for_byte_unchanged(tmp_path):
    state, config, home = _dirs(tmp_path)
    _run(["init"], state=state, config=config, home=home,
         identity="dev@example.com")
    first = _rules_path(home).read_bytes()
    _run(["init"], state=state, config=config, home=home,
         identity="dev@example.com")
    second = _rules_path(home).read_bytes()
    assert second == first, (
        "the user-level ruleset must be byte-for-byte stable on re-run"
    )
