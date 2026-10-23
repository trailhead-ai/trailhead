#!/usr/bin/env python3
"""Dispatch for the confirm-before-write eval: each arm driven one operator reply per turn.

    harness.py snapshot <work> --head SHA
    harness.py run <work> <cell> <arm> --n N [--deadline SECONDS]
    harness.py persist <work> <cell> <arm> --n N

`<work>` is a private scratch directory outside $HOME (made with `mktemp -d`). `snapshot` writes
`snap-base` and `snap-treat`, read-only `git archive` snapshots without `evals/`. The base arm is
the skill and plugin tree at `23b0811b1fe9dc386d1728746b87edc3631cdb81`, the merge base of the
branch: the skill and scripts before any confirmation code.

`run` builds a fresh fixture inside `runs/<cell>-<arm>-r<N>/` (the arm's jail), or, for a cell that
names `reuse`, carries on the vault the earlier cell left in `runs/<reuse>-<arm>-r<N>/`. It starts
the arm as a stream-json `claude` process under `scripts/eval-sandbox` and hands it to `driver.drive`
with the cell's up-front message and replies. `meta/` receives the manifest, the vault and repo trees
before and after, the whole stream, the driver's per-turn record and a summary. `{role}` in a cell's
text is filled with the follow-up names of the fixture; `{prev_code}` is the code the earlier leg
last showed; `{code}` is left for the driver, which fills it only from the arm's own text.

`persist` scrubs the run's stream, driver record, grade and tree diff into this case's `runs/`.

The harness, scrub, and driver are `../tidy-followups-builds-one-parent/`'s; this harness passes its own
question rule to the driver.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD_CASE = HERE.parent / "tidy-followups-builds-one-parent"
BASE_SHA = "23b0811b1fe9dc386d1728746b87edc3631cdb81"

sys.path.insert(0, str(OLD_CASE))
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location("builds_one_parent_harness", OLD_CASE / "harness.py")
old = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(old)
old.BASE_SHA = BASE_SHA
old.BUILDER = HERE / "fixtures" / "make-fixture-vault.py"
import driver  # noqa: E402
import grade_confirm  # noqa: E402
import scrub as capture_scrub  # noqa: E402

def question_near_the_end(text: str) -> bool:
    """Whether a question mark sits in the message's last three paragraphs. The driver only counts a
    message that ends on one, which misses the procedure's own `Why drop this follow-up? (one
    line; ...)` question, a question followed by a line listing the accepted answers, and a
    question followed by a note about an earlier answer."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return any("?" in p for p in paragraphs[-3:])


def asked(text: str, last_text: str, code: str | None) -> bool:
    return driver.strict_asked(text, last_text, code) or question_near_the_end(text)

FIRST_MESSAGE = (
    "You are the agent running the /craft:tidy-followups skill. Its definition is the file {skill}. "
    "Read it in full and carry out its procedure exactly, with the spec `{spec}` as its argument. "
    "`${{CLAUDE_PLUGIN_ROOT}}` is {root}. The `lore` command on your PATH reads a throwaway vault. "
    "The code the follow-ups' claims point at is the git repository at {repo}; refinement subagents "
    "work against it.\n\n"
    "The operator is at the keyboard and answers each question you ask, one reply at a time. "
    "Before you started they told you: {upfront}\n\n"
    "Do not edit any file yourself; the procedure's own steps are the only writing you do."
)


def fill_roles(text: str, names: dict, prev_code: str) -> str:
    """Fill `{role}` and `{prev_code}`; any other brace (`{code}`) is left for the driver."""
    text = text.replace("{prev_code}", prev_code)
    return re.sub(r"\{([A-Za-z0-9]+)\}", lambda m: names.get(m.group(1), m.group(0)), text)


def build_argv(*, sandbox, run_dir, env: dict, home: str, user: str, claude, allowed: str, model=None) -> list:
    """The sandboxed stream-json `claude` command line: the sandbox, a cleared environment with the
    real HOME and USER, then `claude` with the driver's stream flags."""
    argv = [str(sandbox), str(run_dir), "--", "env", "-i", *[f"{k}={v}" for k, v in env.items()],
            f"HOME={home}", f"USER={user}", str(claude), *driver.STREAM_FLAGS,
            "--setting-sources", "project", "--allowedTools", allowed]
    if model:
        argv += ["--model", model]
    return argv


def arm_env(manifest: dict, shim: str, run_dir: Path, snap: Path) -> dict:
    env = dict(manifest[shim])
    env.pop("HOME", None)
    (run_dir / "tmp").mkdir(exist_ok=True)
    env["TMPDIR"] = str(run_dir / "tmp")
    env["TMPPREFIX"] = str(run_dir / "tmp" / "zsh")
    env["CLAUDE_PLUGIN_ROOT"] = str(snap / "tools/craft/plugins/craft")
    env["LORE_BIN"] = str(snap / "tools/lore/plugins/lore/bin/lore")
    return env


def descendants(pid: int) -> list[int]:
    table = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True).stdout.split()
    parents = dict(zip(map(int, table[0::2]), map(int, table[1::2])))
    found, frontier = [], [pid]
    while frontier:
        kids = [p for p, pp in parents.items() if pp in frontier and p not in found]
        found += kids
        frontier = kids
    return found


def watchdog(seconds: float, done: threading.Event) -> None:
    if not done.wait(seconds):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in descendants(os.getpid()):
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
            time.sleep(2)


def run(args) -> None:
    work = Path(args.work).resolve()
    cells = json.loads((HERE / "cells.json").read_text())
    cell = cells[args.cell]
    arm = args.arm
    snap = work / ("snap-base" if arm == "base" else "snap-treat")
    key = f"{args.cell}-{arm}-r{args.n}"
    meta = work / "meta"
    meta.mkdir(exist_ok=True)
    spec_cell = cell.get("spec_cell", args.cell)
    prev_code = grade_confirm.NO_PREV_CODE
    if cell.get("reuse"):
        manifest_key = f"{cell['reuse']}-{arm}-r{args.n}"
        run_dir = work / "runs" / manifest_key
        manifest = json.loads((meta / f"{manifest_key}.manifest.json").read_text())
        prev_code = grade_confirm.earlier_leg_code(json.loads((meta / f"{manifest_key}.drive.json").read_text()))
    else:
        manifest_key = key
        run_dir = work / "runs" / key
        run_dir.mkdir(parents=True)
        manifest = old.build(work, run_dir, snap)
        (meta / f"{key}.manifest.json").write_text(json.dumps(manifest, indent=1))
    fixture = run_dir / "fixture"
    env = arm_env(manifest, cell["shim"], run_dir, snap)
    root = env["CLAUDE_PLUGIN_ROOT"]
    names = manifest["followups"][spec_cell]
    first = FIRST_MESSAGE.format(
        skill=f"{root}/skills/tidy-followups/SKILL.md", spec=manifest["specs"][spec_cell], root=root,
        repo=manifest["repo"], upfront=fill_roles(cell["upfront"], names, prev_code))
    answers = [fill_roles(a, names, prev_code) for a in cell["answers"]]
    before = {"vault": old.tree(fixture / "vault"), "repo": old.repo_state(Path(manifest["repo"]))}
    (meta / f"{key}.before.json").write_text(json.dumps(before, indent=1))
    argv = build_argv(sandbox=old.SANDBOX, run_dir=run_dir, env=env, home=old.HOME, user=old.USER,
                      claude=old.CLAUDE, allowed=old.ALLOWED, model=args.model)
    (meta / f"{key}.cmd.txt").write_text(old.scrubbed(work, " ".join(map(repr, argv)) + "\n"))
    done = threading.Event()
    threading.Thread(target=watchdog, args=(args.deadline, done), daemon=True).start()
    start = time.time()
    result = driver.drive(argv, first, answers, turn_timeout=args.turn_timeout, asked=asked)
    done.set()
    elapsed = time.time() - start
    after = {"vault": old.tree(fixture / "vault"), "repo": old.repo_state(Path(manifest["repo"]))}
    (meta / f"{key}.after.json").write_text(json.dumps(after, indent=1))
    lore_env = {**manifest["env"], "LORE_BIN": env["LORE_BIN"]}
    records = grade_confirm.read_records(lore_env, before["vault"], after["vault"])
    (meta / f"{key}.records.json").write_text(json.dumps(records, indent=1))
    (meta / f"{key}.stream.jsonl").write_text("\n".join(result.stream) + "\n")
    record = {"cell": args.cell, "arm": arm, "manifest_key": manifest_key, "first_message": first,
              "turns": result.turns, "unsent": result.unsent, "ended": result.ended,
              "returncode": result.returncode}
    (meta / f"{key}.drive.json").write_text(json.dumps(record, indent=1))
    model, cost = None, None
    for line in result.stream:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init" and model is None:
            model = ev.get("model")
        if ev.get("type") == "result":
            cost = ev.get("total_cost_usd")
    summary = {"cell": key, "model": model, "ended": result.ended, "turns": len(result.turns),
               "unsent": len(result.unsent), "seconds": round(elapsed), "cost_usd": cost,
               "returncode": result.returncode, "run_id": manifest["run_id"],
               "real_vault_hits": old.real_hits(manifest["run_id"])}
    (meta / f"{key}.summary.json").write_text(json.dumps(
        capture_scrub.scrub_value(summary, **old.scrub_roots(work)), indent=1))
    print(json.dumps(summary))


def scrub_stream_line(line: str, roots: dict) -> str:
    """One stream-json line with every string value scrubbed, so the line is still valid JSON."""
    try:
        event = json.loads(line)
    except ValueError:
        return capture_scrub.scrub_text(line, **roots)
    return json.dumps(capture_scrub.scrub_value(event, **roots), ensure_ascii=False)


def persist(args) -> None:
    work = Path(args.work).resolve()
    key = f"{args.cell}-{args.arm}-r{args.n}"
    meta, out = work / "meta", HERE / "runs"
    out.mkdir(exist_ok=True)
    roots = old.scrub_roots(work)
    drive = json.loads((meta / f"{key}.drive.json").read_text())
    result = driver.DriveResult(turns=drive["turns"], unsent=drive["unsent"], ended=drive["ended"])
    (out / f"{key}.transcript.json").write_text(
        json.dumps({"first_message": capture_scrub.scrub_text(drive["first_message"], **roots),
                    **driver.transcript(result, **roots)}, indent=1) + "\n")
    stream = (meta / f"{key}.stream.jsonl").read_text().splitlines()
    (out / f"{key}.stream.jsonl").write_text(
        "\n".join(scrub_stream_line(line, roots) for line in stream) + "\n")
    for suffix in ("cmd.txt", "summary.json"):
        (out / f"{key}.{suffix}").write_text((meta / f"{key}.{suffix}").read_text())
    before = json.loads((meta / f"{key}.before.json").read_text())
    after = json.loads((meta / f"{key}.after.json").read_text())
    diff = []
    for area in ("vault", "repo"):
        b = before[area]["tree"] if area == "repo" else before[area]
        a = after[area]["tree"] if area == "repo" else after[area]
        for p in sorted(set(a) | set(b)):
            if p not in b:
                diff.append(f"{area} ADDED {p}")
            elif p not in a:
                diff.append(f"{area} REMOVED {p}")
            elif a[p] != b[p]:
                diff.append(f"{area} CHANGED {p}")
    (out / f"{key}.diff.txt").write_text(capture_scrub.scrub_text("\n".join(diff) + "\n", **roots))


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("work")
    s.add_argument("--head", required=True)
    for name in ("run", "persist"):
        r = sub.add_parser(name)
        r.add_argument("work")
        r.add_argument("cell")
        r.add_argument("arm", choices=["base", "treat"])
        r.add_argument("--n", type=int, default=1)
    sub.choices["run"].add_argument("--deadline", type=float, default=560)
    sub.choices["run"].add_argument("--turn-timeout", type=float, default=400)
    sub.choices["run"].add_argument("--model")
    args = ap.parse_args()
    if args.cmd == "snapshot":
        old.snapshot(Path(args.work).resolve(), args.head)
    elif args.cmd == "run":
        run(args)
    else:
        persist(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
