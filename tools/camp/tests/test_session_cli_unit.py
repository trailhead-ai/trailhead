"""Unit-level tests for camp.cli.session — collaborators mocked directly.

Complements test_session_cli.py's end-to-end subprocess coverage with a seam
that is awkward to exercise through the real CLI binary: `_addressable_harnesses`'s
store keying and dropped-store notice.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


class _AccountAwareHarness:
    """A harness stand-in whose account binding is a simple, opaque mapping —
    proving camp reads no key of its own out of it (it uses whatever name
    THIS harness chooses, never ``CLAUDE_CONFIG_DIR`` or any other credential
    path camp might otherwise hardcode).
    """

    name = "acctharness"

    def session_launch_env_unset(self):
        return []

    def session_launch_env_set(self, account, *, env=None):
        if account is None:
            return {}
        return {"FAKE_STORE_DIR": account}


class TestAddressableHarnessesStoreKeying:
    """`_addressable_harnesses` keys its pool by (harness, credential store),
    not by harness name alone — see the module docstring for why."""

    def test_two_groups_sharing_a_harness_with_different_accounts_are_two_candidates(
        self, monkeypatch
    ):
        """The pinned regression: before this keying, two groups sharing a
        harness with different accounts collapsed into ONE queried store."""
        import camp.cli.session as cli_session
        import camp.launch.profile as profile

        monkeypatch.setattr(profile, "harness_for", lambda group: _AccountAwareHarness())
        groups = [
            {"group": {"name": "g1"}, "launch": {"account": "/acct/a"}},
            {"group": {"name": "g2"}, "launch": {"account": "/acct/b"}},
        ]

        stores = cli_session._addressable_harnesses(groups, env={})

        declared = [s for s in stores if s.account is not None]
        assert len(declared) == 2
        assert {s.account for s in declared} == {"/acct/a", "/acct/b"}

    def test_two_groups_sharing_a_harness_with_the_same_account_are_one_candidate(
        self, monkeypatch
    ):
        import camp.cli.session as cli_session
        import camp.launch.profile as profile

        monkeypatch.setattr(profile, "harness_for", lambda group: _AccountAwareHarness())
        groups = [
            {"group": {"name": "g1"}, "launch": {"account": "/acct/a"}},
            {"group": {"name": "g2"}, "launch": {"account": "/acct/a"}},
        ]

        stores = cli_session._addressable_harnesses(groups, env={})

        declared = [s for s in stores if s.account is not None]
        assert len(declared) == 1
        assert declared[0].account == "/acct/a"

    def test_groups_declaring_no_account_contribute_the_default_store_once(
        self, monkeypatch
    ):
        import camp.cli.session as cli_session
        import camp.launch.profile as profile

        monkeypatch.setattr(profile, "harness_for", lambda group: _AccountAwareHarness())
        groups = [{"group": {"name": "g1"}}, {"group": {"name": "g2"}}, {"group": {"name": "g3"}}]

        stores = cli_session._addressable_harnesses(groups, env={})

        assert len(stores) == 1
        assert stores[0].account is None

    def test_every_configured_group_declaring_an_account_still_yields_the_default_store(
        self, monkeypatch
    ):
        """The pinned regression: the default store used to enter the pool
        only when the group list was EMPTY, so a machine where every group
        declares an account never queried the default store at all — a
        session running under it was invisible to every reference."""
        import camp.cli.session as cli_session
        import camp.launch.profile as profile

        monkeypatch.setattr(profile, "harness_for", lambda group: _AccountAwareHarness())
        groups = [
            {"group": {"name": "g1"}, "launch": {"account": "/acct/a"}},
            {"group": {"name": "g2"}, "launch": {"account": "/acct/b"}},
        ]

        stores = cli_session._addressable_harnesses(groups, env={})

        assert {s.account for s in stores} == {"/acct/a", "/acct/b", None}

    def test_a_group_declaring_no_account_does_not_duplicate_the_default_store(
        self, monkeypatch
    ):
        """One group declares an account, the other declares none — the
        no-account group already contributes the default store, so the
        default probe must not add a second copy of it."""
        import camp.cli.session as cli_session
        import camp.launch.profile as profile

        monkeypatch.setattr(profile, "harness_for", lambda group: _AccountAwareHarness())
        groups = [
            {"group": {"name": "g1"}, "launch": {"account": "/acct/a"}},
            {"group": {"name": "g2"}},
        ]

        stores = cli_session._addressable_harnesses(groups, env={})

        assert {s.account for s in stores} == {"/acct/a", None}
        assert len(stores) == 2

    def test_a_group_whose_harness_camp_cannot_name_contributes_nothing(self, monkeypatch):
        import camp.cli.session as cli_session
        import camp.launch.profile as profile

        def fake_harness_for(group):
            if (group.get("group") or {}).get("name") == "bad":
                return None
            return _AccountAwareHarness()

        monkeypatch.setattr(profile, "harness_for", fake_harness_for)
        groups = [{"group": {"name": "bad"}}, {"group": {"name": "good"}}]

        stores = cli_session._addressable_harnesses(groups, env={})

        assert len(stores) == 1

    def test_no_groups_configured_yields_the_default_harness_profiles_default_store(self):
        """No monkeypatching: the real ClaudeCodeHarness resolves, and its
        `session_launch_env_set(None, ...)` is pure — no filesystem write."""
        import camp.cli.session as cli_session

        stores = cli_session._addressable_harnesses([], env={})

        assert len(stores) == 1
        assert stores[0].account is None

    def test_each_stores_environment_differs_and_comes_from_the_harness(self, monkeypatch):
        """camp names no credential path of its own: the store's env carries
        exactly the mapping THIS harness chose to return, under whatever key
        it named — never a hardcoded `CLAUDE_CONFIG_DIR` or similar."""
        import camp.cli.session as cli_session
        import camp.launch.profile as profile

        monkeypatch.setattr(profile, "harness_for", lambda group: _AccountAwareHarness())
        groups = [
            {"group": {"name": "g1"}, "launch": {"account": "/acct/a"}},
            {"group": {"name": "g2"}, "launch": {"account": "/acct/b"}},
        ]

        stores = cli_session._addressable_harnesses(groups, env={"BASE": "1"})

        by_account = {s.account: s.env.get("FAKE_STORE_DIR") for s in stores if s.account is not None}
        assert by_account == {"/acct/a": "/acct/a", "/acct/b": "/acct/b"}
        for store in stores:
            assert store.env["BASE"] == "1"


class _UnbindableHarness:
    """A harness camp CAN name (`harness_for` resolves it), whose declared
    account it refuses to bind — a store that was a real candidate a moment
    ago, distinct from a group whose harness camp never heard of."""

    name = "unbindable"

    def session_launch_env_set(self, account, *, env=None):
        raise ValueError("account is not absolute")

    def session_launch_env_unset(self):
        return []


class TestAddressableHarnessesDroppedStoreIsNeverSilent:
    """A group whose harness resolves but whose store cannot be bound is a
    dropped candidate, not a silently-absent one — the caller states which
    group and why, or refuses outright via `on_drop`."""

    def test_a_dropped_store_prints_a_notice_naming_the_group_and_reason(
        self, monkeypatch, capsys
    ):
        import camp.cli.session as cli_session
        import camp.launch.profile as profile

        monkeypatch.setattr(profile, "harness_for", lambda group: _UnbindableHarness())
        groups = [{"group": {"name": "flaky"}, "launch": {"account": "rel/path"}}]

        stores = cli_session._addressable_harnesses(groups, env={})

        assert stores == []
        err = capsys.readouterr().err
        assert "flaky" in err
        assert "not absolute" in err

    def test_on_drop_replaces_the_default_notice(self, monkeypatch, capsys):
        import camp.cli.session as cli_session
        import camp.launch.profile as profile

        monkeypatch.setattr(profile, "harness_for", lambda group: _UnbindableHarness())
        groups = [{"group": {"name": "flaky"}, "launch": {"account": "rel/path"}}]

        seen = []

        def on_drop(config, error):
            seen.append((config["group"]["name"], str(error)))

        stores = cli_session._addressable_harnesses(groups, env={}, on_drop=on_drop)

        assert stores == []
        assert seen == [("flaky", "account is not absolute")]
        assert capsys.readouterr().err == ""
