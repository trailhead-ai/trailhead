"""Merge holds — the structural never-merge guard.

A hold names one `(repo, branch)`. `portage merge` refuses any pair whose PR
head branch is that branch while the hold exists (`cli/pr.py`), before any merge
call and whatever `[release].auto_merge` or `--operator-directed` say. Only an
operator or the orchestrator runs `portage release`; the unattended PR step never
does.

Scope: a hold is local. It is keyed by the repo's real path and the branch name on this
machine's portage state directory, so it binds only a `portage merge` run that reads that
same state directory against that same checkout path. It does not stop a merge made by hand,
through the host's own interface, by a run with a different state directory, or against the
same repository cloned at another path.

State lives under `<portage state dir>/holds/`, resolved through
`trailhead.paths.state_dir` and never built by hand. One file per hold, named by
a digest of the repo's real path and the branch, so no caller-supplied string
becomes a path segment. Holding is idempotent: the same pair always names the
same file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from trailhead import paths

BRANCH_PATTERN = re.compile(r"[A-Za-z0-9._/-]+")


class HoldError(ValueError):
    """A hold/release request that is refused (invalid branch name)."""


def validate_branch(branch: str) -> None:
    if not BRANCH_PATTERN.fullmatch(branch):
        raise HoldError(f"invalid branch name {branch!r}: must match ^[A-Za-z0-9._/-]+$")


def _repo_key(repo: str) -> str:
    return os.path.realpath(repo)


def _holds_dir() -> Path:
    return paths.state_dir("portage") / "holds"


def _hold_file(repo: str, branch: str) -> Path:
    digest = hashlib.sha256(f"{_repo_key(repo)}\0{branch}".encode()).hexdigest()
    return _holds_dir() / f"{digest}.json"


def hold(repo: str, branch: str) -> None:
    validate_branch(branch)
    f = _hold_file(repo, branch)
    paths.ensure_dir(f.parent)
    tmp = f.with_name(f.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"repo": _repo_key(repo), "branch": branch}), encoding="utf-8")
    os.replace(tmp, f)


def release(repo: str, branch: str) -> None:
    """Clear the hold; releasing a branch that is not held is not an error."""
    validate_branch(branch)
    _hold_file(repo, branch).unlink(missing_ok=True)


def is_held(repo: str, branch: str) -> bool:
    return _hold_file(repo, branch).is_file()


def has_any_hold(repo: str) -> bool:
    """Whether any branch of `repo` is held. Fails closed: a holds directory that
    exists but cannot be listed counts as held."""
    key = _repo_key(repo)
    try:
        with os.scandir(_holds_dir()) as it:
            entries = [Path(e.path) for e in it if e.name.endswith(".json")]
    except FileNotFoundError:
        return False
    except OSError:
        return True
    for f in entries:
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("repo") == key:
                return True
        except (OSError, ValueError):
            return True
    return False
