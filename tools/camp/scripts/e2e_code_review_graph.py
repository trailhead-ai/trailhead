#!/usr/bin/env python3
"""THROWAWAY-ENV-ONLY end-to-end verification of the code-review-graph provision task.

This is a MANUAL verification script, not part of the pytest suite: it drives the
REAL `code-review-graph` CLI (must be on PATH) against a REAL throwaway git clone
of trailhead. It never touches the live install — everything runs under a fresh
mktemp dir with an injected CAMP_STATE_DIR, and the clone is a disposable copy of
the current worktree. The live `~/.config/camp` and any canonical checkout are
never read or written.

It proves the four-step provision flow:
  1. A fresh workspace reconcile creates the trailhead worktree and marks the
     code-review-graph task "ok".
  2. `code-review-graph build --repo {worktree}` actually ran there:
     `status --repo {worktree}` reports the worktree's own HEAD as the built
     commit, and node file_path values in the graph.db are rooted at {worktree},
     not at the original clone path.
  3. A second reconcile (simulating a later SessionStart) SKIPS the completed
     task — the manifest state stays "ok" and the graph.db is not rebuilt
     (mtime unchanged).

Run:  .venv/bin/python tools/camp/scripts/e2e_code_review_graph.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead worktree root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
sys.path.insert(0, str(_PLUGIN_DIR))
sys.path.insert(0, str(_REPO_ROOT))  # so `import trailhead.paths` resolves

from camp.group.config import load_group  # noqa: E402
from camp.group.manifest import read_central_manifest  # noqa: E402
from camp.provision.reconcile import manifest_path_for, reconcile_worktree  # noqa: E402

SLUG = "crg-e2e"
CONFIG_TOML = """\
[group]
name = "trailhead"

[[members]]
name = "trailhead"
repo_root = "{clone}"
base = "HEAD"
tasks = ["code-review-graph"]

[branch]
pattern = "worktree-{{slug}}"

[tasks.code-review-graph]
phase = "provision"
required = false
timeout_seconds = 600

[[tasks.code-review-graph.steps]]
name = "build"
cmd = ["code-review-graph", "build", "--repo", "{{worktree}}"]
"""


def _step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def _fail(msg: str) -> None:
    print(f"\nFAIL: {msg}", flush=True)
    sys.exit(1)


def _git_head(repo: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _status(worktree: Path) -> dict[str, str]:
    out = subprocess.run(
        ["code-review-graph", "status", "--repo", str(worktree)],
        capture_output=True,
        text=True,
        check=True,
    )
    fields: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
    return fields


def _sample_file_paths(db: Path, n: int = 5) -> list[str]:
    out = subprocess.run(
        [
            "sqlite3",
            str(db),
            f"select file_path from nodes where file_path is not null limit {n};",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [ln for ln in out.stdout.splitlines() if ln]


def main() -> None:
    if shutil.which("code-review-graph") is None:
        _fail("code-review-graph not on PATH")
    if shutil.which("sqlite3") is None:
        _fail("sqlite3 not on PATH")

    work = Path(tempfile.mkdtemp(prefix="crg-e2e."))
    print(f"throwaway workdir: {work}", flush=True)
    try:
        state_dir = work / "state"
        clone = work / "clone"
        config_path = work / "trailhead.toml"

        _step("Clone trailhead (throwaway)")
        subprocess.run(
            ["git", "clone", "--quiet", str(_REPO_ROOT), str(clone)], check=True
        )
        clone_head = _git_head(clone)
        print(f"clone HEAD: {clone_head[:12]}", flush=True)

        config_path.write_text(CONFIG_TOML.format(clone=clone))
        group = load_group(config_path)

        env = {**os.environ, "CAMP_STATE_DIR": str(state_dir)}

        _step("First reconcile — provision + build")
        t0 = time.monotonic()
        reconcile_worktree(group, SLUG, env=env)
        build_elapsed = time.monotonic() - t0
        print(f"first reconcile took {build_elapsed:.1f}s", flush=True)

        mpath = manifest_path_for("trailhead", SLUG, env=env)
        manifest = read_central_manifest(mpath)
        member = next(m for m in manifest["members"] if m["name"] == "trailhead")
        worktree = Path(member["worktree_path"])
        task_state = (member.get("tasks") or {}).get("code-review-graph", {}).get("state")
        print(f"worktree: {worktree}", flush=True)
        print(f"task state: {task_state}", flush=True)
        if task_state != "ok":
            _fail(f"code-review-graph task state is {task_state!r}, expected 'ok'")

        _step("Verify build ran in the worktree")
        wt_head = _git_head(worktree)
        status = _status(worktree)
        print(f"worktree HEAD:     {wt_head[:12]}", flush=True)
        print(f"status built at:   {status.get('Built at commit')}", flush=True)
        print(f"status nodes/files: {status.get('Nodes')} / {status.get('Files')}", flush=True)
        if not wt_head.startswith(status.get("Built at commit", "\0")):
            _fail("status 'Built at commit' does not match the worktree HEAD")

        db = worktree / ".code-review-graph" / "graph.db"
        if not db.is_file():
            _fail(f"graph.db not found at {db}")
        samples = _sample_file_paths(db)
        print("sample node file_paths:", flush=True)
        for p in samples:
            print(f"  {p}", flush=True)
        wt_prefix = str(worktree.resolve())
        rooted = [p for p in samples if p.startswith(wt_prefix)]
        if not rooted:
            _fail(f"no sampled node file_path is rooted at the worktree {wt_prefix}")
        if any(p.startswith(str(clone.resolve()) + "/") for p in samples):
            _fail("a node file_path is rooted at the ORIGINAL clone path, not the worktree")
        print(f"all {len(samples)} sampled paths rooted at the worktree", flush=True)

        _step("Second reconcile — run-once skip")
        db_mtime_before = db.stat().st_mtime_ns
        t1 = time.monotonic()
        reconcile_worktree(group, SLUG, env=env)
        second_elapsed = time.monotonic() - t1
        db_mtime_after = db.stat().st_mtime_ns
        manifest2 = read_central_manifest(mpath)
        member2 = next(m for m in manifest2["members"] if m["name"] == "trailhead")
        task_state2 = (member2.get("tasks") or {}).get("code-review-graph", {}).get("state")
        print(f"second reconcile took {second_elapsed:.1f}s", flush=True)
        print(f"task state after 2nd run: {task_state2}", flush=True)
        print(f"graph.db mtime changed: {db_mtime_before != db_mtime_after}", flush=True)
        if task_state2 != "ok":
            _fail(f"task state regressed to {task_state2!r} on second run")
        if db_mtime_before != db_mtime_after:
            _fail("graph.db was rebuilt on the second reconcile — run-once did NOT hold")

        _step("PASS")
        print(
            "code-review-graph provision recipe verified end-to-end "
            f"(build {build_elapsed:.0f}s, skip {second_elapsed:.1f}s)",
            flush=True,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
