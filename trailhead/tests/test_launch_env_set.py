"""``session_launch_env_set`` — the account binding a launched session must carry.

The seam turns an opaque, harness-neutral account string (or ``None``) into the
environment assignments a launching caller sets explicitly on the child. Two
properties carry the whole point of the method:

- The answer NEVER comes from the ambient environment. A caller that inherits
  whatever the surrounding process (or a long-lived tmux server) happened to
  carry lands the session on the wrong account, which is the defect this seam
  exists to remove.
- The value is the config DIRECTORY itself — the directory holding
  ``settings.json``, ``plugins/`` and ``.claude.json`` — returned verbatim, not a
  base some suffix is appended to.
- ``account=None`` is the harness's own default, and no VALUE of
  ``CLAUDE_CONFIG_DIR`` reproduces it: unset, Claude Code reads the config dir
  ``~/.claude`` AND the config file ``~/.claude.json``; ``=$HOME`` gets the file
  right and the dir wrong; ``=$HOME/.claude`` gets the dir right and moves the
  file to ``~/.claude/.claude.json``, orphaning the real one. The default is
  therefore expressed as the variable's ABSENCE — no assignment, and the name in
  ``session_launch_env_unset`` so every launching caller scrubs it.

Every test injects ``env``; none may read the developer's real home (Axiom 6).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trailhead.harness import claude_config_file
from trailhead.harness.base import HarnessError
from trailhead.harness.claude_code import ClaudeCodeHarness, _claude_dir

CONFIG_DIR = "CLAUDE_CONFIG_DIR"


@pytest.fixture()
def harness():
    return ClaudeCodeHarness()


class TestTheDefaultAccountIsAbsence:
    """``account=None`` contributes NO assignment, and the name is scrubbed instead.

    The two halves are one contract: the caller always removes the variable, then
    re-asserts it only for a declared account. An ambient value can therefore
    never win, and the undeclared case lands on exactly the state Claude Code
    starts from when nobody has set the variable at all.
    """

    def test_no_assignment_is_contributed(self, harness, tmp_path):
        assert harness.session_launch_env_set(None, env={"HOME": str(tmp_path)}) == {}

    def test_the_name_is_scrubbed_so_the_absence_is_asserted_not_inherited(self, harness):
        """The empty mapping is only honest because the scrub carries the other
        half: without the name here, an ambient value would simply survive."""
        assert CONFIG_DIR in harness.session_launch_env_unset()

    def test_a_poisoned_ambient_config_dir_is_not_carried_forward(self, harness, tmp_path):
        env = {"HOME": str(tmp_path), CONFIG_DIR: str(tmp_path / "poison")}
        assert harness.session_launch_env_set(None, env=env) == {}


class TestADeclaredAccount:
    def test_an_absolute_account_is_returned_verbatim(self, harness, tmp_path):
        account = str(tmp_path / ".claude-levr")
        env = {"HOME": str(tmp_path)}
        assert harness.session_launch_env_set(account, env=env) == {CONFIG_DIR: account}

    def test_it_wins_over_a_third_ambient_value(self, harness, tmp_path):
        """The poisoned-env case: the declaration decides, the ambient does not."""
        account = str(tmp_path / ".claude-levr")
        env = {"HOME": str(tmp_path), CONFIG_DIR: str(tmp_path / "poison")}
        assert harness.session_launch_env_set(account, env=env) == {CONFIG_DIR: account}

    def test_a_leading_tilde_expands_against_the_injected_home(self, harness, tmp_path):
        env = {"HOME": str(tmp_path)}
        assert harness.session_launch_env_set("~/.claude-levr", env=env) == {
            CONFIG_DIR: str(tmp_path / ".claude-levr")
        }

    def test_a_bare_tilde_is_the_injected_home(self, harness, tmp_path):
        assert harness.session_launch_env_set("~", env={"HOME": str(tmp_path)}) == {
            CONFIG_DIR: str(tmp_path)
        }

    def test_userprofile_stands_in_for_home_when_expanding_a_tilde(self, harness, tmp_path):
        assert harness.session_launch_env_set("~", env={"USERPROFILE": str(tmp_path)}) == {
            CONFIG_DIR: str(tmp_path)
        }

    def test_a_trailing_separator_is_normalized_away(self, harness, tmp_path):
        account = str(tmp_path / ".claude-levr")
        assert harness.session_launch_env_set(account + "/", env={"HOME": str(tmp_path)}) == {
            CONFIG_DIR: account
        }

    def test_the_value_is_the_config_dir_itself_not_a_base_it_sits_under(self, harness, tmp_path):
        """``CLAUDE_CONFIG_DIR`` names the config directory ITSELF: the account
        directory holds ``settings.json``, ``plugins/`` and ``.claude.json``
        directly and has no ``.claude`` child. An account whose own basename is
        ``.claude`` is therefore returned verbatim, never rewritten."""
        account = str(tmp_path / ".claude")
        assert harness.session_launch_env_set(account, env={"HOME": str(tmp_path)}) == {
            CONFIG_DIR: account
        }

    def test_the_config_dir_resolver_reads_the_value_verbatim(self, harness, tmp_path):
        """The agreement that makes the previous test's claim load-bearing: the
        resolver every other config-dir consumer goes through appends nothing."""
        account = str(tmp_path / ".claude-levr")
        resolved = harness.session_launch_env_set(account, env={"HOME": str(tmp_path)})
        assert _claude_dir({**resolved, "HOME": str(tmp_path)}) == Path(account)


class TestARelativeAccountIsRefused:
    """Mirrors camp's pretrust refusal: a relative override resolves against
    whoever's cwd happens to be current, so it is never guessed at."""

    @pytest.mark.parametrize("account", [".claude-levr", "foo/bar", "..", ""])
    def test_it_raises_naming_the_value(self, harness, account, tmp_path):
        with pytest.raises(HarnessError) as exc_info:
            harness.session_launch_env_set(account, env={"HOME": str(tmp_path)})
        assert repr(account) in str(exc_info.value)

    def test_a_named_user_tilde_is_refused_rather_than_resolved_on_this_machine(
        self, harness, tmp_path
    ):
        """``~someone`` must never be looked up in the system password database —
        that would resolve a path outside the injected environment entirely."""
        with pytest.raises(HarnessError) as exc_info:
            harness.session_launch_env_set("~someone/.claude", env={"HOME": str(tmp_path)})
        assert repr("~someone/.claude") in str(exc_info.value)


class TestConflictWithTheTrailheadSeam:
    """The account must not become a fourth config-dir resolution that walks
    around ``_refuse_conflicting_config_dirs``."""

    def test_a_disagreeing_seam_refuses_naming_both_values(self, harness, tmp_path):
        seam = tmp_path / "seam"
        account = str(tmp_path / ".claude-levr")
        with pytest.raises(HarnessError) as exc_info:
            harness.session_launch_env_set(
                account, env={"HOME": str(tmp_path), "TRAILHEAD_CLAUDE_DIR": str(seam)}
            )
        message = str(exc_info.value)
        assert str(seam) in message
        assert account in message

    def test_an_equal_seam_does_not_refuse(self, harness, tmp_path):
        account = str(tmp_path / ".claude-levr")
        env = {"HOME": str(tmp_path), "TRAILHEAD_CLAUDE_DIR": account}
        assert harness.session_launch_env_set(account, env=env) == {CONFIG_DIR: account}

    def test_a_realpath_equal_seam_does_not_refuse(self, harness, tmp_path):
        real = tmp_path / ".claude-levr"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        env = {"HOME": str(tmp_path), "TRAILHEAD_CLAUDE_DIR": str(link)}
        assert harness.session_launch_env_set(str(real), env=env) == {CONFIG_DIR: str(real)}

    def test_the_default_states_no_value_so_it_contradicts_nothing(self, harness, tmp_path):
        """The refusal is on a value this seam ASSERTS. The default asserts none,
        so there is no second statement of intent for the seam to disagree with —
        and refusing here would block every undeclared launch on a condition no
        group declaration could clear."""
        seam = tmp_path / "seam"
        assert (
            harness.session_launch_env_set(
                None, env={"HOME": str(tmp_path), "TRAILHEAD_CLAUDE_DIR": str(seam)}
            )
            == {}
        )


class TestAgreementWithTheTrustResolver:
    """The seam's output and the pretrust target agree BY CONSTRUCTION, not by a
    parity test kept in step by hand."""

    def test_a_declared_account_feeds_claude_config_file(self, harness, tmp_path):
        account = tmp_path / ".claude-levr"
        resolved = harness.session_launch_env_set(str(account), env={"HOME": str(tmp_path)})
        assert claude_config_file(resolved) == account / ".claude.json"

    def test_the_default_leaves_the_trust_target_on_the_home_config_file(self, harness, tmp_path):
        """Merged over the scrubbed launch environment, contributing nothing
        resolves the trust target to the SAME file an unset variable does — the
        real ``~/.claude.json``, not a fresh stub under ``~/.claude``."""
        env = {"HOME": str(tmp_path)}
        resolved = harness.session_launch_env_set(None, env=env)
        assert claude_config_file({**env, **resolved}) == tmp_path / ".claude.json"


class TestEnvDefaulting:
    def test_env_none_falls_back_to_the_process_environment(self, harness, monkeypatch, tmp_path):
        """``env=None`` reads ``os.environ`` — proven with an injected HOME so the
        real one is never touched."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TRAILHEAD_CLAUDE_DIR", raising=False)
        monkeypatch.delenv(CONFIG_DIR, raising=False)
        assert harness.session_launch_env_set("~") == {CONFIG_DIR: str(tmp_path)}


class TestNoRealHomeReached:
    def test_an_env_without_a_home_fails_loudly_instead_of_reaching_the_real_one(self, harness):
        with pytest.raises(AssertionError):
            harness.session_launch_env_set("~", env={"SOMETHING_ELSE": "x"})

    @pytest.mark.real_home
    def test_with_no_injected_home_the_real_home_is_the_documented_fallback(self, harness):
        assert harness.session_launch_env_set("~", env={}) == {CONFIG_DIR: str(Path.home())}
