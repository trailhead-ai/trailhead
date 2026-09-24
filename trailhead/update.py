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
Either being nonzero is "behind"; both zero is "ok". The second hop degrades
to null on its own — a wired sha that was force-pushed away or gc'd is no
longer a valid revision — without taking the first hop's verdict with it.

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

The `--json` output is a pinned schema (schema_version 4) — the producer
contract a SessionStart hook consumes:

    {"schema_version": 4, "outcome": "ok"|"behind"|"unanswerable",
     "commits_behind": <int|null>, "install_commits_behind": <int|null>,
     "installed_sha": <str|null>, "reason": <str|null>,
     "changelog_delta": {"available": <bool>, "lines": [<str>, ...],
                          "truncated": <bool>},
     "outpost": null | {"outcome": "ok"|"behind"|"unanswerable",
                        "commits_behind": <int|null>, "reason": <str|null>}}

`outpost` reports the outpost checkout named by `config_dir("outpost")/
config.toml`'s `checkout` key (see `outpost_lifecycle.configured_checkout`):
`null` when none is configured, otherwise how far that checkout is behind its
own tracked branch, probed with the same read-only git calls and the same
fetch throttle window as the install. It is independent of the top-level
verdict, which stays the install's alone — an unanswerable outpost check never
masks the install's answer, nor the reverse.

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
install` uses — and refreshes the provenance stamp. When an outpost checkout
is configured it is upgraded next: fast-forwarded, its dependencies
reinstalled (`npm ci`), and rebuilt — through `outpost_lifecycle.restart`
when the daemon is answering, `outpost_lifecycle.build` when it is not. Consent is a technical
gate: apply mode requires an interactive TTY confirmation or an explicit
`--yes`; a non-interactive invocation without it refuses before any git
invocation runs. Nothing is read from either checkout before that gate, its
tracked upstream branch included — so a checkout with no upstream configured
is confirmed and only then refused, rather than probed ahead of consent. Only
outpost's config file is read ahead of it, so the prompt can name the outpost
checkout it will touch, and an unusable outpost config refuses right there.
Both checkouts are preflighted — upstream branch resolvable, working tree
clean — before anything is fetched: a problem with either refuses the whole
upgrade while nothing has moved.
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

The outpost upgrade runs only after the install upgrade has completed, under
the same lock, and never undoes it: the two are independent, so a failure on
the outpost side keeps the upgraded install and exits nonzero. A failed
dependency install, build, or restart resets the outpost checkout to its HEAD
from immediately before its fast-forward and reinstalls + rebuilds (or
restarts) from there; as with the install, the message reports truthfully
whether that reset and rebuild worked and names the manual repair when not.
`restart` builds before it stops anything, so a failed build never takes a
running daemon down.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from trailhead import outpost_lifecycle
from trailhead.install import resolve_config_for_env, wire_all_harnesses
from trailhead.outpost_lifecycle import OutpostLifecycleError
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

SCHEMA_VERSION = 4
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


def _run_git(checkout: Path, *args: str, runner, timeout: int, env: dict[str, str] | None = None):
    kw = {"env": env} if env is not None else {}
    try:
        return runner(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            **kw,
        )
    except (subprocess.TimeoutExpired, OSError, UnicodeDecodeError):
        return None


def _unattended_git_env(env: dict[str, str]) -> dict[str, str]:
    """The environment for a fetch nobody is watching (the check runs from a
    SessionStart hook): a remote that needs credentials must fail fast, never
    stop to prompt on the terminal."""
    return {**env, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}


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
    """The remote to fetch from, derived from the tracked upstream branch.

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

    The branch is the value every later git call takes as a ref positional,
    and several of those call sites (`diff`, `rev-parse`) have no `--`
    end-of-options guard available. Reading the branch from git is NOT on
    its own enough to make it argv-safe: only `git branch` refuses a name
    beginning with `-`. `git check-ref-format` accepts one, and a remote
    named `--output=<path>` with a matching remote-tracking ref makes
    `rev-parse --abbrev-ref @{u}` hand back `--output=<path>/main`, which
    `git diff` parses as an option and uses to truncate that path. So the
    shape is checked here, at the single point the branch enters the
    program, rather than at each call site that consumes it.

    Control characters need no check: `check-ref-format` rejects them, so
    git cannot produce a ref carrying one.
    """
    proc = _run_git(
        checkout, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
        runner=runner, timeout=timeout,
    )
    branch = (proc.stdout or "").strip() if proc else ""
    if proc is None or proc.returncode != 0 or not branch:
        return None, (f"could not resolve the tracked upstream branch: {_proc_stderr(proc)}")
    if branch.startswith("-"):
        return None, "the tracked upstream branch is option-shaped; refusing to pass it to git"
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
        "outpost": None,
    }


def check_for_update(
    *,
    env: dict[str, str] | None = None,
    runner=None,
    timeout: int = 10,
    window: int = FRESHNESS_WINDOW_SECONDS,
    confine_root: Path | str | None = None,
) -> dict:
    """Check whether the stamped checkout is behind its tracked remote branch,
    and whether a configured outpost checkout is behind its own.

    Returns the pinned `{"schema_version", "outcome", "commits_behind",
    "install_commits_behind", "installed_sha", "reason", "changelog_delta",
    "outpost"}` shape (see the module docstring). Never raises for a git-side
    failure — those all collapse to `outcome == "unanswerable"`, for the
    install and for outpost independently.
    """
    _env = env if env is not None else dict(os.environ)
    _runner = runner if runner is not None else _default_runner()

    # One throttle window covers both checkouts' fetches.
    fetch_due = not _fetch_is_fresh(_env, window)
    result = _check_install(
        env=_env, runner=_runner, timeout=timeout, fetch_due=fetch_due, confine_root=confine_root
    )
    result["outpost"] = _check_outpost(
        env=_env, runner=_runner, timeout=timeout, fetch_due=fetch_due
    )
    return result


def _outpost_verdict(outcome: str, commits_behind: int | None, reason: str | None) -> dict:
    return {
        "outcome": outcome,
        "commits_behind": commits_behind,
        "reason": redact_credentials(reason) if reason else None,
    }


def _check_outpost(*, env: dict[str, str], runner, timeout: int, fetch_due: bool) -> dict | None:
    """How far the configured outpost checkout is behind its tracked branch.

    `None` when no outpost checkout is configured. An outpost config that names
    a checkout trailhead cannot use is "unanswerable" with the reason, and no
    git runs against it. Read-only, like the install probe: `rev-parse`,
    `fetch`, `rev-list` only.
    """
    try:
        checkout = outpost_lifecycle.configured_checkout(env)
    except OutpostLifecycleError as exc:
        return _outpost_verdict("unanswerable", None, str(exc))
    if checkout is None:
        return None

    branch, branch_error = _resolve_upstream_branch(checkout, runner=runner, timeout=timeout)
    if branch is None:
        return _outpost_verdict("unanswerable", None, branch_error)

    if fetch_due:
        _stamp_fetch_attempt(env)
        fetch_proc = _run_git(
            checkout, "fetch", "--quiet", "--", _remote_name(branch),
            runner=runner, timeout=timeout, env=_unattended_git_env(env),
        )
        if fetch_proc is None or fetch_proc.returncode != 0:
            return _outpost_verdict(
                "unanswerable", None, f"git fetch failed: {_proc_stderr(fetch_proc)}"
            )

    commits_behind, error = _count_commits(
        checkout, f"HEAD..{branch}", runner=runner, timeout=timeout
    )
    if commits_behind is None:
        return _outpost_verdict("unanswerable", None, error)
    return _outpost_verdict("ok" if commits_behind == 0 else "behind", commits_behind, None)


def _check_install(
    *,
    env: dict[str, str],
    runner,
    timeout: int,
    fetch_due: bool,
    confine_root: Path | str | None,
) -> dict:
    """The install's half of `check_for_update`: the stamped checkout against
    its tracked branch, and the wired install against that checkout."""
    _env = env
    _runner = runner

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

    if fetch_due:
        _stamp_fetch_attempt(_env)
        # `--` ends option parsing before the remote name. The name is derived
        # from git's own upstream ref, which can never begin with `-`; the
        # guard costs nothing and holds the invariant at the call site.
        fetch_proc = _run_git(
            checkout, "fetch", "--quiet", "--", remote_name,
            runner=_runner, timeout=timeout, env=_unattended_git_env(_env),
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

    # A wired sha that was force-pushed away, amended, or gc'd is no longer a
    # valid revision here. That hop degrades to null on its own; it never
    # takes the checkout-versus-branch verdict down with it, or an install
    # whose sha went missing would be silent forever.
    #
    # An install AHEAD of HEAD (the checkout was reset backwards) counts 0:
    # there is nothing to bring the install forward TO, and the checkout hop
    # still reports whatever the remote holds. Apply mode treats the same
    # state as a re-wire, since re-wiring is what makes the install match the
    # checkout again.
    install_behind, _error = _count_commits(
        checkout, f"{installed_sha}..HEAD", runner=_runner, timeout=timeout
    )

    changelog_delta = _extract_changelog_delta(
        checkout, installed_sha, branch, runner=_runner, timeout=timeout
    )

    outcome = "ok" if commits_behind == 0 and not install_behind else "behind"
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
    """Perform the upgrade: fast-forward the stamped checkout, then re-wire,
    then upgrade a configured outpost checkout.

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

    Outpost is preflighted with the install and refused together with it, but
    upgraded after it and rolled back on its own (see the module docstring).

    Returns 0 on success or a genuine no-op (already up to date); 1 on any
    refusal or failure, including an outpost failure after a kept install
    upgrade.
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

    # Reading outpost's config is not reading either checkout, so it may run
    # ahead of consent — which lets the prompt name everything it will touch.
    try:
        outpost_checkout = outpost_lifecycle.configured_checkout(_env)
    except OutpostLifecycleError as exc:
        print(
            f"trailhead: {exc} Fix the outpost config, then re-run: trailhead update",
            file=sys.stderr,
        )
        return 1

    # ------------------------------------------------------------------
    # Consent gate — technical, not a courtesy. Nothing below this point may
    # run before it passes (dry-run previews without mutating, so it bypasses
    # the gate entirely).
    # ------------------------------------------------------------------
    if not dry_run and not assume_yes:
        if _is_tty():
            outpost_note = (
                f" It then fast-forwards the outpost checkout at {outpost_checkout}, "
                f"reinstalls its dependencies, and rebuilds it (restarting the "
                f"daemon if it is running)."
                if outpost_checkout is not None
                else ""
            )
            print(
                f"This upgrades the trailhead install from {checkout}: "
                f"fetches its tracked upstream branch, fast-forwards, and "
                f"re-wires every configured plugin.{outpost_note}\n"
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

    # ------------------------------------------------------------------
    # Preflight — both checkouts, before mutating either. A problem with the
    # outpost checkout refuses the whole upgrade here, while nothing has moved.
    # ------------------------------------------------------------------
    preflight = [(checkout, "")]
    if outpost_checkout is not None:
        preflight.append((outpost_checkout, "outpost "))
    branches: dict[Path, str] = {}
    for target, label in preflight:
        branch, branch_error = _resolve_upstream_branch(target, runner=_runner, timeout=timeout)
        if branch is None:
            escape = (
                " To upgrade trailhead without outpost, remove or re-point the "
                "'checkout' key in the outpost config."
                if target == outpost_checkout
                else ""
            )
            print(
                f"trailhead: {label}{branch_error}. Inspect directly: "
                f"git -C {target} rev-parse --abbrev-ref --symbolic-full-name @{{u}}.{escape}",
                file=sys.stderr,
            )
            return 1
        if not _is_clean(target, runner=_runner, timeout=timeout):
            return 1
        branches[target] = branch

    if dry_run:
        outpost_note = (
            f" Would then fast-forward {outpost_checkout} to {branches[outpost_checkout]} "
            f"if possible and rebuild outpost."
            if outpost_checkout is not None
            else ""
        )
        print(
            f"trailhead: dry run — would fetch {_remote_name(branches[checkout])}, fast-forward "
            f"{checkout} to {branches[checkout]} if possible, then re-wire.{outpost_note} "
            f"No changes made."
        )
        return 0

    # ------------------------------------------------------------------
    # Everything from here mutates the checkouts and/or the composed trees —
    # held under the shared wire lock so a concurrent install or upgrade can
    # never interleave with an in-flight one.
    # ------------------------------------------------------------------
    try:
        with wire_lock(env=_env):
            # Resolved BEFORE any mutation: a config error must refuse cleanly,
            # never surface after the checkout has already been fast-forwarded
            # with nothing left to roll it back.
            cfg = resolve_config_for_env(_env)

            if not _upgrade_install(
                checkout, pre_sha, branches[checkout], cfg, env=_env, runner=_runner, timeout=timeout
            ):
                return 1
            if outpost_checkout is not None and not _upgrade_outpost(
                outpost_checkout, branches[outpost_checkout], env=_env, runner=_runner, timeout=timeout
            ):
                return 1
    except LockError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


def _is_clean(checkout: Path, *, runner, timeout: int) -> bool:
    """True when *checkout* has no uncommitted changes. Prints the named
    refusal and returns False otherwise, or when the status is unreadable."""
    status_proc = _run_git(checkout, "status", "--porcelain", runner=runner, timeout=timeout)
    if status_proc is None or status_proc.returncode != 0:
        print(
            f"trailhead: could not read the checkout's working-tree status: "
            f"{_proc_stderr(status_proc)}. Inspect it directly: "
            f"git -C {checkout} status",
            file=sys.stderr,
        )
        return False
    if (status_proc.stdout or "").strip():
        print(
            f"trailhead: refusing to upgrade — {checkout} has uncommitted "
            f"changes. Commit or stash them, then re-run: trailhead update",
            file=sys.stderr,
        )
        return False
    return True


def _fast_forward(checkout: Path, branch: str, *, runner, timeout: int):
    """Fetch *branch*'s remote and fast-forward *checkout* onto it.

    Returns ``(status, pre_head, remote_sha)`` where status is ``"advanced"``
    (the checkout moved), ``"current"`` (nothing to pull — level with, or
    carrying local commits ahead of, the branch), or ``"failed"`` (a named
    error was printed and the checkout was not changed). ``pre_head`` is HEAD
    read immediately before any mutation — the rollback target.
    """
    remote_name = _remote_name(branch)
    # `--` ends option parsing before the remote name. The name is derived
    # from git's own upstream ref, which can never begin with `-`; the guard
    # costs nothing and holds the invariant here too.
    fetch_proc = _run_git(
        checkout, "fetch", "--quiet", "--", remote_name, runner=runner, timeout=timeout
    )
    if fetch_proc is None or fetch_proc.returncode != 0:
        print(
            f"trailhead: git fetch failed: {_proc_stderr(fetch_proc)}. "
            f"Retry: trailhead update, or inspect directly: "
            f"git -C {checkout} fetch {remote_name}",
            file=sys.stderr,
        )
        return "failed", "", ""

    # `git rev-parse -- <rev>` does NOT mean "end of options" — rev-parse
    # echoes a literal `--` back as one of its outputs, corrupting the
    # single-sha stdout this call depends on. The branch is read from git's
    # own upstream ref, which can never begin with `-`, so there is nothing
    # option-shaped for a guard to stop here.
    remote_sha_proc = _run_git(checkout, "rev-parse", branch, runner=runner, timeout=timeout)
    remote_sha = (remote_sha_proc.stdout or "").strip() if remote_sha_proc else ""
    if remote_sha_proc is None or remote_sha_proc.returncode != 0 or not remote_sha:
        print(
            f"trailhead: could not resolve {branch}: "
            f"{_proc_stderr(remote_sha_proc)}. Inspect directly: "
            f"git -C {checkout} rev-parse {branch}",
            file=sys.stderr,
        )
        return "failed", "", ""

    # Captured immediately before any mutation, NOT read from a stamp: a
    # checkout manually advanced past its wired sha must roll back to where
    # it actually was, not below it.
    pre_merge_proc = _run_git(checkout, "rev-parse", "HEAD", runner=runner, timeout=timeout)
    pre_head = (pre_merge_proc.stdout or "").strip() if pre_merge_proc else ""
    if pre_merge_proc is None or pre_merge_proc.returncode != 0 or not pre_head:
        print(
            f"trailhead: could not resolve HEAD before fast-forwarding: "
            f"{_proc_stderr(pre_merge_proc)}. Inspect directly: "
            f"git -C {checkout} status",
            file=sys.stderr,
        )
        return "failed", "", ""

    # A checkout carrying local commits is AHEAD of its tracked branch, not
    # diverged from it: there is nothing to fetch down, and refusing it would
    # name a merge that does nothing.
    remote_is_behind = remote_sha != pre_head and _run_git(
        checkout,
        "merge-base",
        "--is-ancestor",
        "--",
        branch,
        "HEAD",
        runner=runner,
        timeout=timeout,
    )
    nothing_to_pull = remote_sha == pre_head or (
        remote_is_behind is not None and remote_is_behind.returncode == 0
    )
    if nothing_to_pull:
        return "current", pre_head, remote_sha

    ancestor_proc = _run_git(
        checkout,
        "merge-base",
        "--is-ancestor",
        "HEAD",
        "--",
        branch,
        runner=runner,
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
        return "failed", pre_head, remote_sha

    print(f"trailhead: fast-forwarding {checkout} to {branch}…")
    merge_proc = _run_git(
        checkout, "merge", "--ff-only", "--", branch, runner=runner, timeout=timeout
    )
    if merge_proc is None or merge_proc.returncode != 0:
        print(
            f"trailhead: fast-forward failed: {_proc_stderr(merge_proc)}. "
            f"The checkout was not changed. Inspect directly: "
            f"git -C {checkout} status",
            file=sys.stderr,
        )
        return "failed", pre_head, remote_sha
    return "advanced", pre_head, remote_sha


def _upgrade_install(
    checkout: Path, pre_sha: str, branch: str, cfg, *, env: dict[str, str], runner, timeout: int
) -> bool:
    """Fast-forward the install's checkout and re-wire, rolling back on a
    failed re-wire. Returns True on success or a genuine no-op; False after
    printing a named failure. Caller holds the wire lock."""
    status, pre_merge_head, remote_sha = _fast_forward(checkout, branch, runner=runner, timeout=timeout)
    if status == "failed":
        return False

    # Two independent hops. The checkout may have nothing to pull while the
    # install behind it is stale — an install snapshots the plugin trees — in
    # which case the re-wire below still runs.
    if status == "current" and pre_merge_head == pre_sha:
        print(f"trailhead: already up to date (installed {pre_sha[:8]})")
        return True

    print("trailhead: re-wiring plugins…")
    try:
        wire_all_harnesses(cfg, env=env, runner=runner, quiet=True)
    except Exception as exc:
        reset_proc = _run_git(
            checkout, "reset", "--hard", pre_merge_head, runner=runner, timeout=timeout
        )
        reset_ok = reset_proc is not None and reset_proc.returncode == 0
        rewired_ok = False
        if reset_ok:
            try:
                wire_all_harnesses(cfg, env=env, runner=runner, quiet=True)
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
        return False

    write_stamp(checkout, env=env, runner=runner)
    print(f"trailhead: upgraded to {remote_sha[:8]}")
    return True


def _refresh_outpost(env: dict[str, str], *, restart: bool) -> None:
    """Install outpost's pinned dependencies, then build it — through
    ``restart`` when the daemon is running, which builds before it stops
    anything, so a failed build never takes a running daemon down."""
    outpost_lifecycle.install_dependencies(env=env)
    if restart:
        outpost_lifecycle.restart(env=env)
    else:
        outpost_lifecycle.build(env=env)


def _upgrade_outpost(
    checkout: Path, branch: str, *, env: dict[str, str], runner, timeout: int
) -> bool:
    """Fast-forward the outpost checkout, reinstall its dependencies, and
    rebuild it (restarting the daemon if it was answering). Runs only after
    the install upgrade succeeded, and never undoes it: a failure here rolls
    back outpost alone — reset to its pre-upgrade HEAD, then reinstalled and
    rebuilt/restarted from there — and reports truthfully whether that
    restore worked. Returns True on success or a no-op; False after printing
    a named failure."""
    status, pre_head, remote_sha = _fast_forward(checkout, branch, runner=runner, timeout=timeout)
    if status == "failed":
        print(
            "trailhead: outpost was not upgraded; the trailhead install is unaffected.",
            file=sys.stderr,
        )
        return False
    if status == "current":
        print(f"trailhead: outpost already up to date ({pre_head[:8]})")
        return True

    was_running = outpost_lifecycle.is_answering()
    try:
        _refresh_outpost(env, restart=was_running)
    except Exception as exc:
        reset_proc = _run_git(checkout, "reset", "--hard", pre_head, runner=runner, timeout=timeout)
        reset_ok = reset_proc is not None and reset_proc.returncode == 0
        restored = False
        if reset_ok:
            try:
                _refresh_outpost(env, restart=was_running)
                restored = True
            except Exception:
                pass  # best-effort restore; the error below still stands
        if reset_ok and restored:
            print(
                f"trailhead: outpost upgrade failed ({exc}); rolled {checkout} back "
                f"to {pre_head[:8]} and restored the prior build. The trailhead "
                f"install is unaffected. Re-run: trailhead update",
                file=sys.stderr,
            )
        elif reset_ok:
            then_restart = ", then: trailhead outpost restart" if was_running else ""
            print(
                f"trailhead: outpost upgrade failed ({exc}); rolled {checkout} back "
                f"to {pre_head[:8]} but the prior build could NOT be rebuilt "
                f"automatically. Repair manually: cd {checkout} && npm ci && "
                f"npm run build{then_restart}",
                file=sys.stderr,
            )
        else:
            print(
                f"trailhead: outpost upgrade failed ({exc}); {checkout} could NOT "
                f"be rolled back to {pre_head[:8]}. Inspect and repair manually: "
                f"git -C {checkout} reset --hard {pre_head}",
                file=sys.stderr,
            )
        return False

    restarted = " and restarted the daemon" if was_running else ""
    print(f"trailhead: upgraded outpost to {remote_sha[:8]}{restarted}")
    return True
