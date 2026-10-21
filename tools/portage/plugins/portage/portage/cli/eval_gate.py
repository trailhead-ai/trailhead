"""``portage eval-gate`` — run the prose-eval cases a branch touches.

A command group: each subcommand is registered by its own ``_add_*`` function on
the group's subparser, so a new one never reshapes the others.

``plan --repo <path> [--base <ref>]`` selects the cases the branch touches and
writes ``<state>/plan.json`` into a fresh 0700 state directory (shape documented in
``portage.eval_gate.select``). Output: ``state: <dir>``, ``status: <status>``,
``cases: <n>``, ``max_cost_usd: <sum>``, then one ``<plugin_root>:<case>`` line per
selected case (and a ``mapping-invalid: <file>: <fault>`` line when a mapping is
refused). Exit 0 whenever a plan was written, including every non-selecting
status; exit 2 only for usage errors (e.g. ``--repo`` is not a git work tree).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..eval_gate.select import GitError, plan


def add_eval_gate_subparser(sub) -> None:
    p = sub.add_parser(
        "eval-gate",
        help="Select and run the prose-eval cases a branch touches.",
        description="Select and run the prose-eval cases a branch touches.",
    )
    group = p.add_subparsers(dest="eval_gate_command", required=True)
    _add_plan(group)


def _add_plan(group) -> None:
    p = group.add_parser(
        "plan",
        help="Select the cases the branch touches and write plan.json.",
        description="Select the cases the branch touches and write plan.json.",
    )
    p.add_argument("--repo", required=True, help="Path to the git repository")
    p.add_argument("--base", default=None, help="Base ref (default: origin/HEAD's target)")
    p.set_defaults(func=cmd_plan)


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        state, result = plan(Path(args.repo), args.base)
    except (GitError, OSError) as e:
        print(f"eval-gate plan: {e}", file=sys.stderr)
        return 2
    print(f"state: {state}")
    print(f"status: {result['status']}")
    print(f"cases: {len(result['cases'])}")
    print(f"max_cost_usd: {result['total_max_cost_usd']}")
    if result["mapping_invalid"]:
        bad = result["mapping_invalid"]
        print(f"mapping-invalid: {bad['file']}: {bad['fault']}")
    for c in result["cases"]:
        print(f"{c['plugin_root']}:{c['case']}")
    return 0
