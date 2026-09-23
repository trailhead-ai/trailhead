"""Unit-level tests for camp.cli.session — collaborators mocked directly.

Complements test_session_cli.py's end-to-end subprocess coverage with a seam
that is awkward to exercise through the real CLI binary:

- `camp sessions <slug>` scoping enumeration by the same resolved workspace
  directory a session is rooted in, so a symlinked workspace root doesn't
  make a live session invisible to a slug-scoped query.
- `_session_pool`'s two postures on a live probe that fails: the stop path
  needs the answer (an unanswerable probe is a refusal), and its
  `live_required=False` default does not (a narrowed pool costs a candidate
  and nothing more).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

GROUP = {"group": {"name": "testgroup"}}


class TestSessionsSlugScopingResolvesTheWorkspace:
    def test_slug_scoped_enumeration_uses_the_resolved_workspace_dir(
        self, monkeypatch, tmp_path
    ):
        import camp.cli.session as cli_session

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real_dir)

        monkeypatch.setattr(
            "camp.group.manifest.workspace_dir", lambda group, slug, env=None: link
        )
        monkeypatch.setattr(
            "camp.cli.dispatch._slug_from_name_or_cwd",
            lambda *a, **k: "feat-x",
        )

        seen: dict[str, Path | None] = {}

        def fake_enumerate(scope, *, env, groups=None):
            # Mirrors the production signature rather than absorbing unknown
            # keywords: a double that swallowed **kwargs would keep passing
            # while drifting out of step with the function it stands in for.
            seen["workspace"] = scope
            return [], [], 1

        monkeypatch.setattr(cli_session, "_enumerate_live_sessions_pool", fake_enumerate)

        cli_session._cmd_sessions_group_cli(["feat-x"], GROUP, {})

        assert seen["workspace"] == link.resolve()
        assert seen["workspace"] != link


class TestSessionPoolLiveProbePosture:
    """A live probe that failed says NOTHING. Which branch that lands on is the
    caller's posture, and the two callers differ."""

    @staticmethod
    def _harness():
        class _Harness:
            name = "probefail"

            def session_transcripts(self, workspace=None, *, env=None):
                return []

        return _Harness()

    def _pool(self, monkeypatch, **kwargs):
        import camp.cli.session as cli_session
        import camp.launch.session as launch_session

        harness = self._harness()
        monkeypatch.setattr(
            cli_session, "_addressable_harnesses", lambda groups, **k: [harness]
        )
        # The enumeration could not be answered — the seam's documented `None`.
        monkeypatch.setattr(launch_session, "enumerate_records", lambda *a, **k: None)
        return cli_session._session_pool([], env={}, **kwargs)

    def test_the_default_posture_degrades_to_a_narrower_pool(self, monkeypatch):
        transcripts, live, answered, accounts = self._pool(monkeypatch, verb="sessions")
        assert live == []
        assert len(answered) == 1

    def test_the_stop_path_refuses_because_it_needs_the_answer(self, monkeypatch, capsys):
        import pytest

        with pytest.raises(SystemExit) as exit_info:
            self._pool(monkeypatch, verb="kill", live_required=True)

        assert exit_info.value.code != 0
        message = capsys.readouterr().err.strip()
        assert message.startswith("camp kill: ")
        assert "live" in message


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


class TestLiveSessionPoolQueriesEachStoreExactlyOnce:
    """`_enumerate_live_sessions_pool` walks the (harness, store) pool once —
    the pool itself is already deduplicated, and this proves the WALK adds no
    second visit of its own. The counted quantity — how many times each store
    was actually asked — is owned by `fake_enumerate_records`'s call log
    below, which the test reads back; nothing here is a derived guess.
    """

    def test_each_store_is_enumerated_exactly_once(self, monkeypatch):
        import camp.cli.session as cli_session
        import camp.launch.profile as profile
        import camp.launch.session as launch_session

        monkeypatch.setattr(profile, "harness_for", lambda group: _AccountAwareHarness())
        groups = [
            {"group": {"name": "g1"}, "launch": {"account": "/acct/a"}},
            {"group": {"name": "g2"}, "launch": {"account": "/acct/b"}},
            {"group": {"name": "g3"}},
        ]

        calls: list = []

        def fake_enumerate_records(store, scope, env):
            calls.append(store.env.get("FAKE_STORE_DIR"))
            return []

        monkeypatch.setattr(launch_session, "enumerate_records", fake_enumerate_records)

        records, failures, total = cli_session._enumerate_live_sessions_pool(
            None, env={}, groups=groups
        )

        assert total == 3
        assert len(calls) == 3
        assert calls.count("/acct/a") == 1
        assert calls.count("/acct/b") == 1
        assert calls.count(None) == 1


class TestLiveSessionPoolBoundsAHangingStore:
    """A store whose enumeration hits the existing per-call timeout must
    degrade exactly like a failing store, not block the pool's answer.
    """

    def test_a_store_that_times_out_degrades_like_a_failing_one(self, monkeypatch):
        import subprocess

        import camp.cli.session as cli_session
        import camp.launch.profile as profile
        import camp.launch.session as launch_session
        from datetime import datetime, timezone
        from trailhead.harness.base import SessionRecord

        monkeypatch.setattr(profile, "harness_for", lambda group: _AccountAwareHarness())
        groups = [
            {"group": {"name": "g1"}, "launch": {"account": "/acct/hangs"}},
            {"group": {"name": "g2"}, "launch": {"account": "/acct/answers"}},
        ]

        answer = [
            SessionRecord(
                session_id="s1",
                cwd=Path("/x"),
                kind="agent",
                controllable=True,
                name=None,
                pid=None,
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        ]

        def fake_enumerate_records(store, scope, env):
            account = store.env.get("FAKE_STORE_DIR")
            if account == "/acct/hangs":
                raise subprocess.TimeoutExpired(cmd=["fake"], timeout=10)
            if account == "/acct/answers":
                return answer
            return []  # the default store, always present alongside the two declared ones

        monkeypatch.setattr(launch_session, "enumerate_records", fake_enumerate_records)

        records, failures, total = cli_session._enumerate_live_sessions_pool(
            None, env={}, groups=groups
        )

        assert total == 3
        assert records == answer
        assert len(failures) == 1
        assert failures[0]["account"] == "/acct/hangs"


# ---------------------------------------------------------------------------
# `--name <slug>` and a bare positional are one workspace, resolved one way.
# ---------------------------------------------------------------------------


def test_a_widened_refusal_quotes_the_same_spelling_the_resolver_would_pick(capsys) -> None:
    """Both spellings given at once: the refusal must quote whichever one would
    actually have been acted on.

    `dispatch._slug_from_name_or_cwd` resolves `--name` ahead of a positional,
    so a refusal that quoted the positional would name a value the resolver
    would have discarded — telling the operator their invocation failed over a
    workspace it was never going to use.
    """
    import pytest
    from camp.cli.session import local_sessions_parser, refuse_sessions_local_only_options

    parsed = local_sessions_parser().parse_args(["--name", "from-flag", "from-positional"])
    with pytest.raises(SystemExit):
        refuse_sessions_local_only_options(parsed, widening_flag="--all-hosts")

    err = capsys.readouterr().err
    assert "'from-flag'" in err
    assert "from-positional" not in err


def test_either_spelling_alone_is_still_refused(capsys) -> None:
    """The precedence above must not be achieved by ignoring one spelling: each
    on its own still reaches the refusal, quoting itself."""
    import pytest
    from camp.cli.session import local_sessions_parser, refuse_sessions_local_only_options

    for argv, quoted in ((["--name", "only-flag"], "only-flag"), (["only-positional"], "only-positional")):
        parsed = local_sessions_parser().parse_args(argv)
        with pytest.raises(SystemExit):
            refuse_sessions_local_only_options(parsed, widening_flag="--all-hosts")
        assert f"'{quoted}'" in capsys.readouterr().err
