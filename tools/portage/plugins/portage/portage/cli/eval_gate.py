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

``run --state <dir> --case <name> [--timeout-seconds N]`` runs one planned case and
writes ``<state>/results/<case>.json`` (shape in ``portage.eval_gate.run``). Output:
``case: <name>``, ``verdict: pass|fail|not-run``, ``reason: <code>`` (not-run only).
Exit 0 whenever a result was written, whatever the verdict; exit 2 on a refusal
(state directory, case name, plugin root, timeout) with no runner started.

``verdict (--state <dir> | --gate-error <step>)`` aggregates the results into a push
decision (exit 0 push, 1 stop, 2 fault) and prints the PR section; ``splice --body-file
<f> (--state <dir> | --section-file <f>) [--out <path>]`` inserts it into a PR body. Output
format, markers, exit codes and the ``persistent:`` line are documented in
``portage.eval_gate.verdict``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..eval_gate.run import DEFAULT_TIMEOUT_SECONDS, RunRefused, run_case
from ..eval_gate import verdict as gate_verdict
from ..eval_gate.select import GitError, plan


def add_eval_gate_subparser(sub) -> None:
    p = sub.add_parser(
        "eval-gate",
        help="Select and run the prose-eval cases a branch touches.",
        description="Select and run the prose-eval cases a branch touches.",
    )
    group = p.add_subparsers(dest="eval_gate_command", required=True)
    _add_plan(group)
    _add_run(group)
    _add_verdict(group)
    _add_splice(group)


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


def _add_run(group) -> None:
    p = group.add_parser(
        "run",
        help="Run one planned case through the harness's eval runner.",
        description="Run one planned case through the harness's eval runner and record a verdict.",
    )
    p.add_argument("--state", required=True, help="State directory written by `plan`")
    p.add_argument("--case", required=True, help="Case name from plan.json")
    p.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS,
                   help="Wall-clock bound on the runner (max 540)")
    p.set_defaults(func=cmd_run)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        result = run_case(Path(args.state), args.case, timeout_seconds=args.timeout_seconds)
    except RunRefused as e:
        print(f"eval-gate run: {e}", file=sys.stderr)
        return 2
    print(f"case: {result['case']}")
    print(f"verdict: {result['verdict']}")
    if result["reason"]:
        print(f"reason: {result['reason']}")
    return 0


def _add_verdict(group) -> None:
    p = group.add_parser(
        "verdict",
        help="Decide push or stop from the case results and print the PR section.",
        description="Decide push (exit 0) or stop (exit 1) from the case results and print the "
                    "`## Eval gate` section.",
    )
    p.add_argument("--state", help="State directory written by `plan`")
    p.add_argument("--gate-error", metavar="STEP",
                   help="Render the gate-error section for a crashed step; reads no state")
    p.set_defaults(func=cmd_verdict)


def cmd_verdict(args: argparse.Namespace) -> int:
    try:
        if args.gate_error is not None:
            code, report, section = 0, ["eval-gate: gate-error"], gate_verdict.render_gate_error(args.gate_error)
        elif args.state is None:
            raise gate_verdict.Fault("one of --state or --gate-error is required")
        else:
            code, report, section = gate_verdict.verdict(Path(args.state))
        if args.state is not None:
            gate_verdict.write_section_file(Path(args.state), section)
    except (gate_verdict.Fault, OSError) as e:
        print(f"eval-gate verdict: {gate_verdict.one_line(e)}", file=sys.stderr)
        return 2
    for line in report:
        print(line)
    print(section)
    return code


def _add_splice(group) -> None:
    p = group.add_parser(
        "splice",
        help="Insert or replace the eval-gate section in a PR body.",
        description="Insert or replace the eval-gate section in a PR body.",
    )
    p.add_argument("--body-file", required=True, help="File holding the PR body")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--state", help="State directory whose eval-gate-section.md to splice")
    src.add_argument("--section-file", help="File holding the rendered section")
    p.add_argument("--out", default=None, help="Write the spliced body here instead of stdout")
    p.set_defaults(func=cmd_splice)


def cmd_splice(args: argparse.Namespace) -> int:
    try:
        section_path = (Path(args.state) / gate_verdict.SECTION_FILE) if args.state \
            else Path(args.section_file)
        if args.state:
            gate_verdict.require_state(Path(args.state))
        body = Path(args.body_file).read_text()
        section = section_path.read_text()
        result = gate_verdict.splice(body, section)
        if args.out:
            Path(args.out).write_text(result)
    except (gate_verdict.Fault, OSError, UnicodeDecodeError) as e:
        print(f"eval-gate splice: {gate_verdict.one_line(e)}", file=sys.stderr)
        return 2
    if not args.out:
        sys.stdout.write(result)
    return 0
