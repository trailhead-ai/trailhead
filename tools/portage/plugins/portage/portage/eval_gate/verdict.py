"""Aggregate case results into a push decision and a pull-request section.

``verdict(state)`` reads ``<state>/plan.json`` (shape in ``select``) and
``<state>/results/<case>.json`` (shape in ``run``) and decides push or stop.
``splice`` inserts or replaces the rendered section in a PR body. The state
directory goes through ``run._check_state`` (not a symlink, caller-owned, 0700);
``canary.json`` at the state root and ``runs/`` (raw runner output) are never read.

The section is built only from shape-validated case names, corpus paths and
grader names, and from the closed reason set (``run``'s plus ``no-result-recorded``
for a planned case with no usable result). Result fields outside that vocabulary
make the result unusable, i.e. ``no-result-recorded``; they are never echoed.

Handoffs the updater (child 7) depends on
-----------------------------------------
Markers, each on its own line, wrapping the section::

    <!-- portage:eval-gate:start -->
    ## Eval gate
    ...
    <!-- portage:eval-gate:end -->

``portage eval-gate verdict --state <dir>`` stdout, in order:

1. the leading report line ``eval-gate: <outcome>`` with outcome one of ``passed``,
   ``failed``, ``not-run``, ``no-cases-mapped``, ``base-unresolved``,
   ``mapping-invalid``, ``gate-error``;
2. ``persistent: <codes>`` — the sorted, comma-joined persistent reason codes among
   the not-run cases (``runner-missing``, ``harness-has-no-eval-runner``,
   ``runner-sandbox-unwritable``: nothing about the branch will change them), or
   ``persistent: none``. When the outcome is ``base-unresolved`` the line is
   ``persistent: base-unresolved`` (a repo with no resolvable base hits it on every
   push). ``case-removed-on-branch`` is not persistent. ``persistent_reasons`` is the
   classification of reason codes;
3. ``ignored:`` lines for result files whose case is not in the plan;
4. on exit 1, the failing cases with counts (``<case>: passed P, failed F, errored E``),
   ``state kept: <dir>``, per-run grader verdicts (``run N: <grader>=pass|fail ...``) and
   the sanctioned ways forward, or ``mapping-invalid: <file>: <fault>``;
5. the section, markers included.

The section is also written to ``<state>/eval-gate-section.md`` (the printed section
plus a final newline) so an agent that cannot redirect shell output can hand it to
``splice --state <dir>``. ``verdict --gate-error <step>`` reads no state; with
``--state`` it still writes the section file there.

Exit codes: 0 push, 1 stop, 2 usage, unusable state directory/plan, an OS error reading or writing, or a marker fault.
A fault writing the section file never turns a decided stop into 2: with a stop decided the
fault goes to stderr, the section is still printed and the exit code stays 1.

``portage eval-gate splice --body-file <f> (--state <dir> | --section-file <f>)
[--out <path>]`` prints the body with the section appended (no section in the body) or
replacing the existing one, byte-for-byte elsewhere; with ``--out`` it writes the body to
that file instead of stdout. A start marker without an end marker, an end before a start,
or more than one section in the body (or a section file without exactly one marker pair)
exits 2 and writes nothing.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

from .mapping import CASE_NAME
from .run import (
    NO_RUNNER,
    RUNNER_MISSING,
    CASE_REMOVED,
    SANDBOX_UNWRITABLE,
    RunRefused,
    _check_state,
    atomic_write,
)

START_MARKER = "<!-- portage:eval-gate:start -->"
END_MARKER = "<!-- portage:eval-gate:end -->"
SECTION_FILE = "eval-gate-section.md"
NO_RESULT = "no-result-recorded"

PERSISTENT = (NO_RUNNER, RUNNER_MISSING, SANDBOX_UNWRITABLE)

REASONS = {
    "harness-has-no-eval-runner": "this harness has no eval runner",
    "runner-missing": "the eval runner binary was not found on this machine",
    "timeout": "the runner was killed at its time limit before scoring enough runs",
    "cost-cap": "the runner stopped at its cost ceiling before scoring enough runs",
    "runner-error": "the runner failed or its authentication failed",
    "runs-errored": "too many runs errored to reach a verdict",
    SANDBOX_UNWRITABLE: (
        "the eval runner on this machine cannot write inside its sandbox, so no case was "
        "scored; set `PORTAGE_EVAL_RUNNER` to a runner build that can write, or wait for "
        "a fixed runner"
    ),
    CASE_REMOVED: "the branch removes this case; it was not run — review the removal",
    NO_RESULT: "no usable result was recorded for this case",
}

_STEP = re.compile(r"^[a-z][a-z0-9-]{0,31}\Z")
_GRADER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_CORPUS = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,199}\Z")
_STATUSES = ("cases-selected", "no-cases-mapped", "base-unresolved", "mapping-invalid")


class Fault(Exception):
    """Usage, state or marker fault — exit 2."""


def persistent_reasons(reasons) -> list[str]:
    """The persistent codes among ``reasons``, sorted and de-duplicated."""
    return sorted({r for r in reasons if r in PERSISTENT})


def _safe(text: object, limit: int = 300) -> str:
    return "".join(c for c in str(text) if c.isprintable())[:limit]


def one_line(exc: BaseException) -> str:
    return _safe(" ".join(str(exc).split()))


def _corpus(root: object) -> str:
    if isinstance(root, str) and _CORPUS.match(root) and ".." not in PurePosixPath(root).parts:
        return root
    return "<invalid-corpus-path>"


def _count(doc: dict, key: str) -> int:
    v = doc.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError(key)
    return v


def _usable(doc: object, case: str) -> dict | None:
    """The result reduced to its closed vocabulary, or None when it is not trustworthy."""
    try:
        if doc["case"] != case or doc["verdict"] not in ("pass", "fail", "not-run"):
            return None
        reason = doc["reason"]
        if doc["verdict"] == "not-run":
            if reason not in REASONS or reason == NO_RESULT:
                return None
        elif reason is not None:
            return None
        runs = []
        for run in doc["runs"]:
            graders = [
                {"name": g["name"] if _GRADER.match(g["name"]) else "<invalid-grader-name>",
                 "passed": g["passed"] is True}
                for g in run["graders"]
                if isinstance(g["name"], str)
            ]
            runs.append(graders)
        return {"verdict": doc["verdict"], "reason": reason, "runs": runs,
                "passed": _count(doc, "passed"), "failed": _count(doc, "failed"),
                "errored": _count(doc, "errored")}
    except (KeyError, TypeError, ValueError):
        return None


def _read_regular(path: Path) -> object:
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _load_plan(state: Path) -> dict:
    try:
        plan = json.loads((state / "plan.json").read_text())
        status = plan["status"]
        cases = plan["cases"]
        if status not in _STATUSES or not isinstance(cases, list):
            raise ValueError("unknown status or cases")
        for c in cases:
            if not isinstance(c["case"], str) or not CASE_NAME.match(c["case"]):
                raise ValueError(f"case name {_safe(c['case'], 60)!r} must match {CASE_NAME.pattern}")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Fault(f"plan.json unusable: {_safe(e)}") from e
    return plan


def _collect(state: Path, plan: dict) -> tuple[list[dict], list[str], int]:
    """Per planned case its usable result (or None); ignored valid names; invalid-name count."""
    results_dir = state / "results"
    planned = {c["case"] for c in plan["cases"]}
    found: dict[str, dict | None] = {}
    ignored: list[str] = []
    invalid = 0
    try:
        is_dir = stat.S_ISDIR(os.lstat(results_dir).st_mode)
    except OSError:
        is_dir = False
    names = sorted(p.name for p in results_dir.iterdir()) if is_dir else []
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        case = name[: -len(".json")]
        if not CASE_NAME.match(case):
            invalid += 1
        elif case not in planned:
            ignored.append(case)
        else:
            found[case] = _usable(_read_regular(results_dir / name), case)
    rows = []
    for c in plan["cases"]:
        res = found.get(c["case"])
        rows.append({"case": c["case"], "corpus": _corpus(c.get("plugin_root")),
                     "grants": c.get("new_runner_grants") is True,
                     "result": res or {"verdict": "not-run", "reason": NO_RESULT, "runs": [],
                                       "passed": 0, "failed": 0, "errored": 0}})
    return rows, ignored, invalid


def _wrap(lines: list[str]) -> str:
    return "\n".join([START_MARKER, "## Eval gate", "", *lines, END_MARKER])


def _bullets(items: list[str]) -> list[str]:
    return [f"- {i}" for i in items]


def _grants_lines(plan: dict, rows: list[dict]) -> list[str]:
    corpora = sorted({r["corpus"] for r in rows if r["grants"]})
    if not corpora and plan.get("new_runner_grants") is True:
        corpora = ["<unspecified>"]
    if not corpora:
        return []
    names = ", ".join(f"`{c}`" for c in corpora)
    return ["", f"new-runner-grants: the branch authors the runner permissions for {names}; "
                "review them before merging."]


def _extras(plan, rows, ignored, invalid) -> list[str]:
    out = _grants_lines(plan, rows)
    if ignored:
        out += ["", "Ignored results for cases not in the plan: " + ", ".join(f"`{c}`" for c in ignored)]
    if invalid:
        out += ["", f"Ignored {invalid} result file(s) with an invalid name."]
    return out


def _not_run_items(rows: list[dict]) -> list[str]:
    return [f"`{r['case']}` — `{r['result']['reason']}`: {REASONS[r['result']['reason']]}"
            for r in rows if r["result"]["verdict"] == "not-run"]


def _failure_details(rows: list[dict], state: Path) -> list[str]:
    out = []
    for r in rows:
        res = r["result"]
        if res["verdict"] != "fail":
            continue
        out.append(f"{r['case']}: passed {res['passed']}, failed {res['failed']}, errored {res['errored']}")
        for i, graders in enumerate(res["runs"], 1):
            verdicts = " ".join(f"{g['name']}={'pass' if g['passed'] else 'fail'}" for g in graders)
            out.append(f"  run {i}: {verdicts}".rstrip())
    out.append(f"state kept: {state}")
    out.append("Ways forward: fix the prose or the case on the branch, or re-run the gate. "
               "There is no override.")
    return out


def verdict(state: Path) -> tuple[int, list[str], str]:
    """Return ``(exit_code, report_lines, section)`` for the state directory."""
    state = Path(state)
    require_state(state)
    plan = _load_plan(state)
    status = plan["status"]

    if status == "mapping-invalid":
        bad = plan.get("mapping_invalid") if isinstance(plan.get("mapping_invalid"), dict) else {}
        file, fault = _corpus(bad.get("file")), _safe(bad.get("fault", "unspecified"))
        section = _wrap([f"The eval-case mapping `{file}` is invalid, so no case was selected. "
                         "The mapping must be fixed on the branch."])
        return 1, ["eval-gate: mapping-invalid", "persistent: none",
                   f"mapping-invalid: {file}: {fault}",
                   "The mapping must be fixed on the branch; there is no override."], section
    if status == "base-unresolved":
        return 0, ["eval-gate: base-unresolved", "persistent: base-unresolved"], _wrap(
            ["The eval gate could not determine the changed files (the base ref did not resolve), "
             "so it selected no cases."])
    if status == "no-cases-mapped" or not plan["cases"]:
        return 0, ["eval-gate: no-cases-mapped", "persistent: none"], _wrap(
            ["No eval cases are mapped to this change."])

    rows, ignored, invalid = _collect(state, plan)
    failed = [r for r in rows if r["result"]["verdict"] == "fail"]
    notrun = [r for r in rows if r["result"]["verdict"] == "not-run"]
    persistent = persistent_reasons(r["result"]["reason"] for r in notrun)
    outcome = "failed" if failed else "not-run" if notrun else "passed"
    report = [f"eval-gate: {outcome}", f"persistent: {','.join(persistent) or 'none'}"]
    if ignored:
        report.append("ignored: " + ", ".join(ignored))
    if invalid:
        report.append(f"ignored: {invalid} result with an invalid name")
    if failed:
        report += _failure_details(rows, state)

    body: list[str] = []
    if failed:
        body += ["Failed — the gate stopped the push.", ""]
        body += _bullets([f"`{r['case']}`: passed {r['result']['passed']}, failed "
                          f"{r['result']['failed']}, errored {r['result']['errored']}" for r in failed])
    passed = [r for r in rows if r["result"]["verdict"] == "pass"]
    if not failed and not notrun:
        body += [f"Passed — {len(passed)} eval case(s) ran and passed.", ""]
        body += _bullets([f"`{r['case']}`" for r in passed])
    elif passed:
        body += ["", "Passed:", ""] if failed else ["Some cases passed:", ""]
        body += _bullets([f"`{r['case']}`" for r in passed])
    if notrun:
        body += ["", "Did not run:", ""] if body else ["Did not run — no verdict was reached for:", ""]
        body += _bullets(_not_run_items(rows))
    body += _extras(plan, rows, ignored, invalid)
    return (1 if failed else 0), report, _wrap(body)


def render_gate_error(step: str) -> str:
    if not _STEP.match(step):
        raise Fault(f"gate-error step {_safe(step, 40)!r} must match {_STEP.pattern}")
    return _wrap([f"The eval gate errored at step `{step}`, so no eval verdict was produced. "
                  "Re-run the gate."])


def require_state(state: Path) -> None:
    try:
        _check_state(Path(state))
    except RunRefused as e:
        raise Fault(str(e)) from e


def write_section_file(state: Path, section: str) -> None:
    state = Path(state)
    require_state(state)
    atomic_write(state / SECTION_FILE, section + "\n")


def _span(text: str) -> tuple[int, int] | None:
    """The ``[start, end)`` span of the single section in ``text``; None when there is none."""
    starts, ends = text.count(START_MARKER), text.count(END_MARKER)
    if starts == 0 and ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise Fault(f"expected one eval-gate section, found {starts} start and {ends} end marker(s)")
    a, b = text.index(START_MARKER), text.index(END_MARKER)
    if b < a:
        raise Fault("eval-gate end marker precedes its start marker")
    return a, b + len(END_MARKER)


def splice(body: str, section: str) -> str:
    """``body`` with ``section`` inserted (appended) or replacing the existing one."""
    if _span(section) is None:
        raise Fault("section has no eval-gate markers")
    section = section[slice(*_span(section))]
    span = _span(body)
    if span is None:
        return section + "\n" if not body.strip() else body.rstrip("\n") + "\n\n" + section + "\n"
    return body[: span[0]] + section + body[span[1]:]
