"""Tests for the harness-process seam: finding the session's harness and its identity."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from trailhead.harness import harness_process, session_key
from trailhead.harness.process import _default_runner, harness_cwd, parse_linux_stat, parse_lstart
from trailhead.tests._harness_process_support import make_fake_harness, run_under_fake_harness

REPO_ROOT = Path(__file__).resolve().parents[2]

_CHILD_PY = """\
import sys
sys.path.insert(0, {root!r})
from trailhead.harness import harness_process, session_key
print(*(harness_process() or ("None",)))
print(session_key())
"""


def _child_cmd(tmp_path: Path) -> str:
    script = tmp_path / "child.py"
    script.write_text(_CHILD_PY.format(root=str(REPO_ROOT)))
    return f"{sys.executable} {script}"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")
class TestRealWalk:
    def test_pid_and_start_time_match_fake_through_sh_intermediate(self, tmp_path: Path) -> None:
        launcher = make_fake_harness(tmp_path)
        # $PPID of the intermediate sh is the fake; read its stat while it is alive.
        script = f"{_child_cmd(tmp_path)}; cat /proc/$PPID/stat"
        done, pid = run_under_fake_harness(launcher, script)
        assert done.returncode == 0, done.stderr
        lines = done.stdout.splitlines()
        got_pid, got_start = map(int, lines[0].split())
        stat = lines[2]
        assert got_pid == pid
        assert got_start == int(stat[stat.rindex(")") + 2 :].split()[19])

    def test_session_key_matches_outpost_shape(self, tmp_path: Path) -> None:
        launcher = make_fake_harness(tmp_path)
        done, pid = run_under_fake_harness(launcher, _child_cmd(tmp_path))
        key = done.stdout.splitlines()[1]
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", key)
        assert key.startswith(f"ws-{pid}-")

    def test_plain_process_gets_none(self, tmp_path: Path) -> None:
        out = subprocess.run(_child_cmd(tmp_path).split(), capture_output=True, text=True)
        assert out.stdout.splitlines() == ["None", "None"], out.stderr

    def test_claude_named_process_with_wrong_exe_path_gets_none(self, tmp_path: Path) -> None:
        launcher = make_fake_harness(tmp_path, versions_dir="elsewhere/bin")
        done, _ = run_under_fake_harness(launcher, _child_cmd(tmp_path))
        assert done.stdout.splitlines() == ["None", "None"], done.stderr


class TestWalkOverFakeProc:
    def _proc(self, root: Path, pid: int, ppid: int, comm: str, exe: str | None) -> None:
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} " + " ".join(["0"] * 17) + " 4242 0\n")
        (d / "comm").write_text(comm + "\n")
        if exe is not None:
            (d / "exe").symlink_to(exe)

    def test_first_recognised_ancestor_wins_when_nested(self, tmp_path: Path) -> None:
        self._proc(tmp_path, 10, 1, "claude", "/h/.local/share/claude/versions/1.0")
        self._proc(tmp_path, 20, 10, "claude", "/h/.local/share/claude/versions/1.0")
        self._proc(tmp_path, 30, 20, "sh", "/bin/dash")
        assert harness_process(start_pid=30, proc_root=tmp_path, system="Linux") == (20, 4242)

    def test_vanished_ancestor_entry_gives_none(self, tmp_path: Path) -> None:
        self._proc(tmp_path, 30, 20, "sh", "/bin/dash")  # pid 20 has no /proc entry
        assert harness_process(start_pid=30, proc_root=tmp_path, system="Linux") is None

    def test_unreadable_exe_gives_none(self, tmp_path: Path) -> None:
        self._proc(tmp_path, 20, 1, "claude", None)  # exe link missing: unreadable
        self._proc(tmp_path, 30, 20, "sh", "/bin/dash")
        assert harness_process(start_pid=30, proc_root=tmp_path, system="Linux") is None

    def test_session_key_is_none_without_harness(self, tmp_path: Path) -> None:
        self._proc(tmp_path, 30, 1, "sh", "/bin/dash")
        assert session_key(start_pid=30, proc_root=tmp_path, system="Linux") is None

    def test_session_key_from_pid_and_start(self, tmp_path: Path) -> None:
        self._proc(tmp_path, 20, 1, "claude", "/h/claude/versions/2")
        self._proc(tmp_path, 30, 20, "sh", "/bin/dash")
        assert session_key(start_pid=30, proc_root=tmp_path, system="Linux") == "ws-20-4242"


class TestHarnessCwd:
    """The working folder of the harness process: where Outpost finds the session's workspace."""

    def _proc(self, root: Path, pid: int, ppid: int, comm: str, exe: str, cwd: str | None) -> None:
        TestWalkOverFakeProc._proc(self, root, pid, ppid, comm, exe)  # type: ignore[arg-type]
        if cwd is not None:
            (root / str(pid) / "cwd").symlink_to(cwd)

    def test_it_is_the_harness_s_folder_not_the_asking_process_s(self, tmp_path: Path) -> None:
        self._proc(tmp_path, 20, 1, "claude", "/h/claude/versions/2", "/ws/one")
        self._proc(tmp_path, 30, 20, "sh", "/bin/dash", "/ws/one/deeper")
        assert harness_cwd(start_pid=30, proc_root=tmp_path, system="Linux") == Path("/ws/one")

    def test_it_follows_the_harness_when_the_harness_changes_folder(self, tmp_path: Path) -> None:
        self._proc(tmp_path, 20, 1, "claude", "/h/claude/versions/2", "/ws/two")
        self._proc(tmp_path, 30, 20, "sh", "/bin/dash", "/ws/one")
        assert harness_cwd(start_pid=30, proc_root=tmp_path, system="Linux") == Path("/ws/two")

    def test_no_harness_gives_none(self, tmp_path: Path) -> None:
        self._proc(tmp_path, 30, 1, "sh", "/bin/dash", "/ws/one")
        assert harness_cwd(start_pid=30, proc_root=tmp_path, system="Linux") is None

    def test_an_unreadable_folder_gives_none(self, tmp_path: Path) -> None:
        self._proc(tmp_path, 20, 1, "claude", "/h/claude/versions/2", None)
        self._proc(tmp_path, 30, 20, "sh", "/bin/dash", "/ws/one")
        assert harness_cwd(start_pid=30, proc_root=tmp_path, system="Linux") is None

    def test_on_macos_it_is_read_with_lsof(self, tmp_path: Path) -> None:
        launcher = make_fake_harness(tmp_path)
        tools = tmp_path / "tools"
        tools.mkdir()
        (tools / "lsof").write_text("")
        (tools / "lsof").chmod(0o755)

        def run(args: list[str]) -> str:
            if args[0] == str(tools / "lsof"):
                assert args[1:] == ["-a", "-p", "50", "-d", "cwd", "-Fn"]
                return "p50\nfcwd\nn/ws/mac\n"
            pid = int(args[-1])
            table = {50: ("40", str(launcher)), 60: ("50", "/bin/zsh")}
            ppid, comm = table[pid]
            return _LSTART if "lstart=" in args else f"{ppid} {comm}"

        assert harness_cwd(start_pid=60, system="Darwin", runner=run, tool_dirs=(str(tools),)) == Path("/ws/mac")

    def _mac_runner(self, launcher: Path):
        def run(args: list[str]) -> str:
            if args[0] == "ps":
                table = {50: ("40", str(launcher)), 60: ("50", "/bin/zsh")}
                ppid, comm = table[int(args[-1])]
                return _LSTART if "lstart=" in args else f"{ppid} {comm}"
            return _default_runner(args)

        return run

    def test_on_macos_lsof_is_found_outside_the_shim_s_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        launcher = make_fake_harness(tmp_path)
        sbin = tmp_path / "sbin"
        sbin.mkdir()
        lsof = sbin / "lsof"
        lsof.write_text("#!/bin/sh\nprintf 'p50\\nfcwd\\nn/ws/found\\n'\n")
        lsof.chmod(0o755)
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        assert harness_cwd(
            start_pid=60, system="Darwin", runner=self._mac_runner(launcher), tool_dirs=(str(sbin),)
        ) == Path("/ws/found")

    def test_on_macos_lsof_is_not_taken_from_the_caller_s_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        launcher = make_fake_harness(tmp_path)
        planted = tmp_path / "planted"
        planted.mkdir()
        lsof = planted / "lsof"
        lsof.write_text("#!/bin/sh\nprintf 'p50\\nfcwd\\nn/ws/planted\\n'\n")
        lsof.chmod(0o755)
        monkeypatch.setenv("PATH", f"{planted}:/usr/bin:/bin")
        empty = tmp_path / "empty"
        empty.mkdir()
        assert harness_cwd(
            start_pid=60, system="Darwin", runner=self._mac_runner(launcher), tool_dirs=(str(empty),)
        ) is None

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")
    def test_a_real_harness_process_reports_its_real_folder(self, tmp_path: Path) -> None:
        launcher = make_fake_harness(tmp_path)
        folder = tmp_path / "harness-cwd"
        folder.mkdir()
        script = tmp_path / "child.py"
        script.write_text(
            f"import sys\nsys.path.insert(0, {str(REPO_ROOT)!r})\n"
            "from trailhead.harness.process import harness_cwd\nprint(harness_cwd())\n"
        )
        proc = subprocess.run(
            [str(launcher), "-c", f"/bin/sh -c 'cd /; {sys.executable} {script}'; :"],
            cwd=folder, capture_output=True, text=True,
        )
        assert proc.stdout.strip() == str(folder.resolve()), proc.stderr


class TestLinuxStatParsing:
    def test_comm_with_spaces_and_parens(self) -> None:
        raw = "123 (we ) ird (name) S 77 " + " ".join(["0"] * 17) + " 999 0\n"
        assert parse_linux_stat(raw) == (77, 999)

    def test_plain_comm(self) -> None:
        raw = "5 (claude) R 6 " + " ".join(["0"] * 17) + " 31337 0\n"
        assert parse_linux_stat(raw) == (6, 31337)

    def test_truncated_stat_is_none(self) -> None:
        assert parse_linux_stat("5 (claude) R 6 0\n") is None


# 2026-11-03 12:34:56 in America/New_York (EST, UTC-5) is 17:34:56 UTC.
_LSTART = "Tue Nov  3 12:34:56 2026"
_LSTART_EPOCH_NEW_YORK = 1793727296


class TestMacOS:
    @pytest.fixture(autouse=True)
    def _new_york_local_time(self, monkeypatch: pytest.MonkeyPatch):
        import time

        monkeypatch.setenv("TZ", "America/New_York")
        time.tzset()
        yield
        monkeypatch.undo()
        time.tzset()

    def _runner(self, table: dict[int, tuple[str, str, str]]):
        def run(args: list[str]) -> str:
            pid = int(args[-1])
            if pid not in table:
                raise OSError("no such process")
            ppid, comm, lstart = table[pid]
            return lstart if "lstart=" in args else f"{ppid} {comm}"

        return run

    def test_lstart_parsed_to_epoch_seconds(self) -> None:
        assert parse_lstart(_LSTART) == _LSTART_EPOCH_NEW_YORK

    def test_lstart_garbage_is_none(self) -> None:
        assert parse_lstart("not a date") is None

    def test_walk_via_injected_runner(self, tmp_path: Path) -> None:
        launcher = make_fake_harness(tmp_path)
        table = {50: ("40", str(launcher), _LSTART), 60: ("50", "/bin/zsh", _LSTART)}
        got = harness_process(start_pid=60, system="Darwin", runner=self._runner(table))
        assert got == (50, _LSTART_EPOCH_NEW_YORK)

    def test_runner_failure_is_none(self) -> None:
        assert harness_process(start_pid=60, system="Darwin", runner=self._runner({})) is None

    def test_wrong_path_is_none(self, tmp_path: Path) -> None:
        launcher = make_fake_harness(tmp_path, versions_dir="elsewhere/bin")
        table = {50: ("1", str(launcher), "Tue Nov  3 12:34:56 2026")}
        assert harness_process(start_pid=50, system="Darwin", runner=self._runner(table)) is None
