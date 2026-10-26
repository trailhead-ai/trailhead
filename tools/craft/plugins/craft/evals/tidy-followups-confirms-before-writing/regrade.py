#!/usr/bin/env python3
"""Re-grade stored captures with the current grader, without running an arm.

    regrade.py [--write] [<run-key> ...]

For each `runs/<key>.grade.json` (all of them when no key is given) this reads the same run's scrubbed
`.stream.jsonl` and `.transcript.json`, grades them against `cells.json` with `grade_confirm.grade`, keeps
the stored `G1`, `G3` and `records` (they need the live vault), and prints every line whose verdict
changed. `--write` stores the new grade. The follow-up names of each cell are the fixture builder's, which
are the same in every build, so the builder is run once in a scratch directory (`LORE_BIN` or the
repository's `lore`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grade_confirm  # noqa: E402

REPO = HERE.parents[5]


def fixture_names() -> dict:
    lore = os.environ.get("LORE_BIN") or str(REPO / "tools/lore/plugins/lore/bin/lore")
    with tempfile.TemporaryDirectory() as scratch:
        r = subprocess.run(
            [sys.executable, "-I", str(HERE / "fixtures/make-fixture-vault.py"), str(Path(scratch) / "fx")],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "LORE_BIN": lore},
            stdin=subprocess.PIPE)
        if r.returncode != 0:
            raise SystemExit(r.stderr)
        return json.loads(r.stdout)["followups"]


def regrade(key: str, names: dict, cells: dict) -> dict:
    runs = HERE / "runs"
    stored = json.loads((runs / f"{key}.grade.json").read_text())
    transcript = json.loads((runs / f"{key}.transcript.json").read_text())
    cell_key = key.rsplit("-", 2)[0]
    cell = dict(cells[cell_key])
    cell["names"] = names[cell.get("spec_cell", cell_key)]
    kept = {line["id"]: line for line in stored["lines"] if line["id"] in ("G1", "G3")}
    prev_key = grade_confirm.earlier_leg_key(key, cell_key, cell)
    prev_code = None
    if prev_key:
        prev_code = grade_confirm.earlier_leg_code(json.loads((runs / f"{prev_key}.transcript.json").read_text()))
    lines = (runs / f"{key}.stream.jsonl").read_text().splitlines()
    fresh = grade_confirm.grade(lines, transcript["turns"], cell, records=stored["records"], prev_code=prev_code)
    fresh["lines"] = [kept["G1"], *fresh["lines"], kept["G3"]]
    fresh["records"] = stored["records"]
    fresh["ended"], fresh["unsent"] = stored.get("ended"), stored.get("unsent")
    return fresh


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--write"]
    write = "--write" in sys.argv[1:]
    runs = HERE / "runs"
    keys = args or sorted(p.name[:-len(".grade.json")] for p in runs.glob("*.grade.json"))
    names, cells = fixture_names(), json.loads((HERE / "cells.json").read_text())
    changed = 0
    for key in keys:
        old = {line["id"]: line for line in json.loads((runs / f"{key}.grade.json").read_text())["lines"]}
        new = regrade(key, names, cells)
        for line in new["lines"]:
            before = old.get(line["id"])
            if before is None or before["pass"] != line["pass"]:
                changed += 1
                print(f"{key} {line['id']}: {'PASS' if before and before['pass'] else 'absent/FAIL'} -> "
                      f"{'PASS' if line['pass'] else 'FAIL'}  {line['detail'][:160]}")
        if write:
            (runs / f"{key}.grade.json").write_text(json.dumps(new, indent=1) + "\n")
    print(f"{len(keys)} runs re-graded, {changed} line verdicts changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
