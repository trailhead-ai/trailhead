"""``session_launch_account_identity`` — the account identity a launch lands on.

The seam answers TWO facts from ONE resolution: the operator-facing identity of
the account a launch with a given declaration will run under, and whether that
account has configuration behind it. Both facts are derived from exactly the
resolvers :meth:`ClaudeCodeHarness.session_launch_env_set` and
:func:`claude_config_file` already use — never from ``_claude_dir``, which
honours ``TRAILHEAD_CLAUDE_DIR`` while the trust pre-seed and the launched
pane's own environment resolution do not (see ``_account_home`` /
``claude_config_file`` docstrings).

Every test injects ``env``; none may read the developer's real home (Axiom 6).
"""

from __future__ import annotations

import pytest

from trailhead.harness import claude_config_file
from trailhead.harness.base import AccountIdentity, HarnessError
from trailhead.harness.claude_code import ClaudeCodeHarness, _claude_dir


@pytest.fixture()
def harness():
    return ClaudeCodeHarness()


class TestTwoInputsTwoAnswers:
    """A declared account and no declaration must resolve to DIFFERENT
    identities — the dependency this method exists to protect."""

    def test_declared_and_default_identities_differ(self, harness, tmp_path):
        home = tmp_path / "home"
        declared = tmp_path / "levr-account"
        env = {"HOME": str(home)}

        default_identity = harness.session_launch_account_identity(None, env=env)
        declared_identity = harness.session_launch_account_identity(str(declared), env=env)

        assert isinstance(default_identity, AccountIdentity)
        assert isinstance(declared_identity, AccountIdentity)
        assert default_identity.label != declared_identity.label
        assert declared_identity.label == str(declared)


class TestAntiDivergencePin:
    """The acceptance condition for this task's hard blocker: under a
    TRAILHEAD_CLAUDE_DIR that separates the config-dir resolver from the
    config-file resolver, the default-account identity must agree with
    ``claude_config_file`` (the resolver the trust pre-seed and the real
    launched process actually read) and must NOT agree with ``_claude_dir``
    (which the launch path never uses for the default case)."""

    def test_default_identity_agrees_with_claude_config_file_not_claude_dir(
        self, harness, tmp_path
    ):
        home = tmp_path / "home"
        separated = tmp_path / "elsewhere" / ".claude"
        env = {"HOME": str(home), "TRAILHEAD_CLAUDE_DIR": str(separated)}

        # The two resolvers must actually disagree for this to be a real pin.
        assert claude_config_file(env).parent != _claude_dir(env)

        identity = harness.session_launch_account_identity(None, env=env)

        assert identity.label == str(claude_config_file(env).parent)
        assert identity.label != str(_claude_dir(env))


class TestExistenceAgreesWithIdentity:
    """The existence answer must be checked at the SAME account the identity
    names — under a separating TRAILHEAD_CLAUDE_DIR, a config file planted at
    the wrong resolver's location must not flip the answer."""

    def test_has_config_true_when_the_real_config_file_exists(self, harness, tmp_path):
        home = tmp_path / "home"
        home.mkdir(parents=True)
        (home / ".claude.json").write_text("{}")
        env = {"HOME": str(home)}

        identity = harness.session_launch_account_identity(None, env=env)

        assert identity.has_config is True

    def test_has_config_false_when_only_the_wrong_resolvers_location_has_a_file(
        self, harness, tmp_path
    ):
        home = tmp_path / "home"
        separated = tmp_path / "elsewhere" / ".claude"
        separated.mkdir(parents=True)
        (separated / ".claude.json").write_text("{}")
        env = {"HOME": str(home), "TRAILHEAD_CLAUDE_DIR": str(separated)}

        identity = harness.session_launch_account_identity(None, env=env)

        # The config file exists at _claude_dir's location, not at
        # claude_config_file's — has_config must reflect the latter.
        assert identity.has_config is False

    def test_has_config_true_for_a_declared_account_with_a_config_file(self, harness, tmp_path):
        declared = tmp_path / "levr-account"
        declared.mkdir(parents=True)
        (declared / ".claude.json").write_text("{}")

        identity = harness.session_launch_account_identity(str(declared), env={})

        assert identity.has_config is True

    def test_has_config_false_for_a_declared_account_with_no_config_file(self, harness, tmp_path):
        declared = tmp_path / "not-yet-signed-in"

        identity = harness.session_launch_account_identity(str(declared), env={})

        assert identity.has_config is False


class TestRefusalRaisesRatherThanAnswering:
    """A declaration the harness refuses outright (the same posture as
    ``session_launch_env_set``) must raise, distinguishable from a clean
    'no configuration' answer for a valid-but-nonexistent account."""

    def test_a_relative_account_raises(self, harness):
        with pytest.raises(HarnessError):
            harness.session_launch_account_identity("not/absolute", env={})

    def test_refusal_is_distinct_from_a_clean_no_config_answer(self, harness, tmp_path):
        nonexistent_but_valid = tmp_path / "never-signed-in"
        identity = harness.session_launch_account_identity(
            str(nonexistent_but_valid), env={}
        )
        assert identity.has_config is False

        with pytest.raises(HarnessError):
            harness.session_launch_account_identity("relative/path", env={})


class TestSymmetry:
    """A harness answering identity for a declared account answers it for the
    default too — both non-None together."""

    def test_both_branches_answer_non_none(self, harness, tmp_path):
        env = {"HOME": str(tmp_path / "home")}
        assert harness.session_launch_account_identity(None, env=env) is not None
        assert (
            harness.session_launch_account_identity(str(tmp_path / "acct"), env=env)
            is not None
        )
