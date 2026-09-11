"""Ephemeral assumption-probe test — DELETE before merge.

Proves/disproves: for the default account (account=None) under a separating
TRAILHEAD_CLAUDE_DIR, is there one well-defined "identity" account, and does
_claude_dir agree with what the trust pre-seed and the pane's real environment
resolve to?

See lore task/the-harness-answers-which-account-a-launch-lands-on.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from trailhead.harness import claude_config_file
from trailhead.harness.claude_code import ClaudeCodeHarness, _claude_dir

from camp.launch.claude_trust import config_file as trust_config_file
from camp.launch.profile import resolve_harness_profile
from camp.launch.session import resolve_launch_environment


def test_claude_dir_and_claude_config_file_disagree_under_separating_trailhead_dir(tmp_path):
    home = tmp_path / "home"
    separated = tmp_path / "elsewhere" / ".claude"
    env = {
        "PATH": "/usr/bin",
        "HOME": str(home),
        "TRAILHEAD_CLAUDE_DIR": str(separated),
        # CLAUDE_CONFIG_DIR deliberately absent so _refuse_conflicting_config_dirs
        # does not fire.
    }

    claude_dir_answer = _claude_dir(env)
    config_file_answer = claude_config_file(env)

    # Fact 1: _claude_dir honours TRAILHEAD_CLAUDE_DIR.
    assert claude_dir_answer == separated
    # Fact 2: claude_config_file does NOT honour it — it lands under HOME.
    assert config_file_answer == home / ".claude.json"
    # The two resolvers name accounts in DIFFERENT directories.
    assert claude_dir_answer != config_file_answer.parent


def test_scrub_list_omits_trailhead_claude_dir():
    harness = ClaudeCodeHarness()
    scrub = harness.session_launch_env_unset()
    assert "CLAUDE_CONFIG_DIR" in scrub
    # Fact 3: TRAILHEAD_CLAUDE_DIR is NOT scrubbed, so it survives into the pane.
    assert "TRAILHEAD_CLAUDE_DIR" not in scrub


def test_default_account_pane_and_trust_file_agree_with_config_file_not_claude_dir(tmp_path):
    """The acceptance-condition pin: run the REAL resolve_launch_environment and
    the REAL trust pre-seed resolver, for account=None, under a separating
    TRAILHEAD_CLAUDE_DIR — and check which resolver the pane and trust file
    actually land on.
    """
    home = tmp_path / "home"
    separated = tmp_path / "elsewhere" / ".claude"
    env = {
        "PATH": "/usr/bin",
        "HOME": str(home),
        "TRAILHEAD_CLAUDE_DIR": str(separated),
    }
    group = {"group": {"name": "testgroup"}}  # no launch.account -> account=None
    harness = ClaudeCodeHarness()
    profile = resolve_harness_profile(group)

    account, binding, scrub, launch_env = resolve_launch_environment(
        harness, profile, group, env=env
    )

    # account=None binds nothing (matches the plan's already-verified fact).
    assert account is None
    assert binding == {}

    # TRAILHEAD_CLAUDE_DIR is NOT scrubbed -> survives into the pane's own env.
    assert "TRAILHEAD_CLAUDE_DIR" in launch_env
    assert launch_env["TRAILHEAD_CLAUDE_DIR"] == str(separated)
    # CLAUDE_CONFIG_DIR was never present and stays absent (no binding for None).
    assert "CLAUDE_CONFIG_DIR" not in launch_env

    # What the trust pre-seed resolver (the file camp's trust pre-seed writes,
    # and what the real `claude` binary itself reads for its config file since
    # it has never heard of TRAILHEAD_CLAUDE_DIR) lands on, fed the PANE's own
    # environment:
    trust_target = trust_config_file(launch_env)
    assert trust_target == home / ".claude.json"

    # The candidate default-account "identity" via _claude_dir, fed the SAME
    # pane environment:
    identity_via_claude_dir = _claude_dir(launch_env)

    # THE DIVERGENCE: _claude_dir names a DIFFERENT account directory than the
    # one the trust file (and the real, TRAILHEAD_CLAUDE_DIR-ignorant `claude`
    # binary) actually resolve to.
    assert identity_via_claude_dir == separated
    assert identity_via_claude_dir != trust_target.parent
