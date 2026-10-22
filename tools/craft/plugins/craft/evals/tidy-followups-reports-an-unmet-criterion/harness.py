#!/usr/bin/env python3
"""Dispatch for the unmet-criterion eval: the existing write-path harness with this case's arms.

    harness.py snapshot <work> --head SHA
    harness.py cell <work> <cell> <arm>
    harness.py run <work> <cell> <arm> --spec KEY --answers TEXT

`cell` reads the cell's answers from `cells.json`. A cell name may end in `-rN` (`unmet-flagged-r2`):
a repeat run of the cell, with its own run directory and captures, against a freshly built fixture. An
answer may carry `{role}` placeholders (`{U}`, `{K}`): they are filled with the follow-up names of the
fixture the arm runs against, built just before the arm starts, so the prompt names real records.

The harness, its scrub, grader and extractor are `../tidy-followups-builds-one-parent/`'s. This
wrapper changes module constants and nothing in that directory: the base arm is the skill at
`437bbe8ecc8cec34301eb304bad63526d2af465e` (before the unmet-criterion check), and the builder is
this case's.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD_CASE = HERE.parent / "tidy-followups-builds-one-parent"
BASE_SHA = "437bbe8ecc8cec34301eb304bad63526d2af465e"

sys.path.insert(0, str(OLD_CASE))
_spec = importlib.util.spec_from_file_location("builds_one_parent_harness", OLD_CASE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

_built: dict = {}


def base_cell(name: str) -> str:
    return re.sub(r"-r\d+$", "", name)


def fill(answers: str, manifest: dict, cell: str) -> str:
    names = manifest["followups"].get(cell, {})
    return re.sub(r"\{([A-Za-z0-9]+)\}", lambda m: names.get(m.group(1), m.group(0)), answers)


class _Prompt:
    def __init__(self, template: str):
        self.template = template

    def format(self, **kw):
        kw["answers"] = fill(kw["answers"], _built["manifest"], _built["cell"])
        return self.template.format(**kw)


def main(argv: list[str]) -> int:
    harness.BASE_SHA = BASE_SHA
    harness.BUILDER = HERE / "fixtures" / "make-fixture-vault.py"
    harness.PROMPT = _Prompt(harness.PROMPT)
    if argv[:1] == ["cell"]:
        work, cell, arm = argv[1:4]
        spec = base_cell(cell)
        answers = json.loads((HERE / "cells.json").read_text())[spec]["answers"]
        argv = ["run", work, cell, arm, "--spec", spec, "--answers", answers]
    if argv[:1] == ["run"]:
        _built["cell"] = argv[argv.index("--spec") + 1]
        real_build = harness.build

        def build(work, run, snap):
            _built["manifest"] = real_build(work, run, snap)
            return _built["manifest"]

        harness.build = build
    sys.argv = [sys.argv[0], *argv]
    return harness.main()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
