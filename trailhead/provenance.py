"""Install provenance stamp — the durable pointer from an install back to its
source checkout.

`${CLAUDE_PLUGIN_ROOT}` inside a plugin-declared hook resolves to the
COMPOSED destination, never the source checkout it was composed from
(trailhead/wire.py's compose-then-promote path always copies). This module is
the only durable pointer back the other way: `trailhead install` writes a
stamp as JSON under `state_dir("trailhead")`, and a later SessionStart hook
reads it through `read_stamp()` to find the checkout to probe for updates.

The stamp records ONLY what cannot be re-derived from the checkout itself:
the checkout path (a process running outside the checkout has no other way
to find it) and the HEAD sha that was actually wired (an install snapshots
the plugin trees rather than pointing at them, so the wired sha diverges
from the checkout's HEAD once the checkout is pulled without re-running
install). The tracked upstream branch and the `origin` URL are read live
from the checkout at check time — deriving them keeps every value that
reaches a git argv position sourced from git itself rather than from a
rewritable file.

`read_stamp()` is the ONLY sanctioned way to consume the stamp: it validates
the stamped checkout path against a confinement root (the user's home, or
`USERPROFILE` on Windows, by default) before returning anything, because a
rewritten stamp file is otherwise a lever to redirect a later consumer's exec
anywhere on disk, and it requires `sha` to be exactly 40 hex characters — the
one stamped value that reaches git as a bare positional, and a shape that can
never be parsed as an option. A path outside the root, or a malformed `sha`,
reads as no stamp at all; `read_stamp_with_reason()` additionally reports WHY
a present-but-rejected stamp was refused, distinguishing that case from a
genuinely absent one.

The stamp also carries the outcome of the last update check (`record_check_
outcome`) — ok / behind / unanswerable, plus a redacted reason and a
timestamp — so a persistently failing check is discoverable (via `trailhead
doctor`) instead of being silently indistinguishable from "up to date".

Writes are atomic (temp file in the same directory + `os.replace`): a crash
mid-write can only ever lose the write in flight, never truncate the stamp
already on disk.

Hermeticity: `env` is threaded through every function so tests never touch a
real state dir or a real git checkout; git itself is injected via `runner`
(same shape as `install.run_lore_init`'s runner — a callable(args, **kw) ->
CompletedProcess-like object).
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

STAMP_FILENAME = "provenance.json"


class GitProbeError(Exception):
    """Raised when a checkout's HEAD sha cannot be resolved (e.g. no commits,
    or not a git checkout at all)."""


def stamp_path(*, env: dict[str, str] | None = None) -> Path:
    """Return the provenance stamp path: state_dir("trailhead")/provenance.json."""
    _env = env if env is not None else dict(os.environ)
    return state_dir("trailhead", env=_env) / STAMP_FILENAME


# ---------------------------------------------------------------------------
# Credential redaction
# ---------------------------------------------------------------------------

# URL-form credentials: https://user:token@host/... or https://token@host/...
# (the bare-token form, no colon) or ssh://user@host/... -> scheme://***@host/...
_URL_CRED_RE = re.compile(r"((?:https?|ssh)://)[^/@\s]+@")
# SCP-shorthand user@host: form: git@github.com:org/repo.git -> ***@github.com:org/repo.git
_SCP_CRED_RE = re.compile(r"\b[\w.\-]+@(?=[\w.\-]+:)")


def redact_credentials(text: str) -> str:
    """Strip embedded credentials from *text* before it is ever persisted.

    Covers every form a git remote URL can carry a secret in: HTTPS/SSH
    basic-auth (``https://user:token@host/...``), the bare-token HTTPS form
    with no colon (``https://token@host/...``), and the SCP ``user@host:``
    shorthand, where the "user" segment is sometimes itself a token.
    """
    text = _URL_CRED_RE.sub(r"\1***@", text)
    text = _SCP_CRED_RE.sub("***@", text)
    return text


# ---------------------------------------------------------------------------
# Git probing (injectable runner)
# ---------------------------------------------------------------------------


def _default_runner():
    """Build the default injectable git runner.

    Shared with `trailhead.update`, which imports it rather than restating it,
    so both modules invoke git through exactly one code path.
    """

    def runner(args, **kw):
        return subprocess.run(args, **kw)

    return runner


def _git(checkout: Path, *args: str, runner, timeout: int = 10):
    return runner(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _probe_head_sha(checkout: Path, *, runner) -> str:
    """Return *checkout*'s HEAD sha.

    Raises GitProbeError if HEAD can't be resolved — deliberately fatal to
    the *stamp write*, not to the install: the caller (`write_stamp`) turns
    this into a warning rather than an install failure.
    """
    sha_proc = _git(checkout, "rev-parse", "HEAD", runner=runner)
    sha = (sha_proc.stdout or "").strip()
    if sha_proc.returncode != 0 or len(sha) != 40:
        raise GitProbeError(
            f"could not resolve HEAD for {checkout}: {(sha_proc.stderr or '').strip()}"
        )
    return sha


# ---------------------------------------------------------------------------
# Atomic JSON write
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: dict, *, prefix: str = ".provenance-") -> None:
    """Write *data* to *path* as JSON, atomically.

    Writes to a temp file named *prefix* in the SAME directory (so the final
    `os.replace` is a same-filesystem rename, never a cross-device copy), then
    replaces *path* in one step. A failure at any point before the replace
    leaves *path* completely untouched — the previous file, if any, survives
    intact. Shared with `trailhead.update`, which passes its own *prefix* for
    the freshness stamp it writes into the same state dir.
    """
    ensure_dir(path.parent, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=prefix, dir=str(path.parent))
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


def _now_iso() -> str:
    """The one UTC timestamp format every trailhead state file is written in."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_stamp(checkout: Path, *, env: dict[str, str] | None = None, runner=None) -> str | None:
    """Write the provenance stamp recording *checkout* as the install source.

    Overwrites any existing stamp (idempotent — a re-run always reflects the
    checkout's current HEAD, never appends).

    Returns a human-readable warning string, and writes NOTHING, if the
    checkout's HEAD can't be resolved — this is deliberately a warning the caller may surface, never
    a raised error: an install must succeed even when provenance can't be
    recorded. This covers a raised ``GitProbeError`` (git ran but its output
    didn't resolve) as well as git being entirely unavailable (``OSError``)
    or timing out (``TimeoutExpired``) — neither may escape and fail an
    install whose wiring already succeeded.
    """
    _env = env if env is not None else dict(os.environ)
    _runner = runner if runner is not None else _default_runner()

    try:
        sha = _probe_head_sha(checkout, runner=_runner)
    except (GitProbeError, OSError, subprocess.TimeoutExpired) as exc:
        return f"could not record install provenance for {checkout}: {exc}"

    stamp = {
        "checkout": str(checkout),
        "sha": sha,
        "wired_at": _now_iso(),
        "last_check": None,
    }
    _atomic_write_json(stamp_path(env=_env), stamp)
    return None


_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")


def read_stamp(
    *, env: dict[str, str] | None = None, confine_root: Path | str | None = None
) -> dict | None:
    """Return the provenance stamp, or None if absent, malformed, or its
    stamped checkout path resolves outside the confinement root.

    Never raises: any read/parse failure — missing file, garbage bytes,
    wrong shape, a malformed `sha` — reads as `None`. The
    checkout confinement is the one check every consumer relies on: nothing
    outside `confine_root` (the user's home directory by default) is ever
    returned, because a later consumer execs out of this path. Confinement
    fails CLOSED: if `confine_root` isn't given and neither `HOME` nor
    `USERPROFILE` is set either, there is no root to confine against, so the
    stamp reads as absent rather than unconfined.

    A caller that needs to tell "no stamp was ever written" apart from "a
    stamp exists but was rejected" (e.g. to report the difference in
    `trailhead doctor` or an `--check` failure reason) should call
    `read_stamp_with_reason` instead.
    """
    data, _reason = read_stamp_with_reason(env=env, confine_root=confine_root)
    return data


def read_stamp_with_reason(
    *, env: dict[str, str] | None = None, confine_root: Path | str | None = None
) -> tuple[dict | None, str | None]:
    """Return `(stamp, rejected_reason)`.

    `rejected_reason` is `None` whenever the stamp is entirely absent (no
    file on disk) OR fully valid (`stamp` is returned). It is a short,
    human-readable string ONLY when a stamp file exists on disk but was
    rejected — malformed JSON, missing/wrong-typed fields, a malformed
    `sha`, or a checkout path outside the confinement root — so a
    caller can tell "never installed" apart from "installed, but the stamp
    is unusable" (a distinction that matters because the latter is a sign
    something is actively wrong, not merely unset).
    """
    _env = env if env is not None else dict(os.environ)
    path = stamp_path(env=_env)

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None, None

    try:
        data = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        return None, "the provenance stamp is not valid JSON"

    if not isinstance(data, dict):
        return None, "the provenance stamp is not a JSON object"

    required = ("checkout", "sha", "wired_at")
    if not all(isinstance(data.get(k), str) and data.get(k) for k in required):
        return None, "the provenance stamp is missing required fields"

    # `sha` is the one stamped value that reaches git as a bare positional
    # (`_extract_changelog_delta`'s `git diff`). A real git sha is always
    # exactly 40 hex characters, which can never start with `-`; anything
    # else — including an option like `--output=<path>`,
    # which `git diff` would otherwise happily execute as a write target — is
    # rejected outright rather than merely checked for a leading dash, closing
    # the whole option-shape class for this field rather than one example of it.
    if not _SHA_RE.match(data["sha"]):
        return None, "the provenance stamp's sha field is not a 40-character hex sha"

    if confine_root is None:
        home = _env.get("HOME") or _env.get("USERPROFILE")
        if not home:
            return None, "no confinement root available (HOME/USERPROFILE unset)"
        confine_root = Path(home)

    try:
        Path(data["checkout"]).resolve().relative_to(Path(confine_root).resolve())
    except (ValueError, OSError):
        return None, "the provenance stamp's checkout path is outside the confinement root"

    return data, None


def record_check_outcome(
    outcome: str, *, reason: str | None = None, env: dict[str, str] | None = None
) -> None:
    """Record the outcome of the last update check onto the existing stamp.

    `outcome` is one of "ok" / "behind" / "unanswerable". `reason` (when a
    check failed or was inconclusive) is redacted of embedded credentials
    BEFORE it is written — it must never round-trip a live secret back out
    through `read_stamp` or `trailhead doctor`.

    A missing stamp is not an error here — the last-check field is set on
    whatever is on disk (an empty dict if nothing has been written yet), so
    callers never need to sequence this after `write_stamp`.
    """
    _env = env if env is not None else dict(os.environ)
    path = stamp_path(env=_env)

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            data = {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        data = {}

    data["last_check"] = {
        "outcome": outcome,
        "reason": redact_credentials(reason) if reason else None,
        "checked_at": _now_iso(),
    }
    _atomic_write_json(path, data)


def remove_stamp(*, env: dict[str, str] | None = None) -> None:
    """Remove the provenance stamp file, if present. Never raises on absence."""
    _env = env if env is not None else dict(os.environ)
    path = stamp_path(env=_env)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
