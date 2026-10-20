#!/usr/bin/env python3
"""Dispatch for the drop-and-merge eval: the existing write-path harness with this case's arms.

    harness.py snapshot <work> --head SHA
    harness.py cell <work> <cell> <arm>
    harness.py run <work> <cell> <arm> --spec KEY --answers TEXT [--shim env|dropfail_env|supersedefail_env]

`cell` reads the cell's spec, shim and answers from `cells.json`. An answer may carry `{role}`
placeholders (`{X1}`, `{S}`): they are filled with the follow-up names of the fixture the arm runs
against, built just before the arm starts, so the prompt names real records.

The harness, its scrub, grader and extractor are `../tidy-followups-builds-one-parent/`'s. This
wrapper changes module constants and nothing in that directory: the base arm is the skill at
`4722bd0b45f838ecd02b17baa90931e4d47166e6` (before drop and merge), the builder is this case's, and
`--shim` picks which of the fixture's three environments becomes the arm's `env` (the plain one, or
the one whose `lore` shim fails one named write).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD_CASE = HERE.parent / "tidy-followups-builds-one-parent"
BASE_SHA = "4722bd0b45f838ecd02b17baa90931e4d47166e6"

sys.path.insert(0, str(OLD_CASE))
_spec = importlib.util.spec_from_file_location("builds_one_parent_harness", OLD_CASE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

_built: dict = {}


def fill(answers: str, manifest: dict, cell: str) -> str:
    names = {**manifest["followups"].get(cell, {}), **manifest.get("others", {}).get(cell, {})}
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
    shim = "env"
    if argv[:1] == ["cell"]:
        work, cell, arm = argv[1:4]
        spec = json.loads((HERE / "cells.json").read_text())[cell]
        argv = ["run", work, cell, arm, "--spec", cell, "--answers", spec["answers"],
                "--shim", spec["shim"]]
    if "--shim" in argv:
        i = argv.index("--shim")
        shim = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if argv[:1] == ["run"]:
        _built["cell"] = argv[argv.index("--spec") + 1]
        real_build = harness.build

        def build(work, run, snap):
            manifest = real_build(work, run, snap)
            manifest["plain_env"] = manifest["env"]
            manifest["env"] = manifest[shim]
            _built["manifest"] = manifest
            return manifest

        harness.build = build
    sys.argv = [sys.argv[0], *argv]
    return harness.main()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
