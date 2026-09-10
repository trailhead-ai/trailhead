"""Behavioural tests for `_sessions_live_answer` — the value-returning
extraction of `camp sessions`' live-listing enumeration.

`_cmd_sessions_group_cli` used to enumerate and print in one function. This
pins the split: `_sessions_live_answer` returns `(rows, notices, exit_code)`
— the same JSON-shaped rows, the same stderr notices, and the same exit code
the printed form produces — never printing and never calling `sys.exit`
itself. Every test here calls BOTH the value function and the real CLI
entry point (`_cmd_sessions_group_cli`, through capsys) with the SAME
collaborators mocked once, then diffs the two answers — proving the
extraction, not just the function's own internal consistency.

Task: task/extract-a-value-returning-local-answer-for-camp-sessions.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _record(session_id, *, cwd="/nowhere/probe", kind="agent", name=None):
    from trailhead.harness.base import SessionRecord

    return SessionRecord(
        session_id=session_id,
        cwd=Path(cwd),
        kind=kind,
        controllable=True,
        name=name,
        pid=None,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class _SingleHarness:
    """Resolves for both the default probe and any declared account, so a
    fake `enumerate_records` can branch on `store.account`/`store.env` alone."""

    name = "singleharness"

    def session_launch_env_unset(self):
        return []

    def session_launch_env_set(self, account, *, env=None):
        return {} if account is None else {"FAKE_STORE_DIR": account}


def _install_single_harness(monkeypatch):
    import camp.launch.profile as profile

    monkeypatch.setattr(profile, "harness_for", lambda group: _SingleHarness())


def _call_cli_json(monkeypatch, capsys, args, group, env, *, all_groups=False):
    """Drive the real `_cmd_sessions_group_cli` entry point and read back
    what it actually printed/exited with — the printed-form half of the
    parity check every test here performs."""
    import camp.cli.session as cli_session

    exit_code = 0
    try:
        cli_session._cmd_sessions_group_cli(
            list(args) + ["--json"], group, env, all_groups=all_groups
        )
    except SystemExit as e:
        exit_code = e.code or 0
    out = capsys.readouterr()
    stdout_rows = json.loads(out.out) if out.out.strip() else None
    notices = [line for line in out.err.splitlines() if line]
    return stdout_rows, notices, exit_code


class TestRowsMatchThePrintedJsonForm:
    """`rows` is exactly the JSON array `--json` prints today, as data."""

    @pytest.mark.parametrize("count", [0, 1, 3])
    def test_zero_one_many_sessions(self, monkeypatch, capsys, tmp_path, count):
        import camp.cli.session as cli_session
        import camp.group.config as group_config
        import camp.launch.session as launch_session

        _install_single_harness(monkeypatch)
        records = [_record(f"sess-{i}") for i in range(count)]
        monkeypatch.setattr(launch_session, "enumerate_records", lambda *a, **k: records)

        group = {"group": {"name": "g1"}, "launch": {}, "members": []}
        monkeypatch.setattr(group_config, "load_all_groups", lambda groups_dir: [group])

        env = {"HOME": str(tmp_path)}
        scope_dir = tmp_path / "scope"
        scope_dir.mkdir()
        args = ["--dir", str(scope_dir)]

        rows, notices, exit_code = cli_session._sessions_live_answer(
            scope_dir.resolve(),
            env=env,
            all_groups=False,
            group=group,
            described=f"directory {str(scope_dir.resolve())!r}",
        )
        printed_rows, printed_notices, printed_exit = _call_cli_json(
            monkeypatch, capsys, args, group, env
        )

        assert exit_code == 0 and printed_exit == 0
        assert len(rows) == count
        assert rows == printed_rows
        assert notices == printed_notices == []

    def test_a_credential_store_that_could_not_be_read_keeps_its_ok_false_row(
        self, monkeypatch, capsys, tmp_path
    ):
        import camp.cli.session as cli_session
        import camp.group.config as group_config
        import camp.launch.profile as profile
        import camp.launch.session as launch_session

        class _AcctHarness:
            name = "acctharness"

            def session_launch_env_unset(self):
                return []

            def session_launch_env_set(self, account, *, env=None):
                return {} if account is None else {"FAKE_STORE_DIR": account}

        monkeypatch.setattr(profile, "harness_for", lambda group: _AcctHarness())

        group_a = {"group": {"name": "g1"}, "launch": {"account": "/acct/a"}, "members": []}
        group_b = {"group": {"name": "g2"}, "launch": {"account": "/acct/b"}, "members": []}
        monkeypatch.setattr(
            group_config, "load_all_groups", lambda groups_dir: [group_a, group_b]
        )

        record_b = _record("sess-b")

        def fake_enumerate_records(store, scope, env):
            account = store.env.get("FAKE_STORE_DIR")
            if account == "/acct/a":
                raise RuntimeError("boom")
            if account == "/acct/b":
                return [record_b]
            return []

        monkeypatch.setattr(launch_session, "enumerate_records", fake_enumerate_records)

        env = {"HOME": str(tmp_path)}
        scope_dir = tmp_path / "scope"
        scope_dir.mkdir()
        args = ["--dir", str(scope_dir)]

        rows, notices, exit_code = cli_session._sessions_live_answer(
            scope_dir.resolve(),
            env=env,
            all_groups=False,
            group=group_a,
            described=f"directory {str(scope_dir.resolve())!r}",
        )
        printed_rows, printed_notices, printed_exit = _call_cli_json(
            monkeypatch, capsys, args, group_a, env
        )

        assert exit_code == 0 and printed_exit == 0
        assert rows == printed_rows
        failure_rows = [r for r in rows if r.get("ok") is False]
        assert len(failure_rows) == 1
        assert failure_rows[0] == {
            "ok": False,
            "account": "/acct/a",
            "reason": "sessions could not be enumerated for this credential store",
        }
        assert notices == printed_notices
        assert any(
            "could not enumerate sessions for" in n and "/acct/a" in n for n in notices
        )

    def test_all_groups_merges_two_groups_sessions(self, monkeypatch, capsys, tmp_path):
        import camp.cli.session as cli_session
        import camp.launch.profile as profile
        import camp.launch.session as launch_session
        import camp.provision.lifecycle as lifecycle

        class _AcctHarness:
            name = "acctharness"

            def session_launch_env_unset(self):
                return []

            def session_launch_env_set(self, account, *, env=None):
                return {} if account is None else {"FAKE_STORE_DIR": account}

        monkeypatch.setattr(profile, "harness_for", lambda group: _AcctHarness())

        group_a = {"group": {"name": "g1"}, "launch": {"account": "/acct/a"}, "members": []}
        group_b = {"group": {"name": "g2"}, "launch": {"account": "/acct/b"}, "members": []}
        monkeypatch.setattr(
            lifecycle,
            "answerable_groups_or_refuse",
            lambda groups_dir, *, verb: ([group_a, group_b], []),
        )

        rec_a = _record("sess-a")
        rec_b = _record("sess-b")

        def fake_enumerate_records(store, scope, env):
            account = store.env.get("FAKE_STORE_DIR")
            if account == "/acct/a":
                return [rec_a]
            if account == "/acct/b":
                return [rec_b]
            return []

        monkeypatch.setattr(launch_session, "enumerate_records", fake_enumerate_records)

        env = {"HOME": str(tmp_path)}

        rows, notices, exit_code = cli_session._sessions_live_answer(
            None, env=env, all_groups=True, group=None, described="every configured group"
        )
        printed_rows, printed_notices, printed_exit = _call_cli_json(
            monkeypatch, capsys, [], None, env, all_groups=True
        )

        assert exit_code == 0 and printed_exit == 0
        assert rows == printed_rows
        ids = {r.get("session_id") for r in rows if r.get("ok")}
        assert ids == {"sess-a", "sess-b"}
        assert notices == printed_notices == []


class TestExitCodeMatchesThePrintedRefusal:
    def test_every_store_failing_returns_exit_code_one(self, monkeypatch, capsys, tmp_path):
        import camp.cli.session as cli_session
        import camp.group.config as group_config
        import camp.launch.profile as profile
        import camp.launch.session as launch_session

        class _AlwaysFailHarness:
            name = "failharness"

            def session_launch_env_unset(self):
                return []

            def session_launch_env_set(self, account, *, env=None):
                return {} if account is None else {"FAKE_STORE_DIR": account}

        monkeypatch.setattr(profile, "harness_for", lambda group: _AlwaysFailHarness())
        group_a = {"group": {"name": "g1"}, "launch": {"account": "/acct/a"}, "members": []}
        monkeypatch.setattr(group_config, "load_all_groups", lambda groups_dir: [group_a])

        def always_fail(store, scope, env):
            raise RuntimeError("boom")

        monkeypatch.setattr(launch_session, "enumerate_records", always_fail)

        env = {"HOME": str(tmp_path)}

        rows, notices, exit_code = cli_session._sessions_live_answer(
            None, env=env, all_groups=False, group=group_a, described="group 'g1'"
        )

        assert exit_code == 1
        assert rows == []
        assert any("every credential store failed" in n for n in notices)

        with pytest.raises(SystemExit) as exc:
            cli_session._cmd_sessions_group_cli(
                ["--json"], group_a, env, all_groups=False
            )
        assert exc.value.code == exit_code
        err = capsys.readouterr().err
        assert "every credential store failed" in err


class TestAddressableHarnessesNoticeReachesReturnedNotices:
    """The added contract item: `_addressable_harnesses`' own per-group
    credential-store-binding notice was never covered through the real CLI
    surface, only as a unit test on that helper. Both halves of the split
    must carry it."""

    def test_store_binding_notice_reaches_notices_via_all_groups(
        self, monkeypatch, capsys, tmp_path
    ):
        import camp.cli.session as cli_session
        import camp.launch.profile as profile
        import camp.launch.session as launch_session
        import camp.provision.lifecycle as lifecycle

        flaky_group = {
            "group": {"name": "flaky"},
            "launch": {"account": "rel/path"},
            "members": [],
        }

        class _DefaultOnlyHarness:
            name = "defaultharness"

            def session_launch_env_unset(self):
                return []

            def session_launch_env_set(self, account, *, env=None):
                return {} if account is None else {"FAKE_STORE_DIR": account}

        class _UnbindableHarness:
            name = "unbindable"

            def session_launch_env_set(self, account, *, env=None):
                raise ValueError("account is not absolute")

            def session_launch_env_unset(self):
                return []

        def fake_harness_for(group):
            name = (group or {}).get("group", {}).get("name")
            if name == "flaky":
                return _UnbindableHarness()
            return _DefaultOnlyHarness()

        monkeypatch.setattr(profile, "harness_for", fake_harness_for)
        monkeypatch.setattr(
            lifecycle,
            "answerable_groups_or_refuse",
            lambda groups_dir, *, verb: ([flaky_group], []),
        )

        live_row = _record("probe-sess-1")

        def fake_enumerate_records(store, scope, env):
            return [live_row] if store.account is None else []

        monkeypatch.setattr(launch_session, "enumerate_records", fake_enumerate_records)

        env = {"HOME": str(tmp_path)}

        # Through the extracted value function.
        rows, notices, exit_code = cli_session._sessions_live_answer(
            None, env=env, all_groups=True, group=None, described="every configured group"
        )
        assert exit_code == 0
        assert any(
            "could not address group" in n
            and "flaky" in n
            and "account is not absolute" in n
            for n in notices
        )
        assert any(r.get("session_id") == "probe-sess-1" for r in rows)

        # And through the real CLI dispatch — the renderer half of the split.
        cli_session._cmd_sessions_group_cli([], group=None, env=env, all_groups=True)
        err = capsys.readouterr().err
        assert "could not address group" in err and "flaky" in err


class TestNoticeOrderIsPreserved:
    def test_a_drop_notice_precedes_a_later_store_failure_notice(
        self, monkeypatch, capsys, tmp_path
    ):
        """A store dropped while the pool is being built (`_addressable_harnesses`)
        notices BEFORE a store that entered the pool but failed to answer
        (`_enumerate_live_sessions_pool`'s own per-failure notice) — both must
        land in `notices` in that same relative order."""
        import camp.cli.session as cli_session
        import camp.launch.profile as profile
        import camp.launch.session as launch_session
        import camp.provision.lifecycle as lifecycle

        flaky_group = {
            "group": {"name": "flaky"},
            "launch": {"account": "rel/path"},
            "members": [],
        }
        failing_group = {
            "group": {"name": "failing"},
            "launch": {"account": "/acct/failing"},
            "members": [],
        }

        class _AcctHarness:
            name = "acctharness"

            def session_launch_env_unset(self):
                return []

            def session_launch_env_set(self, account, *, env=None):
                return {} if account is None else {"FAKE_STORE_DIR": account}

        class _UnbindableHarness:
            name = "unbindable"

            def session_launch_env_set(self, account, *, env=None):
                raise ValueError("account is not absolute")

            def session_launch_env_unset(self):
                return []

        def fake_harness_for(group):
            name = (group or {}).get("group", {}).get("name")
            if name == "flaky":
                return _UnbindableHarness()
            return _AcctHarness()

        monkeypatch.setattr(profile, "harness_for", fake_harness_for)
        monkeypatch.setattr(
            lifecycle,
            "answerable_groups_or_refuse",
            lambda groups_dir, *, verb: ([flaky_group, failing_group], []),
        )

        def fake_enumerate_records(store, scope, env):
            account = store.env.get("FAKE_STORE_DIR")
            if account == "/acct/failing":
                raise RuntimeError("boom")
            return []

        monkeypatch.setattr(launch_session, "enumerate_records", fake_enumerate_records)

        env = {"HOME": str(tmp_path)}

        rows, notices, exit_code = cli_session._sessions_live_answer(
            None, env=env, all_groups=True, group=None, described="every configured group"
        )
        printed_rows, printed_notices, printed_exit = _call_cli_json(
            monkeypatch, capsys, [], None, env, all_groups=True
        )

        assert exit_code == 0 and printed_exit == 0
        assert notices == printed_notices
        assert len(notices) == 2
        assert "could not address group" in notices[0] and "flaky" in notices[0]
        assert "could not enumerate sessions for" in notices[1] and "failing" in notices[1]
