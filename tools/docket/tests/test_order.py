"""The order commands, set-order and vault-order, through `docket.cli.main`.

Same harness as the change commands: lore and git behind a scripted runner, the vault, its lists and the
lock real files under tmp_path.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os

import pytest

from _docket_cli import PLUGIN_ROOT  # noqa: F401  (puts the plugin on sys.path)
from test_changes import DEFAULT, env, hermetic_git_env, ref  # noqa: F401  (fixtures and helpers)

from docket.store import LockTimeoutError, Store, resolve_order

OTHER = "other"
A, B, C = "task/a", "task/b", "task/c"
X, Y = "task/x", "task/y"


def order_argv(*groups):
    """groups: (vault, was, now) with was and now lists of kind/slug within that vault."""
    argv = ["set-order", "--"]
    for vault, was, now in groups:
        argv += [vault, "was", *[ref(s, vault) for s in was], "now", *[ref(s, vault) for s in now]]
    return argv


def two_vaults(env):
    env.put(DEFAULT, A, B, C)
    env.put(OTHER, X, Y)


def order_file(env):
    return env.vault_dir / "sites" / "docket" / "vault-order.json"


def stored_order(env):
    path = order_file(env)
    return json.loads(path.read_text())["order"] if path.exists() else None


class TestSetOrder:
    def test_a_matching_was_with_a_valid_now_rewrites_both_vaults(self, env):
        two_vaults(env)
        code, doc, _ = env.run(*order_argv((DEFAULT, [A, B, C], [C, A, B]), (OTHER, [X, Y], [Y, X])))
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries(DEFAULT) == [C, A, B]
        assert env.entries(OTHER) == [Y, X]
        assert doc["entry_count"] == {DEFAULT: 3, OTHER: 2}
        assert env.fake.tags().count("sync") == 2
        assert {e["ref"] for e in doc["entries"]} == {ref(s) for s in (A, B, C)} | {ref(s, OTHER) for s in (X, Y)}

    def test_each_entry_reports_whether_it_moved(self, env):
        env.put(DEFAULT, A, B, C)
        _, doc, _ = env.run(*order_argv((DEFAULT, [A, B, C], [A, C, B])))
        assert {e["ref"]: e["status"] for e in doc["entries"]} == {
            ref(A): "in-place", ref(C): "moved", ref(B): "moved"}

    def test_one_vault_that_another_session_reordered_refuses_the_whole_change(self, env):
        two_vaults(env)
        before = {v: env.raw(v) for v in (DEFAULT, OTHER)}
        code, doc, _ = env.run(*order_argv((DEFAULT, [A, C, B], [B, A, C]), (OTHER, [X, Y], [Y, X])))
        assert (code, doc["outcome"]) == (2, "refused")
        assert doc["stale"] == [DEFAULT]
        assert DEFAULT in doc["message"]
        assert {v: env.raw(v) for v in (DEFAULT, OTHER)} == before
        assert env.fake.tags().count("sync") == 1

    def test_every_moved_vault_is_named(self, env):
        two_vaults(env)
        _, doc, _ = env.run(*order_argv((DEFAULT, [B, A, C], [A, B, C]), (OTHER, [Y, X], [X, Y])))
        assert doc["stale"] == [DEFAULT, OTHER]

    def test_a_list_that_gained_an_entry_since_was_is_stale(self, env):
        env.put(DEFAULT, A, B, C)
        before = env.raw()
        code, doc, _ = env.run(*order_argv((DEFAULT, [A, B], [B, A])))
        assert (code, doc["outcome"], doc["stale"]) == (2, "refused", [DEFAULT])
        assert env.raw() == before

    @pytest.mark.parametrize("now", [
        [A, B],
        [A, B, B],
        [A, B, C, "task/outsider"],
        [A, B, "task/outsider"],
    ])
    def test_a_now_that_does_not_name_each_current_entry_once_is_refused(self, env, now):
        two_vaults(env)
        before = {v: env.raw(v) for v in (DEFAULT, OTHER)}
        code, doc, _ = env.run(*order_argv((DEFAULT, [A, B, C], now), (OTHER, [X, Y], [Y, X])))
        assert (code, doc["outcome"]) == (2, "refused")
        assert "stale" not in doc
        assert DEFAULT in doc["message"]
        assert {v: env.raw(v) for v in (DEFAULT, OTHER)} == before

    def test_a_now_that_repeats_an_entry_of_the_same_set_size_is_refused(self, env):
        env.put(DEFAULT, A, B)
        before = env.raw()
        code, doc, _ = env.run(*order_argv((DEFAULT, [A, B], [A, A])))
        assert (code, doc["outcome"]) == (2, "refused")
        assert "more than once" in doc["message"]
        assert env.raw() == before

    def test_a_now_equal_to_the_current_order_changes_nothing(self, env):
        two_vaults(env)
        before = {v: env.raw(v) for v in (DEFAULT, OTHER)}
        code, doc, _ = env.run(*order_argv((DEFAULT, [A, B, C], [A, B, C]), (OTHER, [X, Y], [X, Y])))
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert {v: env.raw(v) for v in (DEFAULT, OTHER)} == before
        assert env.fake.tags().count("sync") == 1

    def test_a_reorder_keeps_each_entrys_workspace(self, env):
        env.put(DEFAULT, A, (B, "trailhead", "docket"))
        code, _, _ = env.run(*order_argv((DEFAULT, [A, B], [B, A])))
        assert code == 0
        assert env.entries() == [B + "@trailhead/docket", A]

    @pytest.mark.parametrize("argv", [
        ["set-order", "--", DEFAULT, "was", ref(A), "now"],
        ["set-order", "--", DEFAULT, "now", ref(A), "was", ref(A)],
        ["set-order", "--", DEFAULT, ref(A), ref(B)],
        ["set-order", "--", DEFAULT, "was", ref(A), "now", ref(A, OTHER)],
        ["set-order", "--", "nowhere", "was", ref(A, "nowhere"), "now", ref(A, "nowhere")],
        ["set-order", "--", DEFAULT, "was", ref(A), "now", ref(A), DEFAULT, "was", ref(A), "now", ref(A)],
    ])
    def test_a_malformed_request_is_refused_before_anything_is_read(self, env, argv):
        env.put(DEFAULT, A, B)
        before = env.raw()
        code, doc, _ = env.run(*argv)
        assert (code, doc["outcome"]) == (2, "refused")
        assert env.raw() == before
        assert env.fake.tags().count("sync") == 0

    def test_a_reorder_that_a_remote_copy_replaced_is_lost_to_remote(self, env):
        two_vaults(env)
        original = env.raw(OTHER).decode()
        env.fake.sync_hooks = {1: lambda fake: env.list_path(OTHER).write_text(original)}
        code, doc, _ = env.run(*order_argv((DEFAULT, [A, B, C], [C, B, A]), (OTHER, [X, Y], [Y, X])))
        assert (code, doc["outcome"]) == (4, "lost-to-remote")
        assert doc["lists"][OTHER] == [{"kind": "task", "slug": "x"}, {"kind": "task", "slug": "y"}]

    def test_a_reorder_whose_sync_failed_is_saved_not_synced(self, env):
        two_vaults(env)
        env.fake.sync_plan = {1: "holding"}
        code, doc, _ = env.run(*order_argv((DEFAULT, [A, B, C], [C, B, A]), (OTHER, [X, Y], [Y, X])))
        assert (code, doc["outcome"]) == (3, "saved-not-synced")
        assert env.entries(OTHER) == [Y, X]


def _fail_nth_replace(n):
    calls = []

    def replace(*args, **kwargs):
        calls.append(args)
        if len(calls) == n:
            raise OSError(5, "disk went away")
        os.replace(*args, **kwargs)

    return replace


class TestInterruptedWrite:
    def test_a_failure_between_the_two_writes_names_the_vault_that_was_written(self, env, monkeypatch):
        two_vaults(env)
        old = {v: env.raw(v) for v in (DEFAULT, OTHER)}
        env.install(monkeypatch, replace=_fail_nth_replace(2))
        code, doc, _ = env.run(*order_argv((DEFAULT, [A, B, C], [C, B, A]), (OTHER, [X, Y], [Y, X])))
        assert code == 1
        assert doc["outcome"] == "partial"
        assert doc["written"] == [DEFAULT]
        assert doc["not_written"] == [OTHER]
        assert DEFAULT in doc["message"] and OTHER in doc["message"]
        assert env.entries(DEFAULT) == [C, B, A]
        assert env.raw(OTHER) == old[OTHER]
        assert env.fake.tags().count("sync") == 1

    def test_a_failure_on_the_first_write_leaves_both_files_old_and_is_a_plain_error(self, env, monkeypatch):
        two_vaults(env)
        old = {v: env.raw(v) for v in (DEFAULT, OTHER)}
        env.install(monkeypatch, replace=_fail_nth_replace(1))
        code, doc, err = env.run(*order_argv((DEFAULT, [A, B, C], [C, B, A]), (OTHER, [X, Y], [Y, X])))
        assert code == 1
        assert doc is None
        assert err.startswith("docket: ")
        assert {v: env.raw(v) for v in (DEFAULT, OTHER)} == old

    @pytest.mark.parametrize("failing", [1, 2])
    def test_each_file_is_wholly_old_or_wholly_new_never_partial(self, env, monkeypatch, failing):
        two_vaults(env)
        old = {v: env.raw(v) for v in (DEFAULT, OTHER)}
        env.install(monkeypatch, replace=_fail_nth_replace(failing))
        env.run(*order_argv((DEFAULT, [A, B, C], [C, B, A]), (OTHER, [X, Y], [Y, X])))
        new = {DEFAULT: [C, B, A], OTHER: [Y, X]}
        for vault in (DEFAULT, OTHER):
            assert env.raw(vault) == old[vault] or env.entries(vault) == new[vault]
            json.loads(env.raw(vault))
        assert sorted(v for v in (DEFAULT, OTHER) if env.raw(v) != old[v]) == [DEFAULT][: failing - 1]


class TestVaultOrder:
    def test_the_given_subset_is_stored_and_reported(self, env):
        env.put(DEFAULT, A)
        code, doc, _ = env.run("vault-order", "--", OTHER)
        assert (code, doc["outcome"]) == (0, "saved")
        assert stored_order(env) == [OTHER]
        assert doc["order"] == [OTHER, DEFAULT]
        assert env.fake.tags().count("sync") == 2

    def test_a_full_order_is_stored_as_given(self, env):
        code, doc, _ = env.run("vault-order", "--", "team", OTHER, DEFAULT)
        assert code == 0
        assert stored_order(env) == ["team", OTHER, DEFAULT]
        assert doc["order"] == ["team", OTHER, DEFAULT]

    def test_an_unknown_vault_is_refused_and_nothing_is_stored(self, env):
        env.run("vault-order", "--", OTHER)
        before = order_file(env).read_bytes()
        code, doc, _ = env.run("vault-order", "--", DEFAULT, "nowhere")
        assert (code, doc["outcome"]) == (2, "refused")
        assert "nowhere" in doc["message"]
        assert order_file(env).read_bytes() == before

    def test_a_vault_named_twice_is_refused(self, env):
        code, doc, _ = env.run("vault-order", "--", OTHER, OTHER)
        assert (code, doc["outcome"]) == (2, "refused")
        assert not order_file(env).exists()

    def test_storing_the_order_already_stored_changes_nothing(self, env):
        env.run("vault-order", "--", OTHER, DEFAULT)
        env.fake.calls.clear()
        code, doc, _ = env.run("vault-order", "--", OTHER, DEFAULT)
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert env.fake.tags().count("sync") == 1

    def test_a_failed_sync_leaves_it_saved_not_synced(self, env):
        env.fake.sync_plan = {1: "holding"}
        code, doc, _ = env.run("vault-order", "--", OTHER)
        assert (code, doc["outcome"]) == (3, "saved-not-synced")
        assert stored_order(env) == [OTHER]

    def test_an_order_that_a_remote_copy_replaced_is_lost_to_remote(self, env):
        def remote(fake):
            order_file(env).write_text(json.dumps({"version": 1, "order": [DEFAULT]}))

        env.fake.sync_hooks = {1: remote}
        code, doc, _ = env.run("vault-order", "--", OTHER, DEFAULT)
        assert (code, doc["outcome"]) == (4, "lost-to-remote")
        assert doc["order"] == [DEFAULT]

    def test_a_newer_format_remote_order_after_the_write_is_saved_not_synced(self, env):
        newer = json.dumps({"version": 2, "order": []})
        env.fake.sync_hooks = {1: lambda fake: order_file(env).write_text(newer)}
        code, doc, _ = env.run("vault-order", "--", OTHER)
        assert (code, doc["outcome"]) == (3, "saved-not-synced")
        assert "newer docket" in doc["sync_warning"]

    def test_a_lock_held_past_the_deadline_after_the_write_is_saved_not_synced(self, env):
        holder = contextlib.ExitStack()
        env.fake.sync_hooks = {1: lambda fake: holder.enter_context(Store(env.vault_dir).locked())}
        try:
            code, doc, _ = env.run("vault-order", "--", OTHER)
        finally:
            holder.close()
        assert (code, doc["outcome"]) == (3, "saved-not-synced")
        assert "docket lock" in doc["sync_warning"]
        assert stored_order(env) == [OTHER]

    def test_a_write_that_fails_is_a_docket_line_not_a_traceback(self, env, monkeypatch):
        def failing_replace(*_args, **_kwargs):
            raise OSError(errno.EIO, "Input/output error")

        env.install(monkeypatch, replace=failing_replace)
        code, doc, err = env.run("vault-order", "--", OTHER)
        assert (code, doc) == (1, None)
        assert err.startswith("docket: cannot write")
        assert "Input/output error" in err
        assert stored_order(env) is None

    def test_an_unchanged_order_whose_reread_fails_stays_unchanged_with_a_warning(self, env, monkeypatch):
        env.run("vault-order", "--", OTHER, DEFAULT)
        real_locked = Store.locked
        entered = []

        @contextlib.contextmanager
        def locked(self):
            entered.append(1)
            if len(entered) == 3:
                raise LockTimeoutError("timed out waiting for the docket lock")
            with real_locked(self):
                yield

        monkeypatch.setattr(Store, "locked", locked)
        code, doc, err = env.run("vault-order", "--", OTHER, DEFAULT)
        assert (code, err) == (0, "")
        assert doc["outcome"] == "unchanged"
        assert "docket lock" in doc["sync_warning"]
        assert "order" not in doc

    def test_an_order_the_remote_kept_is_saved(self, env):
        env.fake.sync_hooks = {1: lambda fake: None}
        code, doc, _ = env.run("vault-order", "--", OTHER)
        assert (code, doc["outcome"]) == (0, "saved")


class TestEffectiveOrder:
    @pytest.mark.parametrize("stored, lists, expected", [
        ([], ["proj", "other", "team"], ["other", "proj", "team"]),
        (["team"], ["proj", "other", "team"], ["team", "other", "proj"]),
        (["team", "proj"], ["proj", "other", "team"], ["team", "proj", "other"]),
        (["proj", "other", "team"], ["proj", "other", "team"], ["proj", "other", "team"]),
        (["zzz"], ["proj", "other"], ["zzz", "other", "proj"]),
        (["other"], [], ["other"]),
    ])
    def test_stored_vaults_come_first_then_the_rest_with_a_list_alphabetically(self, tmp_path, stored, lists, expected):
        vault = tmp_path / "v"
        (vault / ".git").mkdir(parents=True)
        store = Store(vault)
        for name in lists:
            store.write_list(name, store.read_list(name))
        if stored:
            store.write_order(stored)
        assert store.effective_order() == expected
        assert resolve_order(stored, lists) == expected

    def test_the_reported_order_follows_the_stored_order_and_the_vaults_that_have_lists(self, env):
        env.put(DEFAULT, A)
        env.put("team", A)
        _, doc, _ = env.run("vault-order", "--", "team")
        assert doc["order"] == ["team", DEFAULT]
        _, doc, _ = env.run("vault-order", "--", DEFAULT)
        assert doc["order"] == [DEFAULT, "team"]
