"""The docket change commands (add, remove, top, attach, detach) through `docket.cli.main`.

Collaborators (lore, git) reach the code through an injected runner; the vault, its lists and the lock
are real files under tmp_path. The runner is a scripted fake of lore and git that records every call,
so the order of sync, read, write and sync is observed from what each sync call finds on disk.
"""
from __future__ import annotations

import contextlib
import json
import os

import pytest

from _docket_cli import PLUGIN_ROOT  # noqa: F401  (puts the plugin on sys.path)
from _docket_env import DEFAULT, ref

from docket.store import Store

pytest_plugins = ["_docket_env"]


BASE = ("task/a", "task/b", "task/c")


class TestAddPlacement:
    @pytest.mark.parametrize("flags, expected", [
        ([], ["task/a", "task/b", "task/c", "task/n1"]),
        (["--top"], ["task/n1", "task/a", "task/b", "task/c"]),
        (["--before", ref("task/b")], ["task/a", "task/n1", "task/b", "task/c"]),
        (["--after", ref("task/b")], ["task/a", "task/b", "task/n1", "task/c"]),
        (["--before", ref("task/a")], ["task/n1", "task/a", "task/b", "task/c"]),
        (["--after", ref("task/c")], ["task/a", "task/b", "task/c", "task/n1"]),
    ])
    def test_the_entry_lands_where_it_was_told(self, env, flags, expected):
        env.put(DEFAULT, *BASE)
        code, doc, _ = env.run("add", *flags, "--", ref("task/n1"))
        assert code == 0
        assert doc["outcome"] == "saved"
        assert env.entries() == expected

    def test_an_add_to_a_vault_with_no_list_yet_creates_it(self, env):
        code, doc, _ = env.run("add", "--", ref("task/n1"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == ["task/n1"]

    @pytest.mark.parametrize("flags, expected", [
        ([], ["task/a", "task/b", "task/c", "task/n1", "task/n2", "task/n3"]),
        (["--top"], ["task/n1", "task/n2", "task/n3", "task/a", "task/b", "task/c"]),
        (["--before", ref("task/c")], ["task/a", "task/b", "task/n1", "task/n2", "task/n3", "task/c"]),
        (["--after", ref("task/a")], ["task/a", "task/n1", "task/n2", "task/n3", "task/b", "task/c"]),
    ])
    def test_several_refs_land_consecutively_in_the_order_given_with_one_sync_pair(self, env, flags, expected):
        env.put(DEFAULT, *BASE)
        code, doc, _ = env.run("add", *flags, "--", ref("task/n1"), ref("task/n2"), ref("task/n3"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == expected
        assert env.fake.tags().count("sync") == 2
        assert [e["status"] for e in doc["entries"]] == ["added", "added", "added"]

    @pytest.mark.parametrize("flag", ["--before", "--after"])
    def test_an_anchor_not_in_the_list_is_refused_and_the_file_is_unchanged(self, env, flag):
        env.put(DEFAULT, *BASE)
        before = env.raw()
        code, doc, _ = env.run("add", flag, ref("task/zzz"), "--", ref("task/n1"))
        assert code == 2
        assert doc["outcome"] == "refused"
        assert "task/zzz" in doc["message"]
        assert env.raw() == before

    def test_an_anchor_in_another_vault_than_the_refs_is_refused(self, env):
        env.put(DEFAULT, *BASE)
        env.fake.know("other/task/n9")
        env.put("other", "task/y")
        before = (env.raw(), env.raw("other"))
        code, doc, _ = env.run("add", "--before", ref("task/y", "other"), "--", ref("task/n1"))
        assert (code, doc["outcome"]) == (2, "refused")
        assert (env.raw(), env.raw("other")) == before

    def test_refs_for_two_vaults_each_land_at_the_bottom_of_their_own_list(self, env):
        env.fake.know("other/task/y")
        env.put(DEFAULT, *BASE)
        env.put("other", "task/x")
        code, doc, _ = env.run("add", "--", ref("task/n1"), ref("task/y", "other"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == ["task/a", "task/b", "task/c", "task/n1"]
        assert env.entries("other") == ["task/x", "task/y"]
        assert doc["entry_count"] == {"proj": 4, "other": 2}


class TestAddDuplicate:
    def test_an_entry_already_listed_is_unchanged_with_nothing_written_and_no_post_sync(self, env):
        env.put(DEFAULT, *BASE)
        before = env.raw()
        code, doc, _ = env.run("add", "--top", "--", ref("task/b"))
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert doc["entries"] == [{"ref": ref("task/b"), "status": "already-present"}]
        assert env.raw() == before
        assert list((env.vault_dir / ".git" / "docket").glob("*.tmp")) == []
        assert env.fake.tags().count("sync") == 1

    def test_a_ref_repeated_in_one_command_is_added_once(self, env):
        env.put(DEFAULT, *BASE)
        code, doc, _ = env.run("add", "--", ref("task/n1"), ref("task/n1"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == ["task/a", "task/b", "task/c", "task/n1"]
        assert [e["status"] for e in doc["entries"]] == ["added", "already-present"]

    def test_a_failed_pre_sync_on_an_unchanged_result_carries_a_sync_warning(self, env):
        env.put(DEFAULT, *BASE)
        env.fake.sync_plan = {0: "holding"}
        code, doc, _ = env.run("add", "--", ref("task/b"))
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert "holding" in doc["sync_warning"]

    def test_a_clean_pre_sync_on_an_unchanged_result_carries_no_sync_warning(self, env):
        env.put(DEFAULT, *BASE)
        code, doc, _ = env.run("add", "--", ref("task/b"))
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert "sync_warning" not in doc


class TestKindCheck:
    @pytest.mark.parametrize("kind, keywords", [
        ("spec", []),
        ("task", []),
        ("blob", ["note"]),
        ("blob", ["misc", "note"]),
    ])
    def test_a_spec_a_task_or_a_note_blob_is_accepted(self, env, kind, keywords):
        env.fake.know(f"{DEFAULT}/{kind}/thing", keywords)
        code, doc, _ = env.run("add", "--", ref(f"{kind}/thing"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == [f"{kind}/thing"]

    @pytest.mark.parametrize("kind, keywords", [
        ("blob", []),
        ("blob", ["notes"]),
        ("adr", []),
        ("decision", []),
        ("lesson", ["note"]),
    ])
    def test_any_other_kind_is_refused_with_nothing_written_and_no_sync(self, env, kind, keywords):
        env.fake.know(f"{DEFAULT}/{kind}/thing", keywords)
        code, doc, _ = env.run("add", "--", ref(f"{kind}/thing"))
        assert code == 2
        assert doc["outcome"] == "refused"
        assert doc["entries"][0]["status"] == "refused"
        assert env.entries() is None
        assert "sync" not in env.fake.tags()

    def test_a_lookup_that_exits_nonzero_is_refused_whatever_it_printed(self, env):
        env.fake.failing.add(f"{DEFAULT}/task/odd")
        code, doc, _ = env.run("add", "--", ref("task/odd"))
        assert (code, doc["outcome"]) == (2, "refused")
        assert env.entries() is None

    def test_a_record_that_does_not_resolve_is_refused(self, env):
        code, doc, _ = env.run("add", "--", ref("task/ghost"))
        assert (code, doc["outcome"]) == (2, "refused")
        assert env.entries() is None
        assert "sync" not in env.fake.tags()

    def test_one_bad_ref_among_several_refuses_the_whole_change(self, env):
        env.put(DEFAULT, *BASE)
        before = env.raw()
        code, doc, _ = env.run("add", "--", ref("task/n1"), ref("task/ghost"))
        assert (code, doc["outcome"]) == (2, "refused")
        assert env.raw() == before

    def test_the_kind_is_read_from_stdout_not_stderr(self, env):
        env.fake.know(f"{DEFAULT}/spec/s")
        code, doc, _ = env.run("add", "--", ref("spec/s"))
        assert code == 0, doc


class TestRemoveTopAttachDetach:
    def test_remove_drops_each_present_entry_and_reports_the_absent_one(self, env):
        env.put(DEFAULT, *BASE)
        code, doc, _ = env.run("remove", "--", ref("task/a"), ref("task/zzz"), ref("task/c"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == ["task/b"]
        assert doc["entries"] == [
            {"ref": ref("task/a"), "status": "removed"},
            {"ref": ref("task/zzz"), "status": "not-found"},
            {"ref": ref("task/c"), "status": "removed"},
        ]
        assert doc["entry_count"] == {"proj": 1}

    def test_remove_of_only_absent_entries_is_unchanged_and_writes_nothing(self, env):
        env.put(DEFAULT, *BASE)
        before = env.raw()
        code, doc, _ = env.run("remove", "--", ref("task/zzz"))
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert env.raw() == before
        assert env.fake.tags().count("sync") == 1

    def test_an_entry_whose_record_is_missing_removes_like_any_other(self, env):
        env.put(DEFAULT, "task/gone", "task/a")
        code, doc, _ = env.run("remove", "--", ref("task/gone"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == ["task/a"]
        assert "show" not in env.fake.tags()

    def test_top_moves_entries_up_keeping_the_order_they_had_in_the_list(self, env):
        env.put(DEFAULT, *BASE, "task/d")
        code, doc, _ = env.run("top", "--", ref("task/d"), ref("task/b"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == ["task/b", "task/d", "task/a", "task/c"]
        assert [e["status"] for e in doc["entries"]] == ["moved", "moved"]

    def test_top_moves_within_each_entrys_own_vault(self, env):
        env.put(DEFAULT, *BASE)
        env.put("other", "task/x", "task/y")
        code, doc, _ = env.run("top", "--", ref("task/c"), ref("task/y", "other"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == ["task/c", "task/a", "task/b"]
        assert env.entries("other") == ["task/y", "task/x"]

    def test_top_of_entries_already_on_top_is_unchanged(self, env):
        env.put(DEFAULT, *BASE)
        before = env.raw()
        code, doc, _ = env.run("top", "--", ref("task/a"))
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert env.raw() == before

    def test_top_reports_an_absent_entry(self, env):
        env.put(DEFAULT, *BASE)
        code, doc, _ = env.run("top", "--", ref("task/zzz"), ref("task/c"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert doc["entries"][0] == {"ref": ref("task/zzz"), "status": "not-found"}
        assert env.entries() == ["task/c", "task/a", "task/b"]

    def test_attach_then_detach_round_trips_the_workspace(self, env):
        env.put(DEFAULT, *BASE)
        original = env.entries()
        code, doc, _ = env.run("attach", "--", ref("task/b"), "trailhead/docket")
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == ["task/a", "task/b@trailhead/docket", "task/c"]
        assert doc["entries"] == [{"ref": ref("task/b"), "status": "attached", "workspace": "trailhead/docket"}]
        code, doc, _ = env.run("detach", "--", ref("task/b"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert doc["entries"] == [{"ref": ref("task/b"), "status": "detached"}]
        assert env.entries() == original

    def test_attach_to_the_same_workspace_is_unchanged(self, env):
        env.put(DEFAULT, "task/a", ("task/b", "trailhead", "docket"))
        before = env.raw()
        code, doc, _ = env.run("attach", "--", ref("task/b"), "trailhead/docket")
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert env.raw() == before

    def test_attach_replaces_a_different_workspace(self, env):
        env.put(DEFAULT, ("task/b", "trailhead", "old"))
        code, doc, _ = env.run("attach", "--", ref("task/b"), "trailhead/new")
        assert (code, doc["outcome"]) == (0, "saved")
        assert env.entries() == ["task/b@trailhead/new"]

    def test_attach_to_an_entry_not_in_the_list_is_refused(self, env):
        env.put(DEFAULT, *BASE)
        before = env.raw()
        code, doc, _ = env.run("attach", "--", ref("task/zzz"), "trailhead/docket")
        assert (code, doc["outcome"]) == (2, "refused")
        assert env.raw() == before

    def test_detach_of_an_entry_with_no_workspace_is_unchanged(self, env):
        env.put(DEFAULT, *BASE)
        before = env.raw()
        code, doc, _ = env.run("detach", "--", ref("task/a"))
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert doc["entries"] == [{"ref": ref("task/a"), "status": "not-attached"}]
        assert env.raw() == before

    def test_detach_of_an_entry_not_in_the_list_is_refused(self, env):
        env.put(DEFAULT, *BASE)
        code, doc, _ = env.run("detach", "--", ref("task/zzz"))
        assert (code, doc["outcome"]) == (2, "refused")


class TestCallOrder:
    def test_the_runner_log_shows_sync_then_the_read_then_the_write_then_sync(self, env):
        seen = {}

        def remote_arrives(fake):
            seen["before"] = env.raw()
            env.put(DEFAULT, "task/from-remote")

        def after_write(fake):
            seen["after"] = env.entries()

        env.fake.sync_hooks = {0: remote_arrives, 1: after_write}
        code, doc, _ = env.run("add", "--", ref("task/n1"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert seen["before"] is None
        assert seen["after"] == ["task/from-remote", "task/n1"]
        assert env.entries() == ["task/from-remote", "task/n1"]
        tags = env.fake.tags()
        assert [t for t in tags if t in ("sync", "show")] == ["show", "sync", "sync"]
        assert tags.index("rev-list") > tags.index("sync")

    def test_the_syncs_target_the_default_vault_by_name(self, env):
        env.run("add", "--", ref("task/n1"))
        syncs = [c for c in env.fake.calls if c[:2] == ["lore", "sync"]]
        assert syncs == [["lore", "sync", "--vault", DEFAULT, "--json"]] * 2

    def test_the_synced_test_reads_the_vault_git_state(self, env):
        env.run("add", "--", ref("task/n1"))
        git = [c for c in env.fake.calls if c[0] == "git"]
        assert git == [
            ["git", "-C", str(env.vault_dir), "rev-list", "--count", "@{u}..HEAD"],
            ["git", "-C", str(env.vault_dir), "status", "--porcelain", "--", "sites/docket"],
        ]


class TestNotSynced:
    @pytest.mark.parametrize("plan", [{0: "holding"}, {1: "holding"}, {0: "holding", 1: "holding"}])
    def test_a_failed_sync_before_or_after_still_saves_and_exits_3(self, env, plan):
        env.put(DEFAULT, *BASE)
        env.fake.sync_plan = plan
        code, doc, _ = env.run("add", "--", ref("task/n1"))
        assert code == 3
        assert doc["outcome"] == "saved-not-synced"
        assert env.entries() == [*BASE, "task/n1"]

    @pytest.mark.parametrize("setup", ["ahead", "dirty", "no-upstream"])
    def test_a_sync_that_reports_success_but_leaves_git_unpublished_exits_3(self, env, setup):
        if setup == "ahead":
            env.fake.ahead = "1"
        elif setup == "dirty":
            env.fake.dirty = " M sites/docket/lists/proj.json\n"
        else:
            env.fake.has_upstream = False
        code, doc, _ = env.run("add", "--", ref("task/n1"))
        assert code == 3
        assert doc["outcome"] == "saved-not-synced"
        assert env.entries() == ["task/n1"]

    def test_a_clean_sync_and_clean_git_is_saved_with_exit_0(self, env):
        code, doc, _ = env.run("add", "--", ref("task/n1"))
        assert (code, doc["outcome"]) == (0, "saved")
        assert doc["entry_count"] == {"proj": 1}


def _restore(env, text):
    def hook(fake):
        env.list_path().write_text(text)
    return hook


class TestLostToRemote:
    @pytest.mark.parametrize("op, argv", [
        ("add", ["add", "--", ref("task/n1")]),
        ("remove", ["remove", "--", ref("task/b")]),
        ("top", ["top", "--", ref("task/c")]),
        ("attach", ["attach", "--", ref("task/b"), "trailhead/docket"]),
    ])
    def test_a_change_that_a_remote_copy_replaced_is_lost_to_remote_with_exit_4(self, env, op, argv):
        env.put(DEFAULT, *BASE)
        original = env.list_path().read_text()
        env.fake.sync_hooks = {1: _restore(env, original)}
        code, doc, _ = env.run(*argv)
        assert code == 4
        assert doc["outcome"] == "lost-to-remote"
        assert "another host" in doc["message"]
        assert doc["lists"] == {"proj": [{"kind": "task", "slug": s} for s in ("a", "b", "c")]}
        assert env.entries() == list(BASE)

    def test_a_detach_that_a_remote_copy_replaced_is_lost_to_remote(self, env):
        env.put(DEFAULT, "task/a", ("task/b", "trailhead", "docket"))
        original = env.list_path().read_text()
        env.fake.sync_hooks = {1: _restore(env, original)}
        code, doc, _ = env.run("detach", "--", ref("task/b"))
        assert (code, doc["outcome"]) == (4, "lost-to-remote")
        assert doc["lists"]["proj"][1]["workspace"] == {"group": "trailhead", "slug": "docket"}

    def test_a_lost_add_is_never_reported_saved_even_when_git_says_synced(self, env):
        env.fake.sync_hooks = {1: lambda fake: env.put(DEFAULT, "task/other-host")}
        code, doc, _ = env.run("add", "--", ref("task/n1"))
        assert doc["outcome"] != "saved"
        assert (code, doc["outcome"]) == (4, "lost-to-remote")

    def test_a_remote_edit_that_kept_the_change_is_saved(self, env):
        env.put(DEFAULT, *BASE)

        def merged(fake):
            env.put(DEFAULT, "task/other-host", *BASE, "task/n1")

        env.fake.sync_hooks = {1: merged}
        code, doc, _ = env.run("add", "--", ref("task/n1"))
        assert (code, doc["outcome"]) == (0, "saved")

    def test_an_add_whose_place_was_lost_is_lost_even_though_the_entry_is_present(self, env):
        env.put(DEFAULT, *BASE)
        env.fake.sync_hooks = {1: lambda fake: env.put(DEFAULT, "task/n1", *BASE)}
        code, doc, _ = env.run("add", "--after", ref("task/c"), "--", ref("task/n1"))
        assert (code, doc["outcome"]) == (4, "lost-to-remote")

    def test_a_multi_ref_add_whose_order_was_lost_is_lost(self, env):
        env.put(DEFAULT, *BASE)
        env.fake.sync_hooks = {1: lambda fake: env.put(DEFAULT, *BASE, "task/n2", "task/n1")}
        code, doc, _ = env.run("add", "--", ref("task/n1"), ref("task/n2"))
        assert (code, doc["outcome"]) == (4, "lost-to-remote")

    def test_a_lost_removal_whose_entry_came_back_is_lost(self, env):
        env.put(DEFAULT, *BASE)
        env.fake.sync_hooks = {1: lambda fake: env.put(DEFAULT, *BASE)}
        code, doc, _ = env.run("remove", "--", ref("task/a"))
        assert (code, doc["outcome"]) == (4, "lost-to-remote")

    def test_a_lost_top_whose_entry_dropped_back_is_lost(self, env):
        env.put(DEFAULT, *BASE)
        env.fake.sync_hooks = {1: lambda fake: env.put(DEFAULT, "task/a", "task/c", "task/b")}
        code, doc, _ = env.run("top", "--", ref("task/c"))
        assert (code, doc["outcome"]) == (4, "lost-to-remote")

    def test_a_lost_not_synced_change_is_lost_not_saved_not_synced(self, env):
        env.put(DEFAULT, *BASE)
        original = env.list_path().read_text()
        env.fake.sync_hooks = {1: _restore(env, original)}
        env.fake.ahead = "1"
        code, doc, _ = env.run("remove", "--", ref("task/a"))
        assert (code, doc["outcome"]) == (4, "lost-to-remote")


class TestValidation:
    @pytest.mark.parametrize("argv", [
        ["add", "--", "-rf"],
        ["add", "--", "proj/task/-rf"],
        ["add", "--", "../x"],
        ["add", "--", "proj/../x"],
        ["add", "--", "Task/X"],
        ["add", "--", "proj/Task/x"],
        ["add", "--", "proj/task/X"],
        ["add", "--", "proj//x"],
        ["add", "--", "/task/x"],
        ["add", "--", "proj/task/"],
        ["add", "--", "proj/task/x/y"],
        ["add", "--", "proj/task"],
        ["add", "--", "proj/task/x\n"],
        ["add", "--", "proj/task/a b"],
        ["add", "--", ref("task/n1"), "-rf"],
        ["add", "--before", "../x", "--", ref("task/n1")],
        ["remove", "--", "-rf"],
        ["remove", "--", "proj/-task/x"],
        ["top", "--", "../x"],
        ["attach", "--", ref("task/a"), "-rf/x"],
        ["attach", "--", ref("task/a"), "Group/slug"],
        ["attach", "--", ref("task/a"), "group/Slug"],
        ["attach", "--", ref("task/a"), "group"],
        ["attach", "--", ref("task/a"), "group/"],
        ["attach", "--", ref("task/a"), "g/s/t"],
        ["attach", "--", "-rf", "g/s"],
        ["detach", "--", "-rf"],
        ["detach", "--", "Task/X"],
    ])
    def test_a_malformed_value_is_refused_with_exit_2_and_no_call_at_all(self, env, argv):
        env.put(DEFAULT, *BASE)
        before = env.raw()
        code, doc, _ = env.run(*argv)
        assert code == 2
        assert doc["outcome"] == "refused"
        assert env.fake.calls == []
        assert env.raw() == before

    @pytest.mark.parametrize("argv", [
        ["add", "--", "-rf"],
        ["add", "--", "-proj/task/x"],
        ["remove", "--", "-x"],
        ["attach", "--", ref("task/a"), "-g/s"],
    ])
    def test_a_value_beginning_with_a_dash_is_refused_naming_the_dash(self, env, argv):
        code, doc, _ = env.run(*argv)
        assert (code, doc["outcome"]) == (2, "refused")
        assert "dash" in doc["message"]
        assert env.fake.calls == []

    @pytest.mark.parametrize("argv", [
        ["add", "--", "nope/task/x"],
        ["remove", "--", "nope/task/x"],
        ["top", "--", ref("task/a"), "nope/task/x"],
        ["attach", "--", "nope/task/x", "g/s"],
        ["detach", "--", "nope/task/x"],
        ["add", "--after", "nope/task/x", "--", "nope/task/y"],
    ])
    def test_an_unconfigured_vault_is_refused_before_any_sync_or_read(self, env, argv):
        env.put(DEFAULT, *BASE)
        before = env.raw()
        code, doc, _ = env.run(*argv)
        assert code == 2
        assert doc["outcome"] == "refused"
        assert "nope" in doc["message"]
        assert env.fake.tags() == ["vault-ls"]
        assert env.raw() == before


class TestUnreadableList:
    def test_a_conflicted_list_is_refused_and_its_bytes_are_kept(self, env):
        path = env.list_path()
        path.parent.mkdir(parents=True)
        path.write_text('<<<<<<< HEAD\n{"version": 1, "entries": []}\n=======\n>>>>>>> x\n')
        before = env.raw()
        code, doc, _ = env.run("add", "--", ref("task/n1"))
        assert (code, doc["outcome"]) == (2, "refused")
        assert "conflict" in doc["message"]
        assert env.raw() == before

    def test_an_unwritable_vault_without_a_git_dir_is_an_error_not_a_refusal(self, env, tmp_path):
        env.fake.vault_dir = tmp_path / "elsewhere"
        env.fake.vault_dir.mkdir()
        env.vault_dir = env.fake.vault_dir
        code, doc, err = env.run("add", "--", ref("task/n1"))
        assert code == 1
        assert err.startswith("docket: ")


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only directory")
class TestUnwritableGitDir:
    def test_the_first_change_prints_a_docket_line_not_a_traceback(self, env):
        env.put(DEFAULT, *BASE)
        os.chmod(env.vault_dir / ".git", 0o555)
        try:
            code, doc, err = env.run("add", "--", ref("task/n1"))
        finally:
            os.chmod(env.vault_dir / ".git", 0o755)
        assert (code, doc) == (1, None)
        assert err.startswith("docket: ")
        assert env.entries() == list(BASE)


class TestWriteLandedThenReReadFails:
    def test_a_newer_format_remote_copy_after_the_write_is_saved_not_synced(self, env):
        env.put(DEFAULT, *BASE)
        newer = json.dumps({"version": 2, "entries": []})
        env.fake.sync_hooks = {1: lambda fake: env.list_path().write_text(newer)}
        code, doc, _ = env.run("add", "--", ref("task/n1"))
        assert (code, doc["outcome"]) == (3, "saved-not-synced")
        assert "newer docket" in doc["sync_warning"]

    def test_a_lock_held_past_the_deadline_after_the_write_is_saved_not_synced(self, env):
        env.put(DEFAULT, *BASE)
        holder = contextlib.ExitStack()

        def hold(fake):
            holder.enter_context(Store(env.vault_dir).locked())

        env.fake.sync_hooks = {1: hold}
        try:
            code, doc, _ = env.run("add", "--", ref("task/n1"))
        finally:
            holder.close()
        assert (code, doc and doc["outcome"]) == (3, "saved-not-synced")
        assert "docket lock" in doc["sync_warning"]
        assert env.entries() == [*BASE, "task/n1"]


class TestRelayedTextCannotBreakOutOfTheDataFence:
    def _assert_safe(self, text, cap):
        assert "`" not in text
        assert all(c.isprintable() for c in text)
        assert len(text) <= cap

    def test_a_sync_stderr_line_with_control_characters_backticks_and_length_is_cleaned_and_capped(self, env):
        env.put(DEFAULT, *BASE)
        env.fake.sync_plan = {0: "holding"}
        env.fake.holding_stderr = "boom\x1b[31m\x07 ``` after" + "y" * 500 + "\n"
        code, doc, _ = env.run("add", "--", ref("task/b"))
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert "holding" in doc["sync_warning"]
        assert "boom" in doc["sync_warning"]
        self._assert_safe(doc["sync_warning"], 400)

    def test_a_lore_supplied_sync_outcome_is_cleaned_and_capped(self, env):
        env.put(DEFAULT, *BASE)
        env.fake.sync_plan = {0: "holding"}
        env.fake.holding_outcome = "odd\x1b[2J```" + "z" * 500
        code, doc, _ = env.run("add", "--", ref("task/b"))
        assert (code, doc["outcome"]) == (0, "unchanged")
        assert "odd" in doc["sync_warning"]
        self._assert_safe(doc["sync_warning"], 400)

    def test_a_vault_listing_failure_carrying_lore_stderr_is_cleaned_and_capped(self, env):
        env.fake.vault_ls_result = (1, "", "boom\x1b[31m\x07 ``` after" + "y" * 500 + "\n")
        code, doc, err = env.run("add", "--", ref("task/n1"))
        assert (code, doc) == (1, None)
        assert err.startswith("docket: ") and "boom" in err
        self._assert_safe(err.strip(), 400)


class TestMalformedInputIsACleanFailure:
    def test_a_deeply_nested_list_file_is_refused_naming_the_file(self, env):
        env.list_path().parent.mkdir(parents=True)
        env.list_path().write_text("[" * 200_000)
        code, doc, _ = env.run("add", "--", ref("task/n1"))
        assert (code, doc["outcome"]) == (2, "refused")
        assert str(env.list_path()) in doc["message"]
        assert env.raw() == b"[" * 200_000

    @pytest.mark.parametrize("result", [
    (0, '[{"scope": "default", "path": "/vault"}]', ""),
    (0, '[{"name": "proj", "scope": "default", "path": "/vault"}, 5]', ""),
    (0, '[{"name": 5, "scope": "default", "path": "/vault"}]', ""),
    (0, "[" * 100_000, ""),
])
    def test_malformed_vault_listing_output_is_a_docket_error(self, env, result):
        env.fake.vault_ls_result = result
        code, doc, err = env.run("add", "--", ref("task/n1"))
        assert (code, doc) == (1, None)
        assert err.startswith("docket: ")
        assert "Traceback" not in err
