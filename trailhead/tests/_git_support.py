"""Hermetic git environment for trailhead's tests.

One definition of "git sees nothing of the machine it runs on", used at the
subprocess boundary (the env dict) and in-process (the `hermetic_git_env`
fixture in conftest, which applies the same dict to ``os.environ`` so every
spawned git inherits it).
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

HERMETIC_GIT_IDENTITY = ("Test", "t@e.st")

#: Variables a parent git process (a hook, a worktree command) exports to its
#: children and that redirect every later git call to the parent's repository.
_GIT_REDIRECTS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_NAMESPACE", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_PARAMETERS",
)


def make_hermetic_git_env(base: Mapping[str, str], root: Path) -> dict[str, str]:
    """Return *base* with git fenced off from the machine and the enclosing worktree.

    No system or global config (``GIT_CONFIG_GLOBAL`` is an empty file under
    *root*), a fixed author and committer, no commit signing, no hooks, and
    ``GIT_CEILING_DIRECTORIES`` at *root* so a directory under it can never
    discover a repository above it. The variables that redirect git to a
    parent's repository are dropped first; ``GIT_CONFIG_COUNT`` is overwritten,
    which makes git ignore any ambient ``GIT_CONFIG_KEY_n`` past the two set here.
    """
    root = Path(root)
    env = {k: v for k, v in base.items() if k not in _GIT_REDIRECTS}
    empty = root / "git-empty-global-config"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text("")
    name, email = HERMETIC_GIT_IDENTITY
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(empty),
        GIT_AUTHOR_NAME=name,
        GIT_AUTHOR_EMAIL=email,
        GIT_COMMITTER_NAME=name,
        GIT_COMMITTER_EMAIL=email,
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="commit.gpgsign",
        GIT_CONFIG_VALUE_0="false",
        GIT_CONFIG_KEY_1="core.hooksPath",
        GIT_CONFIG_VALUE_1=os.devnull,
        GIT_CEILING_DIRECTORIES=str(root.resolve()),
    )
    return env
