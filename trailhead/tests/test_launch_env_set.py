"""``session_launch_env_set`` — the account binding a launched session must carry.

The seam turns an opaque, harness-neutral account string (or ``None``) into the
environment assignments a launching caller sets explicitly on the child. Two
properties carry the whole point of the method:

- The answer NEVER comes from the ambient environment. A caller that inherits
  whatever the surrounding process (or a long-lived tmux server) happened to
  carry lands the session on the wrong account, which is the defect this seam
  exists to remove — so ``account=None`` resolves the harness's OWN default and
  returns it as an explicit assignment, never an empty dict.
- The value is a BASE directory, agreeing by construction with
  ``claude_config_file``'s ``<base>/.claude.json``.

Every test injects ``env``; none may read the developer's real home (Axiom 6).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trailhead.harness import claude_config_file
from trailhead.harness.base import HarnessError
from trailhead.harness.claude_code import ClaudeCodeHarness

CONFIG_DIR = "CLAUDE_CONFIG_DIR"


@pytest.fixture()
def harness():
    return ClaudeCodeHarness()


class TestTheDefaultAccountIsExplicit:
    """``account=None`` means 'the harness's own default' — stated, not inherited."""

    def test_home_is_returned_as_an_explicit_assignment(self, harness, tmp_path):
        assert harness.session_launch_env_set(None, env={"HOME": str(tmp_path)}) == {
            CONFIG_DIR: str(tmp_path)
        }

    def test_it_is_not_an_empty_dict(self, harness, tmp_path):
        """The anti-inheritance pin: an empty dict would leave the child on
        whatever the ambient environment carried."""
        assert harness.session_launch_env_set(None, env={"HOME": str(tmp_path)}) != {}

    def test_a_poisoned_ambient_config_dir_does_not_win(self, harness, tmp_path):
        env = {"HOME": str(tmp_path), CONFIG_DIR: str(tmp_path / "poison")}
        assert harness.session_launch_env_set(None, env=env) == {CONFIG_DIR: str(tmp_path)}

    def test_userprofile_stands_in_for_home(self, harness, tmp_path):
        assert harness.session_launch_env_set(None, env={"USERPROFILE": str(tmp_path)}) == {
            CONFIG_DIR: str(tmp_path)
        }


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

    def test_a_trailing_separator_is_normalized_away(self, harness, tmp_path):
        account = str(tmp_path / ".claude-levr")
        assert harness.session_launch_env_set(account + "/", env={"HOME": str(tmp_path)}) == {
            CONFIG_DIR: account
        }

    def test_the_value_is_a_base_dir_not_the_claude_subdir_or_the_file(self, harness, tmp_path):
        account = str(tmp_path / ".claude-levr")
        resolved = harness.session_launch_env_set(account, env={"HOME": str(tmp_path)})[CONFIG_DIR]
        assert not resolved.endswith(".claude")
        assert not resolved.endswith(".claude.json")


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

    def test_the_default_account_is_checked_against_the_seam_too(self, harness, tmp_path):
        """The refusal is on the RESOLVED value, so a default that disagrees with
        the seam refuses exactly like a declared one — the launched session would
        otherwise read a different directory than trailhead registers into."""
        seam = tmp_path / "seam"
        with pytest.raises(HarnessError) as exc_info:
            harness.session_launch_env_set(
                None, env={"HOME": str(tmp_path), "TRAILHEAD_CLAUDE_DIR": str(seam)}
            )
        assert str(seam) in str(exc_info.value)
        assert str(tmp_path) in str(exc_info.value)


class TestAgreementWithTheTrustResolver:
    """The seam's output and the pretrust target agree BY CONSTRUCTION, not by a
    parity test kept in step by hand."""

    def test_a_declared_account_feeds_claude_config_file(self, harness, tmp_path):
        account = tmp_path / ".claude-levr"
        resolved = harness.session_launch_env_set(str(account), env={"HOME": str(tmp_path)})
        assert claude_config_file(resolved) == account / ".claude.json"

    def test_the_default_account_feeds_claude_config_file(self, harness, tmp_path):
        resolved = harness.session_launch_env_set(None, env={"HOME": str(tmp_path)})
        assert claude_config_file(resolved) == tmp_path / ".claude.json"


class TestEnvDefaulting:
    def test_env_none_falls_back_to_the_process_environment(self, harness, monkeypatch, tmp_path):
        """``env=None`` reads ``os.environ`` — proven with an injected HOME so the
        real one is never touched."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TRAILHEAD_CLAUDE_DIR", raising=False)
        monkeypatch.delenv(CONFIG_DIR, raising=False)
        assert harness.session_launch_env_set(None) == {CONFIG_DIR: str(tmp_path)}


class TestNoRealHomeReached:
    def test_an_env_without_a_home_fails_loudly_instead_of_reaching_the_real_one(self, harness):
        with pytest.raises(AssertionError):
            harness.session_launch_env_set(None, env={"SOMETHING_ELSE": "x"})

    @pytest.mark.real_home
    def test_with_no_injected_home_the_real_home_is_the_documented_fallback(self, harness):
        assert harness.session_launch_env_set(None, env={}) == {CONFIG_DIR: str(Path.home())}
