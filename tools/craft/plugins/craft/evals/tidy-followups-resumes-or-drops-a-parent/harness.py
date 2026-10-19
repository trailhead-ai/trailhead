#!/usr/bin/env python3
"""Dispatch for the resume-or-drop eval: the existing write-path harness with this case's arms.

    harness.py [--fixture new|old] snapshot <work> --head SHA
    harness.py [--fixture new|old] run <work> <cell> <arm> --spec KEY --answers TEXT [--env env|nestfail_env]

The harness, its scrub, grader and extractor are `../tidy-followups-builds-one-parent/`'s. This
wrapper changes two of its module constants and nothing in that directory: the base arm is the
skill at `7a48b25e5ef7cf054fbb595bc99d29bea36d5b07` (the existing case's own pinned base stays
`ce19d0ce`, which its recorded runs mean), and `--fixture` picks the builder (`new`: this case's;
`old`: the existing case's, for its re-registered `nest-fail` cell).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD_CASE = HERE.parent / "tidy-followups-builds-one-parent"
BASE_SHA = "7a48b25e5ef7cf054fbb595bc99d29bea36d5b07"

sys.path.insert(0, str(OLD_CASE))
_spec = importlib.util.spec_from_file_location("builds_one_parent_harness", OLD_CASE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)


def main(argv: list[str]) -> int:
    fixture = "new"
    if argv[:1] == ["--fixture"]:
        fixture, argv = argv[1], argv[2:]
    if fixture not in ("new", "old"):
        raise SystemExit("--fixture is new or old")
    harness.BASE_SHA = BASE_SHA
    harness.BUILDER = (HERE if fixture == "new" else OLD_CASE) / "fixtures" / "make-fixture-vault.py"
    sys.argv = [sys.argv[0], *argv]
    return harness.main()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
