#!/usr/bin/env python3
"""Dispatch and capture for the tidy-followups write-path eval (macOS, scripts/eval-sandbox).

    harness.py snapshot <work>
    harness.py run <work> <cell> <arm> --spec KEY [--env env|nestfail_env] [--reuse CELL-ARM]
                   (--answers TEXT | --task TEXT)

`<work>` is a private scratch directory outside $HOME (for example under /private/tmp). It
holds `snap-base` and `snap-treat` (read-only `git archive` snapshots without `evals/`),
`runs/<cell>-<arm>/` (the arm's jail: the fixture vault, its repo and the shims) and
`meta/` (before/after trees, the arm's stream-json, the command line used).

`run` builds a fresh fixture inside the jail (unless `--reuse` names an earlier run whose
fixture state is carried on), records the vault and repo trees, runs the arm through
`scripts/eval-sandbox` with the manifest env, records the trees again, and greps the
developer's real lore vaults and config for the fixture's unique run id.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[6]
BASE_SHA = "ce19d0cec6b8913e7c12ccbb159abe7d926b72d9"
SNAP_PATHS = ["tools/craft/plugins/craft", "tools/lore/plugins/lore", "trailhead"]
SANDBOX = REPO / "scripts" / "eval-sandbox"
BUILDER = Path(__file__).resolve().parent / "fixtures" / "make-fixture-vault.py"
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
ALLOWED = "Bash,Read,Glob,Grep,Agent"
HOME = os.environ["HOME"]
USER = os.environ.get("USER", "")

PROMPT = (
    "You are the agent running the /craft:tidy-followups skill. Its definition is the file "
    "{skill}. Read it in full and carry out its procedure exactly, with the spec `{spec}` as "
    "its argument. `${{CLAUDE_PLUGIN_ROOT}}` is {root}. The `lore` command on your PATH reads a "
    "throwaway vault. The code the follow-ups' claims point at is the git repository at {repo}; "
    "refinement subagents work against it.\n\n"
    "No operator is available while you run, so the operator's answers are given here:\n"
    "{answers}\n\n"
    "Carry out the procedure as if you had asked each question: display each confirmation "
    "question in your output, together with everything the procedure says to show with it, at "
    "the point the procedure puts it, and then continue on the answer given for it. Where a step "
    "says to dispatch a subagent, use the Agent tool. Do not edit any file yourself; the "
    "procedure's own steps are the only writing you do."
)

TASK_PROMPT = (
    "You are an agent working with a throwaway lore vault; `lore` on your PATH reads it. The "
    "craft plugin is at {root}. {task}"
)


def sh(argv, **kw):
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, **kw)


def snapshot(work: Path, head: str) -> None:
    for name, sha in (("snap-base", BASE_SHA), ("snap-treat", head)):
        dest = work / name
        dest.mkdir(parents=True)
        tar = subprocess.run(
            ["git", "-C", str(REPO), "archive", sha, *SNAP_PATHS], capture_output=True, check=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)
        evals = dest / "tools/craft/plugins/craft/evals"
        if evals.exists():
            subprocess.run(["rm", "-rf", str(evals)], check=True)
        (dest / "SHA").write_text(sha + "\n")
        subprocess.run(["chmod", "-R", "a-w", str(dest)], check=True)
    print(json.dumps({"base": BASE_SHA, "treat": head}))


def tree(path: Path) -> dict:
    out = {}
    for p in sorted(path.rglob("*")):
        if p.is_file() and ".git" not in p.relative_to(path).parts:
            out[str(p.relative_to(path))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def repo_state(repo: Path) -> dict:
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "GIT_CONFIG_GLOBAL": "/dev/null"}
    return {
        "head": sh(["git", "-C", repo, "rev-parse", "HEAD"], env=env).stdout.strip(),
        "status": sh(["git", "-C", repo, "status", "--porcelain"], env=env).stdout,
        "tree": tree(repo),
    }


def build(work: Path, run: Path, snap: Path) -> dict:
    lore = snap / "tools/lore/plugins/lore/bin/lore"
    r = sh(
        [sys.executable, "-I", BUILDER, run / "fixture"],
        env={"PATH": "/usr/bin:/bin", "LORE_BIN": str(lore)},
    )
    if r.returncode != 0:
        raise SystemExit(f"fixture build failed: {r.stderr}")
    return json.loads(r.stdout)


def real_hits(run_id: str) -> str:
    r = sh(["grep", "-rIl", "--", run_id, f"{HOME}/.local/state/lore", f"{HOME}/.config/lore"])
    return r.stdout.strip()


def run(args) -> None:
    work = Path(args.work).resolve()
    arm = args.arm
    snap = work / ("snap-base" if arm == "base" else "snap-treat")
    key = f"{args.cell}-{arm}"
    meta = work / "meta"
    meta.mkdir(exist_ok=True)
    if args.reuse:
        run_dir = work / "runs" / args.reuse
        manifest = json.loads((meta / f"{args.reuse}.manifest.json").read_text())
    else:
        run_dir = work / "runs" / key
        run_dir.mkdir(parents=True)
        manifest = build(work, run_dir, snap)
        (meta / f"{key}.manifest.json").write_text(json.dumps(manifest, indent=1))
    fixture = run_dir / "fixture"
    env = dict(manifest[args.env])
    env.pop("HOME", None)
    (run_dir / "tmp").mkdir(exist_ok=True)
    env["TMPDIR"] = str(run_dir / "tmp")
    env["CLAUDE_PLUGIN_ROOT"] = str(snap / "tools/craft/plugins/craft")
    root = env["CLAUDE_PLUGIN_ROOT"]
    env["LORE_BIN"] = str(snap / "tools/lore/plugins/lore/bin/lore")
    spec = manifest["specs"].get(args.spec, args.spec)
    if args.add or args.moves_none:
        if args.moves_none:
            moves = "none (do not move anything onto or off the shortlist)"
        else:
            names = [manifest["followups"][args.spec][r] for r in args.add.split(",")]
            moves = (
                "make the shortlist exactly these follow-ups: " + ", ".join(names)
                + ". Add any of them that is not already shortlisted, and remove any other "
                "shortlisted follow-up."
            )
        args.answers = (
            f"1. Shortlist moves (step 8): {moves}\n"
            "2. Disposition set: yes\n"
            "3. Scope confirmation (step 11): yes"
        )
    if args.task:
        prompt = TASK_PROMPT.format(root=root, task=args.task.format(spec=spec, root=root))
    else:
        prompt = PROMPT.format(
            skill=f"{root}/skills/tidy-followups/SKILL.md",
            spec=spec,
            root=root,
            repo=manifest["repo"],
            answers=args.answers,
        )
    before = {"vault": tree(fixture / "vault"), "repo": repo_state(Path(manifest["repo"]))}
    (meta / f"{key}.before.json").write_text(json.dumps(before, indent=1))
    argv = [
        SANDBOX, run_dir, "--", "env", "-i",
        *[f"{k}={v}" for k, v in env.items()],
        f"HOME={HOME}", f"USER={USER}",
        CLAUDE, "-p", prompt, "--setting-sources", "project",
        "--allowedTools", ALLOWED, "--output-format", "stream-json", "--verbose",
    ]
    (meta / f"{key}.cmd.txt").write_text(
        " ".join(repr(str(a)) if " " in str(a) or "\n" in str(a) else str(a) for a in argv) + "\n"
    )
    start = time.time()
    with open(meta / f"{key}.stream.jsonl", "w") as out, open(meta / f"{key}.stderr.txt", "w") as err:
        done = subprocess.run(
            [str(a) for a in argv], stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=args.timeout
        )
    elapsed = time.time() - start
    after = {"vault": tree(fixture / "vault"), "repo": repo_state(Path(manifest["repo"]))}
    (meta / f"{key}.after.json").write_text(json.dumps(after, indent=1))
    hits = real_hits(manifest["run_id"])
    cost = None
    for line in (meta / f"{key}.stream.jsonl").read_text().splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            cost = ev.get("total_cost_usd")
    summary = {"cell": key, "exit": done.returncode, "seconds": round(elapsed), "cost_usd": cost,
               "real_vault_hits": hits, "run_id": manifest["run_id"]}
    (meta / f"{key}.summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("work")
    s.add_argument("--head", required=True)
    r = sub.add_parser("run")
    r.add_argument("work")
    r.add_argument("cell")
    r.add_argument("arm", choices=["base", "treat"])
    r.add_argument("--spec", required=True)
    r.add_argument("--env", default="env", choices=["env", "nestfail_env"])
    r.add_argument("--reuse")
    r.add_argument("--answers")
    r.add_argument("--task")
    r.add_argument("--add", help="comma-separated roles to make the shortlist")
    r.add_argument("--moves-none", action="store_true")
    r.add_argument("--timeout", type=int, default=580)
    args = ap.parse_args()
    if args.cmd == "snapshot":
        snapshot(Path(args.work).resolve(), args.head)
    else:
        run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
