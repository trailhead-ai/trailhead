"""Shared helpers for camp's test suite.

Helpers here are the ones several test modules need identically. A helper that
only one module needs stays in that module; a helper whose callers genuinely
disagree about its behaviour stays split rather than growing a flag per caller.
"""
from __future__ import annotations

import atexit
import shutil
import subprocess
import tempfile
from pathlib import Path

#: Absolute paths git records inside `.git` when a repo is built, keyed by the
#: file that holds them. Copying a repo to a new location leaves these pointing
#: at the original, so `_clone_template` rewrites exactly these two and nothing
#: else — every other path git stores is relative to the repo and survives a
#: move untouched. Rewriting by allowlist rather than scanning `.git` wholesale
#: keeps the substitution away from the object store and the index, where a
#: blind byte replacement would corrupt content it cannot parse.
_PATH_BEARING_GIT_FILES = ("config", "FETCH_HEAD")

#: One built-from-scratch repo per `origin` variant, reused for the life of the
#: process. Building a repo costs seven git subprocesses — an `init`, two
#: `config`s, an `add`, a `commit` that fsyncs, and for `origin` a `remote add`
#: and a `fetch` — which the ~100 call sites here otherwise pay per test. A
#: session-scoped template turns that into one directory copy plus a two-file
#: rewrite. Under xdist each worker is its own process and so builds its own
#: templates: a handful of builds total, rather than one per test.
_TEMPLATES: dict[bool, Path] = {}


def _template(origin: bool) -> Path:
    """Return the cached pristine repo for *origin*, building it on first use."""
    cached = _TEMPLATES.get(origin)
    if cached is not None:
        return cached

    root = Path(tempfile.mkdtemp(prefix="camp-tests-git-template-"))
    atexit.register(shutil.rmtree, root, True)
    repo = root / "repo"
    _build_git_repo(repo, origin=origin)
    _TEMPLATES[origin] = repo
    return repo


def _build_git_repo(path: Path, *, origin: bool) -> None:
    """Create the repo `init_git_repo` promises, the long way, via git itself."""
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


def _clone_template(template: Path, path: Path) -> None:
    """Copy *template* to *path*, repointing the paths git recorded inside it."""
    shutil.copytree(template, path, symlinks=True, dirs_exist_ok=True)

    for name in _PATH_BEARING_GIT_FILES:
        recorded = path / ".git" / name
        if not recorded.exists():
            continue
        recorded.write_text(
            recorded.read_text().replace(str(template), str(path))
        )


def init_git_repo(path: Path, *, origin: bool = False) -> None:
    """Create a git repo at *path* with one commit on `main`.

    Commits are made with `--no-gpg-sign` and a fixed identity so the suite does
    not depend on the developer's git configuration.

    With *origin* true the repo additionally gains an `origin` remote pointing at
    itself, fetched so remote-tracking refs exist. Callers that resolve a base
    like `origin/main`, or that exercise fetch, need it. Callers that do not
    leave it off so the repo stays the minimum their test needs — not because a
    remote would break them; it does not.

    The repo is copied from a per-process template rather than built call by
    call. What lands at *path* is a real, independent repository — its own
    object store, its own history to commit onto — and `test_helpers_git_repo`
    holds that equivalence.
    """
    _clone_template(_template(origin), path)


def camp_state_env(tmp_path: Path) -> dict[str, str]:
    """Return an env override pointing `CAMP_STATE_DIR` inside *tmp_path*.

    The directory is created, so a caller can write to it without a further
    mkdir. Callers needing more of the environment merge this into their own
    mapping rather than this helper growing their cases.
    """
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root)}
