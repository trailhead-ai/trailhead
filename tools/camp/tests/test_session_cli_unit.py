"""Unit-level tests for camp.cli.session — collaborators mocked directly.

Complements test_session_cli.py's end-to-end subprocess coverage with two
seams that are awkward to exercise through the real CLI binary:

- `wait_for_provisioning` degrading a corrupt/missing manifest (ManifestError)
  into the same one-line refusal shape as any other provisioning failure,
  rather than letting the exception escape as a raw traceback.
- `camp sessions <slug>` scoping enumeration by the same resolved workspace
  directory the launch engine spawns into, so a symlinked workspace root
  doesn't make a just-launched session invisible to a slug-scoped query.
- `_session_pool`'s two postures on a live probe that fails: the stop path needs
  the answer (an unanswerable probe is a refusal), and the resume path does not
  (a narrowed pool costs a candidate and nothing more).
- `camp launch --json` emitting the launch engine's own `tmux_name` verbatim.
  A sentinel name the derivation could never produce is the only way to tell
  reading it apart from rebuilding `camp-<slug>-<uuid8>` at the print site,
  which the end-to-end suite cannot inject.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

GROUP = {"group": {"name": "testgroup"}}


class TestWaitForProvisioningManifestError:
    def test_a_manifest_error_degrades_to_a_camp_launch_refusal_line(self, monkeypatch, capsys):
        import camp.cli.session as cli_session
        from camp.group.manifest import ManifestError

        def boom(*a, **k):
            raise ManifestError("manifest for 'feat-x' is corrupt: not valid JSON")

        monkeypatch.setattr(
            "camp.provision.lifecycle.wait_for_provisioning_ready", boom
        )

        result = cli_session.wait_for_provisioning(GROUP, "feat-x", env={})

        assert result is False
        err = capsys.readouterr().err
        assert "camp launch:" in err
        assert "not valid JSON" in err

    def test_a_manifest_error_does_not_propagate_as_a_traceback(self, monkeypatch):
        import camp.cli.session as cli_session
        from camp.group.manifest import ManifestError

        def boom(*a, **k):
            raise ManifestError("no manifest found")

        monkeypatch.setattr(
            "camp.provision.lifecycle.wait_for_provisioning_ready", boom
        )

        # No exception should escape — the function returns a plain bool.
        assert cli_session.wait_for_provisioning(GROUP, "feat-x", env={}) is False


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
            "camp.cli.dispatch._slug_from_args_or_cwd",
            lambda *a, **k: "feat-x",
        )

        seen: dict[str, Path | None] = {}

        def fake_enumerate(scope, *, env):
            seen["workspace"] = scope
            return [], [], 1

        monkeypatch.setattr(cli_session, "_enumerate_live_sessions_pool", fake_enumerate)

        cli_session._cmd_sessions_group_cli(["feat-x"], GROUP, {})

        assert seen["workspace"] == link.resolve()
        assert seen["workspace"] != link


class TestLaunchJsonCarriesTheEngineReportedTmuxName:
    """`camp launch --json` must print the name the launch engine reported.

    Rebuilding `camp-<slug>-<uuid8>` at the print site reproduces the engine's
    own string, so only a name the derivation could never produce distinguishes
    the two.
    """

    def test_tmux_name_in_json_output_is_the_engine_reported_value_verbatim(
        self, monkeypatch, capsys
    ):
        import camp.cli.session as cli_session
        from camp.launch.session import LaunchedSession

        monkeypatch.setattr(
            "camp.cli.dispatch._slug_from_args_or_cwd", lambda *a, **k: "feat-x"
        )
        monkeypatch.setattr(
            cli_session,
            "launch_and_confirm",
            lambda *a, **k: LaunchedSession(
                session_id="11111111-2222-3333-4444-555555555555",
                tmux_name="not-a-derived-name-at-all",
                launch_dir=Path("/tmp/wherever"),
            ),
        )

        cli_session._cmd_launch_group_cli(["feat-x", "--json"], GROUP, {})

        payload = json.loads(capsys.readouterr().out)
        assert payload["tmux_name"] == "not-a-derived-name-at-all", (
            "tmux_name must be the launch engine's reported value, not a "
            "re-derivation of camp-<slug>-<uuid8> at the print site"
        )
        assert payload["session_id"] == "11111111-2222-3333-4444-555555555555"


class TestConfirmationReadsThePaneEnvironment:
    """The confirmation reports which config file the launched session reads.

    Handing it the CLI's own ambient environment makes that report answer from
    the shell camp was invoked in — which is precisely what the launch scrubbed
    off the pane. The pane's recorded environment is the only one that can name
    the file the session actually opens.
    """

    def test_confirm_session_is_handed_the_pane_environment_not_the_ambient_one(
        self, monkeypatch, capsys
    ):
        import camp.cli.session as cli_session
        from camp.launch.session import LaunchedSession

        pane_env = {"PATH": "/usr/bin", "HOME": "/home/pane"}
        launched = LaunchedSession(
            session_id="sess-1",
            tmux_name="camp-feat-x-abcd1234",
            launch_dir=Path("/tmp/ws"),
            pane_env=pane_env,
        )
        seen: list[dict] = []
        monkeypatch.setattr("camp.launch.profile.harness_for", lambda group: object())
        monkeypatch.setattr(
            "camp.launch.session.launch_session", lambda *a, **k: launched
        )
        monkeypatch.setattr(
            "camp.launch.session.confirm_session",
            lambda harness, _launched, env=None: seen.append(env),
        )

        cli_session.launch_and_confirm(
            GROUP, "feat-x", env={"PATH": "/usr/bin", "HOME": "/home/ambient"}
        )

        assert seen == [pane_env]


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

    def test_the_resume_path_degrades_to_a_narrower_pool(self, monkeypatch):
        transcripts, live, answered, accounts = self._pool(monkeypatch, verb="launch")
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

        assert len(stores) == 2
        assert {s.account for s in stores} == {"/acct/a", "/acct/b"}

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

        assert len(stores) == 1
        assert stores[0].account == "/acct/a"

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

    def test_a_group_whose_harness_camp_cannot_name_contributes_nothing(self, monkeypatch):
        import camp.cli.session as cli_session
        import camp.launch.profile as profile

        def fake_harness_for(group):
            if group["group"]["name"] == "bad":
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

        by_account = {s.account: s.env.get("FAKE_STORE_DIR") for s in stores}
        assert by_account == {"/acct/a": "/acct/a", "/acct/b": "/acct/b"}
        for store in stores:
            assert store.env["BASE"] == "1"


class TestLiveSessionPoolQueriesEachStoreExactlyOnce:
    """`_enumerate_live_sessions_pool` walks the (harness, store) pool once —
    the pool itself is already deduplicated, and this proves the WALK adds no
    second visit of its own. The counted quantity — how many times each store
    was actually asked — is owned by `fake_enumerate_records`'s call log
    below, which the test reads back; nothing here is a derived guess.
    """

    def test_each_store_is_enumerated_exactly_once(self, monkeypatch):
        import camp.cli.session as cli_session
        import camp.group.config as group_config
        import camp.launch.profile as profile
        import camp.launch.session as launch_session

        monkeypatch.setattr(profile, "harness_for", lambda group: _AccountAwareHarness())
        groups = [
            {"group": {"name": "g1"}, "launch": {"account": "/acct/a"}},
            {"group": {"name": "g2"}, "launch": {"account": "/acct/b"}},
            {"group": {"name": "g3"}},
        ]
        monkeypatch.setattr(group_config, "load_all_groups", lambda *a, **k: groups)

        calls: list = []

        def fake_enumerate_records(store, scope, env):
            calls.append(store.env.get("FAKE_STORE_DIR"))
            return []

        monkeypatch.setattr(launch_session, "enumerate_records", fake_enumerate_records)

        records, failures, total = cli_session._enumerate_live_sessions_pool(None, env={})

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
        import camp.group.config as group_config
        import camp.launch.profile as profile
        import camp.launch.session as launch_session
        from datetime import datetime, timezone
        from trailhead.harness.base import SessionRecord

        monkeypatch.setattr(profile, "harness_for", lambda group: _AccountAwareHarness())
        groups = [
            {"group": {"name": "g1"}, "launch": {"account": "/acct/hangs"}},
            {"group": {"name": "g2"}, "launch": {"account": "/acct/answers"}},
        ]
        monkeypatch.setattr(group_config, "load_all_groups", lambda *a, **k: groups)

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
            if store.env.get("FAKE_STORE_DIR") == "/acct/hangs":
                raise subprocess.TimeoutExpired(cmd=["fake"], timeout=10)
            return answer

        monkeypatch.setattr(launch_session, "enumerate_records", fake_enumerate_records)

        records, failures, total = cli_session._enumerate_live_sessions_pool(None, env={})

        assert total == 2
        assert records == answer
        assert len(failures) == 1
        assert failures[0]["account"] == "/acct/hangs"
