"""Update detection probe — `trailhead update --check`.

Reads the install provenance stamp (`trailhead/provenance.py`), runs a
read-only, timeout-bounded `git fetch` against the stamped checkout, and
reports whether it is behind its tracked remote branch. Modelled on lore's
sync freshness probe: a freshness stamp under `state_dir("trailhead")`
throttles the network fetch to once per window, written on ATTEMPT rather
than success, so an offline session pays one timeout per window instead of
one per invocation.

Any errored git invocation — nonzero exit, empty stdout, a missing upstream
ref, a timed-out fetch — reports the outcome "unanswerable", never "ok" or
"behind": a wrong confident answer here is worse than no answer. The
`origin` URL recorded at install time is compared against the checkout's
current `origin` on every check; a mismatch also reports "unanswerable" and
the comparison never proceeds against the repointed remote.

The check performs no mutation of the checkout: every git invocation it
makes is one of `remote get-url`, `fetch`, `rev-list` — never `pull`,
`checkout`, `merge`, or `reset`. git is injected via `runner` (same shape as
`provenance`'s — a callable(args, **kw) -> CompletedProcess-like object),
argv-only, never `shell=True`. git stderr is redacted of credentials
(`provenance.redact_credentials`) before it ever reaches a reason string.

The outcome is recorded back onto the provenance stamp via
`provenance.record_check_outcome` so a persistently failing check is
discoverable (`trailhead doctor`) rather than silently indistinguishable
from "up to date".

The `--json` output is a pinned schema (schema_version 2) — the producer
contract a SessionStart hook consumes:

    {"schema_version": 2, "outcome": "ok"|"behind"|"unanswerable",
     "commits_behind": <int|null>, "installed_sha": <str|null>,
     "reason": <str|null>,
     "changelog_delta": {"available": <bool>, "lines": [<str>, ...],
                          "truncated": <bool>}}

`changelog_delta` is the ADDED lines of `git diff <installed_sha>
<tracked_branch> -- CHANGELOG.md` — no markdown parsing, no version scheme,
no tags. It is attacker-reachable: anyone who lands a commit on the tracked
branch authors text that lands here before a human reads it. So it is
treated as untrusted data end to end — control characters, ANSI escape
sequences, and markdown fence-breaking sequences (` ``` `) are neutralised at
extraction (`_sanitize_delta_line`), never left for a presentation layer to
catch; it is carried as a plain data field and never interpolated into a
shell command line (the diff invocation is argv-only, like every other git
call here); and it is bounded (`CHANGELOG_DELTA_MAX_LINES`), degrading to an
explicit truncation notice past the cap rather than growing unbounded.

`available` is false whenever the delta could not be computed at all — no
resolvable remote, an errored diff invocation — and in that case `lines` is
always empty and `truncated` is always false: a caller must never mistake a
failed extraction for a complete-but-empty one. The verdict fields
(`outcome`, `commits_behind`) are computed independently and stay correct
even when the delta extraction itself fails.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from trailhead.paths import ensure_dir, state_dir
from trailhead.provenance import read_stamp, record_check_outcome, redact_credentials

SCHEMA_VERSION = 2
FRESHNESS_WINDOW_SECONDS = 24 * 60 * 60
FRESHNESS_STAMP_FILENAME = "update-check.json"

CHANGELOG_PATH = "CHANGELOG.md"
CHANGELOG_DELTA_MAX_LINES = 200
CHANGELOG_DELTA_MAX_LINE_CHARS = 500

# Strips ANSI/VT escape sequences (CSI and simple two-byte forms) before any
# changelog content is ever surfaced to an agent.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b[@-Z\\-_]")
# C0/C1 control characters, excluding none — a changelog line is prose, it
# never legitimately carries a raw control byte.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _default_runner():
    def runner(args, **kw):
        return subprocess.run(args, **kw)

    return runner


# ---------------------------------------------------------------------------
# Freshness stamp — attempted-at, not succeeded-at
# ---------------------------------------------------------------------------


def freshness_stamp_path(*, env: dict[str, str] | None = None) -> Path:
    """Return the freshness-throttle stamp path: state_dir("trailhead")/update-check.json."""
    _env = env if env is not None else dict(os.environ)
    return state_dir("trailhead", env=_env) / FRESHNESS_STAMP_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write *data* to *path* as JSON, atomically (temp file + os.replace)."""
    ensure_dir(path.parent, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=".update-check-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _fetch_is_fresh(env: dict[str, str], window: int) -> bool:
    path = freshness_stamp_path(env=env)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    attempted_at = data.get("attempted_at") if isinstance(data, dict) else None
    if not attempted_at:
        return False
    try:
        ts = datetime.strptime(attempted_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - ts).total_seconds() < window


def _stamp_fetch_attempt(env: dict[str, str]) -> None:
    """Record that a fetch was ATTEMPTED, regardless of whether it succeeds.

    Written unconditionally before the fetch runs, atomically — two
    near-simultaneous callers each replace the file wholesale, so the file on
    disk is always exactly one well-formed JSON object.
    """
    _atomic_write_json(freshness_stamp_path(env=env), {"attempted_at": _now_iso()})


# ---------------------------------------------------------------------------
# Git probing
# ---------------------------------------------------------------------------


def _run_git(checkout: Path, *args: str, runner, timeout: int):
    try:
        return runner(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None


def _unavailable_delta() -> dict:
    return {"available": False, "lines": [], "truncated": False}


def _sanitize_delta_line(line: str) -> str:
    """Neutralise one changelog delta line before it can reach an agent.

    Strips ANSI escapes and control characters, then breaks any markdown
    fence sequence (```) so the delta can later be embedded inside a
    delimited untrusted-content block without letting attacker text close
    that fence early. Also bounds a single line's length — an attacker
    controls this text and a single absurdly long line would otherwise
    defeat the line-count cap.
    """
    line = _ANSI_ESCAPE_RE.sub("", line)
    line = _CONTROL_CHAR_RE.sub("", line)
    line = line.replace("```", "'''")
    if len(line) > CHANGELOG_DELTA_MAX_LINE_CHARS:
        line = line[:CHANGELOG_DELTA_MAX_LINE_CHARS] + "…"
    return line


def _extract_changelog_delta(
    checkout: Path, installed_sha: str, remote_ref: str, *, runner, timeout: int
) -> dict:
    """Return the sanitized, bounded added-lines delta of CHANGELOG.md.

    Runs `git diff <installed_sha> <remote_ref> -- CHANGELOG.md` (argv-only,
    read-only) and keeps only lines that are genuinely added — never removed
    or context lines, and never the `+++ b/CHANGELOG.md` file header, which
    also starts with `+`. Any diff failure (nonzero exit, timeout) reports
    `available: False` rather than a partial delta.
    """
    proc = _run_git(
        checkout, "diff", installed_sha, remote_ref, "--", CHANGELOG_PATH, runner=runner, timeout=timeout
    )
    if proc is None or proc.returncode != 0:
        return _unavailable_delta()

    added = [
        _sanitize_delta_line(raw_line[1:])
        for raw_line in (proc.stdout or "").splitlines()
        if raw_line.startswith("+") and not raw_line.startswith("+++")
    ]

    truncated = len(added) > CHANGELOG_DELTA_MAX_LINES
    if truncated:
        omitted = len(added) - CHANGELOG_DELTA_MAX_LINES
        added = added[:CHANGELOG_DELTA_MAX_LINES]
        added.append(f"… truncated: {omitted} more line(s) omitted (delta exceeds {CHANGELOG_DELTA_MAX_LINES} lines)")

    return {"available": True, "lines": added, "truncated": truncated}


def _result(
    outcome: str,
    commits_behind: int | None,
    installed_sha: str | None,
    reason: str | None,
    changelog_delta: dict | None = None,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "outcome": outcome,
        "commits_behind": commits_behind,
        "installed_sha": installed_sha,
        "reason": reason,
        "changelog_delta": changelog_delta if changelog_delta is not None else _unavailable_delta(),
    }


def _finish(
    outcome: str,
    commits_behind: int | None,
    installed_sha: str | None,
    reason: str | None,
    env: dict[str, str],
    changelog_delta: dict | None = None,
) -> dict:
    redacted_reason = redact_credentials(reason) if reason else None
    record_check_outcome(outcome, reason=redacted_reason, env=env)
    return _result(outcome, commits_behind, installed_sha, redacted_reason, changelog_delta)


def check_for_update(
    *,
    env: dict[str, str] | None = None,
    runner=None,
    timeout: int = 10,
    window: int = FRESHNESS_WINDOW_SECONDS,
    confine_root: Path | str | None = None,
) -> dict:
    """Check whether the stamped checkout is behind its tracked remote branch.

    Returns the pinned `{"schema_version", "outcome", "commits_behind",
    "installed_sha", "reason"}` shape. Never raises for a git-side failure —
    those all collapse to `outcome == "unanswerable"`.
    """
    _env = env if env is not None else dict(os.environ)
    _runner = runner if runner is not None else _default_runner()

    stamp = read_stamp(env=_env, confine_root=confine_root)
    if stamp is None:
        return _finish("unanswerable", None, None, "no install provenance stamp found", _env)

    checkout = Path(stamp["checkout"])
    installed_sha = stamp["sha"]
    stamped_branch = stamp["branch"]
    stamped_origin = stamp["origin_url"]

    origin_proc = _run_git(checkout, "remote", "get-url", "origin", runner=_runner, timeout=timeout)
    if origin_proc is None or origin_proc.returncode != 0 or not (origin_proc.stdout or "").strip():
        stderr = (origin_proc.stderr or "").strip() if origin_proc else "timed out"
        return _finish(
            "unanswerable", None, installed_sha, f"could not resolve origin remote: {stderr}", _env
        )

    current_origin = origin_proc.stdout.strip()
    if current_origin != stamped_origin:
        return _finish(
            "unanswerable",
            None,
            installed_sha,
            "origin remote has changed since install; refusing to compare against it",
            _env,
        )

    remote_name = stamped_branch.split("/", 1)[0] if "/" in stamped_branch else "origin"

    if not _fetch_is_fresh(_env, window):
        _stamp_fetch_attempt(_env)
        fetch_proc = _run_git(checkout, "fetch", "--quiet", remote_name, runner=_runner, timeout=timeout)
        if fetch_proc is None or fetch_proc.returncode != 0:
            stderr = (fetch_proc.stderr or "").strip() if fetch_proc else "timed out"
            return _finish(
                "unanswerable", None, installed_sha, f"git fetch failed: {stderr}", _env
            )

    count_proc = _run_git(
        checkout, "rev-list", "--count", f"HEAD..{stamped_branch}", runner=_runner, timeout=timeout
    )
    stdout = (count_proc.stdout or "").strip() if count_proc else ""
    if count_proc is None or count_proc.returncode != 0 or not stdout:
        stderr = (count_proc.stderr or "").strip() if count_proc else "timed out"
        return _finish(
            "unanswerable",
            None,
            installed_sha,
            f"could not determine commits behind: {stderr}",
            _env,
        )

    try:
        commits_behind = int(stdout)
    except ValueError:
        return _finish(
            "unanswerable", None, installed_sha, "unexpected rev-list output", _env
        )

    changelog_delta = _extract_changelog_delta(
        checkout, installed_sha, stamped_branch, runner=_runner, timeout=timeout
    )

    if commits_behind == 0:
        return _finish("ok", 0, installed_sha, None, _env, changelog_delta)
    return _finish("behind", commits_behind, installed_sha, None, _env, changelog_delta)
