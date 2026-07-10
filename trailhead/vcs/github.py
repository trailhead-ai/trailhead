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
no hardcoded review-bot login (a passed param). The pr_number/job_id digits-only
guards are enforced inside ``_check_status`` and ``_GitHubDeploy.logs`` themselves
(not just at the merge call site), so every caller — status/checks/wait reads
included — is covered before anything reaches ``gh`` argv.
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
from trailhead.vcs.interface import (
    CISurface,
    DeploySurface,
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


class InvalidInputError(Exception):
    """Raised on option-injection attack vectors (pr_number / branch validation)."""


class DeployError(Exception):
    """Raised by the deploy surface when a gh call fails legibly.

    Unlike the lossy ``_gh`` collapse used by repos/pr/ci, the deploy paths ARE
    the doctor signal, so this carries the cause: it distinguishes a gh nonzero
    exit (returncode + stderr) from a gh that exited zero but returned non-JSON
    / empty stdout.
    """


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


def _gh_or_raise(args: list[str], cwd: str, runner: rp.Runner) -> Any:
    """gh call for the deploy surface — raises DeployError carrying the cause.

    Distinguishes the two failure modes the lossy ``_gh`` collapses into ``None``:
    a nonzero gh exit (returncode + stderr) vs. a zero exit with non-JSON / empty
    stdout. The deploy paths feed doctor, so an opaque ``None`` is not acceptable.
    """
    r = rp.run(["gh"] + args, cwd=cwd, runner=runner)
    if r.returncode != 0:
        stderr = (r.stderr or "").strip()
        raise DeployError(
            f"gh {' '.join(args[:2])} failed (returncode {r.returncode}): {stderr or '<no stderr>'}"
        )
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise DeployError(f"gh {' '.join(args[:2])} returned non-JSON / empty stdout: {e}") from e


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
    _validate_pr_number(pr_number)
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
        result["botReviews"] = bot_reviews

    return result


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


def _validate_pr_number(pr_number: str) -> None:
    if not re.fullmatch(r"\d+", pr_number):
        raise InvalidInputError(f"pr_number must be all digits, got: {pr_number!r}")


def _validate_job_id(job_id: str) -> None:
    if not re.fullmatch(r"\d+", job_id):
        raise InvalidInputError(f"job_id must be all digits, got: {job_id!r}")


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
        _validate_pr_number(pair.pr_number)

    manifest_data = _load_manifest(manifest_path)
    member_names = {m["name"] for m in manifest_data.get("members", [])}

    merge_order = _load_merge_order(toml_path)

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

    def merge(
        self,
        pr_pairs: Sequence[PRPair],
        manifest_path: str,
        *,
        toml_path: str | None = None,
    ) -> dict[str, Any]:
        return _merge_prs(list(pr_pairs), manifest_path, toml_path, self._runner)


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


_WORKFLOW_RUN_FIELDS = (
    "id",
    "name",
    "status",
    "conclusion",
    "head_sha",
    "created_at",
    "html_url",
    "workflow_id",
)


def _resolve_owner_repo(cwd: str, runner: rp.Runner) -> str:
    owner_repo = _get_owner_repo(cwd, runner)
    if not owner_repo:
        raise DeployError(f"could not resolve github owner/repo from origin remote in {cwd}")
    return owner_repo


class _GitHubDeploy(DeploySurface):
    """GHA workflow-run + deployment status + failure-log interrogation.

    The surface landing's ``doctor`` interrogates for a post-merge deploy
    regression. Every gh call is list-form through ``trailhead.vcs.runner``
    (shell=False); the jq ``-q`` filter is a list element, not a shell pipe.

    ``workflow_runs`` and ``status`` use ``_gh_or_raise`` (not the lossy
    ``_gh``) so a failed deploy query surfaces a legible cause to doctor.
    ``logs`` is the exception: it bypasses ``_gh_or_raise`` to special-case a
    404 (not-found job) into ``[]`` so a missing job never false-alarms — any
    *other* nonzero gh exit still raises (see ``logs``).
    """

    def __init__(self, runner: rp.Runner) -> None:
        self._runner = runner

    def workflow_runs(
        self,
        repo_path: str,
        *,
        status: str | None = None,
        per_page: int | None = None,
    ) -> list[dict]:
        """List GHA workflow runs via REST ``actions/runs``.

        REST (not ``gh run list``) so ``id`` is the int the run→job→annotation
        chain needs — ``gh run list`` names it ``databaseId``.
        """
        owner_repo = _resolve_owner_repo(repo_path, self._runner)
        path = f"repos/{owner_repo}/actions/runs"
        query: list[str] = []
        if status is not None:
            query.append(f"status={status}")
        if per_page is not None:
            query.append(f"per_page={per_page}")
        if query:
            path = f"{path}?{'&'.join(query)}"

        data = _gh_or_raise(["api", path], repo_path, self._runner)
        runs = data.get("workflow_runs", []) if isinstance(data, dict) else []
        return [{k: run.get(k) for k in _WORKFLOW_RUN_FIELDS} for run in runs]

    def status(self, repo_path: str, **kwargs: Any) -> list[dict]:
        """List the latest deployment status per GitHub Deployment.

        Zero deployments is a valid steady state (the deployments API is opt-in
        and some repos deploy out-of-band) — that case returns ``[]``, it does
        not raise. A *failed* gh query (auth, rate-limit, non-JSON) still raises
        ``DeployError`` via ``_gh_or_raise`` — a broken query is the doctor
        signal, not a silent empty list.
        """
        owner_repo = _resolve_owner_repo(repo_path, self._runner)
        deployments = _gh_or_raise(
            ["api", f"repos/{owner_repo}/deployments"], repo_path, self._runner
        )
        if not isinstance(deployments, list):
            return []

        results: list[dict] = []
        for dep in deployments:
            dep_id = dep.get("id")
            statuses = _gh_or_raise(
                ["api", f"repos/{owner_repo}/deployments/{dep_id}/statuses"],
                repo_path,
                self._runner,
            )
            latest = statuses[0] if isinstance(statuses, list) and statuses else {}
            results.append(
                {
                    "id": dep_id,
                    "sha": dep.get("sha"),
                    "state": latest.get("state"),
                    "environment": latest.get("environment", dep.get("environment")),
                    "created_at": latest.get("created_at"),
                    "log_url": latest.get("log_url", ""),
                }
            )
        return results

    def logs(
        self,
        repo_path: str,
        *,
        job_id: str,
        max_annotations: int = 10,
    ) -> list[dict]:
        """Failure annotations for a run's job — the doctor signal.

        Filters ``check-runs/{job_id}/annotations`` to ``annotation_level=="failure"``.
        A not-found / clean job yields ``[]`` (no false alarm); the truncation
        sentinel is appended when the raw count exceeds ``max_annotations``.
        """
        _validate_job_id(job_id)
        owner_repo = _resolve_owner_repo(repo_path, self._runner)
        args = [
            "api",
            f"repos/{owner_repo}/check-runs/{job_id}/annotations",
            "--paginate",
            "-q",
            '[.[] | select(.annotation_level=="failure") | {path, start_line, message: .message}]',
        ]
        r = rp.run(["gh"] + args, cwd=repo_path, runner=self._runner)
        if r.returncode != 0:
            stderr = (r.stderr or "").strip()
            if "404" in stderr or "Not Found" in stderr:
                return []
            raise DeployError(
                f"gh api check-runs/{job_id}/annotations failed (returncode {r.returncode}): "
                f"{stderr or '<no stderr>'}"
            )
        try:
            raw = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise DeployError(
                f"gh api check-runs/{job_id}/annotations returned non-JSON / empty stdout: {e}"
            ) from e
        if not raw:
            return []
        annotations = raw[:max_annotations]
        if len(raw) > max_annotations:
            annotations.append({"truncated": True, "total": len(raw)})
        return annotations


class GitHubProvider(Provider):
    """GitHub backend implementing the repos/pr/ci/deploy surfaces."""

    def __init__(self, runner: rp.Runner | None = None) -> None:
        effective = runner if runner is not None else rp._default_runner
        self._runner = effective
        self.repos = _GitHubRepos(effective)
        self.pr = _GitHubPR(effective)
        self.ci = _GitHubCI(effective, self.pr)
        self.deploy = _GitHubDeploy(effective)
