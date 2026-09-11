"""Behavioural tests for `camp.host.merge.merge_all_hosts_answer` — the pure
merge over the local answer and the per-host answers, per
`docs/design/the-all-hosts-answer-merges-every-declared-machine.md`.

One test per enumerated state in that document (zero / one / many /
collection failure / no hosts declared / every host failed / some answered
some failed / same slug on two machines), plus the local-identity and
group-filter properties the task's test contract names separately.

Task: task/merge-per-host-answers-into-one-ordered-machine-stamped-result.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _merge_module():
    return importlib.import_module("camp.host.merge")


def _relay_module():
    return importlib.import_module("camp.host.relay")


def _host_answer(rows, notices=None, exit_code=0, answered=True):
    relay = _relay_module()
    return relay.HostAnswer(
        rows=rows, notices=notices or [], exit_code=exit_code, answered=answered
    )


# ---------------------------------------------------------------------------
# zero / one / many


def test_zero_across_two_machines_is_empty_merged_answer():
    merge = _merge_module()
    rows, notices, exit_code = merge.merge_all_hosts_answer(
        [], [], 0,
        self_name=None,
        host_answers=[
            ("andromeda", _host_answer([])),
            ("lookout", _host_answer([])),
        ],
    )
    assert rows == []
    assert exit_code == 0


def test_one_row_across_two_machines():
    merge = _merge_module()
    rows, notices, exit_code = merge.merge_all_hosts_answer(
        [], [], 0,
        self_name=None,
        host_answers=[
            ("andromeda", _host_answer([{"ok": True, "slug": "camp-attach", "host": "andromeda"}])),
            ("lookout", _host_answer([])),
        ],
    )
    assert rows == [{"ok": True, "slug": "camp-attach", "host": "andromeda"}]
    assert exit_code == 0


def test_many_rows_across_two_machines_in_declared_order():
    merge = _merge_module()
    local_rows = [{"ok": True, "slug": "local-slug"}]
    rows, notices, exit_code = merge.merge_all_hosts_answer(
        local_rows, [], 0,
        self_name="this-machine",
        host_answers=[
            ("andromeda", _host_answer([
                {"ok": True, "slug": "camp-attach", "host": "andromeda"},
                {"ok": True, "slug": "lookout-fix", "host": "andromeda"},
            ])),
            ("lookout", _host_answer([
                {"ok": True, "slug": "other-slug", "host": "lookout"},
            ])),
        ],
    )
    assert [r["slug"] for r in rows] == ["local-slug", "camp-attach", "lookout-fix", "other-slug"]
    assert rows[0]["host"] == "this-machine"
    assert rows[1]["host"] == "andromeda"
    assert rows[3]["host"] == "lookout"


# ---------------------------------------------------------------------------
# same slug on two machines — no de-dup, no marker


def test_same_slug_on_two_machines_produces_two_rows():
    merge = _merge_module()
    rows, _, _ = merge.merge_all_hosts_answer(
        [], [], 0,
        self_name=None,
        host_answers=[
            ("andromeda", _host_answer([{"ok": True, "slug": "camp-attach", "host": "andromeda"}])),
            ("lookout", _host_answer([{"ok": True, "slug": "camp-attach", "host": "lookout"}])),
        ],
    )
    assert len(rows) == 2
    assert [r["host"] for r in rows] == ["andromeda", "lookout"]
    assert all(r["slug"] == "camp-attach" for r in rows)


# ---------------------------------------------------------------------------
# every declared host failed / some answered some failed


def test_every_declared_host_failed_keeps_local_rows_and_local_exit_code():
    merge = _merge_module()
    local_rows = [{"ok": True, "slug": "local-slug"}]
    rows, notices, exit_code = merge.merge_all_hosts_answer(
        local_rows, ["local notice"], 0,
        self_name=None,
        host_answers=[
            ("andromeda", _host_answer(
                [{"ok": False, "host": "andromeda", "reason": "unreachable — no response within 10s"}],
                notices=["camp list: host 'andromeda' is unreachable — no response within 10s"],
                exit_code=1,
                answered=False,
            )),
            ("lookout", _host_answer(
                [{"ok": False, "host": "lookout", "reason": "camp could not be run on the host"}],
                notices=["camp list: host 'lookout' answered, but camp could not be run there"],
                exit_code=1,
                answered=False,
            )),
        ],
    )
    assert [r.get("ok") for r in rows] == [True, False, False]
    assert rows[1] == {"ok": False, "host": "andromeda", "reason": "unreachable — no response within 10s"}
    assert rows[2] == {"ok": False, "host": "lookout", "reason": "camp could not be run on the host"}
    # exit code is the LOCAL answer's alone, regardless of every host failing
    assert exit_code == 0


def test_some_answered_some_failed_in_one_answer():
    merge = _merge_module()
    rows, _, exit_code = merge.merge_all_hosts_answer(
        [], [], 0,
        self_name=None,
        host_answers=[
            ("andromeda", _host_answer([{"ok": True, "slug": "camp-attach", "host": "andromeda"}])),
            ("lookout", _host_answer(
                [{"ok": False, "host": "lookout", "reason": "unreachable — no response within 10s"}],
                exit_code=1,
                answered=False,
            )),
        ],
    )
    assert [r.get("ok") for r in rows] == [True, False]
    assert exit_code == 0


def test_one_host_unparsable_answer_does_not_change_merged_exit_code():
    """The merged answer's exit status has always been decided by the local
    answer alone — a host whose remote answer was unparsable (and so gets a
    non-zero `HostAnswer.exit_code` from `answer_for_host`) must not flip
    the merged exit code when the local answer itself is fine."""
    merge = _merge_module()
    rows, _, exit_code = merge.merge_all_hosts_answer(
        [{"ok": True, "slug": "local-slug"}], [], 0,
        self_name=None,
        host_answers=[
            ("andromeda", _host_answer(
                [{"ok": False, "host": "andromeda", "reason": "remote answer could not be parsed"}],
                exit_code=1,
                answered=False,
            )),
        ],
    )
    assert [r.get("ok") for r in rows] == [True, False]
    assert exit_code == 0


# ---------------------------------------------------------------------------
# no hosts declared


def test_no_hosts_declared_returns_local_answer_stamped_with_host():
    merge = _merge_module()
    local_rows = [{"ok": True, "slug": "local-slug"}]
    rows, notices, exit_code = merge.merge_all_hosts_answer(
        local_rows, ["a local notice"], 0,
        self_name=None,
        host_answers=[],
    )
    assert rows == [{"ok": True, "slug": "local-slug", "host": None}]
    assert notices == ["a local notice"]
    assert exit_code == 0


# ---------------------------------------------------------------------------
# collection failure, all three sources


def test_local_group_config_failure_row_is_stamped_and_kept():
    # "A local group config that will not parse" — already stated by the
    # local answer as its own ok:false row; merge only stamps the machine.
    merge = _merge_module()
    local_rows = [{"ok": False, "group": None, "reason": "bad.toml: TOML parse error"}]
    rows, _, exit_code = merge.merge_all_hosts_answer(
        local_rows, [], 0,
        self_name="this-machine",
        host_answers=[],
    )
    assert rows == [
        {"ok": False, "group": None, "reason": "bad.toml: TOML parse error", "host": "this-machine"}
    ]
    assert exit_code == 0


def test_remote_collection_failure_row_is_relayed_unchanged():
    # "A remote machine's own collection failure" — relay.py already stamps
    # `host`; merge just concatenates it in. Exercised WITH a group=
    # narrowing (IMPORTANT 7) — without one, this test cannot observe the
    # row being incorrectly dropped by the group filter.
    merge = _merge_module()
    remote_failure_row = {"ok": False, "group": None, "reason": "remote-groups.toml: bad", "host": "andromeda"}
    rows, _, _ = merge.merge_all_hosts_answer(
        [], [], 0,
        self_name=None,
        host_answers=[("andromeda", _host_answer([remote_failure_row]))],
        group="trailhead",
    )
    assert rows == [remote_failure_row]


def test_unparsable_hosts_toml_yields_local_rows_plus_one_failure_row():
    merge = _merge_module()
    local_rows = [{"ok": True, "slug": "local-slug"}]
    rows, notices, exit_code = merge.merge_all_hosts_answer(
        local_rows, [], 0,
        self_name=None,
        host_answers=[],
        hosts_error="/home/tom/.config/camp/hosts.toml: TOML parse error — bad",
    )
    assert len(rows) == 2
    assert rows[0]["slug"] == "local-slug"
    assert rows[1]["ok"] is False
    assert "hosts.toml" in rows[1]["reason"]
    assert any("hosts.toml" in n for n in notices)
    assert exit_code == 0


# ---------------------------------------------------------------------------
# local identity


def test_self_name_declared_puts_name_on_every_local_row():
    merge = _merge_module()
    local_rows = [{"ok": True, "slug": "a"}, {"ok": True, "slug": "b"}]
    rows, _, _ = merge.merge_all_hosts_answer(
        local_rows, [], 0, self_name="camp-attach-box", host_answers=[],
    )
    assert [r["host"] for r in rows] == ["camp-attach-box", "camp-attach-box"]


def test_self_name_undeclared_puts_none_on_every_local_row():
    merge = _merge_module()
    local_rows = [{"ok": True, "slug": "a"}, {"ok": True, "slug": "b"}]
    rows, _, _ = merge.merge_all_hosts_answer(
        local_rows, [], 0, self_name=None, host_answers=[],
    )
    assert [r["host"] for r in rows] == [None, None]


# ---------------------------------------------------------------------------
# group filter


def test_group_filter_drops_remote_rows_of_other_groups_and_null_group():
    merge = _merge_module()
    rows, _, _ = merge.merge_all_hosts_answer(
        [], [], 0,
        self_name=None,
        host_answers=[
            ("andromeda", _host_answer([
                {"ok": True, "slug": "s1", "group": "trailhead", "host": "andromeda"},
                {"ok": True, "slug": "s2", "group": "otherteam", "host": "andromeda"},
                {"ok": True, "slug": "s3", "group": None, "host": "andromeda"},
            ])),
        ],
        group="trailhead",
    )
    assert [r["slug"] for r in rows] == ["s1"]


def test_group_filter_never_drops_a_row_with_no_group_key():
    merge = _merge_module()
    rows, _, _ = merge.merge_all_hosts_answer(
        [], [], 0,
        self_name=None,
        host_answers=[
            ("andromeda", _host_answer([{"ok": False, "host": "andromeda", "reason": "unreachable"}])),
        ],
        group="trailhead",
    )
    assert rows == [{"ok": False, "host": "andromeda", "reason": "unreachable"}]


def test_group_filter_never_drops_a_remote_collection_failure_row_that_carries_a_group_key():
    # IMPORTANT 3 — a remote camp's own collection-failure row carries a
    # `group` key (set to null), so the old "group" in r discriminator
    # drops it under -a --group X. Discriminating on `ok` instead keeps it.
    merge = _merge_module()
    remote_failure_row = {
        "ok": False, "group": None, "reason": "remote-groups.toml: bad", "host": "andromeda",
    }
    rows, _, _ = merge.merge_all_hosts_answer(
        [], [], 0,
        self_name=None,
        host_answers=[("andromeda", _host_answer([remote_failure_row]))],
        group="trailhead",
    )
    assert rows == [remote_failure_row]


def test_group_filter_drops_an_ok_row_missing_the_group_key_entirely():
    # IMPORTANT 3 — a version-skewed remote's ok row that omits `group`
    # entirely must still be narrowed by the resolved group, not leaked
    # through because the key is absent.
    merge = _merge_module()
    rows, _, _ = merge.merge_all_hosts_answer(
        [], [], 0,
        self_name=None,
        host_answers=[
            ("andromeda", _host_answer([{"ok": True, "slug": "s1", "host": "andromeda"}])),
        ],
        group="trailhead",
    )
    assert rows == []


def test_group_filter_is_never_applied_to_local_rows():
    # IMPORTANT 4 — the local answer arrives already narrowed by
    # local_list_answer/local_sessions_answer; re-filtering it drops a
    # workspace whose manifest group is "" or absent.
    merge = _merge_module()
    local_rows = [
        {"ok": True, "slug": "local-empty-group", "group": ""},
        {"ok": True, "slug": "local-no-group-key"},
    ]
    rows, _, _ = merge.merge_all_hosts_answer(
        local_rows, [], 0,
        self_name=None,
        host_answers=[],
        group="trailhead",
    )
    assert {r["slug"] for r in rows} == {"local-empty-group", "local-no-group-key"}


def test_no_group_given_filters_nothing():
    merge = _merge_module()
    rows, _, _ = merge.merge_all_hosts_answer(
        [], [], 0,
        self_name=None,
        host_answers=[
            ("andromeda", _host_answer([
                {"ok": True, "slug": "s1", "group": "trailhead", "host": "andromeda"},
                {"ok": True, "slug": "s2", "group": "otherteam", "host": "andromeda"},
                {"ok": True, "slug": "s3", "group": None, "host": "andromeda"},
            ])),
        ],
        group=None,
    )
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# AC5 conjunction — machine identity AND group carry-through, independently


def test_ac5_every_row_identifies_its_machine_and_session_rows_keep_group():
    merge = _merge_module()
    # A session row carries a genuinely non-null group locally, so deleting
    # group carry-through in the merge is independently observable from
    # deleting host stamping.
    local_rows = [
        {"ok": True, "session_id": "abc123", "kind": "agent", "cwd": "/x", "group": "trailhead"},
    ]
    rows, _, _ = merge.merge_all_hosts_answer(
        local_rows, [], 0, self_name="camp-attach-box", host_answers=[],
    )
    assert rows[0]["host"] == "camp-attach-box"
    assert rows[0]["group"] == "trailhead"
