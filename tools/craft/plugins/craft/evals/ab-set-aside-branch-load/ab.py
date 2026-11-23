#!/usr/bin/env python3
"""Driver for the set-aside branch-load A/B (see expected.md). The logic is the shared driver of
`../ab-found-work-branch-load/ab.py`; this file holds only what differs: the section moved, its branch
file, the load sentence and the citation it replaces. Commands as there:

  ab.py check | prepare ARM CASE DEST | count-reads TRACE | evidence TRACE ROOT ARM | grants | bar

Stdlib only. The live skills/ tree is never written."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent
_path = EXP.parent / "ab-found-work-branch-load" / "ab.py"
_spec = importlib.util.spec_from_file_location("ab_shared", _path)
shared = importlib.util.module_from_spec(_spec)
sys.modules["ab_shared"] = shared
_spec.loader.exec_module(shared)

EXPERIMENT = shared.Experiment(
    dir=EXP,
    heading="### A criterion waiting on something outside the work",
    branch_file="waiting-criterion.md",
    load="read `../_shared/waiting-criterion.md` now and follow it in full.",
    citation="(#a-criterion-waiting-on-something-outside-the-work)",
)

check_arms = lambda inline, branch: shared.check_arms(inline, branch, EXPERIMENT)  # noqa: E731
build_arm_plugin = lambda arm, case, dest, craft_root=None: shared.build_arm_plugin(arm, case, dest, craft_root, EXPERIMENT)  # noqa: E731

if __name__ == "__main__":
    sys.exit(shared.main(sys.argv, EXPERIMENT))
