"""The merge-tier backstop behind ``portage merge --eval-state``.

``check_pair`` decides, for one pull request's repo, whether a merge may go ahead and which head
commit to pin it to. It applies only when the repo's change touches a ``merge_only`` eval case
(decided by ``select.merge_only_touched``, the selection ``plan`` uses); anywhere else it answers
"not applicable" and the merge is unchanged. Where it applies, every one of these refuses, and the
check name is what the refusal prints::

    no-state                    no ``--eval-state`` was given for the repo
    state-unusable              ``run.check_state`` refused the state directory
    plan-unusable               ``plan.json`` is missing, unparsable or has an unknown shape
    state-tier-not-merge        the plan was made at the push tier
    state-for-another-repo      the plan's ``repo`` is not this repo
    plan-not-selected           the plan's status is not ``cases-selected`` (a dirty tree, an invalid
                                mapping, an unresolved base, or nothing selected)
    plan-head-malformed         the plan's ``head`` is not a full lowercase hex commit id
    plan-misses-merge-only-case the plan does not list a merge-only case this change touches
    head-unreadable             the PR's remote head could not be read
    head-mismatch               the plan's ``head`` is not the PR's current remote head
    verdict-stops-the-change    ``verdict`` on the state exits non-zero, or cannot be computed
    selection-failed            git could not answer which cases the change touches
    local-head-stale            the change touches no merge-only case, but the repo holds an eval-gate
                                mapping and the PR's remote head is not the local ``HEAD`` the
                                "touches" answer was computed from (a remote-only commit); pull the
                                PR head and retry

A case is merge-only when the merge-base's or HEAD's committed mapping marks it; a dirty working
tree changes nothing. When the applicability is unknown (the base does not resolve, or a mapping is
invalid) the check applies: an unknown answer is never read as "no merge-only case". Where the check
does not apply but the repo holds any ``eval-gate.json`` mapping (at HEAD or the merge-base), the merge
is pinned to the remote head, and refused when that head is not local ``HEAD``; a repo with no mapping
merges unpinned, with no head read. A failure of git or the OS to answer is ``selection-failed``, and
only git's explicit "not a git repository" makes the check not applicable. The contents of ``plan.json`` are
untrusted; the head is shape-checked before it is compared or reaches a ``gh`` argv.

The stderr form is ``portage merge: eval-gate check failed for <repo>: <check> — <detail>``
followed by the remedy commands; ``format_refusal`` builds it.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from trailhead.vcs.github import InvalidInputError, validate_commit_sha

from .run import RunRefused, check_state
from .select import GitError, NotAWorkTree, local_head, merge_only_touched, repo_has_mapping
from . import verdict as gate_verdict


STALE_HEAD = "local-head-stale"


@dataclass(frozen=True)
class Refusal:
    check: str
    detail: str
    base_unresolved: bool = False


def parse_eval_states(values: list[str]) -> dict[str, Path]:
    """``<repo_path>=<state_dir>`` pairs keyed by resolved repo path. Raises ``ValueError``."""
    states: dict[str, Path] = {}
    for value in values:
        repo, sep, state = value.partition("=")
        if not sep or not repo or not state:
            raise ValueError(f"--eval-state expects <repo_path>=<state_dir>, got {value!r}")
        key = str(Path(repo).resolve())
        if key in states:
            raise ValueError(f"--eval-state given twice for {repo!r}")
        states[key] = Path(state)
    return states


def _applies(repo: Path) -> tuple[bool, str, list[dict]]:
    """``(applies, status, merge_only_entries)``; raises ``GitError`` when git cannot answer."""
    try:
        status, entries = merge_only_touched(repo)
    except NotAWorkTree:
        return False, "not-a-work-tree", []
    return (status == "mapping-invalid" or bool(entries)), status, entries


def _pin_untouched(repo: Path, status: str, read_head: Callable[[], str]) -> tuple[Refusal | None, str | None]:
    if status == "not-a-work-tree":
        return None, None
    try:
        if not repo_has_mapping(repo):
            return None, None
        local = local_head(repo)
    except (GitError, OSError) as e:
        return Refusal("selection-failed", gate_verdict.one_line(e)), None
    try:
        remote = validate_commit_sha(read_head())
    except (RuntimeError, InvalidInputError) as e:
        return Refusal("head-unreadable", gate_verdict.one_line(e)), None
    if remote != local:
        return Refusal(STALE_HEAD, f"the PR's head is {remote}, this checkout is at {local}; "
                                           "the commits that landed on the PR were not checked here"), None
    return None, remote


def check_pair(
    repo_path: str,
    state: Path | None,
    read_head: Callable[[], str],
) -> tuple[Refusal | None, str | None]:
    """``(None, None)`` when the check does not apply, ``(None, head)`` when it passed (pin the
    merge to ``head``), ``(Refusal, None)`` when the merge must be refused."""
    repo = Path(repo_path)
    try:
        applies, status, merge_only = _applies(repo)
    except (GitError, OSError) as e:
        return Refusal("selection-failed", gate_verdict.one_line(e)), None
    if not applies:
        return _pin_untouched(repo, status, read_head)

    def refuse(check: str, detail: str, unresolved: bool = False) -> Refusal:
        return Refusal(check, detail, unresolved or status == "base-unresolved")

    if state is None:
        return refuse("no-state", "this change touches a merge-only eval case and no --eval-state "
                                   "was given for this repo"), None
    try:
        check_state(state)
    except RunRefused as e:
        return refuse("state-unusable", gate_verdict.one_line(e)), None
    try:
        plan = gate_verdict._load_plan(state)
    except gate_verdict.Fault as e:
        return refuse("plan-unusable", gate_verdict.one_line(e)), None
    if plan.get("tier") != "merge":
        return refuse("state-tier-not-merge", "the state was planned at the push tier; plan with "
                                               "--tier merge"), None
    try:
        same_repo = Path(plan["repo"]).resolve() == repo.resolve()
    except (KeyError, TypeError, ValueError, OSError):
        same_repo = False
    if not same_repo:
        return refuse("state-for-another-repo", "the state's plan was made for a different repo"), None
    if plan["status"] != "cases-selected":
        return refuse("plan-not-selected", f"the plan's status is {gate_verdict._safe(plan['status'], 40)!r}, "
                                           "not 'cases-selected'",
                      plan["status"] == "base-unresolved"), None
    try:
        planned_head = validate_commit_sha(plan.get("head"))
    except InvalidInputError as e:
        return refuse("plan-head-malformed", str(e)), None
    if status == "ok":
        planned = {(c.get("plugin_root"), c["case"]) for c in plan["cases"]}
        missing = sorted(c["case"] for c in merge_only if (c["plugin_root"], c["case"]) not in planned)
        if missing:
            return refuse("plan-misses-merge-only-case",
                           "the plan does not list " + ", ".join(missing)), None
    try:
        remote_head = validate_commit_sha(read_head())
    except (RuntimeError, InvalidInputError) as e:
        return refuse("head-unreadable", gate_verdict.one_line(e)), None
    if remote_head != planned_head:
        return refuse("head-mismatch", f"the PR's head is {remote_head}, the state was made at "
                                        f"{planned_head}; a push landed after the gate"), None
    try:
        code, report, _ = gate_verdict.verdict(state)
    except (gate_verdict.Fault, OSError) as e:
        return refuse("verdict-stops-the-change", f"verdict could not be computed: "
                                                   f"{gate_verdict.one_line(e)}"), None
    if code != 0:
        return refuse("verdict-stops-the-change", "; ".join(report[:3])), None
    return None, planned_head


def format_refusal(repo_path: str, refusal: Refusal) -> str:
    shown = gate_verdict._safe(repo_path)
    quoted = shlex.quote(repo_path)
    base = " --base <ref>" if refusal.base_unresolved else ""
    first = (f"portage merge: eval-gate check failed for {shown}: {refusal.check} — "
             f"{gate_verdict._safe(refusal.detail)}")
    if refusal.check == STALE_HEAD:
        return "\n".join([first, f"portage merge: pull the PR head into {quoted} (the PR branch at its "
                                  "remote head) and retry the merge."])
    return "\n".join([
        first,
        "portage merge: a change that touches a merge-only eval case needs a current merge-tier "
        "verdict before it merges (--operator-directed does not waive this). To produce one:",
        f"  portage eval-gate plan --repo {quoted} --tier merge{base}     # prints `state: <dir>`",
        "  portage eval-gate run --state <dir> --case <case>      # once per planned case",
        "  portage eval-gate verdict --state <dir>                 # must exit 0",
        f"then merge with --eval-state {quoted}=<dir>",
    ])
