"""`portage eval-gate` inside the sandbox: it stops and tells the operator to run it from a terminal.

The detector reads what the sandbox does to Outpost's credential folders (a tmpfs mounted over each
on Linux, a refused read on macOS), never an environment variable or an argument a session sets.
Every test drives the real detector or the real `dispatch.main` against a throwaway home and a
fabricated mountinfo.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.cli import dispatch
from portage.eval_gate import sandbox

FOLDERS = ("operator", "registration", "sessions", "db")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".local" / "state" / "outpost" / "db").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(sandbox, "account_home", lambda: h)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("SANDBOX_RUNTIME", raising=False)
    return h


def _mountinfo(tmp_path, monkeypatch, *mounts: tuple[str, str]) -> None:
    lines = [f"{i} 1 0:{i} / {mp} rw,nosuid - {fstype} {fstype} rw" for i, (mp, fstype) in enumerate(mounts, 40)]
    p = tmp_path / "mountinfo"
    p.write_text("\n".join(["1 0 8:1 / / rw - xfs /dev/sda1 rw", *lines]) + "\n")
    monkeypatch.setattr(sandbox, "MOUNTINFO", p)


def _outpost(home: Path, folder: str) -> str:
    return str(home / ".local" / "state" / "outpost" / folder)


@pytest.mark.parametrize("folder", FOLDERS)
def test_a_tmpfs_over_any_outpost_credential_folder_reads_as_sandboxed(home, tmp_path, monkeypatch, folder):
    _mountinfo(tmp_path, monkeypatch, (_outpost(home, folder), "tmpfs"))
    assert sandbox.is_sandboxed() is True


def test_mountinfo_with_no_mask_over_outpost_reads_as_not_sandboxed(home, tmp_path, monkeypatch):
    _mountinfo(tmp_path, monkeypatch, (str(home / "elsewhere"), "tmpfs"), (_outpost(home, "operator"), "xfs"))
    assert sandbox.is_sandboxed() is False


def test_a_mask_over_a_folder_with_an_escaped_space_in_the_home_reads_as_sandboxed(tmp_path, monkeypatch):
    h = tmp_path / "my home"
    (h / ".local" / "state" / "outpost" / "db").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(sandbox, "account_home", lambda: h)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    _mountinfo(tmp_path, monkeypatch, (_outpost(h, "sessions").replace(" ", "\\040"), "tmpfs"))
    assert sandbox.is_sandboxed() is True


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 folder")
def test_a_credential_folder_the_sandbox_refuses_to_read_reads_as_sandboxed(home, tmp_path, monkeypatch):
    _mountinfo(tmp_path, monkeypatch)
    folder = Path(_outpost(home, "operator"))
    folder.mkdir()
    folder.chmod(0)
    try:
        assert sandbox.is_sandboxed() is True
    finally:
        folder.chmod(0o700)


def test_a_readable_credential_folder_reads_as_not_sandboxed(home, tmp_path, monkeypatch):
    _mountinfo(tmp_path, monkeypatch)
    Path(_outpost(home, "operator")).mkdir()
    assert sandbox.is_sandboxed() is False


def test_the_marker_variable_a_session_controls_does_not_change_the_answer(home, tmp_path, monkeypatch):
    _mountinfo(tmp_path, monkeypatch)
    monkeypatch.setenv("SANDBOX_RUNTIME", "1")
    assert sandbox.is_sandboxed() is False
    _mountinfo(tmp_path, monkeypatch, (_outpost(home, "db"), "tmpfs"))
    monkeypatch.delenv("SANDBOX_RUNTIME")
    assert sandbox.is_sandboxed() is True


@pytest.fixture
def symlinked_home(tmp_path, monkeypatch):
    """An account home reached through a symlink (/home -> /var/home): the password database names the link."""
    real = tmp_path / "var" / "home" / "u"
    (real / ".local" / "state" / "outpost" / "db").mkdir(parents=True)
    (tmp_path / "home").symlink_to(tmp_path / "var" / "home")
    reached = tmp_path / "home" / "u"
    monkeypatch.setenv("HOME", str(reached))
    monkeypatch.setattr(sandbox, "account_home", lambda: reached)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    return reached, real


def test_a_mask_listed_at_the_resolved_path_of_a_symlinked_home_reads_as_sandboxed(
    symlinked_home, tmp_path, monkeypatch
):
    reached, real = symlinked_home
    _mountinfo(tmp_path, monkeypatch, (_outpost(real, "db"), "tmpfs"))
    assert sandbox.is_sandboxed() is True


def test_a_mask_listed_at_the_raw_path_of_a_symlinked_home_reads_as_sandboxed(symlinked_home, tmp_path, monkeypatch):
    reached, real = symlinked_home
    _mountinfo(tmp_path, monkeypatch, (_outpost(reached, "db"), "tmpfs"))
    assert sandbox.is_sandboxed() is True


def test_a_symlinked_home_with_no_mask_reads_as_not_sandboxed(symlinked_home, tmp_path, monkeypatch):
    reached, real = symlinked_home
    _mountinfo(tmp_path, monkeypatch, (_outpost(real, "db"), "xfs"), (str(real / "elsewhere"), "tmpfs"))
    assert sandbox.is_sandboxed() is False


def _env_folders(tmp_path: Path) -> Path:
    elsewhere = tmp_path / "elsewhere"
    for name in FOLDERS:
        (elsewhere / name).mkdir(parents=True)
    return elsewhere


def test_an_outpost_state_dir_a_session_sets_cannot_move_the_probed_folders(home, tmp_path, monkeypatch):
    _mountinfo(tmp_path, monkeypatch, (_outpost(home, "sessions"), "tmpfs"))
    monkeypatch.setenv("OUTPOST_STATE_DIR", str(_env_folders(tmp_path)))
    assert sandbox.is_sandboxed() is True


def test_an_xdg_state_home_a_session_sets_cannot_move_the_probed_folders(home, tmp_path, monkeypatch):
    _mountinfo(tmp_path, monkeypatch, (_outpost(home, "sessions"), "tmpfs"))
    xdg = tmp_path / "xdg"
    (xdg / "outpost").mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    assert sandbox.is_sandboxed() is True


def test_a_home_with_none_of_the_credential_folders_fails_closed_as_sandboxed(home, tmp_path, monkeypatch):
    _mountinfo(tmp_path, monkeypatch)
    Path(_outpost(home, "db")).rmdir()
    assert sandbox.is_sandboxed() is True


def test_a_masked_folder_is_found_when_the_environment_points_elsewhere_and_the_real_one_is_readable(
    home, tmp_path, monkeypatch
):
    _mountinfo(tmp_path, monkeypatch)
    monkeypatch.setenv("OUTPOST_STATE_DIR", str(_env_folders(tmp_path)))
    assert sandbox.is_sandboxed() is False


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "c"], check=True)
    return repo


def test_a_sandboxed_run_stops_and_tells_the_operator_to_use_a_terminal(home, tmp_path, monkeypatch, capsys):
    _mountinfo(tmp_path, monkeypatch, (_outpost(home, "sessions"), "tmpfs"))
    state = tmp_path / "state"
    state.mkdir(mode=0o700)

    code = dispatch.main(["eval-gate", "run", "--state", str(state), "--case", "c"])

    out = capsys.readouterr()
    assert code == 3
    assert "eval-gate: sandboxed" in out.out
    assert "terminal" in out.out
    assert not (state / "results").exists()


def test_a_sandboxed_plan_for_a_gate_that_is_on_stops_before_planning(home, tmp_path, monkeypatch, capsys):
    _mountinfo(tmp_path, monkeypatch, (_outpost(home, "sessions"), "tmpfs"))
    repo = _repo(tmp_path)

    code = dispatch.main(["eval-gate", "plan", "--repo", str(repo), "--opt-in"])

    out = capsys.readouterr().out
    assert code == 3
    assert "eval-gate: sandboxed" in out
    assert "terminal" in out
    assert "state:" not in out


def test_the_same_plan_outside_the_sandbox_is_planned(home, tmp_path, monkeypatch, capsys):
    _mountinfo(tmp_path, monkeypatch)
    repo = _repo(tmp_path)

    code = dispatch.main(["eval-gate", "plan", "--repo", str(repo), "--opt-in"])

    assert code == 0
    assert "state:" in capsys.readouterr().out


def test_a_sandboxed_plan_for_a_gate_that_is_off_still_plans_nothing_and_exits_zero(home, tmp_path, monkeypatch, capsys):
    _mountinfo(tmp_path, monkeypatch, (_outpost(home, "sessions"), "tmpfs"))
    repo = _repo(tmp_path)

    code = dispatch.main(["eval-gate", "plan", "--repo", str(repo)])

    out = capsys.readouterr().out
    assert code == 0
    assert "status: gate-off" in out
