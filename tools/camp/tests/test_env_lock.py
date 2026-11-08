"""Tests for camp.env.lock: the lock every `camp env attach` by one user on one machine meets on.

Test contract:
- Exclusion across real processes: busy after about the timeout, free once the holder
  releases, acquired mid-wait at a time that depends on when the holder lets go.
- Released when the holder is SIGKILLed; not inherited by children the holder spawned.
- A FIFO planted at the path and a file owned by another user are refused as LockUnavailable
  with a printable reason, leaving the planted file as it was; the same file owned by the
  caller is accepted.
- An unopenable path and a symlink planted at the path raise LockUnavailable and create
  or touch nothing; the lock file is never written, truncated or unlinked.
- The first-try-found-held signal (on_wait) fires once when busy and never when free.
- A KeyboardInterrupt while waiting propagates with nothing held.
"""
from __future__ import annotations

import contextlib
import os
import stat
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.env.lock import LockBusy, LockUnavailable, attach_lock, attach_lock_path  # noqa: E402

_HOLDER = r"""
import os, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from camp.env.lock import attach_lock
path, mode = sys.argv[2], sys.argv[3]
with attach_lock(path, timeout=5):
    print("held", flush=True)
    if mode == "release-on-stdin":
        sys.stdin.readline()
    elif mode == "sleep":
        time.sleep(60)
    elif mode == "spawn":
        plain = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        detached = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        keep = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], close_fds=False)
        print(plain.pid, detached.pid, keep.pid, flush=True)
"""


def _hold(path: Path, mode: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(_PLUGIN_DIR), str(path), mode],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert proc.stdout.readline().strip() == "held"
    return proc


def _release(proc: subprocess.Popen) -> None:
    proc.stdin.write("\n")
    proc.stdin.flush()
    proc.wait(timeout=10)


@pytest.fixture
def lock_file(tmp_path: Path) -> Path:
    return tmp_path / "state" / "env-attach.lock"


def _acquire_now(path: Path) -> None:
    with attach_lock(path, timeout=0):
        pass


def test_a_held_lock_is_busy_after_about_the_timeout_then_free_once_released(lock_file: Path):
    holder = _hold(lock_file, "release-on-stdin")
    try:
        start = time.monotonic()
        with pytest.raises(LockBusy):
            with attach_lock(lock_file, timeout=0.5):
                pass
        elapsed = time.monotonic() - start
        assert 0.5 <= elapsed < 2.5
    finally:
        _release(holder)
    _acquire_now(lock_file)


@pytest.mark.parametrize("release_after", [0.4, 1.2])
def test_a_waiter_gets_the_lock_when_the_holder_releases_mid_wait(lock_file: Path, release_after: float):
    holder = _hold(lock_file, "release-on-stdin")
    releaser_start = time.monotonic()
    import threading
    threading.Timer(release_after, lambda: _release(holder)).start()
    with attach_lock(lock_file, timeout=5):
        waited = time.monotonic() - releaser_start
    assert release_after <= waited < release_after + 1.5


def test_the_lock_is_released_when_the_holder_is_killed(lock_file: Path):
    holder = _hold(lock_file, "sleep")
    holder.send_signal(signal.SIGKILL)
    holder.wait(timeout=10)
    _acquire_now(lock_file)


def test_children_of_a_holder_do_not_keep_the_lock(lock_file: Path):
    holder = _hold(lock_file, "spawn")
    pids = [int(p) for p in holder.stdout.readline().split()]
    holder.wait(timeout=10)
    try:
        for pid in pids:
            os.kill(pid, 0)
        _acquire_now(lock_file)
    finally:
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_on_wait_fires_once_when_found_held_and_not_when_free(lock_file: Path):
    calls: list[int] = []
    with attach_lock(lock_file, timeout=0, on_wait=lambda: calls.append(1)):
        pass
    assert calls == []
    holder = _hold(lock_file, "release-on-stdin")
    try:
        with pytest.raises(LockBusy):
            with attach_lock(lock_file, timeout=0.5, on_wait=lambda: calls.append(1)):
                pass
    finally:
        _release(holder)
    assert calls == [1]


def test_keyboard_interrupt_while_waiting_propagates_and_holds_nothing(lock_file: Path):
    holder = _hold(lock_file, "release-on-stdin")

    def _interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGALRM, _interrupt)
    signal.setitimer(signal.ITIMER_REAL, 0.3)
    try:
        with pytest.raises(KeyboardInterrupt):
            with attach_lock(lock_file, timeout=10):
                pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _release(holder)
    _acquire_now(lock_file)


def test_an_unopenable_path_is_unavailable_with_printable_text_and_creates_nothing(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    blocker = root / "blocker"
    blocker.write_text("not a directory")
    target = blocker / "state" / "env-attach.lock"
    with pytest.raises(LockUnavailable) as err:
        with attach_lock(target, timeout=0):
            pass
    assert err.value.path == target
    assert str(target) in str(err.value)
    assert err.value.reason
    assert sorted(p.name for p in root.iterdir()) == ["blocker"]
    assert blocker.read_text() == "not a directory"


def _plant_symlink(tmp_path: Path) -> tuple[Path, Path]:
    victim = tmp_path / "victim"
    victim.write_bytes(b"precious")
    os.utime(victim, (1_000_000_000, 1_000_000_000))
    link = tmp_path / "env-attach.lock"
    link.symlink_to(victim)
    return link, victim


def test_a_symlink_at_the_lock_path_is_refused_as_unavailable(tmp_path: Path):
    link, _victim = _plant_symlink(tmp_path)
    with pytest.raises(LockUnavailable):
        with attach_lock(link, timeout=0):
            pass


def test_a_symlink_at_the_lock_path_leaves_its_target_untouched(tmp_path: Path):
    link, victim = _plant_symlink(tmp_path)
    before = victim.stat()
    with contextlib.suppress(LockUnavailable):
        with attach_lock(link, timeout=0):
            pass
    after = victim.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert victim.read_bytes() == b"precious"


def test_a_fifo_at_the_lock_path_is_refused_and_left_a_fifo(tmp_path: Path):
    fifo = tmp_path / "env-attach.lock"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(LockUnavailable) as err:
        with attach_lock(fifo, timeout=0):
            pass
    assert err.value.path == fifo
    assert err.value.reason.isprintable() and "regular file" in err.value.reason
    assert str(fifo) in str(err.value)
    assert stat.S_ISFIFO(fifo.lstat().st_mode)


def test_a_lock_file_owned_by_another_user_is_refused_and_the_same_file_owned_by_the_caller_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    lock = tmp_path / "env-attach.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)
    monkeypatch.setattr("camp.env.lock._euid", lambda: os.geteuid() + 1)
    with pytest.raises(LockUnavailable) as err:
        with attach_lock(lock, timeout=0):
            pass
    assert err.value.path == lock
    assert err.value.reason.isprintable() and "another user" in err.value.reason
    monkeypatch.setattr("camp.env.lock._euid", os.geteuid)
    _acquire_now(lock)


def test_a_refused_lock_file_leaves_no_descriptor_open(tmp_path: Path):
    fifo = tmp_path / "env-attach.lock"
    os.mkfifo(fifo, 0o600)
    before = set(os.listdir("/proc/self/fd"))
    with contextlib.suppress(LockUnavailable):
        with attach_lock(fifo, timeout=0):
            pass
    assert set(os.listdir("/proc/self/fd")) == before


def test_the_lock_file_is_never_written_or_removed(lock_file: Path):
    _acquire_now(lock_file)
    assert lock_file.stat().st_mode & 0o777 == 0o600
    assert lock_file.parent.stat().st_mode & 0o777 == 0o700
    first = lock_file.stat()
    witness = os.open(lock_file, os.O_RDONLY)
    try:
        for _ in range(3):
            _acquire_now(lock_file)
        now = os.fstat(witness)
        assert now.st_nlink == 1
        assert os.stat(lock_file).st_ino == now.st_ino == first.st_ino
        assert lock_file.stat().st_size == 0
        assert lock_file.stat().st_mtime_ns == first.st_mtime_ns
    finally:
        os.close(witness)


def test_a_missing_nested_state_directory_is_created_with_the_state_directory_private(tmp_path: Path):
    lock_file = tmp_path / "a" / "b" / "state" / "env-attach.lock"
    old = os.umask(0o022)
    try:
        with attach_lock(lock_file, timeout=1):
            pass
    finally:
        os.umask(old)

    assert lock_file.parent.stat().st_mode & 0o777 == 0o700
    assert lock_file.stat().st_mode & 0o777 == 0o600


def test_the_path_helper_follows_camp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "a"))
    first = attach_lock_path()
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "b"))
    second = attach_lock_path()
    assert first == tmp_path / "a" / "env-attach.lock"
    assert second == tmp_path / "b" / "env-attach.lock"
