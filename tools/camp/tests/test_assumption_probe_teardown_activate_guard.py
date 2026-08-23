"""ASSUMPTION PROBE — ephemeral, delete before merge.

Resolves the Known Unknown blocking
task/non-blocking-activation-with-an-os-released-concurrency-guard:
"Interaction between the activate-phase concurrency guard and teardown."

Models the future per-member concurrency guard as a flocked lockfile living
OUTSIDE ws_dir, member-scoped, following the exact discipline
`lock_path_for` already uses for the slug lock (see
tools/camp/plugins/camp/camp/group/manifest.py:139-159). No such guard exists
in the codebase yet — this probe simulates one to answer two questions before
the slice builds the real thing:

1. Does `reconcile_break` (the function behind `camp remove`) BLOCK for the
   duration a detached activate-phase task run would hold that guard?
2. Does `reconcile_break` leave that guard's lockfile behind (orphaned) once
   it has fully torn the slug down?

Cleanup: delete this whole file. It duplicates no permanent test — the real
behavioral tests belong in test_activation.py per the slice's Files list.
"""

from __future__ import annotations

import fcntl
import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_STUB_SOURCE = Path(__file__).resolve().parent / "_harness_stub.py"
_stub_spec = importlib.util.spec_from_file_location(
    "camp_tests_harness_stub_teardown_guard_probe", _STUB_SOURCE
)
assert _stub_spec and _stub_spec.loader, _STUB_SOURCE
_stub = importlib.util.module_from_spec(_stub_spec)
sys.modules[_stub_spec.name] = _stub
_stub_spec.loader.exec_module(_stub)


def _init_git_repo(path: Path) -> None:
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"], check=True, capture_output=True
    )
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True
    )


def _camp_state_env(tmp_path: Path) -> dict[str, str]:
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root), **_stub.harness_env(tmp_path)}


def _member_guard_path(ws_dir: Path, member_name: str) -> Path:
    """The candidate per-member guard lockfile path.

    Mirrors lock_path_for's discipline exactly: a SIBLING of ws_dir (outside
    the directory reconcile_break rmtree's), keyed by slug AND member so two
    different members' guards never collide.
    """
    return ws_dir.parent / f"{ws_dir.name}.{member_name}.activate.lock"


@pytest.fixture()
def probe_group(tmp_path: Path, monkeypatch):
    import camp.provision.provision as provision

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)

    repo_a = tmp_path / "repo_a"
    _init_git_repo(repo_a)
    group = {
        "group": {"name": "teardownguard"},
        "members": [
            {"name": "repo_a", "repo_root": str(repo_a), "bootstrap": [], "base": "origin/main"},
        ],
        "branch_pattern": "worktree-{slug}",
    }
    env = _camp_state_env(tmp_path)
    return {"group": group, "repo_a": repo_a, "env": env}


def _provision(g, slug):
    from camp.provision.provision import bring_up_workspace
    from camp.provision.lifecycle import cmd_setup_group

    bring_up_workspace(g["group"], slug, env=g["env"])
    cmd_setup_group(g["group"], slug, env=g["env"])


class TestTeardownVsActivateGuard:
    def test_reconcile_break_does_not_block_on_a_held_member_guard(self, probe_group):
        """A held per-member guard lockfile (simulating an in-flight detached
        activate-phase task run) must NOT stall `camp remove` for its duration.

        This is the composability half of the unknown: reconcile_break only
        ever acquires the SLUG reconcile lock, never the per-member guard, so
        it must return long before the guard is released.
        """
        from camp.group.manifest import manifest_path_for, workspace_dir
        from camp.provision.reconcile import reconcile_break

        g = probe_group
        slug = "feat-guard-nonblock"
        _provision(g, slug)

        ws_dir = workspace_dir("teardownguard", slug, env=g["env"])
        mpath = manifest_path_for("teardownguard", slug, env=g["env"])
        assert mpath.is_file()

        guard_path = _member_guard_path(ws_dir, "repo_a")
        guard_path.parent.mkdir(parents=True, exist_ok=True)

        held = threading.Event()
        release = threading.Event()

        def hold_guard():
            fd = open(str(guard_path), "w")
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
            held.set()
            release.wait(timeout=5)
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()

        t = threading.Thread(target=hold_guard)
        t.start()
        assert held.wait(timeout=5), "background thread never acquired the guard lock"

        start = time.monotonic()
        try:
            reconcile_break(g["group"], slug, env=g["env"], force=True)
        finally:
            release.set()
            t.join(timeout=5)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, (
            f"reconcile_break took {elapsed:.2f}s while a per-member guard was held — "
            "teardown appears to block on it"
        )
        assert not mpath.is_file(), "reconcile_break must have fully removed the manifest"

    def test_reconcile_break_leaves_the_member_guard_lockfile_orphaned(self, probe_group):
        """After a FULL teardown, the per-member guard lockfile (living outside
        ws_dir, exactly like the slug lock) is left on disk — reconcile_break
        has no code path that knows about it and cannot reap what it does not
        name.

        This is the orphan half of the unknown: unlike the slug lock (which
        reconcile_break explicitly reaps via reap_lock_unlocked once the slug
        is fully torn down), a member guard introduced by the activation slice
        needs its OWN reap call wired into reconcile_break — it will not be
        cleaned up for free.
        """
        from camp.group.manifest import manifest_path_for, workspace_dir
        from camp.provision.reconcile import reconcile_break

        g = probe_group
        slug = "feat-guard-orphan"
        _provision(g, slug)

        ws_dir = workspace_dir("teardownguard", slug, env=g["env"])
        mpath = manifest_path_for("teardownguard", slug, env=g["env"])

        guard_path = _member_guard_path(ws_dir, "repo_a")
        guard_path.parent.mkdir(parents=True, exist_ok=True)
        # Simulate a guard lockfile left behind by a since-completed detached
        # activate-phase run (created, flocked briefly, released — the normal
        # lifecycle for a non-blocking guard that is never explicitly reaped).
        guard_path.write_text("")

        reconcile_break(g["group"], slug, env=g["env"], force=True)

        assert not mpath.is_file(), "sanity: teardown must have fully removed the manifest"
        assert not ws_dir.exists(), "sanity: teardown must have removed the workspace dir"
        assert guard_path.exists(), (
            "EXPECTED (documents the gap): reconcile_break does not reap the per-member "
            "guard lockfile today. If this assertion ever fails, the executor has already "
            "wired guard-reaping into teardown and this probe (and its Known Unknown) is moot."
        )
