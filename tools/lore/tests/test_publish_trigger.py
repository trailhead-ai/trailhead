"""Behavioral proof of the write-triggered publish path.

Covers ``lore.cli.publish.request_publish`` and ``warn_stale_publish`` and their
three call sites: ``record create`` (``_cmd_record_create``), ``record update``
(``_cmd_record_update``, both the in-place and the relocation branch), and
``lore flush``'s default closing step (exercised in ``test_flush.py`` instead,
since it needs a multi-vault config to prove the shared-vault inclusion).

``lore session candidate`` is deliberately never wired to this path (it writes
through ``session/store.py``, not the record-command call sites this module
hooks) — proven here by a negative test.

Every test that mocks the spawner runs the CLI IN-PROCESS (``conftest.run_cli``,
via ``dispatch.main`` in this interpreter), so ``monkeypatch.setattr`` on the
``lore.cli.publish`` module object patches the exact module the command's own
``from . import publish as publish_mod`` resolves to. The one exception —
``TestRealSpawn`` — runs a real subprocess and proves the UNMOCKED spawn wiring
actually launches a live worker holding its own lock.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import (
    CLI_PATH,
    make_vault as _make_vault,
    run_cli as _run,
    run_cli_subprocess,
)
from test_record_cli_update import _run_cfg, _two_team_config, _create_routed

import lore.cli.publish as publish_mod

SID = "22222222-3333-4444-5555-666666666666"


def _record_spawner(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(publish_mod, "_spawn_worker", lambda argv: calls.append(list(argv)))
    return calls


def _write_auto_publish_false_config(config_home: Path, vault: Path) -> None:
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    (lore_cfg / "config.json").write_text(
        json.dumps(
            {
                "vaults": [
                    {
                        "name": "default",
                        "scope": "default",
                        "path": str(vault),
                        "auto_publish": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _create(vault, state, *, title, env_extra=None):
    return _run(
        ["record", "create", "--kind", "decision", "--title", title],
        vault=vault, state_dir=state, stdin_text="body\n", env_extra=env_extra,
    )


# ---------------------------------------------------------------------------
# record create — writes the stamp and invokes the spawner
# ---------------------------------------------------------------------------


class TestRecordCreateTriggersPublish:

    def test_create_writes_the_stamp_and_invokes_the_spawner_with_worker_argv(
        self, tmp_path, monkeypatch
    ):
        calls = _record_spawner(monkeypatch)
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        r = _create(vault, state, title="A finding")
        assert r.returncode == 0, r.stderr

        assert publish_mod.request_stamp_path(vault).exists()
        assert len(calls) == 1
        argv = calls[0]
        assert argv[0] == sys.executable
        assert argv[-3:] == ["publish", "--vault", "default"]

    def test_exit_code_and_output_unchanged_by_the_trigger(self, tmp_path, monkeypatch):
        """Comparing a run with the trigger MOCKED (fires, spawns nothing real)
        against a run with the trigger DISABLED (`auto_publish: false`, never
        even stamps) — both must produce byte-identical stdout/stderr/exit code,
        since the trigger runs strictly after the write it must never affect."""
        vault_a, state_a = _make_vault(tmp_path / "a")
        monkeypatch.setattr(publish_mod, "_spawn_worker", lambda argv: None)
        enabled = _create(vault_a, state_a, title="Same Title")
        assert enabled.returncode == 0, enabled.stderr

        vault_b, state_b = _make_vault(tmp_path / "b")
        config_home = tmp_path / "disabled-config"
        _write_auto_publish_false_config(config_home, vault_b)
        disabled_calls = _record_spawner(monkeypatch)
        disabled = _create(
            vault_b, state_b, title="Same Title",
            env_extra={"XDG_CONFIG_HOME": str(config_home)},
        )
        assert disabled.returncode == 0, disabled.stderr

        assert disabled_calls == [], "auto_publish: false must never spawn"
        assert enabled.stdout == disabled.stdout
        assert enabled.stderr == disabled.stderr
        assert enabled.returncode == disabled.returncode

    def test_auto_publish_false_writes_no_stamp_and_the_write_still_succeeds(
        self, tmp_path, monkeypatch
    ):
        calls = _record_spawner(monkeypatch)
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        config_home = tmp_path / "disabled-config"
        _write_auto_publish_false_config(config_home, vault)

        r = _create(vault, state, title="x", env_extra={"XDG_CONFIG_HOME": str(config_home)})
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip(), "the record ID must still print"

        assert calls == []
        assert not publish_mod.request_stamp_path(vault).exists()

    def test_a_raising_spawner_leaves_exit_code_0_and_names_the_vault(
        self, tmp_path, monkeypatch
    ):
        def boom(argv):
            raise OSError("no such file or directory: lore")

        monkeypatch.setattr(publish_mod, "_spawn_worker", boom)
        vault, state = _make_vault(tmp_path)

        r = _create(vault, state, title="x")
        assert r.returncode == 0, r.stderr

        lines = [
            line for line in r.stderr.splitlines()
            if "could not schedule publish" in line
        ]
        assert len(lines) == 1, f"expected exactly one notice; stderr={r.stderr!r}"
        assert "default" in lines[0]

    def test_a_lock_already_held_skips_the_spawn_but_still_stamps(self, tmp_path, monkeypatch):
        calls = _record_spawner(monkeypatch)
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        lp = publish_mod.lock_path(vault)
        lp.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lp, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            r = _create(vault, state, title="x")
            assert r.returncode == 0, r.stderr
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        assert publish_mod.request_stamp_path(vault).exists()
        assert calls == []

    def test_stale_publish_marker_holding_warns_naming_the_vault_and_lore_sync(
        self, tmp_path, monkeypatch
    ):
        _record_spawner(monkeypatch)
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        publish_mod._write_marker(vault, {"outcome": "holding"})

        r = _create(vault, state, title="x")
        assert r.returncode == 0, r.stderr

        lines = [line for line in r.stderr.splitlines() if "did not succeed" in line]
        assert len(lines) == 1, f"expected exactly one notice; stderr={r.stderr!r}"
        assert vault.name in lines[0]
        assert "lore sync" in lines[0]

    def test_no_marker_prints_no_stale_publish_warning(self, tmp_path, monkeypatch):
        _record_spawner(monkeypatch)
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        r = _create(vault, state, title="x")
        assert r.returncode == 0, r.stderr
        assert "did not succeed" not in r.stderr

    def test_running_marker_prints_no_stale_publish_warning(self, tmp_path, monkeypatch):
        _record_spawner(monkeypatch)
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        publish_mod._write_marker(vault, {"outcome": publish_mod.OUTCOME_RUNNING})

        r = _create(vault, state, title="x")
        assert r.returncode == 0, r.stderr
        assert "did not succeed" not in r.stderr

    def test_session_candidate_never_triggers_publish(self, tmp_path, monkeypatch):
        calls = _record_spawner(monkeypatch)
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        r = _run(
            ["session", "candidate", "--session-id", SID, "--kind", "decision", "--phase", "build"],
            vault=vault, state_dir=state, stdin_text="a finding\n",
            env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""},
        )
        assert r.returncode == 0, r.stderr

        assert calls == []
        assert not publish_mod.request_stamp_path(vault).exists()


# ---------------------------------------------------------------------------
# every step the trigger takes must be non-raising — a stamp-write failure,
# a lock-probe failure, or a corrupt marker must never change the exit code
# of the write that already succeeded (AC44).
# ---------------------------------------------------------------------------


class TestTriggerNeverRaisesIntoTheWrite:

    def test_a_raising_stamp_write_leaves_exit_code_0_and_names_the_vault(
        self, tmp_path, monkeypatch
    ):
        _record_spawner(monkeypatch)
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        def boom(vault_root, *, clock=time.time):
            raise OSError("[Errno 28] No space left on device")

        monkeypatch.setattr(publish_mod, "touch_request_stamp", boom)

        r = _create(vault, state, title="x")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip(), "the record ID must still print"

        lines = [
            line for line in r.stderr.splitlines()
            if "could not schedule publish" in line
        ]
        assert len(lines) == 1, f"expected exactly one notice; stderr={r.stderr!r}"
        assert "default" in lines[0]

    def test_a_raising_lock_probe_leaves_exit_code_0_and_names_the_vault(
        self, tmp_path, monkeypatch
    ):
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        def boom(vault_root):
            raise OSError("[Errno 13] Permission denied")

        monkeypatch.setattr(publish_mod, "_lock_held", boom)

        r = _create(vault, state, title="x")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip(), "the record ID must still print"

        lines = [
            line for line in r.stderr.splitlines()
            if "could not schedule publish" in line
        ]
        assert len(lines) == 1, f"expected exactly one notice; stderr={r.stderr!r}"
        assert "default" in lines[0]

    def test_a_corrupt_marker_file_does_not_fail_the_write(self, tmp_path, monkeypatch):
        _record_spawner(monkeypatch)
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        mp = publish_mod.marker_path(vault)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text("42", encoding="utf-8")

        r = _create(vault, state, title="x")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip(), "the record ID must still print"
        assert "could not schedule publish" not in r.stderr

    def test_a_raising_auto_publish_resolution_leaves_exit_code_0_and_names_the_vault(
        self, tmp_path, monkeypatch
    ):
        """`_resolve_vault_entry` (config load + path resolution) must be inside
        the same non-raising guard as the stamp write and spawn — a broken
        config must not fail the write it follows any more than a broken
        stamp write does."""
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        def boom(vault_root):
            raise OSError("[Errno 13] Permission denied reading config.json")

        monkeypatch.setattr(publish_mod, "_resolve_vault_entry", boom)

        r = _create(vault, state, title="x")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip(), "the record ID must still print"

        lines = [
            line for line in r.stderr.splitlines()
            if "could not schedule publish" in line
        ]
        assert len(lines) == 1, f"expected exactly one notice; stderr={r.stderr!r}"

    def test_a_symlinked_marker_path_does_not_fail_the_write(self, tmp_path, monkeypatch):
        """`warn_stale_publish` reads the marker before the write's own trigger
        runs; a symlink planted at the marker path makes ``marker_path``'s
        confinement check raise ``LayerConfinementError`` (not an ``OSError``),
        which ``read_marker``'s own except clause does not catch. That must
        never traceback into a record write."""
        _record_spawner(monkeypatch)
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        mp = publish_mod.marker_path(vault)
        mp.parent.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside-target"
        outside.write_text("not a marker", encoding="utf-8")
        mp.symlink_to(outside)

        r = _create(vault, state, title="x")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip(), "the record ID must still print"
        assert "Traceback" not in r.stderr


# ---------------------------------------------------------------------------
# record update — in-place and relocation both request the DESTINATION vault
# ---------------------------------------------------------------------------


class TestRecordUpdateTriggersPublish:

    def test_in_place_update_requests_publish_for_the_current_vault(self, tmp_path, monkeypatch):
        vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
        rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        calls = _record_spawner(monkeypatch)

        r = _run_cfg(
            ["record", "update", rid, "--status", "superseded"],
            vault=vault_a, state=state, config_home=config_home, stdin_text="",
        )
        assert r.returncode == 0, r.stderr
        assert "moved:" not in r.stdout

        assert publish_mod.request_stamp_path(vault_a).exists()
        assert not publish_mod.request_stamp_path(vault_b).exists()
        assert len(calls) == 1

    def test_relocating_update_requests_publish_for_the_destination_vault(
        self, tmp_path, monkeypatch
    ):
        vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
        rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        calls = _record_spawner(monkeypatch)

        r = _run_cfg(
            ["record", "update", rid, "--team", "beta"],
            vault=vault_a, state=state, config_home=config_home, stdin_text="",
        )
        assert r.returncode == 0, r.stderr
        assert f"moved: {rid} →" in r.stdout

        assert publish_mod.request_stamp_path(vault_b).exists(), (
            "the relocation must request a publish for the DESTINATION vault"
        )
        assert calls, "the relocating update must invoke the spawner"
        assert calls[-1][-1] == "beta", (
            f"the relocation's own request must name the destination vault; calls={calls!r}"
        )


# ---------------------------------------------------------------------------
# worker argv derivation — must not trust sys.argv[0]
# ---------------------------------------------------------------------------


class TestWorkerArgvDerivation:
    """``_worker_argv`` must derive the real ``cli/lore`` entry script from the
    ``lore`` package's own location, not from ``sys.argv[0]`` — which, under
    ``conftest.run_cli``'s in-process dispatch (used by nearly every other test
    in this file), is whatever launched THIS test run, not the CLI."""

    def test_worker_argv_points_at_the_real_cli_script_regardless_of_sys_argv0(
        self, monkeypatch
    ):
        monkeypatch.setattr(sys, "argv", ["/not/the/cli/entrypoint"])

        argv = publish_mod._worker_argv("default")

        assert argv == [sys.executable, str(CLI_PATH), "publish", "--vault", "default"]

    def test_a_missing_cli_script_skips_the_spawn_with_one_stderr_line(
        self, tmp_path, monkeypatch
    ):
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        monkeypatch.setattr(
            publish_mod, "_cli_script_path", lambda: tmp_path / "no-such-cli-script"
        )

        r = _create(vault, state, title="x")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip(), "the record ID must still print"

        lines = [
            line for line in r.stderr.splitlines()
            if "could not schedule publish" in line
        ]
        assert len(lines) == 1, f"expected exactly one notice; stderr={r.stderr!r}"
        assert "default" in lines[0]


class TestPublishDisableFenceAtTheSubprocessBoundary:
    """``LORE_PUBLISH_DISABLE`` — the guard ``conftest``'s autouse fixture sets
    for every test — must stop a REAL, unmocked subprocess from spawning a
    real detached worker, not just the in-process ``_spawn_worker`` patch that
    only reaches ``conftest.run_cli`` calls. Every OTHER test in this module
    proves the in-process half; this one proves the half that actually
    protects ``run_cli_subprocess``-driven suites (search, session-vault-pin,
    record-cli-*) from leaving live workers running past the test."""

    def test_subprocess_record_create_never_leaves_a_worker_holding_the_lock(
        self, tmp_path
    ):
        vault, state = _make_vault(tmp_path)

        r = run_cli_subprocess(
            ["record", "create", "--kind", "decision", "--title", "fenced spawn"],
            vault=vault, state_dir=state, stdin_text="body\n",
        )
        assert r.returncode == 0, r.stderr

        lock = publish_mod.lock_path(vault)
        time.sleep(0.3)
        assert not _lock_is_held(lock), (
            "LORE_PUBLISH_DISABLE must stop the real subprocess from spawning "
            "a worker that holds this vault's publish lock"
        )
        assert publish_mod.request_stamp_path(vault).exists(), (
            "the request stamp must still be written even though the spawn "
            "was fenced off"
        )


# ---------------------------------------------------------------------------
# a real spawn — the one integration test, not mocked
# ---------------------------------------------------------------------------


def _lock_is_held(lock_path: Path) -> bool:
    """Non-blocking probe from the TEST's own process — mirrors `_lock_held`."""
    if not lock_path.exists():
        return False
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _pids_with_open_file(path: Path, *, vault_root: "Path | None" = None) -> "list[int]":
    """PIDs holding *path* open, via ``lsof -t`` — used to find and reap the
    real worker process ``TestRealSpawn`` deliberately leaves running (a
    ``flock`` is a BSD-style lock, invisible to ``fcntl``'s POSIX ``F_GETLK``,
    so ``lsof`` on the lock file itself is the portable way to name the
    holder).

    Falls back to the worker's own publish marker (``publish._write_marker``
    stamps ``pid`` into it before the debounce wait even starts — see
    ``_run_publish``) when ``lsof`` isn't on this host's PATH, so a host
    without it still reaps the worker instead of raising past a live process.
    *vault_root* is required for the fallback (it derives the marker path);
    without it, an unavailable ``lsof`` yields no PIDs rather than raising.
    """
    if shutil.which("lsof") is None:
        if vault_root is None:
            return []
        marker = publish_mod.read_marker(vault_root)
        pid = marker.get("pid") if marker else None
        return [pid] if isinstance(pid, int) else []
    result = subprocess.run(["lsof", "-t", str(path)], capture_output=True, text=True)
    return [int(pid) for pid in result.stdout.split() if pid.strip()]


class TestRealSpawn:

    @pytest.fixture
    def _allow_real_publish_spawn(self):
        """This class's whole point is the real, unmocked spawn — opt back
        into it explicitly against the autouse no-op default."""
        return True

    @pytest.fixture
    def _reap_real_workers(self):
        """Kill the process group of every PID this test registers, so the
        real worker it deliberately spawns (still mid-debounce, since
        `--quiet-for` defaults to 5s) never survives to run real git against
        this test's own `tmp_path` after it has been torn down."""
        pids: list[int] = []
        yield pids
        for pid in pids:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def test_a_real_spawn_leaves_a_worker_process_running(
        self, tmp_path, monkeypatch, _reap_real_workers
    ):
        """The UNMOCKED spawn path: a real `lore record create` subprocess, with
        no injected spawner, must leave a real `lore publish --vault default`
        process alive afterwards — proven by that process holding the worker's
        own lock, which only a live process holding it (still debouncing, since
        `--quiet-for` defaults to 5s) can do."""
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        r = run_cli_subprocess(
            ["record", "create", "--kind", "decision", "--title", "real spawn"],
            vault=vault, state_dir=state, stdin_text="body\n",
        )
        assert r.returncode == 0, r.stderr

        lock = publish_mod.lock_path(vault)
        deadline = time.monotonic() + 5.0
        held = False
        while time.monotonic() < deadline:
            if _lock_is_held(lock):
                held = True
                break
            time.sleep(0.05)
        assert held, (
            "expected a real, unmocked spawn to leave a worker process holding "
            f"its lock at {lock}"
        )
        _reap_real_workers.extend(_pids_with_open_file(lock, vault_root=vault))


class TestPidsWithOpenFileFallback:
    """``_pids_with_open_file`` must reap the worker even on a host with no
    ``lsof`` on PATH, via the marker's own ``pid`` field."""

    def test_falls_back_to_the_marker_pid_when_lsof_is_unavailable(
        self, tmp_path, monkeypatch
    ):
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        monkeypatch.setattr(shutil, "which", lambda name: None)
        publish_mod._write_marker(vault, {"pid": 424242, "outcome": publish_mod.OUTCOME_RUNNING})

        result = _pids_with_open_file(publish_mod.lock_path(vault), vault_root=vault)

        assert result == [424242]

    def test_uses_lsof_when_available_even_if_a_marker_pid_also_exists(
        self, tmp_path, monkeypatch
    ):
        """The two sources must not conflate: with `lsof` on PATH, its answer
        (empty here — no real holder) governs, not the marker's stale `pid`."""
        vault, state = _make_vault(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        publish_mod._write_marker(vault, {"pid": 999999, "outcome": publish_mod.OUTCOME_RUNNING})

        result = _pids_with_open_file(publish_mod.lock_path(vault), vault_root=vault)

        assert result == []
