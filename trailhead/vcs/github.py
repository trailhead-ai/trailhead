"""GitHubProvider — the GitHub backend for the VCS interface (repos/pr/ci).

Ports the pure callables out of craft's release scripts, routing every gh/git
call through ``trailhead.vcs.runner`` (shell=False, injectable). camp stays the
membership source of truth: ``repos.detect()`` consumes the manifest, it does
not own membership.

Ported sources (logic lifted, not imported — craft's copies stay in place until
the release cluster is deleted):
  - detect_repos.py          → repos.detect()
  - release_prs_sidecar.py   → pr.open() / pr.read_sidecar()
  - check_pr_status.py       → pr.status() + ci.checks()
  - pr_evaluate_status.py    → pr.evaluate()
  - merge_prs.py             → pr.merge() (ordered, with the merge safety gate)
  - wait_for_actionable.py   → ci.wait()

Safety invariants preserved: the merge gate, shell=False, option-injection
guards (pr_number digits-only, branch leading-dash / push ``--`` terminator),
no hardcoded review-bot login (a passed param). The pr_number digits-only
guard is enforced inside ``_check_status`` itself (not just at the merge call
site), so every caller — status/checks/wait reads included — is covered
before anything reaches ``gh`` argv.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from trailhead.vcs import runner as rp
from trailhead.vcs.untrusted import wrap_untrusted
from trailhead.vcs.interface import (
    CISurface,
    PRSurface,
    Provider,
    ReposSurface,
)


# ---------------------------------------------------------------------------
# Exceptions (ported, re-homed under trailhead.vcs.github)
# ---------------------------------------------------------------------------


class ManifestReadError(Exception):
    """Raised on a missing or malformed camp manifest (path in the message)."""


class SidecarError(Exception):
    """Raised on a missing, malformed, or schema-invalid prs.json sidecar."""


class MergeOrderRequiredError(Exception):
    """Raised when >1 PR is queued but no merge_order is declared."""


class MergeConfigError(Exception):
    """Raised when merge_order names a member not in the manifest."""


class AutoMergeDisabledError(Exception):
    """Raised when auto_merge is absent or false in the [release] block.

    Fail-closed default: existing installs that never opted into auto_merge
    must not silently keep merging once this gate lands.
    """


class InvalidInputError(Exception):
    """Raised on option-injection attack vectors (pr_number / branch validation)."""


@dataclass
class PRPair:
    repo_path: str
    pr_number: str
    member_name: str


# ---------------------------------------------------------------------------
# Shared manifest reader (ported from manifest_read.py)
# ---------------------------------------------------------------------------


def _load_manifest(manifest_path: str) -> dict[str, Any]:
    p = Path(manifest_path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestReadError(f"cannot read manifest at {manifest_path}: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestReadError(f"malformed manifest at {manifest_path}: {e}") from e
    if not isinstance(data, dict):
        raise ManifestReadError(f"manifest at {manifest_path} is not a JSON object")
    return data


# ---------------------------------------------------------------------------
# repos.detect — ported from detect_repos.py
# ---------------------------------------------------------------------------


def _inspect(repo_name: str, worktree_path: str, *, runner: rp.Runner) -> dict | None:
    r = rp.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=worktree_path,
        runner=runner,
    )
    if r.returncode != 0:
        return None
    branch = r.stdout.strip()
    if not branch or branch == "main":
        return None

    ahead_r = rp.run(
        ["git", "rev-list", "origin/main..HEAD", "--count"],
        cwd=worktree_path,
        runner=runner,
    )
    ahead = int(ahead_r.stdout.strip() or "0") if ahead_r.returncode == 0 else 0

    dirty_r = rp.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_path,
        runner=runner,
    )
    dirty_lines = dirty_r.stdout.splitlines() if dirty_r.returncode == 0 else []
    dirty = len([line for line in dirty_lines if line.strip()])

    if ahead == 0 and dirty == 0:
        return None

    return {
        "repo": repo_name,
        "path": worktree_path,
        "branch": branch,
        "ahead": ahead,
        "dirty": dirty,
    }


class _GitHubRepos(ReposSurface):
    def __init__(self, runner: rp.Runner) -> None:
        self._runner = runner

    def detect(self, manifest_path: str) -> list[dict]:
        data = _load_manifest(manifest_path)
        members = data.get("members", [])

        active: list[dict] = []
        for member in members:
            name = member.get("name", "")
            wt_path = member.get("worktree_path", "")

            # Graceful degrade — missing worktree_path → skip.
            if not wt_path or not Path(wt_path).exists():
                continue

            entry = _inspect(name, wt_path, runner=self._runner)
            if entry is not None:
                active.append(entry)

        return active


# ---------------------------------------------------------------------------
# gh / status helpers — ported from check_pr_status.py
# ---------------------------------------------------------------------------


def _gh(args: list[str], cwd: str, runner: rp.Runner) -> Any | None:
    r = rp.run(["gh"] + args, cwd=cwd, runner=runner)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _get_owner_repo(cwd: str, runner: rp.Runner) -> str | None:
    r = rp.run(["git", "remote", "get-url", "origin"], cwd=cwd, runner=runner)
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def _fetch_annotations(
    check: dict,
    owner_repo: str | None,
    cwd: str,
    runner: rp.Runner,
    max_annotations: int = 10,
) -> list[dict]:
    link = check.get("link", "")
    m = re.search(r"/actions/runs/(\d+)/job/(\d+)", link)
    if not m or not owner_repo:
        return []
    job_id = m.group(2)
    raw = _gh(
        [
            "api",
            f"repos/{owner_repo}/check-runs/{job_id}/annotations",
            "--paginate",
            "-q",
            '[.[] | select(.annotation_level=="failure") | {path, start_line, message: .message}]',
        ],
        cwd=cwd,
        runner=runner,
    )
    if not raw:
        return []
    annotations = raw[:max_annotations]
    # `message` is the one attacker-influenced free-text field on an annotation;
    # wrap it at the boundary. `path`/`start_line` are structural — `_evaluate`
    # gates on `path`'s truthiness, so wrapping it would flip classification.
    for ann in annotations:
        if isinstance(ann.get("message"), str):
            ann["message"] = wrap_untrusted(ann["message"], source="ci-annotation")
    if len(raw) > max_annotations:
        annotations.append({"truncated": True, "total": len(raw)})
    return annotations


def _check_status(
    repo_path: str,
    pr_number: str,
    *,
    since: str | None,
    review_bot_login: str | None,
    runner: rp.Runner,
) -> dict[str, Any]:
    validate_pr_number(pr_number)
    pr = _gh(
        ["pr", "view", pr_number, "--json", "mergeable,mergeStateStatus,isDraft,reviews"],
        cwd=repo_path,
        runner=runner,
    )
    if not pr:
        raise RuntimeError(f"check_pr_status: could not fetch PR #{pr_number} in {repo_path}")

    checks_raw = (
        _gh(
            ["pr", "checks", pr_number, "--json", "name,state,link"],
            cwd=repo_path,
            runner=runner,
        )
        or []
    )

    failing = [
        c
        for c in checks_raw
        if c.get("state")
        not in ("SUCCESS", "SKIPPED", "NEUTRAL", "PENDING", "IN_PROGRESS", "QUEUED")
    ]

    owner_repo = _get_owner_repo(repo_path, runner)
    for check in failing:
        check["annotations"] = _fetch_annotations(check, owner_repo, repo_path, runner)

    result: dict[str, Any] = {
        "mergeable": pr.get("mergeable"),
        "mergeStateStatus": pr.get("mergeStateStatus"),
        "isDraft": pr.get("isDraft", False),
        "failingChecks": failing,
    }

    if review_bot_login:
        all_reviews = pr.get("reviews", [])
        bot_reviews = [
            r
            for r in all_reviews
            if r.get("author", {}).get("login") == review_bot_login
            and (not since or r.get("submittedAt", "") > since)
        ]
        # `body` is the attacker-influenced free-text field; wrap it in place on the
        # (otherwise unprojected) review dict. `state`/`author`/`submittedAt` are
        # structural — `_bot_review_action` keys on `state`, so it stays untouched.
        for review in bot_reviews:
            if isinstance(review.get("body"), str):
                review["body"] = wrap_untrusted(review["body"], source="bot-review")
        result["botReviews"] = bot_reviews

    return result


_ROLLUP_FREE_TEXT_FIELDS = (
    # StatusContext (commit-status API)
    "context",
    "targetUrl",
    "description",
    # CheckRun (GitHub Actions / Checks API)
    "name",
    "workflowName",
    "detailsUrl",
)


def _wrap_status_check_rollup(rollup: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Marker-wrap the free-text subfields of ``statusCheckRollup`` entries.

    ``statusCheckRollup`` is a union of GitHub's ``CheckRun`` and ``StatusContext``
    GraphQL types; ``gh pr view --json statusCheckRollup`` projects each union member
    to a disjoint field set (verified against ``cli/cli``'s ``api/export_pr.go``).
    Every free-text field either member exposes is attacker-composable and wrapped;
    dispatch is by field presence (the two members' field sets are disjoint), not by
    trusting the ``__typename`` discriminator.

    - ``StatusContext`` (commit-status API entries — ``context``, ``state``,
      ``targetUrl``, ``startedAt``): ``context``, ``targetUrl``, and ``description``
      (not currently emitted by ``gh``, wrapped anyway as defense-in-depth — see
      commit history) are all set by whoever posts the commit status (``POST
      /repos/{owner}/{repo}/statuses/{sha}``) — attacker-postable by any CI Action
      with default ``statuses: write``, or any third-party status-posting
      integration. All three are wrapped. ``state`` is a GitHub-validated enum
      (``error|failure|pending|success``, rejected server-side otherwise) and stays
      structural.
    - ``CheckRun`` (native GitHub Actions / Checks-API entries — ``name``,
      ``workflowName``, ``status``, ``conclusion``, ``startedAt``, ``completedAt``,
      ``detailsUrl``): ``name`` (the job name) and ``workflowName`` (the workflow's
      top-level ``name:``) come straight from the workflow YAML. A ``pull_request``
      workflow runs in the context of the PR *merge commit* (``refs/pull/N/merge``),
      i.e. the workflow file **from the PR head** — so a fork PR that adds or edits a
      ``.github/workflows/*.yml`` ``name:`` composes these fields directly. (Only the
      separate ``pull_request_target`` trigger pins to the base-branch workflow file,
      and it does so to grant elevated permissions/secrets safely — a different
      concern, not a trust guarantee for plain ``pull_request``.) Both are wrapped.
      ``detailsUrl`` is a URL rendered as a clickable link that an agent still reads
      as a raw string, and for third-party Checks-API apps it is app-settable to an
      arbitrary value (the ``details_url`` the app supplies), so it is wrapped for
      the same reason as ``StatusContext.targetUrl``. ``status`` / ``conclusion`` are
      Checks-API-validated enums and ``startedAt`` / ``completedAt`` are
      runtime-generated timestamps — none is workflow-YAML free text, so all stay
      structural.
    """
    wrapped = []
    for entry in rollup:
        patch = {
            field: wrap_untrusted(entry[field], source="status-check")
            for field in _ROLLUP_FREE_TEXT_FIELDS
            if isinstance(entry.get(field), str)
        }
        if patch:
            entry = {**entry, **patch}
        wrapped.append(entry)
    return wrapped


def _summary_inputs(
    repo_path: str,
    pr_number: str,
    *,
    runner: rp.Runner,
) -> dict[str, Any]:
    """Fetch the PR reads a summarizer needs, marker-wrapping every free-text field.

    Consolidates the three direct-``gh`` reads a PR summary otherwise makes — ``pr
    view`` (metadata), ``pr diff`` (the diff), and the inline review comments API —
    behind the VCS boundary so the untrusted-content marker covers them. The
    attacker-influenced free-text (``title``/``body``/``diff``/each comment
    ``body``/each ``statusCheckRollup`` entry's free-text fields — a
    ``StatusContext``'s ``context``/``targetUrl``/``description`` and a ``CheckRun``'s
    ``name``/``workflowName``/``detailsUrl``; see ``_wrap_status_check_rollup`` for
    the full per-union-member field breakdown) is wrapped; structural metadata
    (``state``/``mergeable``, each rollup entry's validated enums
    (``state``/``status``/``conclusion``) and runtime timestamps, each comment's
    ``path``/``line``/``author``) passes through unwrapped.
    """
    validate_pr_number(pr_number)
    view = _gh(
        ["pr", "view", pr_number, "--json", "number,title,body,state,mergeable,statusCheckRollup"],
        cwd=repo_path,
        runner=runner,
    )
    if not view:
        raise RuntimeError(f"summary_inputs: could not fetch PR #{pr_number} in {repo_path}")

    owner_repo = _get_owner_repo(repo_path, runner)
    raw_comments = (
        _gh(
            ["api", f"repos/{owner_repo}/pulls/{pr_number}/comments", "--paginate"],
            cwd=repo_path,
            runner=runner,
        )
        if owner_repo
        else None
    ) or []

    diff_r = rp.run(["gh", "pr", "diff", pr_number], cwd=repo_path, runner=runner)
    diff = diff_r.stdout if diff_r.returncode == 0 else ""

    comments = [
        {
            "path": c.get("path"),
            "line": c.get("line"),
            "author": c.get("user", {}).get("login"),
            "body": wrap_untrusted(c.get("body") or "", source="pr-review-comment"),
        }
        for c in raw_comments
    ]

    return {
        "number": view.get("number"),
        "title": wrap_untrusted(view.get("title") or "", source="pr-metadata"),
        "body": wrap_untrusted(view.get("body") or "", source="pr-metadata"),
        "state": view.get("state"),
        "mergeable": view.get("mergeable"),
        "statusCheckRollup": _wrap_status_check_rollup(view.get("statusCheckRollup", [])),
        "diff": wrap_untrusted(diff, source="pr-diff"),
        "comments": comments,
    }


# ---------------------------------------------------------------------------
# pr.evaluate — ported from pr_evaluate_status.py
# ---------------------------------------------------------------------------


def _bot_review_action(
    status: dict[str, Any], review_bot_login: str | None
) -> dict[str, Any] | None:
    """Build the "review" action when review_bot_login left blocking feedback
    (CHANGES_REQUESTED or COMMENTED), else None."""
    if not review_bot_login:
        return None
    bot_reviews = status.get("botReviews", [])
    changes = [r for r in bot_reviews if r.get("state") == "CHANGES_REQUESTED"]
    comments = [r for r in bot_reviews if r.get("state") == "COMMENTED"]
    if not (changes or comments):
        return None
    return {
        "action": "review",
        "reason": (
            f"{len(changes)} changes requested, {len(comments)} comments from {review_bot_login}"
        ),
        "details": {"reviews": bot_reviews},
    }


def _evaluate(
    status: dict[str, Any],
    *,
    review_bot_login: str | None,
    fail_count: int,
) -> dict[str, Any]:
    mergeable = status.get("mergeable", "")
    merge_state = status.get("mergeStateStatus", "")
    is_draft = status.get("isDraft", False)
    failing = status.get("failingChecks", [])

    if is_draft:
        return {"action": "wait", "reason": "PR is a draft", "details": {}}

    if mergeable == "MERGEABLE" and merge_state == "CLEAN":
        review_action = _bot_review_action(status, review_bot_login)
        if review_action is not None:
            return review_action
        return {"action": "done", "reason": "PR is mergeable and clean", "details": status}

    if mergeable == "CONFLICTING":
        return {
            "action": "rebase",
            "reason": "PR has merge conflicts — rebase and resolve",
            "details": {},
        }

    if failing:
        has_code_annotations = any(
            ann.get("path") and not ann.get("truncated")
            for check in failing
            for ann in check.get("annotations", [])
        )

        if fail_count >= 2:
            return {
                "action": "fix_ci",
                "reason": f"CI failing {fail_count + 1} times — treat as real failure",
                "details": {"checks": failing},
            }

        if has_code_annotations:
            return {
                "action": "fix_ci",
                "reason": "CI failure with code annotations — fix the code",
                "details": {"checks": failing},
            }

        run_ids: set[str] = set()
        for check in failing:
            link = check.get("link", "")
            m = re.search(r"/actions/runs/(\d+)", link)
            if m:
                run_ids.add(m.group(1))

        return {
            "action": "rerun_ci",
            "reason": "CI failure without clear code annotations — rerun failed jobs",
            "details": {
                "checks": failing,
                "commands": (
                    [f"gh run rerun {rid} --failed" for rid in sorted(run_ids)] if run_ids else []
                ),
            },
        }

    review_action = _bot_review_action(status, review_bot_login)
    if review_action is not None:
        return review_action

    return {
        "action": "wait",
        "reason": "CI still running or no actionable state yet",
        "details": status,
    }


# ---------------------------------------------------------------------------
# pr.merge — ported from merge_prs.py
# ---------------------------------------------------------------------------


def _load_merge_order(toml_path: str | None) -> list[str] | None:
    if not toml_path:
        return None
    p = Path(toml_path)
    if not p.is_file():
        return None
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    release = raw.get("release")
    if not isinstance(release, dict):
        return None
    order = release.get("merge_order")
    if isinstance(order, list) and all(isinstance(x, str) for x in order):
        return order
    return None


def _load_auto_merge(toml_path: str | None) -> bool:
    if not toml_path:
        return False
    p = Path(toml_path)
    if not p.is_file():
        return False
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return False
    release = raw.get("release")
    if not isinstance(release, dict):
        return False
    return release.get("auto_merge") is True


def validate_pr_number(pr_number: str) -> None:
    """Validate ``pr_number`` is all-digits; raise InvalidInputError otherwise.

    Public (not underscore-prefixed) so callers outside this module — e.g.
    ``tools/portage``'s ``portage.pairs`` — can share this one validation rule
    instead of re-deriving their own digit regex.
    """
    if not re.fullmatch(r"\d+", pr_number):
        raise InvalidInputError(f"pr_number must be all digits, got: {pr_number!r}")


def _resolve_author_email(runner: rp.Runner) -> str:
    r = rp.run(["git", "config", "user.email"], runner=runner)
    email = r.stdout.strip()
    if r.returncode != 0 or not email:
        raise RuntimeError(
            "git config user.email is unset — set it with: "
            "git config --global user.email you@example.com"
        )
    return email


def _get_pr_state(repo_path: str, pr_number: str, runner: rp.Runner) -> dict | None:
    r = rp.run(
        [
            "gh",
            "pr",
            "view",
            pr_number,
            "--json",
            "mergeable,mergeStateStatus,isDraft,state,headRefName",
        ],
        cwd=repo_path,
        runner=runner,
    )
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _do_merge(
    repo_path: str, pr_number: str, author_email: str, runner: rp.Runner
) -> tuple[bool, str]:
    r = rp.run(
        ["gh", "pr", "merge", pr_number, "--merge", "--author-email", author_email],
        cwd=repo_path,
        runner=runner,
    )
    if r.returncode != 0:
        return False, r.stderr.strip() or "gh pr merge failed"
    return True, ""


def _delete_remote_branch(repo_path: str, branch: str, runner: rp.Runner) -> None:
    if not branch or branch in ("main", "master"):
        return
    if branch.startswith("-"):
        return
    rp.run(
        ["git", "-C", repo_path, "push", "origin", "--delete", "--", branch],
        runner=runner,
    )


def _skip_remaining(
    ordered: list[PRPair],
    merged: list[str],
    failed: dict[str, str],
    skipped: dict[str, str],
    failed_pair: PRPair,
) -> None:
    failed_key = f"{failed_pair.repo_path}:{failed_pair.pr_number}"
    for pair in ordered:
        key = f"{pair.repo_path}:{pair.pr_number}"
        if key not in merged and key not in failed and key not in skipped:
            skipped[key] = f"blocked by {failed_key}"


def _merge_prs(
    pr_pairs: list[PRPair],
    manifest_path: str,
    toml_path: str | None,
    runner: rp.Runner,
) -> dict[str, Any]:
    for pair in pr_pairs:
        validate_pr_number(pair.pr_number)

    manifest_data = _load_manifest(manifest_path)
    member_names = {m["name"] for m in manifest_data.get("members", [])}

    merge_order = _load_merge_order(toml_path)

    # auto_merge gate — fail-closed: refuse before any subprocess call unless
    # [release].auto_merge is explicitly true.
    if not _load_auto_merge(toml_path):
        raise AutoMergeDisabledError(
            "refusing to merge — auto_merge is unset/false — "
            "add `[release] auto_merge = true` to the group TOML to merge automatically."
        )

    # Merge safety gate
    if len(pr_pairs) > 1 and not merge_order:
        n = len(pr_pairs)
        raise MergeOrderRequiredError(
            f"refusing to merge {n} PRs with no merge_order declared — "
            f"add merge_order = [...] to the [release] block of your group TOML"
        )

    if merge_order:
        for entry in merge_order:
            if entry not in member_names:
                raise MergeConfigError(
                    f"merge_prs: merge_order entry '{entry}' not in manifest members "
                    f"(known: {sorted(member_names)})"
                )

    if merge_order:
        pair_by_name = {p.member_name: p for p in pr_pairs}
        ordered: list[PRPair] = []
        for name in merge_order:
            if name in pair_by_name:
                ordered.append(pair_by_name[name])
        covered = {p.member_name for p in ordered}
        for pair in pr_pairs:
            if pair.member_name not in covered:
                ordered.append(pair)
    else:
        ordered = list(pr_pairs)

    author_email = _resolve_author_email(runner)

    merged: list[str] = []
    failed: dict[str, str] = {}
    skipped: dict[str, str] = {}

    for pair in ordered:
        key = f"{pair.repo_path}:{pair.pr_number}"

        state = _get_pr_state(pair.repo_path, pair.pr_number, runner)
        if state is None:
            failed[key] = "gh pr view failed"
            _skip_remaining(ordered, merged, failed, skipped, pair)
            break

        if state.get("state") == "MERGED":
            skipped[key] = "already merged"
            continue

        if state.get("isDraft"):
            failed[key] = "draft PR — not ready to merge"
            _skip_remaining(ordered, merged, failed, skipped, pair)
            break

        mergeable = state.get("mergeable")
        merge_state = state.get("mergeStateStatus")
        if mergeable != "MERGEABLE" or merge_state != "CLEAN":
            failed[key] = f"not ready: mergeable={mergeable}, mergeState={merge_state}"
            _skip_remaining(ordered, merged, failed, skipped, pair)
            break

        ok, err = _do_merge(pair.repo_path, pair.pr_number, author_email, runner)
        if not ok:
            failed[key] = err
            _skip_remaining(ordered, merged, failed, skipped, pair)
            break

        merged.append(key)
        branch = state.get("headRefName", "")
        _delete_remote_branch(pair.repo_path, branch, runner)

    return {"merged": merged, "failed": failed, "skipped": skipped}


HUMAN_APPROVED_LABEL = "human-approved"


def _check_approval(repo_path: str, pr_number: str, *, runner: rp.Runner) -> dict[str, Any]:
    """Read-only check: does this PR carry a human-authored approval signal?

    Checked in order: an approving review by a `User` (non-bot) reviewer;
    then the `human-approved` label, current-state-per-timeline, applied by a
    `User` actor with no `performed_via_github_app` (U2, prover-validated —
    self-approval via review 422s for the PR's own author, which is why the
    label path exists for self-authored PRs).
    """
    validate_pr_number(pr_number)
    owner_repo = _get_owner_repo(repo_path, runner)
    if not owner_repo:
        raise RuntimeError(f"approval: could not resolve owner/repo for {repo_path}")

    reviews = _gh(
        ["api", f"repos/{owner_repo}/pulls/{pr_number}/reviews", "--paginate"],
        cwd=repo_path,
        runner=runner,
    )
    if reviews is None:
        raise RuntimeError(f"approval: could not fetch reviews for PR #{pr_number} in {repo_path}")

    for review in reviews:
        user = review.get("user") or {}
        if review.get("state") == "APPROVED" and user.get("type") == "User":
            return {"approved": True, "source": "review", "actor": user.get("login")}

    timeline = _gh(
        ["api", f"repos/{owner_repo}/issues/{pr_number}/timeline", "--paginate"],
        cwd=repo_path,
        runner=runner,
    )
    if timeline is None:
        raise RuntimeError(f"approval: could not fetch timeline for PR #{pr_number} in {repo_path}")

    label_events = [
        e
        for e in timeline
        if e.get("event") in ("labeled", "unlabeled")
        and (e.get("label") or {}).get("name") == HUMAN_APPROVED_LABEL
    ]
    label_events.sort(key=lambda e: e.get("created_at") or "")

    if label_events:
        last = label_events[-1]
        if last.get("event") == "labeled":
            actor = last.get("actor") or {}
            if actor.get("type") == "User" and last.get("performed_via_github_app") is None:
                return {"approved": True, "source": "label", "actor": actor.get("login")}

    return {"approved": False, "source": None, "actor": None}


# ---------------------------------------------------------------------------
# prs.json sidecar — ported from release_prs_sidecar.py
# ---------------------------------------------------------------------------


def _sidecar_write(path: Path | str, prs: list[dict[str, str]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "schema_version": 1,
        "prs": prs,
        "external_tracker": None,
    }

    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".prs-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, str(p))
        os.chmod(str(p), 0o600)
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise SidecarError(f"release_prs_sidecar: write failed at {p}: {e}") from e


def _sidecar_read(path: Path | str) -> dict[str, Any]:
    p = Path(path)

    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise SidecarError(f"release_prs_sidecar: cannot read sidecar at {p}: {e}") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SidecarError(f"release_prs_sidecar: malformed JSON in sidecar at {p}: {e}") from e

    if not isinstance(data, dict):
        raise SidecarError(f"release_prs_sidecar: sidecar at {p} is not a JSON object")

    if "prs" not in data:
        raise SidecarError(f"release_prs_sidecar: sidecar at {p} is missing required 'prs' field")

    return data


# ---------------------------------------------------------------------------
# Surface bindings
# ---------------------------------------------------------------------------


class _GitHubPR(PRSurface):
    def __init__(self, runner: rp.Runner) -> None:
        self._runner = runner

    def open(self, sidecar_path: Path | str, prs: list[dict[str, str]]) -> None:
        _sidecar_write(sidecar_path, prs)

    def read_sidecar(self, sidecar_path: Path | str) -> dict[str, Any]:
        """Read the prs.json sidecar (the read half of pr.open's write)."""
        return _sidecar_read(sidecar_path)

    def status(
        self,
        repo_path: str,
        pr_number: str,
        *,
        since: str | None = None,
        review_bot_login: str | None = None,
    ) -> dict[str, Any]:
        return _check_status(
            repo_path,
            pr_number,
            since=since,
            review_bot_login=review_bot_login,
            runner=self._runner,
        )

    def evaluate(
        self,
        status: dict[str, Any],
        *,
        review_bot_login: str | None = None,
        fail_count: int = 0,
    ) -> dict[str, Any]:
        return _evaluate(status, review_bot_login=review_bot_login, fail_count=fail_count)

    def summary_inputs(self, repo_path: str, pr_number: str) -> dict[str, Any]:
        return _summary_inputs(repo_path, pr_number, runner=self._runner)

    def merge(
        self,
        pr_pairs: Sequence[PRPair],
        manifest_path: str,
        *,
        toml_path: str | None = None,
    ) -> dict[str, Any]:
        return _merge_prs(list(pr_pairs), manifest_path, toml_path, self._runner)

    def approval(self, repo_path: str, pr_number: str) -> dict[str, Any]:
        return _check_approval(repo_path, pr_number, runner=self._runner)


class _GitHubCI(CISurface):
    def __init__(self, runner: rp.Runner, pr: _GitHubPR) -> None:
        self._runner = runner
        self._pr = pr

    def checks(
        self,
        repo_path: str,
        pr_number: str,
        *,
        since: str | None = None,
        review_bot_login: str | None = None,
    ) -> dict[str, Any]:
        # Deliberate same-underlying-read alias of pr.status: ci.checks and pr.status
        # both fetch the current PR check state; they exist as separate surface methods
        # so callers can route through the appropriate surface (CI vs PR concern).
        return _check_status(
            repo_path,
            pr_number,
            since=since,
            review_bot_login=review_bot_login,
            runner=self._runner,
        )

    def wait(
        self,
        pr_pairs: Sequence[tuple[str, str]],
        *,
        since: str | None = None,
        timeout: int = 1800,
        interval: int = 30,
        review_bot_login: str | None = None,
    ) -> dict[str, Any]:
        elapsed = 0

        while elapsed < timeout:
            actionable: dict[str, Any] = {}
            waiting: dict[str, Any] = {}

            for repo, pr in pr_pairs:
                key = f"{repo}:{pr}"
                try:
                    status = self._pr.status(
                        repo,
                        pr,
                        since=since,
                        review_bot_login=review_bot_login,
                    )
                    result = self._pr.evaluate(
                        status,
                        review_bot_login=review_bot_login,
                        fail_count=0,
                    )
                except Exception as e:
                    result = {"action": "error", "reason": str(e)}

                if result.get("action") == "wait":
                    waiting[key] = result
                else:
                    actionable[key] = result

            if actionable:
                return {"actionable": actionable, "waiting": waiting}

            sleep_for = min(interval, timeout - elapsed)
            time.sleep(sleep_for)
            elapsed += sleep_for

        return {"timeout": True, "elapsed_seconds": elapsed}


class GitHubProvider(Provider):
    """GitHub backend implementing the repos/pr/ci surfaces."""

    def __init__(self, runner: rp.Runner | None = None) -> None:
        effective = runner if runner is not None else rp._default_runner
        self._runner = effective
        self.repos = _GitHubRepos(effective)
        self.pr = _GitHubPR(effective)
        self.ci = _GitHubCI(effective, self.pr)
