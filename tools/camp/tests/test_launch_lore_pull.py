"""Tests for camp.launch.lore_pull — the best-effort pre-launch vault refresh.

Test contract:
- The argv is exactly `lore sync --pull-only`: read-only, every vault. No
  commit/push/merge verb and no `--vault` narrowing can appear, ever.
- The child is spawned as a process-GROUP leader (start_new_session=True) so a
  timeout can signal the group; `lore` has no signal handling of its own and a
  bare SIGTERM to its pid would orphan its `git` child.
- A timeout SIGTERMs the child's process group, then SIGKILLs it after a bounded
  grace, and reports "timed-out".
- Every failure mode — lore absent from PATH, a spawn that cannot start, a
  nonzero exit, a timeout — returns an outcome token and NEVER raises. This
  step is best-effort; it may not be able to fail a launch.
- launch_session calls it exactly once, before the tmux spawn, and a failing
  pull still launches the session.
- A launch that refuses (no tmux, trust refusal) pulls nothing.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from .test_launch_session import _launch, rig  # noqa: F401,E402  (shared fixture)


class FakeChild:
    """Stand-in for the spawned `lore` process."""

    def __init__(self, *, returncode=0, timeout_once=False, pid=4242):
        self.pid = pid
        self.returncode = returncode
        self._timeout_once = timeout_once
        self.communicate_calls: list[float | None] = []

    def communicate(self, timeout=None):
        self.communicate_calls.append(timeout)
        if self._timeout_once:
            self._timeout_once = False
            raise subprocess.TimeoutExpired(cmd="lore", timeout=timeout)
        return ("", "")


class Spawner:
    """Captures the one Popen the module is allowed to make."""

    def __init__(self, child=None, raises=None):
        self.child = child if child is not None else FakeChild()
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})
        if self.raises is not None:
            raise self.raises
        return self.child


@pytest.fixture
def pull(monkeypatch):
    """Wire lore_pull's collaborators to fakes; hand back the knobs."""
    import camp.launch.lore_pull as lore_pull

    state = {
        "module": lore_pull,
        "which": "/usr/local/bin/lore",
        "spawner": Spawner(),
        "signals": [],
    }

    monkeypatch.setattr(lore_pull.shutil, "which", lambda binary: state["which"])
    monkeypatch.setattr(lore_pull.subprocess, "Popen", lambda *a, **k: state["spawner"](*a, **k))
    monkeypatch.setattr(lore_pull.os, "killpg", lambda pid, sig: state["signals"].append((pid, sig)))
    monkeypatch.setattr(lore_pull.time, "sleep", lambda seconds: None)
    return state


def _run(pull, **kwargs):
    return pull["module"].pull_lore(**kwargs)


class TestTheCommandIsReadOnly:
    def test_argv_is_exactly_lore_sync_pull_only(self, pull):
        _run(pull)
        assert pull["spawner"].calls[0]["argv"] == ["lore", "sync", "--pull-only"]

    def test_no_write_verb_and_no_vault_narrowing_can_appear(self, pull):
        _run(pull)
        argv = pull["spawner"].calls[0]["argv"]
        for forbidden in ("commit", "push", "record", "flush", "--vault", "-m", "--message"):
            assert forbidden not in argv

    def test_the_child_is_a_process_group_leader(self, pull):
        _run(pull)
        assert pull["spawner"].calls[0]["kwargs"]["start_new_session"] is True


class TestOutcomes:
    def test_a_clean_run_reports_pulled(self, pull):
        assert _run(pull) == "pulled"

    def test_lore_absent_from_path_spawns_nothing_and_reports_skipped(self, pull):
        pull["which"] = None
        assert _run(pull) == "skipped"
        assert pull["spawner"].calls == []

    def test_a_nonzero_exit_reports_failed_and_never_raises(self, pull):
        pull["spawner"] = Spawner(child=FakeChild(returncode=1))
        assert _run(pull) == "failed"

    def test_a_spawn_that_cannot_start_reports_failed_and_never_raises(self, pull):
        pull["spawner"] = Spawner(raises=OSError("boom"))
        assert _run(pull) == "failed"


class TestTimeoutSignalsTheGroup:
    def test_a_timeout_reports_timed_out(self, pull):
        pull["spawner"] = Spawner(child=FakeChild(timeout_once=True))
        assert _run(pull) == "timed-out"

    def test_a_timeout_sigterms_then_sigkills_the_process_group(self, pull):
        child = FakeChild(timeout_once=True, pid=4242)
        pull["spawner"] = Spawner(child=child)
        _run(pull)
        assert pull["signals"] == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]

    def test_the_wait_is_bounded_by_the_caller_supplied_timeout(self, pull):
        _run(pull, timeout=7)
        assert pull["spawner"].child.communicate_calls[0] == 7


class TestTheLaunchEngineCallsIt:
    def test_the_pull_happens_exactly_once_before_the_tmux_spawn(self, rig, monkeypatch):  # noqa: F811  (shared fixture parameter)
        order: list[str] = []
        monkeypatch.setattr(
            rig["module"], "pull_lore", lambda **kwargs: order.append("pull") or "pulled"
        )
        spawn = rig["spawn"]
        original = spawn.__call__

        def recording(argv, **kwargs):
            order.append("spawn")
            return original(argv, **kwargs)

        rig["spawn"] = recording
        _launch(rig)
        assert order == ["pull", "spawn"]

    def test_a_failing_pull_still_launches_the_session(self, rig, monkeypatch):  # noqa: F811  (shared fixture parameter)
        monkeypatch.setattr(rig["module"], "pull_lore", lambda **kwargs: "timed-out")
        result = _launch(rig)
        assert result.session_id
        assert len(rig["spawn"].calls) == 1

    def test_a_pull_that_raises_still_launches_the_session(self, rig, monkeypatch):  # noqa: F811  (shared fixture parameter)
        def boom(**kwargs):
            raise RuntimeError("unreachable remote")

        monkeypatch.setattr(rig["module"], "pull_lore", boom)
        result = _launch(rig)
        assert result.session_id
        assert len(rig["spawn"].calls) == 1

    def test_a_refused_launch_pulls_nothing(self, rig, monkeypatch):  # noqa: F811  (shared fixture parameter)
        calls: list[str] = []
        monkeypatch.setattr(
            rig["module"], "pull_lore", lambda **kwargs: calls.append("pull") or "pulled"
        )
        rig["which"] = None  # tmux absent → refusal
        with pytest.raises(rig["module"].LaunchError):
            _launch(rig)
        assert calls == []
