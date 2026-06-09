"""Deterministic helpers for the `/forge:handoff` dev ritual.

`/forge:handoff` records generic git state and shelves a session note so a
future session can resume. The SKILL.md orchestrates; the testable logic lives
here:

  - ``capture_git_state``     read-only git survey of a single repo, bounded.
  - ``lore_state``            3-state lore-backend detection.
  - ``write_degraded_handoff`` write the out-of-repo fallback handoff file.

GIT CAPTURE IS READ-ONLY. Nothing here commits, pushes, or mutates a user's
repo — it records branch / ahead-count / dirty-flag / bounded commit list only.
All git probes are guarded so a non-git cwd yields an empty, graceful result
with no stderr leak.

The git log is ALWAYS bounded: commits are limited to the range
``<merge-base(HEAD, default-branch)>..HEAD``, and when no merge-base exists
(e.g. an unrelated default branch) it falls back to ``HEAD~<fallback_n>..HEAD``
— never an unbounded ``git log``.
"""
from __future__ import annotations

import enum
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# git-state capture (read-only)
# ---------------------------------------------------------------------------

@dataclass
class GitState:
    is_git: bool
    branch: str
    ahead_count: int
    dirty: bool
    commits: list[str] = field(default_factory=list)


def _git(repo: Path, *args: str) -> tuple[int, str]:
    """Run a git command, fully guarded. Returns (returncode, stripped stdout).

    stderr is captured (never leaked to the terminal) so a non-git cwd produces
    no noise. Any exception (git missing, timeout) is treated as a failed probe.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, (result.stdout or "").strip()
    except Exception:
        return 1, ""


def capture_git_state(
    repo: Path,
    default_branch: str = "main",
    fallback_n: int = 50,
) -> GitState:
    """Survey a single repo's git state, read-only and bounded.

    On a non-git directory (or any probe failure) returns an empty, graceful
    GitState with ``is_git=False`` and no stderr leak.
    """
    repo = Path(repo)
    rc, _ = _git(repo, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        return GitState(is_git=False, branch="", ahead_count=0, dirty=False, commits=[])

    _, branch = _git(repo, "branch", "--show-current")

    _, porcelain = _git(repo, "status", "--porcelain")
    dirty = bool(porcelain.strip())

    # Bound the commit range. Prefer merge-base(HEAD, default-branch); fall back
    # to a bounded HEAD~N range — never an unbounded `git log`.
    base_rc, base = _git(repo, "merge-base", "HEAD", default_branch)
    if base_rc == 0 and base:
        log_range = f"{base}..HEAD"
    else:
        log_range = f"HEAD~{fallback_n}..HEAD"

    log_rc, log_out = _git(repo, "log", log_range, "--oneline", f"--max-count={fallback_n}")
    if log_rc != 0:
        # HEAD~N range can exceed history on a shallow/young repo; retry bounded
        # against the root without a range floor, still capped by --max-count.
        log_rc, log_out = _git(repo, "log", "--oneline", f"--max-count={fallback_n}")

    commits = [ln for ln in log_out.splitlines() if ln.strip()] if log_rc == 0 else []

    return GitState(
        is_git=True,
        branch=branch,
        ahead_count=len(commits),
        dirty=dirty,
        commits=commits,
    )


# ---------------------------------------------------------------------------
# lore 3-state detection
# ---------------------------------------------------------------------------

class LoreState(enum.Enum):
    WORKING = "working"
    ABSENT = "absent"
    BROKEN = "broken"


def _lore_stats_ok() -> bool:
    """True iff a bare ``lore stats`` invocation exits 0."""
    try:
        result = subprocess.run(
            ["lore", "stats"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def lore_state() -> LoreState:
    """Detect the lore backend's state (KU1).

    WORKING requires ALL THREE: ``lore`` resolves on PATH, ``lore stats`` exits
    0, and ``$LORE_VAULT`` is set non-empty. A missing ``$LORE_VAULT`` is
    treated as ABSENT — the CLI default (~/lore) is the wrong shadow vault, so
    we never silently write there. A present-but-failing ``lore stats`` is
    BROKEN (degrade, don't crash).
    """
    if shutil.which("lore") is None:
        return LoreState.ABSENT
    if not os.environ.get("LORE_VAULT", "").strip():
        return LoreState.ABSENT
    if not _lore_stats_ok():
        return LoreState.BROKEN
    return LoreState.WORKING


# ---------------------------------------------------------------------------
# degraded-file write (out of any repo)
# ---------------------------------------------------------------------------

def _render_git_state(state: GitState) -> str:
    if not state.is_git:
        lines = ["_(cwd is not a git repository — no git state captured)_"]
    else:
        lines = [
            f"- Branch: `{state.branch}`",
            f"- Commits ahead of default branch: {state.ahead_count}",
            f"- Uncommitted changes: {'yes' if state.dirty else 'no'}",
        ]
        if state.commits:
            lines.append("- Recent commits:")
            lines.extend(f"  - {c}" for c in state.commits)
    return "\n".join(lines)


def _sanitize_slug(handoff_dir: Path, slug: str) -> Path:
    """Return a safe output path for *slug* that stays under *handoff_dir*.

    Strips any path-separator characters from the slug and resolves the result
    to confirm it is contained within *handoff_dir* (defense-in-depth against
    a ``../``-style slug escaping the handoff directory).  When the resolved
    path would escape, the slug is reduced to only its final name component.
    """
    # Strip explicit path separators so slugs like "../../escape" become a
    # flat name in handoff_dir without traversal.
    import re as _re
    safe_name = _re.sub(r"[/\\]", "_", slug).strip(".")
    if not safe_name:
        safe_name = "handoff"
    candidate = (handoff_dir / f"{safe_name}.md").resolve()
    if not candidate.is_relative_to(handoff_dir.resolve()):
        # Fallback: use only the name part, which cannot escape.
        candidate = handoff_dir.resolve() / f"{Path(safe_name).name}.md"
    return candidate


def write_degraded_handoff(
    handoff_dir: Path,
    slug: str,
    hints: str,
    state: GitState,
) -> Path:
    """Write the fallback handoff file when lore is unavailable.

    Lands at ``<handoff_dir>/<slug>.md`` — by contract handoff_dir is
    ``~/.forge/handoffs/`` (OUT of any repo), so captured git state never leaks
    into a possibly-public user repo, bypassing the leak gate. The directory is
    created if missing.

    The slug is sanitized and the resolved output path is asserted to remain
    inside *handoff_dir* (defense-in-depth, Security C1).
    """
    handoff_dir = Path(handoff_dir)
    handoff_dir.mkdir(parents=True, exist_ok=True)
    out = _sanitize_slug(handoff_dir, slug)
    body = (
        f"# Forge handoff — {slug}\n\n"
        "_lore unavailable — this is a local forge handoff (out of any repo)._\n\n"
        "## Pickup hints\n\n"
        f"{hints.strip()}\n\n"
        "## Captured git state\n\n"
        f"{_render_git_state(state)}\n"
    )
    out.write_text(body)
    return out


# ---------------------------------------------------------------------------
# CLI entrypoint (thin — the SKILL.md orchestrates; this exposes the helpers)
# ---------------------------------------------------------------------------

def _default_handoff_dir() -> Path:
    return Path.home() / ".forge" / "handoffs"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="handoff_capture", description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=False)

    ap.add_argument("--capture", metavar="REPO", help="print captured git state for REPO")
    ap.add_argument("--default-branch", default="main")
    ap.add_argument("--fallback-n", type=int, default=50)

    ap.add_argument("--degraded", action="store_true", help="write the out-of-repo fallback handoff file")
    ap.add_argument("--slug", help="slug for the degraded handoff filename")
    ap.add_argument("--hints", default="", help="pickup hints text")
    ap.add_argument("--repo", default=".", help="repo to capture for the degraded write")
    ap.add_argument(
        "--handoff-dir",
        default=str(_default_handoff_dir()),
        help="directory for the degraded handoff file (default: ~/.forge/handoffs)",
    )

    args = ap.parse_args(argv)

    if args.degraded:
        if not args.slug:
            ap.error("--degraded requires --slug")
        state = capture_git_state(Path(args.repo), args.default_branch, args.fallback_n)
        out = write_degraded_handoff(
            Path(args.handoff_dir).expanduser(), args.slug, args.hints, state
        )
        print(out)
        return 0

    if args.capture:
        state = capture_git_state(Path(args.capture), args.default_branch, args.fallback_n)
        print(f"is_git={state.is_git}")
        print(f"branch={state.branch}")
        print(f"ahead_count={state.ahead_count}")
        print(f"dirty={state.dirty}")
        for c in state.commits:
            print(f"commit={c}")
        return 0

    print(f"state: {lore_state().value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
