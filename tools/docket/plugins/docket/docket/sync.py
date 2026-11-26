"""The change pipeline: sync, lock, read, apply, write, unlock, sync, synced test, presence re-read.

`run_pipeline` is the one path every docket change takes. Its steps, in order:

1. `lore sync --vault <default> --json` so the change is made on the latest copy.
2. Under the host lock: the caller's `apply` reads the lists, computes the change and writes it.
3. Nothing changed: stop, with no second sync (the pre-sync warning, if any, rides along).
4. `lore sync` again, then the synced test, read from git and never from lore's exit code: the branch is
   not ahead of its upstream (a missing upstream prints no count, so it counts as not synced) and `sites/docket` has no
   uncommitted change.
5. Under the lock, re-read each touched list and check the change is still there. Lore settles a
   conflicting `sites/` file by taking the remote copy outright and still reports `published`, so only
   this re-read shows a change another host replaced.

The outcome is `lost-to-remote` if the change did not survive (exit 4), else `saved-not-synced` if the
pre-sync or post-sync failed or the synced test says unpublished (exit 3), else `saved` (exit 0);
`unchanged` is exit 0 and `refused` exit 2.

`lore sync --json` prints progress lines before its JSON document, so the document is found within
stdout. A sync succeeded when it exited 0 and reported no vault outcome other than published or
converged.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from docket.store import DocketList, Runner, Store, Workspace

SAVED = "saved"
SAVED_NOT_SYNCED = "saved-not-synced"
UNCHANGED = "unchanged"
REFUSED = "refused"
LOST_TO_REMOTE = "lost-to-remote"

EXIT_CODES = {SAVED: 0, UNCHANGED: 0, REFUSED: 2, SAVED_NOT_SYNCED: 3, LOST_TO_REMOTE: 4}

_GOOD_SYNC_OUTCOMES = {"published", "converged"}
_DOCUMENT_START = re.compile(r"^\{", re.MULTILINE)

Key = tuple[str, str]


def _sync_document(stdout: str) -> dict | None:
    for match in _DOCUMENT_START.finditer(stdout):
        try:
            doc, _end = json.JSONDecoder().raw_decode(stdout[match.start():])
        except ValueError:
            continue
        if isinstance(doc, dict):
            return doc
    return None


def sync_vault(runner: Runner, vault: str) -> str | None:
    """Run `lore sync` on one vault; None when it succeeded, else a one-line reason."""
    try:
        proc = runner(["lore", "sync", "--vault", vault, "--json"])
    except (OSError, subprocess.SubprocessError) as exc:
        return f"lore sync could not run: {exc}"
    doc = _sync_document(proc.stdout or "")
    outcomes = [v.get("outcome") for v in (doc or {}).get("vaults", []) if isinstance(v, dict)]
    bad = [o for o in outcomes if o not in _GOOD_SYNC_OUTCOMES]
    if proc.returncode == 0 and not bad:
        return None
    reason = (bad[0] if bad else f"exit {proc.returncode}")
    notice = (proc.stderr or "").strip().splitlines()
    return f"lore sync reported {reason}" + (f": {notice[0]}" if notice else "")


def is_synced(runner: Runner, vault_root: Path) -> bool:
    """True when the vault's branch is not ahead of its upstream and `sites/docket` is committed."""
    try:
        ahead = runner(["git", "-C", str(vault_root), "rev-list", "--count", "@{u}..HEAD"])
        if ahead.stdout.strip() != "0":
            return False
        dirty = runner(["git", "-C", str(vault_root), "status", "--porcelain", "--", "sites/docket"])
    except (OSError, subprocess.SubprocessError):
        return False
    return dirty.returncode == 0 and not dirty.stdout.strip()


@dataclass
class Expectation:
    """What must still hold in one vault's list after the post-change sync for the change to have survived."""

    vault: str
    present: Sequence[Key] = ()
    absent: Sequence[Key] = ()
    order: Sequence[Key] = ()
    precede: Sequence[tuple[Sequence[Key], Sequence[Key]]] = ()
    workspaces: dict[Key, Workspace | None] = field(default_factory=dict)

    def holds(self, lst: DocketList) -> bool:
        index = {e.key: i for i, e in enumerate(lst.entries)}
        if any(k not in index for k in self.present) or any(k in index for k in self.absent):
            return False
        placed = [index[k] for k in self.order if k in index]
        if placed != sorted(placed):
            return False
        for firsts, seconds in self.precede:
            if any(index[a] > index[b] for a in firsts if a in index for b in seconds if b in index):
                return False
        for key, workspace in self.workspaces.items():
            if key not in index or lst.entries[index[key]].workspace != workspace:
                return False
        return True


@dataclass
class Applied:
    """What `apply` did: whether it wrote, the per-entry detail, the lists as written, and what must survive."""

    changed: bool
    entries: list[dict]
    lists: dict[str, DocketList]
    expect: list[Expectation] = field(default_factory=list)


@dataclass
class Report:
    outcome: str
    applied: Applied
    lists: dict[str, DocketList]
    sync_warning: str | None = None


def run_pipeline(runner: Runner, store: Store, default_vault: str, apply: Callable[[Store], Applied]) -> Report:
    warnings: list[str] = []
    pre = sync_vault(runner, default_vault)
    if pre:
        warnings.append(pre)
    with store.locked():
        applied = apply(store)
    if not applied.changed:
        return Report(UNCHANGED, applied, applied.lists, "; ".join(warnings) or None)
    post = sync_vault(runner, default_vault)
    if post:
        warnings.append(post)
    synced = post is None and is_synced(runner, store.vault_root)
    with store.locked():
        now = {vault: store.read_list(vault) for vault in applied.lists}
    survived = all(exp.holds(now[exp.vault]) for exp in applied.expect)
    if not survived:
        outcome = LOST_TO_REMOTE
    elif pre or not synced:
        outcome = SAVED_NOT_SYNCED
    else:
        outcome = SAVED
    return Report(outcome, applied, now, "; ".join(warnings) or None)
