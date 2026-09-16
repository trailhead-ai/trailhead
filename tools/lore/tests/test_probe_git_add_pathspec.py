"""EPHEMERAL assumption probe — delete after use.

Resolves the unknown blocking `task/the-loop-s-commit-scope-is-bounded-to-records-and-sites`:
does an explicit `git add -- <pathspec>...` over the vault's record trees + `sites/`
capture deletions and untracked (new) files the same way `git add -A` does, while
leaving `outpost/` and `.lore.lock` completely unstaged?

Not a permanent test — no production code changed. See lore task record for the
real behavioral test contract this probe unblocks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path



def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True
    )


def _make_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    for key, val in (("user.email", "t@e.st"), ("user.name", "Test"), ("commit.gpgsign", "false")):
        _git(path, "config", key, val)
    (path / ".gitignore").write_text("*.lock\n")


def _staged_paths(repo: Path) -> set[str]:
    out = _git(repo, "diff", "--cached", "--name-status").stdout
    paths = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            paths.add(parts[-1])
    return paths


def test_explicit_pathspec_add_captures_deletions_and_untracked(tmp_path):
    repo = tmp_path / "vault"
    _make_repo(repo)

    # Record tree ("task") + sites (free-write zone) + outpost (must stay unstaged).
    (repo / "task").mkdir()
    (repo / "sites").mkdir()
    (repo / "outpost").mkdir()
    (repo / "task" / "keep.md").write_text("keep\n")
    (repo / "task" / "to-delete.md").write_text("bye\n")
    (repo / "sites" / "existing.html").write_text("<html></html>\n")
    (repo / "outpost" / "config.json").write_text("{}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")

    # Mutate: delete a tracked record file, add an untracked record file,
    # add an untracked sites file, touch outpost, and drop a lock sidecar.
    (repo / "task" / "to-delete.md").unlink()
    (repo / "task" / "new-untracked.md").write_text("new\n")
    (repo / "sites" / "new-page.html").write_text("<html>new</html>\n")
    (repo / "outpost" / "config.json").write_text('{"changed": true}\n')
    (repo / ".lore.lock").write_text("locked\n")  # matches *.lock, ignored

    # The candidate replacement: explicit pathspecs over record trees + sites/.
    rc, _, stderr = (lambda cp: (cp.returncode, cp.stdout, cp.stderr))(
        _git(repo, "add", "-A", "--", "task", "sites")
    )
    assert rc == 0, f"git add failed: {stderr}"

    staged = _staged_paths(repo)
    assert staged == {"task/to-delete.md", "task/new-untracked.md", "sites/new-page.html"}, staged

    # outpost/ and the lock sidecar must never be staged.
    status = _git(repo, "status", "--porcelain").stdout
    assert "outpost/config.json" in status  # still dirty, untracked-by-add
    # Confirm it is NOT staged (porcelain 'M' in column 2, not column 1).
    outpost_line = next(line for line in status.splitlines() if "outpost/config.json" in line)
    assert outpost_line[0] != "M" and outpost_line[0] != "A", outpost_line
    assert ".lore.lock" not in staged


def test_bare_git_add_without_all_flag_also_stages_deletions(tmp_path):
    """Confirms whether the historical pre-2.0 `git add <path>` deletion gap
    still matters on the installed git — i.e. whether `-A` is load-bearing
    on the pathspec form or just belt-and-braces on this git version."""
    repo = tmp_path / "vault2"
    _make_repo(repo)
    (repo / "task").mkdir()
    (repo / "task" / "to-delete.md").write_text("bye\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")

    (repo / "task" / "to-delete.md").unlink()

    # Bare `git add -- <pathspec>` (NO -A/--all flag).
    cp = _git(repo, "add", "--", "task")
    assert cp.returncode == 0, cp.stderr

    staged = _staged_paths(repo)
    assert staged == {"task/to-delete.md"}, (
        "bare `git add -- <pathspec>` did NOT stage a deletion on this git "
        f"version; staged={staged}. This means the pathspec form MUST pin "
        "-A/--all to be correct — do not rely on bare `git add --`."
    )
