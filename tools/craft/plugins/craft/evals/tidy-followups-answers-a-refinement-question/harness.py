#!/usr/bin/env python3
"""Dispatch for the answers-a-refinement-question eval: each arm driven one operator reply per turn.

    harness.py snapshot <work> --head SHA
    harness.py run <work> <cell> <arm> --n N [--deadline SECONDS] [--turn-timeout SECONDS] [--model M]
    harness.py persist <work> <cell> <arm> --n N

`<work>` is a private scratch directory outside $HOME (made with `mktemp -d`). `snapshot` writes `snap-base` and
`snap-treat`, read-only `git archive` snapshots of the skill, the craft and lore plugin trees and the `trailhead/`
package (`lore` imports it), without `evals/`. The base arm is the skill without the second pass, at
`BASE_SHA`; its `SKILL.md` must equal `fixtures/baseline-skill/SKILL.md`, whose content fingerprint
(`git hash-object`) is `BASE_SKILL_FINGERPRINT`, or the snapshot refuses.

`run` allows a whole run `--deadline` seconds (default 1500) and one turn `--turn-timeout` (default 700). It builds
a fresh fixture inside `runs/<cell>-<arm>-r<N>/` (the arm's jail), starts the arm as a stream-json `claude` process
under `scripts/eval-sandbox` with `ALLOWED` tools (which include `Agent`, so the second pass can dispatch its
refinement subagents) and hands it to `driver.drive` with the cell's up-front message and replies. `meta/` receives
the manifest, the vault and repo trees before and after, the whole stream, the driver's per-turn record and a
summary. `persist` scrubs the stream, the driver record and the tree diff into this case's `runs/` and stores the
grade beside them as `<cell>-<arm>-r<N>.grade.json`.

The run, scrub and driver code is `../tidy-followups-confirms-before-writing/harness.py`'s (itself built on
`../tidy-followups-builds-one-parent/`); this harness points it at this case's fixture builder, cells and grader.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIRM_CASE = HERE.parent / "tidy-followups-confirms-before-writing"
BASE_SHA = "0ada1cfcfa35d9098d68dc543961a8bfd8ec66f9"
BASE_SKILL_FINGERPRINT = "41578631128fad1bdfec7a91c52af0270c93452a"
BASELINE_SKILL = HERE / "fixtures" / "baseline-skill" / "SKILL.md"
SKILL_IN_SNAPSHOT = "tools/craft/plugins/craft/skills/tidy-followups/SKILL.md"

sys.path.insert(0, str(CONFIRM_CASE))
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location("confirm_harness", CONFIRM_CASE / "harness.py")
conf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conf)
import grade_answers  # noqa: E402

old = conf.old
old.BASE_SHA = BASE_SHA
old.BUILDER = HERE / "fixtures" / "make-fixture-vault.py"
conf.HERE = HERE
conf.grade_confirm = grade_answers
ALLOWED = old.ALLOWED


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def snapshot(work: Path, head: str) -> None:
    old.snapshot(work, head)
    base_skill = (work / "snap-base" / SKILL_IN_SNAPSHOT).read_bytes()
    if git_blob_id(base_skill) != BASE_SKILL_FINGERPRINT or base_skill != BASELINE_SKILL.read_bytes():
        raise SystemExit("the base snapshot's SKILL.md is not the committed baseline skill")


def persist(args) -> None:
    conf.persist(args)
    key = f"{args.cell}-{args.arm}-r{args.n}"
    graded = grade_answers.grade_run(Path(args.work).resolve(), key)
    (HERE / "runs" / f"{key}.grade.json").write_text(json.dumps(graded, indent=1) + "\n")


def main() -> int:
    args = parse_args()
    if args.cmd == "snapshot":
        snapshot(Path(args.work).resolve(), args.head)
    elif args.cmd == "run":
        conf.run(args)
    else:
        persist(args)
    return 0


def parse_args():
    import argparse

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
    sub.choices["run"].add_argument("--deadline", type=float, default=1500)
    sub.choices["run"].add_argument("--turn-timeout", type=float, default=700)
    sub.choices["run"].add_argument("--model")
    return ap.parse_args()


if __name__ == "__main__":
    sys.exit(main())
