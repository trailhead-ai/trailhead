"""Tests for footprint_guard.py — the mechanical write-scope gate for the
whole-change simplify phase.

CLI contract (pinned in the parent plan's Delta design, consumed by slices 3
and 4): three positional args, in order — base SHA, pre-simplify SHA,
post-simplify ref. Footprint = files touched in `base..pre-simplify`. The
guard also folds in any *uncommitted* working-tree drift (staged, unstaged,
untracked) relative to the current state, since the simplifier's own charter
runs this guard before committing (its "post-simplify ref" may equal
pre-simplify with the real delta still sitting in the working tree).

Exit-code contract:
  0 → clean (every post-simplify-touched file is inside the footprint)
  1 → violation (offending paths printed to stdout)
  2 → fail-closed error (bad SHA, not a repo — never exits 0 uncertified)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GUARD = REPO_ROOT / "plugins" / "craft" / "scripts" / "footprint_guard.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r


def _run_guard(repo: Path, base: str, pre: str, post_ref: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD), base, pre, post_ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _rev(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("a\n")
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_clean_pass_exits_0(tmp_path: Path):
    repo = _init_repo(tmp_path)
    base = _rev(repo)
    (repo / "a.txt").write_text("a changed by slice\n")
    _git(repo, "commit", "-aqm", "pre-simplify touches a.txt")
    pre = _rev(repo)
    (repo / "a.txt").write_text("a simplified\n")
    _git(repo, "commit", "-aqm", "simplify touches a.txt only")
    post = _rev(repo)
    r = _run_guard(repo, base, pre, post)
    assert r.returncode == 0, r.stdout + r.stderr


def test_out_of_footprint_edit_exits_1_and_names_path(tmp_path: Path):
    repo = _init_repo(tmp_path)
    base = _rev(repo)
    (repo / "a.txt").write_text("a changed by slice\n")
    _git(repo, "commit", "-aqm", "pre-simplify touches a.txt")
    pre = _rev(repo)
    (repo / "b.txt").write_text("b touched by simplifier — out of footprint\n")
    _git(repo, "commit", "-aqm", "simplify touches b.txt too")
    post = _rev(repo)
    r = _run_guard(repo, base, pre, post)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "b.txt" in r.stdout


def test_new_untracked_file_outside_footprint_exits_1(tmp_path: Path):
    repo = _init_repo(tmp_path)
    base = _rev(repo)
    (repo / "a.txt").write_text("a changed by slice\n")
    _git(repo, "commit", "-aqm", "pre-simplify touches a.txt")
    pre = _rev(repo)
    (repo / "c.txt").write_text("new file the simplifier created, not yet committed\n")
    r = _run_guard(repo, base, pre, pre)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "c.txt" in r.stdout


def test_bad_sha_exits_2(tmp_path: Path):
    repo = _init_repo(tmp_path)
    base = _rev(repo)
    r = _run_guard(repo, base, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "HEAD")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "footprint-guard" in r.stderr


def test_non_repo_dir_exits_2(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    r = subprocess.run(
        [sys.executable, str(GUARD), "HEAD", "HEAD", "HEAD"],
        cwd=plain,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2, r.stdout + r.stderr
    assert "footprint-guard" in r.stderr
