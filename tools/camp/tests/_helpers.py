"""Shared helpers for camp's test suite.

Helpers here are the ones several test modules need identically. A helper that
only one module needs stays in that module; a helper whose callers genuinely
disagree about its behaviour stays split rather than growing a flag per caller.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def init_git_repo(path: Path, *, origin: bool = False) -> None:
    """Create a git repo at *path* with one commit on `main`.

    Commits are made with `--no-gpg-sign` and a fixed identity so the suite does
    not depend on the developer's git configuration.

    With *origin* true the repo additionally gains an `origin` remote pointing at
    itself, fetched so remote-tracking refs exist. Callers that resolve a base
    like `origin/main`, or that exercise fetch, need it; callers that do not must
    leave it off, because its presence changes what those callers observe.
    """
    path.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], check=True, capture_output=True)

    git("init", "-b", "main", str(path))
    git("-C", str(path), "config", "user.email", "test@test.com")
    git("-C", str(path), "config", "user.name", "Test")
    (path / "README.md").write_text("# test\n")
    git("-C", str(path), "add", "README.md")
    git("-C", str(path), "commit", "-m", "init", "--no-gpg-sign")

    if origin:
        git("-C", str(path), "remote", "add", "origin", str(path))
        git("-C", str(path), "fetch", "origin", "--quiet")


def camp_state_env(tmp_path: Path) -> dict[str, str]:
    """Return an env override pointing `CAMP_STATE_DIR` inside *tmp_path*.

    The directory is created, so a caller can write to it without a further
    mkdir. Callers needing more of the environment merge this into their own
    mapping rather than this helper growing their cases.
    """
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root)}
