"""Update detection (`trailhead update --check`) and apply (`trailhead update`).

Reads the install provenance stamp (`trailhead/provenance.py`) for the
checkout path and the sha that was wired, reads the tracked upstream branch
live from that checkout, and runs a read-only, timeout-bounded `git fetch`
against it. Modelled on lore's
sync freshness probe: a freshness stamp under `state_dir("trailhead")`
throttles the network fetch to once per window, written on ATTEMPT rather
than success, so an offline session pays one timeout per window instead of
one per invocation.

Any errored git invocation — nonzero exit, empty stdout, a missing upstream
ref, a timed-out fetch — reports the outcome "unanswerable", never "ok" or
"behind": a wrong confident answer here is worse than no answer.

The verdict answers two independent hops. `install_commits_behind` counts
how far the wired install is behind the checkout: an install snapshots the
plugin trees rather than pointing at them, so pulling the checkout without
re-running install leaves the install stale while the checkout is current.
`commits_behind` counts how far the checkout is behind its tracked branch.
Either being nonzero is "behind"; both zero is "ok".

The check performs no mutation of the checkout: every git invocation it
makes is one of `rev-parse`, `fetch`, `rev-list`, `diff` — never `pull`,
`checkout`, `merge`, or `reset`. git is injected via `runner` (same shape as
`provenance`'s — a callable(args, **kw) -> CompletedProcess-like object),
argv-only, never `shell=True`. git stderr is redacted of credentials
(`provenance.redact_credentials`) before it ever reaches a reason string.

The outcome is recorded back onto the provenance stamp via
`provenance.record_check_outcome` so a persistently failing check is
discoverable (`trailhead doctor`) rather than silently indistinguishable
from "up to date".

The `--json` output is a pinned schema (schema_version 3) — the producer
contract a SessionStart hook consumes:

    {"schema_version": 3, "outcome": "ok"|"behind"|"unanswerable",
     "commits_behind": <int|null>, "install_commits_behind": <int|null>,
     "installed_sha": <str|null>, "reason": <str|null>,
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
(`outcome` and both counts) are computed independently and stay correct
even when the delta extraction itself fails.

`run_update_apply` (`trailhead update`, no `--check`) performs the upgrade:
fast-forwards the checkout when it is behind, then re-wires via
`trailhead.install.wire_all_harnesses` — the same wire entrypoint `trailhead
install` uses — and refreshes the provenance stamp. Consent is a technical
gate: apply mode requires an interactive TTY confirmation or an explicit
`--yes`; a non-interactive invocation without it refuses before any git
invocation runs. A dirty checkout is refused before anything is fetched.
The fetch, the fast-forward, and the re-wire all run
under one acquisition of `trailhead.wire.wire_lock`, so a concurrent install
can never interleave with an in-flight upgrade; the config that drives the
re-wire is resolved before any of it runs, so a config error refuses cleanly
instead of surfacing after the checkout has already moved. If the re-wire
fails after a successful fast-forward, the checkout is reset to its actual
HEAD from immediately before the fast-forward (captured fresh, not read back
off the stamp — a manually-advanced checkout must not be rewound below where
it really was) and re-wired again against that reverted state, so a failed
upgrade is a true no-op rather than a half-upgraded install; the provenance
stamp is written only once that re-wire actually completes, so it never
claims a sha that was never fully wired. A failed re-wire is detected by its
own harness call's returncode, not merely a raised exception, so a `claude
plugin install` that genuinely fails (nonzero exit, no exception) still
triggers the rollback. Every refusal and failure prints a `trailhead:
<message>` line on stderr naming a concrete recovery command, and the
rollback's own message reports truthfully whether the reset and re-wire it
attempted actually succeeded.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from trailhead.install import resolve_config_for_env, wire_all_harnesses
from trailhead.paths import state_dir

# State-file plumbing is shared with `trailhead.provenance` rather than
# restated here: both modules write JSON into the same state dir, stamp the
# same timestamp format, and shell out to git through the same runner shape.
from trailhead.provenance import (
    _atomic_write_json,
    _default_runner,
    _now_iso,
    read_stamp_with_reason,
    record_check_outcome,
    redact_credentials,
    write_stamp,
)
from trailhead.wire import LockError, wire_lock

SCHEMA_VERSION = 3
FRESHNESS_WINDOW_SECONDS = 24 * 60 * 60
FRESHNESS_STAMP_FILENAME = "update-check.json"

CHANGELOG_PATH = "CHANGELOG.md"
CHANGELOG_DELTA_MAX_LINES = 200
CHANGELOG_DELTA_MAX_LINE_CHARS = 500

# Strips ANSI/VT escape sequences (CSI and simple two-byte forms) before any
# changelog content is ever surfaced to an agent.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b[@-Z\\-_]")
# C0 AND C1 control characters (\x00-\x1f, \x7f-\x9f), excluding TAB (\x09) —
# a changelog line is prose, which never legitimately carries a raw control
# byte EXCEPT a tab used as ordinary whitespace (e.g. an indented sub-bullet).
# The C1 range matters even though `_ANSI_ESCAPE_RE` only strips 7-bit ESC
# sequences: C1 codepoints are single-byte escape introducers in their own
# right (U+0090 DCS, U+009B CSI, U+009D OSC) and would otherwise bypass it.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")
# Zero-width space/non-joiner (U+200B-U+200C), bidirectional-override/isolate
# controls (U+202A-U+202E embedding/override, U+2066-U+2069 isolates), and
# U+FEFF (zero-width no-break space / BOM): a changelog line is rendered as
# plain prose, and any of these can make displayed text visually diverge from
# the bytes an agent actually reads or hide content in an invisible gap — the
# same rendering-versus-parsing gap that motivates the ANSI and
# control-character sanitization above. Deliberately EXCLUDES the rest of the
# U+200B-U+200F zero-width/mark range: U+200D ZWJ joins codepoints into one
# emoji glyph (stripping it splits the sequence into unrelated emoji), and
# U+200E/U+200F (LRM/RLM) are directionality HINTS, not overrides — neither
# carries this class's display-vs-parse divergence risk, so both must survive
# sanitization intact.
_BIDI_ZERO_WIDTH_RE = re.compile("[​-‌‪-‮⁦-⁩﻿]")
# A markdown fence is three backticks, but the codepoints deliberately
# preserved above (ZWJ, LRM/RLM, word joiner) are invisible when rendered, so
# backticks separated by them still display as a fence while defeating a
# literal "```" match. Any run of three or more backticks joined only by
# zero-width or directionality codepoints is therefore a fence.
_FENCE_RUN_RE = re.compile(r"(?:`[\u200b-\u200f\u2060-\u2064\ufeff]*){3,}")


# ---------------------------------------------------------------------------
# Freshness stamp — attempted-at, not succeeded-at
# ---------------------------------------------------------------------------


def freshness_stamp_path(*, env: dict[str, str] | None = None) -> Path:
    """Return the freshness-throttle stamp path: state_dir("trailhead")/update-check.json."""
    _env = env if env is not None else dict(os.environ)
    return state_dir("trailhead", env=_env) / FRESHNESS_STAMP_FILENAME


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
    _atomic_write_json(
        freshness_stamp_path(env=env), {"attempted_at": _now_iso()}, prefix=".update-check-"
    )


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
    except (subprocess.TimeoutExpired, OSError, UnicodeDecodeError):
        return None


def _proc_stderr(proc) -> str:
    """Render a git invocation's stderr for a user-facing reason string.

    Credentials are redacted here so no caller can forget to; a `None` proc
    means `_run_git` swallowed a timeout, an OSError (e.g. no `git` on PATH),
    or a `UnicodeDecodeError` (subprocess output that doesn't decode under the
    active locale — a real risk on the diff path, which carries
    attacker-authored non-ASCII).
    """
    return redact_credentials((proc.stderr or "").strip()) if proc is not None else "timed out or git unavailable"


def _remote_name(branch: str) -> str:
    """The remote to fetch from, derived from the stamped tracked branch.

    A tracked branch is normally `<remote>/<branch>`; a bare branch name with
    no remote prefix falls back to `origin`.
    """
    return branch.split("/", 1)[0] if "/" in branch else "origin"


def _unavailable_delta() -> dict:
    return {"available": False, "lines": [], "truncated": False}


def _sanitize_delta_line(line: str) -> str:
    """Neutralise one changelog delta line before it can reach an agent.

    Strips ANSI escapes, control characters (C0 and C1, excluding TAB), and
    the zero-width/bidi-override characters that carry a display-vs-parse
    divergence risk (see `_BIDI_ZERO_WIDTH_RE`), then breaks any markdown
    fence sequence (```) so the delta can later be embedded inside a
    delimited untrusted-content block without letting attacker text close
    that fence early. Also bounds a single line's length — an attacker
    controls this text and a single absurdly long line would otherwise
    defeat the line-count cap.
    """
    line = _ANSI_ESCAPE_RE.sub("", line)
    line = _CONTROL_CHAR_RE.sub("", line)
    line = _BIDI_ZERO_WIDTH_RE.sub("", line)
    line = _FENCE_RUN_RE.sub(lambda m: m.group(0).replace("`", "'"), line)
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


def _resolve_upstream_branch(checkout: Path, *, runner, timeout: int) -> tuple[str | None, str]:
    """Return (tracked upstream branch, error) for *checkout*, read live.

    The branch is the value every later git call takes as a ref positional.
    Reading it from git rather than from a rewritable file means it can only
    ever be a name git itself accepted — git refuses to create a ref that
    begins with `-`, so no derived value here can be parsed as an option.
    """
    proc = _run_git(
        checkout, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
        runner=runner, timeout=timeout,
    )
    branch = (proc.stdout or "").strip() if proc else ""
    if proc is None or proc.returncode != 0 or not branch:
        return None, (
            f"could not resolve the tracked upstream branch: {_proc_stderr(proc)}"
        )
    return branch, ""


def _count_commits(checkout: Path, rev_range: str, *, runner, timeout: int) -> tuple[int | None, str]:
    """Return (commit count for *rev_range*, error). Shared by both hops of
    the verdict so a failure on either degrades identically."""
    proc = _run_git(checkout, "rev-list", "--count", rev_range, runner=runner, timeout=timeout)
    stdout = (proc.stdout or "").strip() if proc else ""
    if proc is None or proc.returncode != 0 or not stdout:
        return None, f"could not determine commits behind: {_proc_stderr(proc)}"
    try:
        return int(stdout), ""
    except ValueError:
        return None, "unexpected rev-list output"


def _finish(
    outcome: str,
    commits_behind: int | None,
    installed_sha: str | None,
    reason: str | None,
    env: dict[str, str],
    changelog_delta: dict | None = None,
    install_commits_behind: int | None = None,
) -> dict:
    """Record the check outcome onto the provenance stamp and return the
    pinned result. Every `check_for_update` exit goes through here, so no
    outcome can reach a caller without also being recorded."""
    redacted_reason = redact_credentials(reason) if reason else None
    record_check_outcome(outcome, reason=redacted_reason, env=env)
    return {
        "schema_version": SCHEMA_VERSION,
        "outcome": outcome,
        "commits_behind": commits_behind,
        "install_commits_behind": install_commits_behind,
        "installed_sha": installed_sha,
        "reason": redacted_reason,
        "changelog_delta": changelog_delta if changelog_delta is not None else _unavailable_delta(),
    }


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

    stamp, rejected_reason = read_stamp_with_reason(env=_env, confine_root=confine_root)
    if stamp is None:
        reason = (
            f"install provenance stamp rejected: {rejected_reason}"
            if rejected_reason
            else "no install provenance stamp found"
        )
        return _finish("unanswerable", None, None, reason, _env)

    checkout = Path(stamp["checkout"])
    installed_sha = stamp["sha"]

    branch, branch_error = _resolve_upstream_branch(checkout, runner=_runner, timeout=timeout)
    if branch is None:
        return _finish("unanswerable", None, installed_sha, branch_error, _env)

    remote_name = _remote_name(branch)

    if not _fetch_is_fresh(_env, window):
        _stamp_fetch_attempt(_env)
        # `--` ends option parsing before the remote name. The name is derived
        # from git's own upstream ref, which can never begin with `-`; the
        # guard costs nothing and holds the invariant at the call site.
        fetch_proc = _run_git(
            checkout, "fetch", "--quiet", "--", remote_name, runner=_runner, timeout=timeout
        )
        if fetch_proc is None or fetch_proc.returncode != 0:
            return _finish(
                "unanswerable",
                None,
                installed_sha,
                f"git fetch failed: {_proc_stderr(fetch_proc)}",
                _env,
            )

    # Two hops, counted separately: the checkout against its remote, and the
    # wired install against the checkout. An install snapshots the plugin
    # trees, so a checkout pulled without re-running install is current while
    # the install behind it is not.
    commits_behind, error = _count_commits(
        checkout, f"HEAD..{branch}", runner=_runner, timeout=timeout
    )
    if commits_behind is None:
        return _finish("unanswerable", None, installed_sha, error, _env)

    install_behind, error = _count_commits(
        checkout, f"{installed_sha}..HEAD", runner=_runner, timeout=timeout
    )
    if install_behind is None:
        return _finish("unanswerable", None, installed_sha, error, _env)

    changelog_delta = _extract_changelog_delta(
        checkout, installed_sha, branch, runner=_runner, timeout=timeout
    )

    outcome = "ok" if commits_behind == 0 and install_behind == 0 else "behind"
    return _finish(
        outcome, commits_behind, installed_sha, None, _env, changelog_delta, install_behind
    )


# ---------------------------------------------------------------------------
# Apply mode — `trailhead update` (no `--check`)
# ---------------------------------------------------------------------------


def _default_is_tty():
    return sys.stdin.isatty()


def _confirm(prompt: str) -> bool:
    """Read a y/N confirmation from stdin. Anything but y/yes is False."""
    print(prompt, end="", flush=True)
    try:
        raw = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        return False
    return raw.strip().lower() in ("y", "yes")


def run_update_apply(
    *,
    env: dict[str, str] | None = None,
    runner=None,
    assume_yes: bool = False,
    dry_run: bool = False,
    timeout: int = 10,
    confine_root: Path | str | None = None,
    is_tty=None,
) -> int:
    """Perform the upgrade: fast-forward the stamped checkout, then re-wire.

    The checkout may already be level with its tracked remote while the
    install behind it is not — an install snapshots the plugin trees rather
    than pointing at them — so the fast-forward is skipped and the re-wire
    still runs. Only a checkout that is level with the remote AND wired from
    that same sha is a no-op.

    Consent is a TECHNICAL gate, not a courtesy: without ``assume_yes`` this
    refuses on any non-interactive invocation, and mutates nothing before that
    gate passes. A dirty checkout is refused BEFORE anything is fetched.
    Every refusal and every
    failure prints a named, actionable ``trailhead: <message>`` line naming a
    recovery command.

    Failure past the fast-forward is a true no-op ATTEMPT: if the re-wire
    raises after a successful fast-forward, the checkout is reset to the
    pre-upgrade sha and ``wire_all_harnesses`` is run again against that
    reverted checkout to restore the prior wiring — but the reported outcome
    is truthful about whether that reset and re-wire actually succeeded,
    never a claimed restoration that didn't happen. The provenance stamp is
    written ONLY after a re-wire actually completes, so it never claims a sha
    that was never fully wired.

    Returns 0 on success or a genuine no-op (already up to date); 1 on any
    refusal or failure.
    """
    _env = env if env is not None else dict(os.environ)
    _runner = runner if runner is not None else _default_runner()
    _is_tty = is_tty if is_tty is not None else _default_is_tty

    stamp, rejected_reason = read_stamp_with_reason(env=_env, confine_root=confine_root)
    if stamp is None:
        if rejected_reason:
            print(
                f"trailhead: install provenance stamp rejected: {rejected_reason}. "
                "Run `trailhead install` again to write a fresh stamp.",
                file=sys.stderr,
            )
        else:
            print(
                "trailhead: no install provenance stamp found — nothing to upgrade. "
                "Run `trailhead install` first.",
                file=sys.stderr,
            )
        return 1

    checkout = Path(stamp["checkout"])
    pre_sha = stamp["sha"]


    # ------------------------------------------------------------------
    # Consent gate — technical, not a courtesy. Nothing below this point may
    # run before it passes (dry-run previews without mutating, so it bypasses
    # the gate entirely).
    # ------------------------------------------------------------------
    if not dry_run and not assume_yes:
        if _is_tty():
            print(
                f"This upgrades the trailhead install from {checkout}: "
                f"fetches its tracked upstream branch, fast-forwards, and "
                f"re-wires every configured plugin.\n"
            )
            if not _confirm("Proceed? [y/N] "):
                print("aborted — nothing was changed")
                return 0
        else:
            print(
                "trailhead: refusing to upgrade without confirmation — re-run "
                "with --yes, or run interactively. `trailhead update` never "
                "mutates the install unprompted.",
                file=sys.stderr,
            )
            return 1

    branch, branch_error = _resolve_upstream_branch(checkout, runner=_runner, timeout=timeout)
    if branch is None:
        print(
            f"trailhead: {branch_error}. Inspect directly: "
            f"git -C {checkout} rev-parse --abbrev-ref --symbolic-full-name @{{u}}",
            file=sys.stderr,
        )
        return 1
    remote_name = _remote_name(branch)

    # ------------------------------------------------------------------
    # Dirty-checkout guard — refuse before mutating anything.
    # ------------------------------------------------------------------
    status_proc = _run_git(checkout, "status", "--porcelain", runner=_runner, timeout=timeout)
    if status_proc is None or status_proc.returncode != 0:
        print(
            f"trailhead: could not read the checkout's working-tree status: "
            f"{_proc_stderr(status_proc)}. Inspect it directly: "
            f"git -C {checkout} status",
            file=sys.stderr,
        )
        return 1
    if (status_proc.stdout or "").strip():
        print(
            f"trailhead: refusing to upgrade — {checkout} has uncommitted "
            f"changes. Commit or stash them, then re-run: trailhead update",
            file=sys.stderr,
        )
        return 1

    if dry_run:
        print(
            f"trailhead: dry run — would fetch {remote_name}, fast-forward "
            f"{checkout} to {branch} if possible, then re-wire. "
            f"No changes made."
        )
        return 0

    # ------------------------------------------------------------------
    # Everything from here mutates the checkout and/or the composed trees —
    # held under the shared wire lock so a concurrent install can never
    # interleave with an in-flight upgrade.
    # ------------------------------------------------------------------
    try:
        with wire_lock(env=_env):
            # Resolved BEFORE any mutation: a config error must refuse cleanly,
            # never surface after the checkout has already been fast-forwarded
            # with nothing left to roll it back.
            cfg = resolve_config_for_env(_env)

            # `--` ends option parsing before the remote name. The name is
            # derived from git's own upstream ref, which can never begin with
            # `-`; the guard costs nothing and holds the invariant here too.
            fetch_proc = _run_git(
                checkout, "fetch", "--quiet", "--", remote_name, runner=_runner, timeout=timeout
            )
            if fetch_proc is None or fetch_proc.returncode != 0:
                print(
                    f"trailhead: git fetch failed: {_proc_stderr(fetch_proc)}. "
                    f"Retry: trailhead update, or inspect directly: "
                    f"git -C {checkout} fetch {remote_name}",
                    file=sys.stderr,
                )
                return 1

            # `git rev-parse -- <rev>` does NOT mean "end of options" — rev-parse
            # echoes a literal `--` back as one of its outputs, corrupting the
            # single-sha stdout this call depends on. The branch is read from
            # git's own upstream ref, which can never begin with `-`, so there
            # is nothing option-shaped for a guard to stop here.
            remote_sha_proc = _run_git(
                checkout, "rev-parse", branch, runner=_runner, timeout=timeout
            )
            remote_sha = (remote_sha_proc.stdout or "").strip() if remote_sha_proc else ""
            if remote_sha_proc is None or remote_sha_proc.returncode != 0 or not remote_sha:
                print(
                    f"trailhead: could not resolve {branch}: "
                    f"{_proc_stderr(remote_sha_proc)}. Inspect directly: "
                    f"git -C {checkout} rev-parse {branch}",
                    file=sys.stderr,
                )
                return 1

            # Captured immediately before any mutation, NOT read from the
            # stamp: a checkout manually advanced past its wired sha must roll
            # back to where it actually was, not below it.
            pre_merge_proc = _run_git(checkout, "rev-parse", "HEAD", runner=_runner, timeout=timeout)
            pre_merge_head = (pre_merge_proc.stdout or "").strip() if pre_merge_proc else ""
            if pre_merge_proc is None or pre_merge_proc.returncode != 0 or not pre_merge_head:
                print(
                    f"trailhead: could not resolve HEAD before fast-forwarding: "
                    f"{_proc_stderr(pre_merge_proc)}. Inspect directly: "
                    f"git -C {checkout} status",
                    file=sys.stderr,
                )
                return 1

            # Two independent hops. The checkout may already be current while
            # the install behind it is not — an install snapshots the plugin
            # trees, so a manually pulled checkout still needs a re-wire.
            if remote_sha == pre_merge_head and pre_merge_head == pre_sha:
                print(f"trailhead: already up to date (installed {pre_sha[:8]})")
                return 0

            # The checkout may already be level with the remote while the
            # install behind it is not: the fast-forward is skipped and the
            # re-wire below still runs.
            if remote_sha != pre_merge_head:
                ancestor_proc = _run_git(
                    checkout,
                    "merge-base",
                    "--is-ancestor",
                    "HEAD",
                    "--",
                    branch,
                    runner=_runner,
                    timeout=timeout,
                )
                if ancestor_proc is None or ancestor_proc.returncode != 0:
                    print(
                        f"trailhead: refusing to upgrade — {checkout}'s HEAD has "
                        f"diverged from {branch} and cannot be "
                        f"fast-forwarded. Resolve it yourself, e.g.: "
                        f"git -C {checkout} merge {branch}",
                        file=sys.stderr,
                    )
                    return 1

                print(f"trailhead: fast-forwarding {checkout} to {branch}…")
                merge_proc = _run_git(
                    checkout, "merge", "--ff-only", "--", branch, runner=_runner, timeout=timeout
                )
                if merge_proc is None or merge_proc.returncode != 0:
                    print(
                        f"trailhead: fast-forward failed: {_proc_stderr(merge_proc)}. "
                        f"The checkout was not changed. Inspect directly: "
                        f"git -C {checkout} status",
                        file=sys.stderr,
                    )
                    return 1

            print("trailhead: re-wiring plugins…")
            try:
                wire_all_harnesses(cfg, env=_env, runner=_runner, quiet=True)
            except Exception as exc:
                reset_proc = _run_git(
                    checkout, "reset", "--hard", pre_merge_head, runner=_runner, timeout=timeout
                )
                reset_ok = reset_proc is not None and reset_proc.returncode == 0
                rewired_ok = False
                if reset_ok:
                    try:
                        wire_all_harnesses(cfg, env=_env, runner=_runner, quiet=True)
                        rewired_ok = True
                    except Exception:
                        pass  # best-effort restore; the error below still stands
                if reset_ok and rewired_ok:
                    print(
                        f"trailhead: upgrade failed while re-wiring ({exc}); rolled "
                        f"the checkout back to {pre_merge_head[:8]} and restored the "
                        f"prior wiring. Re-run: trailhead update",
                        file=sys.stderr,
                    )
                elif reset_ok:
                    print(
                        f"trailhead: upgrade failed while re-wiring ({exc}); rolled "
                        f"the checkout back to {pre_merge_head[:8]} but the prior "
                        f"wiring could NOT be restored automatically. Re-wire "
                        f"manually: trailhead install",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"trailhead: upgrade failed while re-wiring ({exc}); the "
                        f"checkout could NOT be rolled back to {pre_merge_head[:8]}. "
                        f"Inspect and repair manually: "
                        f"git -C {checkout} reset --hard {pre_merge_head}",
                        file=sys.stderr,
                    )
                return 1

            write_stamp(checkout, env=_env, runner=_runner)
    except LockError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"trailhead: upgraded to {remote_sha[:8]}")
    return 0
