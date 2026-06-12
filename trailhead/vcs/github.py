"""GitHubProvider — the GitHub backend for the VCS interface (repos/pr/ci).

Ports the pure callables out of forge's release scripts, routing every gh/git
call through ``trailhead.vcs.runner`` (shell=False, injectable). camp stays the
membership source of truth: ``repos.detect()`` consumes the manifest, it does
not own membership.

Ported sources (logic lifted, not imported — forge's copies stay in place until
the release cluster is deleted):
  - detect_repos.py          → repos.detect()
  - release_prs_sidecar.py   → pr.open() / pr.status_sidecar()
  - check_pr_status.py       → pr.status() + ci.checks()
  - pr_evaluate_status.py    → pr.evaluate()
  - merge_prs.py             → pr.merge() (ordered, R-6 safety gate)
  - wait_for_actionable.py   → ci.wait()

Safety invariants preserved: R-6 merge gate, shell=False, option-injection
guards (pr_number digits-only, branch leading-dash / push ``--`` terminator),
no hardcoded review-bot login (a passed param).
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
    """Raised when >1 PR is queued but no merge_order is declared (R-6/A-1)."""


class MergeConfigError(Exception):
    """Raised when merge_order names a member not in the manifest."""


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

            # R-7: graceful degrade — missing worktree_path → skip.
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
        ["api", f"repos/{owner_repo}/check-runs/{job_id}/annotations",
         "--paginate", "-q",
         '[.[] | select(.annotation_level=="failure") | {path, start_line, message: .message}]'],
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
    pr = _gh(
        ["pr", "view", pr_number, "--json",
         "mergeable,mergeStateStatus,isDraft,reviews"],
        cwd=repo_path,
        runner=runner,
    )
    if not pr:
        raise RuntimeError(
            f"check_pr_status: could not fetch PR #{pr_number} in {repo_path}"
        )

    checks_raw = _gh(
        ["pr", "checks", pr_number, "--json", "name,state,link"],
        cwd=repo_path,
        runner=runner,
    ) or []

    failing = [
        c for c in checks_raw
        if c.get("state") not in (
            "SUCCESS", "SKIPPED", "NEUTRAL", "PENDING", "IN_PROGRESS", "QUEUED"
        )
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
            r for r in all_reviews
            if r.get("author", {}).get("login") == review_bot_login
            and (not since or r.get("submittedAt", "") > since)
        ]
        result["botReviews"] = bot_reviews

    return result


# ---------------------------------------------------------------------------
# pr.evaluate — ported from pr_evaluate_status.py
# ---------------------------------------------------------------------------


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
        if review_bot_login:
            bot_reviews = status.get("botReviews", [])
            changes = [r for r in bot_reviews if r.get("state") == "CHANGES_REQUESTED"]
            comments = [r for r in bot_reviews if r.get("state") == "COMMENTED"]
            if changes or comments:
                return {
                    "action": "review",
                    "reason": f"{len(changes)} changes requested, {len(comments)} comments from {review_bot_login}",
                    "details": {"reviews": bot_reviews},
                }
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
                "commands": [f"gh run rerun {rid} --failed" for rid in sorted(run_ids)] if run_ids else [],
            },
        }

    if review_bot_login:
        bot_reviews = status.get("botReviews", [])
        changes = [r for r in bot_reviews if r.get("state") == "CHANGES_REQUESTED"]
        comments = [r for r in bot_reviews if r.get("state") == "COMMENTED"]
        if changes or comments:
            return {
                "action": "review",
                "reason": f"{len(changes)} changes requested, {len(comments)} comments from {review_bot_login}",
                "details": {"reviews": bot_reviews},
            }

    return {"action": "wait", "reason": "CI still running or no actionable state yet", "details": status}


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
        raise InvalidInputError(
            f"merge_prs: pr_number must be all digits, got: {pr_number!r}"
        )


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
        ["gh", "pr", "view", pr_number, "--json",
         "mergeable,mergeStateStatus,isDraft,state,headRefName"],
        cwd=repo_path,
        runner=runner,
    )
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _do_merge(repo_path: str, pr_number: str, author_email: str, runner: rp.Runner) -> tuple[bool, str]:
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

    # R-6 safety gate
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

    def status_sidecar(self, sidecar_path: Path | str) -> dict[str, Any]:
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
                        repo, pr, since=since, review_bot_login=review_bot_login,
                    )
                    result = self._pr.evaluate(
                        status, review_bot_login=review_bot_login, fail_count=0,
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


class _GitHubDeploy(DeploySurface):
    """Slice-1 stub: the deploy surface is implemented in Slice 2."""

    _NOT_YET = (
        "trailhead.vcs deploy surface is implemented in Slice 2 — "
        "not available on the Slice-1 GitHubProvider"
    )

    def __init__(self, runner: rp.Runner) -> None:
        self._runner = runner

    def workflow_runs(self, repo_path: str, **kwargs: Any) -> list[dict]:
        raise NotImplementedError(self._NOT_YET)

    def status(self, repo_path: str, **kwargs: Any) -> list[dict]:
        raise NotImplementedError(self._NOT_YET)

    def logs(self, repo_path: str, **kwargs: Any) -> list[dict]:
        raise NotImplementedError(self._NOT_YET)


class GitHubProvider(Provider):
    """GitHub backend: repos/pr/ci implemented; deploy in Slice 2."""

    def __init__(self, runner: rp.Runner | None = None) -> None:
        effective = runner if runner is not None else rp._default_runner
        self._runner = effective
        self.repos = _GitHubRepos(effective)
        self.pr = _GitHubPR(effective)
        self.ci = _GitHubCI(effective, self.pr)
        self.deploy = _GitHubDeploy(effective)
