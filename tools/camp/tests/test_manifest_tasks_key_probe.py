"""ASSUMPTION PROBE (ephemeral — delete after slice 3/4 lands its real tests).

Unknown under test: does the existing central-manifest read path tolerate a
NEW `tasks: {...}` dict added to a member entry, without a schema_version
bump? Precedent: activation.py already adds an `"activated"` key to a member
entry (activation.py:48) and reads it via `entry.get("activated", False)`
(activation.py:171) with no schema bump. If that pattern holds, `tasks`
should be equally safe.

This test writes a manifest with a member entry carrying the proposed
`tasks` shape ({"tasks": {"<task-name>": {"state": ..., "reason": ...}}})
in addition to the existing v1 keys, then drives every real reader that
touches a member entry: read_central_manifest, provision_status_code,
cmd_status_group, flip_member_state_unlocked, and activation's
enter_member/_mark_activated path. Passes iff nothing raises AND the
`tasks` key survives every read-mutate-write round trip untouched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


TASKS_BLOB = {
    "install-deps": {"state": "ok"},
    "run-migrations": {"state": "failed", "reason": "exit 1: migration X missing table"},
}


def _write_manifest(tmp_path: Path, wt_path: Path) -> Path:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "group": "mygroup",
                "slug": "my-slug",
                "branch": "worktree-my-slug",
                "members": [
                    {
                        "name": "myrepo",
                        "repo_root": "/tmp/fake-repo",
                        "worktree_path": str(wt_path),
                        "provision_state": "ready",
                        "tasks": TASKS_BLOB,
                    }
                ],
            }
        )
    )
    return manifest_path


def test_read_central_manifest_preserves_unknown_tasks_key(tmp_path: Path) -> None:
    """read_central_manifest returns the tasks dict unchanged (no schema check)."""
    from camp.group.manifest import read_central_manifest

    wt_path = tmp_path / "myrepo"
    wt_path.mkdir()
    mpath = _write_manifest(tmp_path, wt_path)

    data = read_central_manifest(mpath)
    member = data["members"][0]
    assert member["tasks"] == TASKS_BLOB


def test_provision_status_code_ignores_tasks_key(tmp_path: Path) -> None:
    """provision_status_code (backs `camp status`) doesn't choke on the extra key."""
    from camp.provision.lifecycle import provision_status_code

    wt_path = tmp_path / "camp" / "mygroup" / "worktrees" / "my-slug" / "myrepo"
    wt_path.mkdir(parents=True)
    manifest_dir = tmp_path / "camp" / "mygroup" / "worktrees" / "my-slug"
    mpath = manifest_dir / "manifest.json"
    mpath.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "group": "mygroup",
                "slug": "my-slug",
                "branch": "worktree-my-slug",
                "members": [
                    {
                        "name": "myrepo",
                        "repo_root": "/tmp/fake-repo",
                        "worktree_path": str(wt_path),
                        "provision_state": "ready",
                        "tasks": TASKS_BLOB,
                    }
                ],
            }
        )
    )

    group = {"group": {"name": "mygroup"}, "members": []}
    env = {"CAMP_STATE_DIR": str(tmp_path / "camp")}

    code, report = provision_status_code(group, "my-slug", env=env)

    assert code == 0
    assert report["members"] == [{"name": "myrepo", "provision_state": "ready"}]


def test_cmd_status_group_ignores_tasks_key(tmp_path: Path) -> None:
    """cmd_status_group (fleet + scoped view) doesn't choke on the extra key."""
    from camp.provision.lifecycle import cmd_status_group

    wt_path = tmp_path / "camp" / "mygroup" / "worktrees" / "my-slug" / "myrepo"
    wt_path.mkdir(parents=True)
    manifest_dir = tmp_path / "camp" / "mygroup" / "worktrees" / "my-slug"
    mpath = manifest_dir / "manifest.json"
    mpath.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "group": "mygroup",
                "slug": "my-slug",
                "branch": "worktree-my-slug",
                "members": [
                    {
                        "name": "myrepo",
                        "repo_root": "/tmp/fake-repo",
                        "worktree_path": str(wt_path),
                        "provision_state": "ready",
                        "tasks": TASKS_BLOB,
                    }
                ],
            }
        )
    )

    group = {"group": {"name": "mygroup"}, "members": []}
    env = {"CAMP_STATE_DIR": str(tmp_path / "camp")}

    result = cmd_status_group(group, slug="my-slug", env=env)

    assert len(result["worktrees"]) == 1
    member_status = result["worktrees"][0]["members"][0]
    assert member_status["name"] == "myrepo"
    # tasks key is simply absent from the projected status shape — not an error.
    assert "tasks" not in member_status


def test_flip_member_state_preserves_tasks_key(tmp_path: Path) -> None:
    """flip_member_state_unlocked read-mutate-writes the manifest; tasks must survive."""
    from camp.group.manifest import (
        flip_member_state_unlocked,
        read_central_manifest,
        reconcile_lock,
    )

    wt_path = tmp_path / "myrepo"
    wt_path.mkdir()
    mpath = _write_manifest(tmp_path, wt_path)

    with reconcile_lock(mpath.parent):
        flip_member_state_unlocked(mpath, "myrepo", "ready")

    data = read_central_manifest(mpath)
    member = data["members"][0]
    assert member["provision_state"] == "ready"
    assert member["tasks"] == TASKS_BLOB


def test_enter_member_activation_preserves_tasks_key(tmp_path: Path) -> None:
    """enter_member's _mark_activated read-mutate-write (the exact 'activated' key
    precedent this unknown is modeled on) must not silently drop `tasks`."""
    from camp.provision.activation import enter_member
    from camp.group.manifest import read_central_manifest, manifest_path_for

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    env = {"CAMP_STATE_DIR": str(tmp_path / "camp")}

    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True)
    mpath = manifest_path_for(group_name, slug, env=env)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "group": group_name,
                "slug": slug,
                "branch": f"worktree-{slug}",
                "members": [
                    {
                        "name": member_name,
                        "repo_root": "/tmp/fake-repo",
                        "worktree_path": str(wt_path),
                        "provision_state": "ready",
                        "tasks": TASKS_BLOB,
                    }
                ],
            }
        )
    )

    group = {
        "group": {"name": group_name},
        "members": [{"name": member_name, "repo_root": "/tmp/fake-repo"}],
        "branch_pattern": "worktree-{slug}",
        "shared_vaults": [],
    }

    enter_member(group, slug, member_name, env=env)

    data = read_central_manifest(mpath)
    member = data["members"][0]
    assert member["activated"] is True
    assert member["tasks"] == TASKS_BLOB
