"""Claude Code harness implementation.

This module owns **everything** Claude-Code-specific about installing trailhead's
composed plugin trees: the Shape-A ``marketplace.json`` writer, the
``claude plugin …`` CLI calls, and the on-disk registration markers.  Nothing
Claude-Code-specific lives outside this file (Axiom 1) — the shared
install/compose/wire/doctor/uninstall path talks to it only through the generic
:class:`~trailhead.harness.base.Harness` interface.

Global source, per-config-dir state
-----------------------------------
The composed plugin tree is global: one ``composed_root``, shared by every
Claude config dir on the machine.  The install state is NOT.  ``claude plugin
marketplace add`` / ``claude plugin install`` write marketplace registration and
plugin content into the config dir ``CLAUDE_CONFIG_DIR`` selects, so a second
config dir starts with none of it even though it reads the same composed tree
and the same ``settings.json``.  Declaring a marketplace in settings does not
register it.

Registration markers (single source of truth)
----------------------------------------------
Markers therefore live in the **resolved config dir** (see :func:`_claude_dir`),
one set per config dir, and every method that reads or writes them takes an
``env`` mapping so the caller names which dir it means.  They are this harness's
on-disk truth, written only AFTER the corresponding CLI step succeeds:

- ``.trailhead-registered`` — the marketplace has been added *to this config
  dir*.  A skip-optimisation for :meth:`register` (the call itself is
  idempotent).
- per-tool ``.trailhead-installed-<tool>`` — the tool has been installed *into
  this config dir*.  A self-heal signal for the ``wire()`` loop: a missing
  marker means :meth:`install_tool` should run; a present marker means
  :meth:`rewire_tool`.

They are not kept under ``composed_root``: a marker there is global, so once
written for one config dir it gates every config dir — registration is skipped
for a dir that never had it, and ``doctor`` reports a config dir with no plugin
state at all as fully installed.

``_REGISTERED_MARKER`` / ``_INSTALLED_MARKER_PREFIX`` are defined here ONCE; the
rest of trailhead reads registration state via :meth:`is_registered`,
:meth:`is_installed`, and :meth:`installed_tools` rather than re-deriving the
marker filenames.

Input guard
-----------
Every ``tool`` value is validated against ``^[a-z][a-z0-9_-]*$`` before it
reaches any CLI arg, marker filename, or ``source`` path.  Every ruleset ``name``
is likewise validated (``^[A-Za-z0-9][A-Za-z0-9._-]*$``, then re-confined to the
rules dir) before any directory is created or any byte is written — a ruleset
lands under the user's Claude config dir, outside every trailhead-owned tree, so
the name may never address anything but a file directly inside ``rules/``.

Hermeticity contract
--------------------
Each CLI invocation is injectable via a ``runner=`` keyword argument.  Tests
ALWAYS pass a stub runner and NEVER invoke the real ``claude plugin`` CLI against
the user's harness.  The default runner is ``subprocess.run`` with ``check=True``;
it is only exercised in live-session dogfood runs.  Every invocation carries an
explicit ``CLAUDE_CONFIG_DIR`` so it lands in the config dir the caller named
rather than whichever one the ambient environment selects.  This harness NEVER
writes to ``<config-dir>/plugins/`` directly — the CLI manages
``known_marketplaces.json`` and the plugin cache; this harness generates
``marketplace.json`` under the ``state_dir``-rooted ``composed_root`` and writes
its own registration markers at the top of the config dir.

Shape-A marketplace.json
-------------------------
The generated marketplace.json follows Shape A (validated live via
``claude plugin validate``): one marketplace named ``trailhead`` with one
``plugins[]`` entry per tool, each sourced at ``./plugins/<tool>``.  All tools
share one marketplace root (``composed_root``); plugin trees live at
``composed_root/plugins/<tool>/``.

Detection: Claude Code is considered present when ``~/.claude`` exists.  Tests
may redirect this with the ``TRAILHEAD_CLAUDE_DIR`` env override.

Conflicting overrides
---------------------
``TRAILHEAD_CLAUDE_DIR`` is trailhead's own seam; ``CLAUDE_CONFIG_DIR`` is the
variable Claude Code honors.  Set to the same directory they agree.  Set to
different directories they cannot both be honored — the plugin state would land
in one dir while the session reads the other — so every path that reaches a live
subprocess or reports installed state refuses instead of guessing
(:func:`_refuse_conflicting_config_dirs`, enforced at :meth:`_marker_dir`).
"""

from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from trailhead.harness.base import (
    AccountAuthentication,
    AccountIdentity,
    EvalRunner,
    Harness,
    HarnessError,
    SessionFile,
    SessionRecord,
    SessionStart,
    SessionTranscript,
)
from trailhead.launch_profile import BROKER_SERVER

if TYPE_CHECKING:
    from trailhead.launch_profile import BrokerLaunch, ResolvedProfile

_REGISTERED_MARKER = ".trailhead-registered"
#: Ledger dir inside the composed tree holding one file per Claude config dir
#: currently registered against it — the reference count that keeps a teardown
#: run under one config dir from deleting the tree another one still sources
#: its plugins from.
_REGISTRATIONS_SUBDIR = ".trailhead-registrations"
_INSTALLED_MARKER_PREFIX = ".trailhead-installed-"

#: Subdir under the Claude dir holding user-level rulesets (``~/.claude/rules/``).
_RULES_SUBDIR = "rules"

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
#: A ruleset name is a bare file stem written directly into the user's
#: Claude config dir: no separators, no leading dot, no traversal.
_RULESET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Subdir under the Claude dir holding one directory of session transcripts per
#: project (``~/.claude/projects/<munged-cwd>/<session-id>.jsonl``).
_PROJECTS_SUBDIR = "projects"

#: A session id must be a single, inert path COMPONENT before it is joined onto
#: the transcripts root.  Anything else (``..``, a separator, an empty string)
#: would escape the projects dir, so it resolves to "unknown session" instead.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _is_session_id(session_id: object) -> bool:
    """Whether ``session_id`` is a plain token safe to use as a path or an argv.

    One predicate for both uses on purpose: an id is either inert enough to be
    joined onto the transcripts root AND handed to a caller's argv, or it is not
    a session id this harness recognizes at all.
    """
    return isinstance(session_id, str) and _SESSION_ID_RE.match(session_id) is not None


def _projects_key(resolved: Path) -> str:
    """Munge a resolved absolute path into its Claude Code projects-dir key.

    Both ``/`` and ``.`` collapse to ``-``, so this is lossy: distinct paths can
    munge to the same key (see the callers' handling of that collision). This
    is the one place the encoding is derived; every seam member composing a
    projects-dir path calls through here rather than re-deriving it.
    """
    return str(resolved).replace("/", "-").replace(".", "-")


def _directories_munging_to(key: str) -> list[Path]:
    """Every existing, resolved directory whose :func:`_projects_key` is ``key``.

    Walks down from ``/`` one directory at a time, following an entry only when
    its own key is a leading run of ``key``, so the lossy munge is settled by
    what is actually on disk.  A directory that cannot be listed is skipped, and
    so is a single entry whose type cannot be determined or that cannot be
    resolved.

    What lies below a directory depends only on that directory's identity and
    the key consumed to reach it, so each such state is explored once per call.
    Symlinked directories are followed, and the visited set is what keeps a
    symlink alias from multiplying the paths explored.
    """
    if not key.startswith("-"):
        return []
    found: list[Path] = []
    visited: set[tuple[int, int, str]] = set()

    def walk(directory: Path) -> None:
        try:
            st = os.stat(directory)
        except OSError:
            return
        state = (st.st_dev, st.st_ino, _projects_key(directory))
        if state in visited:
            return
        visited.add(state)
        children: list[Path] = []
        try:
            with os.scandir(directory) as entries:
                for e in entries:
                    try:
                        if e.is_dir():
                            children.append(Path(e.path))
                    except OSError:
                        continue
        except OSError:
            return
        for path in children:
            path_key = _projects_key(path)
            if path_key == key:
                try:
                    found.append(path.resolve())
                except (OSError, RuntimeError):
                    continue
            elif key.startswith(path_key + "-"):
                walk(path)

    walk(Path("/"))
    return found


#: Bounded head-scan limits for extracting a session's start cwd out of a
#: transcript (see :func:`_scan_transcript_head`). Real transcripts run to
#: hundreds of megabytes, so this seam must never read one in full:
#: ``_CWD_SCAN_MAX_LINES`` bounds how many leading records are inspected, and
#: ``_CWD_SCAN_MAX_LINE_BYTES`` skips any single record too large to be a
#: plausible cwd-bearing header line rather than paying to decode it.
#:
#: The first cwd-bearing record is a session's first message, and a resumed
#: session's transcript opens with metadata records ahead of it (titles, mode
#: changes, file-history snapshots, queued prompts) — measured on real stores
#: at up to 21 leading records. The line bound sits an order of magnitude
#: above that; the scan still stops at the first cwd it finds.
_CWD_SCAN_MAX_LINES = 200
_CWD_SCAN_MAX_LINE_BYTES = 1_000_000

#: A team verdict is only trusted when the record carrying it ends within this
#: many bytes of the file's start (see :func:`_scan_transcript_head`).
_TEAM_SCAN_MAX_BYTES = 64 * 1024


def _skip_rest_of_overlong_line(f: BinaryIO) -> None:
    """Step past the tail of a line that exceeded the byte cap.

    Reads in cap-sized chunks and holds none of them, so stepping over a
    malformed record costs bounded memory no matter how long the record is.
    The scan continues from the next line rather than abandoning the file: a
    huge early record must not hide a plain cwd-bearing one behind it.
    """
    while True:
        chunk = f.readline(_CWD_SCAN_MAX_LINE_BYTES)
        if not chunk or chunk.endswith(b"\n"):
            return


def _scan_transcript_head(path: Path) -> tuple[Path | None, bool]:
    """Bounded head-scan for the cwd and team membership of a transcript.

    Reads at most ``_CWD_SCAN_MAX_LINES`` lines, JSON-decoding each in turn.
    The first decoded dict carrying a non-empty string ``cwd`` ends the scan;
    the result is that cwd and whether the same record carries a non-empty
    string ``teamName`` (a team member's transcript, which Claude Code's own
    resume listing drops). A line longer than ``_CWD_SCAN_MAX_LINE_BYTES`` is
    skipped without being decoded, so a huge early line can never win over a
    plain one further down within the window.

    The team verdict is deliberately stricter than the cwd answer: the record
    is reported as a team member only when its ``teamName`` sits on the first
    cwd-bearing record, no line was skipped ahead of it (over the byte cap, or
    not decodable as JSON), and that record ends within the first
    ``_TEAM_SCAN_MAX_BYTES`` (64 KiB) of the file. Any other head reads as not
    a team member. An undecided or ambiguous head must read as resumable,
    because a non-resumable answer lets a parked session stop blocking an
    irreversible workspace removal. Claude Code's own ``/resume`` matches
    ``teamName`` anywhere in the first 64 KiB, so this rule is strictly
    narrower.

    The cap bounds the READ, not just the decode: no single call takes more
    than ``_CWD_SCAN_MAX_LINE_BYTES + 1`` bytes, so a corrupt transcript
    carrying no newline at all costs bounded memory instead of its full size.

    Returns ``(None, False)`` — never raises — when the file cannot be opened,
    no line within the window decodes to a JSON object, or no decoded object
    carries a non-empty string ``cwd``. This is the seam's only source of an
    "unreadable/undecodable transcript" outcome; the caller turns that into a
    row with ``cwd=None`` rather than dropping the row.
    """
    try:
        with path.open("rb") as f:
            skipped_a_line = False
            for _ in range(_CWD_SCAN_MAX_LINES):
                # Read WITH the cap, not merely check it afterwards: an
                # unbounded readline() materializes the whole record first, so
                # one malformed line with no newline would pull an entire
                # multi-hundred-megabyte transcript into memory.
                line = f.readline(_CWD_SCAN_MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > _CWD_SCAN_MAX_LINE_BYTES:
                    if not line.endswith(b"\n"):
                        _skip_rest_of_overlong_line(f)
                    skipped_a_line = True
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    skipped_a_line = True
                    continue
                if isinstance(record, dict):
                    cwd = record.get("cwd")
                    if isinstance(cwd, str) and cwd:
                        team_name = record.get("teamName")
                        is_team_member = (
                            isinstance(team_name, str)
                            and bool(team_name)
                            and not skipped_a_line
                            and f.tell() <= _TEAM_SCAN_MAX_BYTES
                        )
                        return Path(cwd), is_team_member
    except OSError:
        return None, False
    return None, False


def _stream_rest_of_overlong_line(f_in: BinaryIO, f_out: BinaryIO) -> None:
    """Copy the tail of a line that exceeded the byte cap, chunk by chunk.

    The write-side twin of :func:`_skip_rest_of_overlong_line`: this seam
    passes an oversized line through verbatim rather than dropping it, but
    must still never hold more than one cap-sized chunk of it in memory at a
    time.
    """
    while True:
        chunk = f_in.readline(_CWD_SCAN_MAX_LINE_BYTES)
        if not chunk:
            return
        f_out.write(chunk)
        if chunk.endswith(b"\n"):
            return


#: Matches a ``"cwd"`` object key and its JSON string value in a transcript
#: line's raw bytes, at any nesting depth — a record can carry nested objects
#: with their own ``cwd`` key (per-tool ingest context on an assistant
#: record), serialized ahead of the record's top-level one. Group 1 is the
#: key, colon, and any surrounding whitespace, preserved verbatim; group 2 is
#: the value's raw (still-escaped) content between the quotes.
#:
#: An occurrence of ``cwd`` inside escaped JSON-string *content* — free prose
#: quoting another record, e.g. ``\"cwd\":\"...\"`` — does not match: the
#: backslash JSON uses to escape that inner quote sits between the ``d`` and
#: the quote this pattern requires immediately after it, so the literal
#: ``"cwd":"`` sequence this regex looks for is never present there.
_CWD_FIELD_RE = re.compile(rb'("cwd"\s*:\s*)"((?:\\.|[^"\\])*)"')


def _location_under_root(
    value: Path, old_root: Path, resolved: dict[Path, Path]
) -> Path | None:
    """*value*'s location relative to *old_root*, or None when it lies outside.

    *old_root* is a recorded root as :meth:`ClaudeCodeHarness.session_transcripts`
    reports it — resolved — while a transcript line records the path exactly as
    the sending host wrote it. The two can name the same directory in different
    strings (on macOS, `/home/...` resolves to `/System/Volumes/Data/home/...`),
    so *value* counts as under *old_root* either as written or once resolved the
    same way. Resolution only ever decides *whether* a value moves; the location
    returned is relative to *old_root*, so a relocated value always lands under
    the new root. *resolved* caches each distinct value's resolution, since a
    transcript repeats the same few paths on thousands of lines.
    """
    if value.is_relative_to(old_root):
        return value.relative_to(old_root)
    real = resolved.get(value)
    if real is None:
        real = resolved[value] = value.resolve()
    if real.is_relative_to(old_root):
        return real.relative_to(old_root)
    return None


def _rewrite_transcript_line(
    chunk: bytes,
    old_root: Path,
    new_root: Path,
    source: Path,
    *,
    keep_foreign_roots: bool,
    resolved: dict[Path, Path],
) -> bytes:
    """Rewrite one already cap-sized transcript line's ``cwd`` in place, or
    pass it through verbatim.

    Verbatim covers: invalid JSON, JSON that is not an object, and an object
    with no string/absolute ``cwd`` — none of those structurally record a
    root, so none of them are this transform's business.

    The rewrite is a targeted byte substitution, not a re-serialization: only
    the bytes of ``cwd`` values' own JSON string literals are replaced (via
    :data:`_CWD_FIELD_RE`), so every other byte on the line — separator
    spacing, key order, other fields' escaping and number formatting — is
    untouched. This is required, not cosmetic:
    ``Harness.rewrite_transcript_workspace``'s contract is byte-identical
    output outside the rewritten root, and re-serializing the whole record
    (e.g. via ``json.dumps``) would reformat every field on the line to
    Python's own JSON spacing, silently breaking that contract on a real,
    compactly-written transcript.

    Every ``cwd`` key on the line whose value lies under ``old_root`` (as
    written or once resolved; see :func:`_location_under_root`) moves —
    the top-level root and any nested one, since both record the same
    sending host's layout; a nested value elsewhere is left as it is. The
    substituted line is decoded again to prove its top-level ``cwd`` is the
    relocated root, so a nested key can never be rewritten in its place.

    Raises :class:`HarnessError`, naming ``source``, when the top-level
    ``cwd`` IS an absolute path but is not under ``old_root`` — a foreign,
    sending-host root this transform must refuse rather than relocate — and
    also when the substitution does not leave the top-level ``cwd`` relocated,
    which would otherwise force a fall back to re-serialization.
    """
    ends_in_newline = chunk.endswith(b"\n")
    body = chunk[:-1] if ends_in_newline else chunk
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return chunk
    try:
        record = json.loads(decoded)
    except ValueError:
        return chunk
    if not isinstance(record, dict):
        return chunk
    cwd = record.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return chunk
    cwd_path = Path(cwd)
    if not cwd_path.is_absolute():
        return chunk
    cwd_location = _location_under_root(cwd_path, old_root, resolved)
    if cwd_location is None:
        if keep_foreign_roots:
            return chunk
        raise HarnessError(
            f"rewrite_transcript_workspace: {source.name} records a root "
            f"({cwd_path}) outside {old_root}; refusing to rewrite the "
            "transcript rather than pass a foreign root through unrewritten"
        )

    def relocate(match: re.Match[bytes]) -> bytes:
        try:
            value = json.loads(b'"' + match.group(2) + b'"')
        except ValueError:
            return match.group(0)
        value_path = Path(value)
        if not value_path.is_absolute():
            return match.group(0)
        location = _location_under_root(value_path, old_root, resolved)
        if location is None:
            return match.group(0)
        new_value = str(new_root / location)
        return match.group(1) + json.dumps(new_value).encode("utf-8")

    new_body = _CWD_FIELD_RE.sub(relocate, body)
    expected = str(new_root / cwd_location)
    try:
        relocated = json.loads(new_body)
    except ValueError:
        relocated = None
    if not isinstance(relocated, dict) or relocated.get("cwd") != expected:
        raise HarnessError(
            f"rewrite_transcript_workspace: {source.name} records a cwd this "
            "transform cannot byte-preservingly locate; refusing rather than "
            "falling back to re-serializing the line"
        )
    return new_body + b"\n" if ends_in_newline else new_body


def _iter_transcript_paths(projects_dir: Path) -> Iterator[Path]:
    """Yield ``<projects>/*/*.jsonl``, stopping quietly at an unreadable tree.

    The walk itself can fail — a project directory whose permissions deny a
    listing raises mid-iteration, not at the call — and the transcript seam's
    contract is that it never raises for an unreadable store. Stopping yields
    the rows found so far, which is strictly more than the empty listing an
    all-or-nothing guard would produce and never less than one.
    """
    try:
        yield from projects_dir.glob("*/*.jsonl")
    except OSError:
        return


#: Cap on the raw-output excerpt embedded in a ``parse_session_list``
#: ``HarnessError`` message. Bounded across the WHOLE payload (not per field):
#: a ``cwd`` carries an absolute path — usually including the username — into
#: terminal scrollback and logs, so a single whole-payload bound is what
#: actually keeps that out, not a per-field one.
_ERROR_EXCERPT_LIMIT = 200


def _excerpt(output: str) -> str:
    """A home-redacted, length-bounded, single-line excerpt of raw output.

    The length bound alone is NOT a confidentiality control: an ordinary
    ``cwd`` is a few dozen characters, so it — and the username inside it —
    fits inside the limit intact. The bound stops a pathological payload from
    flooding a log; REDACTION is what keeps the operator's home path out of
    one, and the realistic leak (a benign ``kind`` schema drift raising an
    error a user then pastes into a bug report) needs the latter. Redact
    first, then bound, so truncation can never strip the prefix that makes a
    path recognizable as home and leave the tail behind.
    """
    flat = output.replace("\n", "\\n")
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):  # no resolvable home; nothing to redact
        home = ""
    if home:
        flat = flat.replace(home, "~")
    if len(flat) > _ERROR_EXCERPT_LIMIT:
        return flat[:_ERROR_EXCERPT_LIMIT] + "…"
    return flat


#: Env markers Claude Code sets for its own child sessions, plus the account
#: variable.  Verified twice (v2.1.229 / v2.1.232) that a session launched with
#: ``CLAUDE_CODE_CHILD_SESSION`` inherited runs and connects remote-control but
#: NEVER appears in ``claude agents --json`` — and the leaked env also carries
#: the parent's live session access token, so scrubbing those names is a
#: credential-hygiene requirement, not merely an enumeration fix.
#:
#: ``CLAUDE_CONFIG_DIR`` is here for a different reason: it is how
#: :meth:`ClaudeCodeHarness.session_launch_env_set` states the DEFAULT account,
#: which is the variable being absent (see that method).  The scrub is
#: unconditional and a declared account is re-asserted after it — ``env -u KEY
#: KEY=value`` yields the value, since ``env`` reads its operands left to right.
#:
#: This is a FLOOR, not an exhaustive guarantee (see the base contract).
#: Applying the scrub is the exec-owning caller's job, done at spawn time — this
#: seam only names them.
_LAUNCH_ENV_UNSET = [
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ACCESS_TOKEN",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CONFIG_DIR",
]

#: The top-level settings key that sets how many days a session transcript is
#: kept before cleanup — named here so a caller can point a user at the
#: right setting without this module handing out a file path to edit.
_CONFIG_FILENAME = ".claude.json"
_CLEANUP_PERIOD_KEY = "cleanupPeriodDays"

#: The OAuth credentials file inside an account's config dir — the
#: authentication signal :meth:`ClaudeCodeHarness.session_launch_account_authentication`
#: reads. Lives inside the config dir for BOTH a declared account and the
#: default (unlike ``.claude.json``, which for the default splits off to
#: ``$HOME`` — see ``claude_config_file``'s docstring), so one resolution
#: serves both branches.
_CREDENTIALS_FILENAME = ".credentials.json"

_TOOL_DESCRIPTIONS: dict[str, str] = {
    "lore": (
        "Portable knowledge-management plugin: session lifecycle, capture skills, and vault recall."
    ),
    "camp": (
        "Portable worktree-orchestration plugin: group config, "
        "dev-env orchestration, and worktree management."
    ),
    "craft": (
        "Portable software-development plugin: general-purpose dev agents and dev-ritual skills."
    ),
    "portage": ("Get the PR merged: PR lifecycle, CI watch, and merge ordering for a camp group."),
}


#: Variables a ``claude plugin eval`` child may inherit: enough to find the binary and
#: authenticate on the operator's login, nothing that can move the run onto another
#: credential or config (API keys, tokens, Bedrock/base-URL switches, lore/XDG overrides).
_EVAL_ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LANG", "TERM")
_EVAL_ENV_PREFIXES = ("LC_",)
_EVAL_MAX_PARALLEL = 8


def _tool_description(tool: str) -> str:
    return _TOOL_DESCRIPTIONS.get(tool, f"Trailhead-composed plugin for {tool}.")


def _validate_tool(tool: str) -> None:
    """Raise ValueError if tool does not match ^[a-z][a-z0-9_-]*$."""
    if not isinstance(tool, str) or not _TOOL_NAME_RE.match(tool):
        raise ValueError(f"Invalid tool name {tool!r}: must match ^[a-z][a-z0-9_-]*$")


def _validate_ruleset_name(name: str) -> None:
    """Raise HarnessError unless *name* is a bare, separator-free file stem.

    A ruleset name becomes a filename directly under the user's Claude config
    dir, so anything that could steer the write elsewhere — a separator, a
    leading dot, ``..``, an empty stem — is refused outright rather than relied
    on to resolve harmlessly.
    """
    if not isinstance(name, str) or not _RULESET_NAME_RE.match(name):
        raise HarnessError(
            f"invalid ruleset name {name!r}: must match {_RULESET_NAME_RE.pattern}"
        )


def _default_runner(args, **kw):
    return subprocess.run(args, check=True, **kw)


def _claude_dir(env: dict[str, str]) -> Path:
    """Resolve Claude Code's config dir (``~/.claude``) from *env*.

    Honors the ``TRAILHEAD_CLAUDE_DIR`` override (tests redirect it here), then
    ``CLAUDE_CONFIG_DIR`` (Claude Code's OWN relocation env var — when a user
    sets it, the config dir really has moved, so every path derived here must
    follow), then ``HOME``/``USERPROFILE``, falling back to the real home.
    Single source of truth for ``detect``, the user-ruleset methods, and the
    session-transcript lookup.
    """
    override = env.get("TRAILHEAD_CLAUDE_DIR") or env.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override)
    home = env.get("HOME") or env.get("USERPROFILE")
    base = Path(home) if home else Path.home()
    return base / ".claude"


def _default_account_credentials_dir(env: Mapping[str, str]) -> Path:
    """Resolve the DEFAULT account's config dir for reading credentials.

    Same precedence as :func:`claude_config_file` — ``CLAUDE_CONFIG_DIR`` then
    ``HOME``/``USERPROFILE``, falling back to the real home — and, like that
    function, DELIBERATELY not ``TRAILHEAD_CLAUDE_DIR``: that variable is a
    trailhead-only test seam Claude Code has never heard of, and the real
    launched process never reads it for the default account (see
    :func:`claude_config_file` and ``session_launch_account_identity``'s
    anti-divergence pin). Reading credentials through ``_claude_dir`` here
    would report on a directory the actual session never touches.

    Unlike ``claude_config_file``, the fallback appends ``.claude`` rather
    than resolving to a bare ``HOME``-level file: the credentials file lives
    INSIDE the config dir for the default account too (only ``.claude.json``
    splits off to sit beside it).
    """
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(config_dir)
    home = env.get("HOME") or env.get("USERPROFILE")
    base = Path(home) if home else Path.home()
    return base / ".claude"


def _credentials_file_is_authoritative(platform: str) -> bool:
    """Whether the credentials file is Claude Code's actual credential store
    on *platform*.

    True on Linux and Windows: the file is where Claude Code keeps its OAuth
    credentials, so its absence or content is a plain, knowable fact about
    the account. False on macOS (``darwin``): Claude Code stores its OAuth
    credentials in the login Keychain there instead (see
    ``docs/eval-protocol.md`` and camp's launch eligibility profile, both
    already treating the Keychain as this harness's real store on darwin),
    so the file is not authoritative and a negative or unreadable reading of
    it proves nothing about whether the account is logged in. Reading the
    Keychain itself is out of scope for this seam: it would require a
    subprocess and can prompt for interaction, both forbidden here.
    """
    return platform != "darwin"


def _read_account_authentication(
    config_dir: Path, *, platform: str | None = None
) -> AccountAuthentication:
    """Reads the credentials-file signal: one file read plus one JSON parse, sub-millisecond.

    Non-empty ``claudeAiOauth.accessToken`` always means authenticated — a
    positive finding is conclusive on every platform, since Claude Code
    would not have written it there without a live credential.

    Everywhere else, the answer depends on whether this file is the
    platform's actual credential store (see
    :func:`_credentials_file_is_authoritative`). Where it is, a missing
    credentials file, a missing ``claudeAiOauth`` key, a missing
    ``accessToken`` key, or an empty token mean NOT_AUTHENTICATED — the
    account has no live credential, which is a plain, knowable fact. Where
    it is not (macOS), none of those same readings are evidence of
    anything — the credential may simply live in the Keychain, which this
    function never reads — so they all mean CANNOT_TELL instead.

    An unreadable file (permissions, a locked network mount), a file that
    fails to parse as JSON, a ``claudeAiOauth`` value that is not an object,
    or an ``accessToken`` value that is not a string all mean CANNOT_TELL
    unconditionally, on every platform: the signal exists but could not be
    read or does not have the shape this function knows how to interpret,
    which is a genuinely different fact from "no credential" and must never
    collapse into it.

    Never spawns a subprocess, never touches the network, and never blocks on
    anything beyond a local filesystem read — a stat plus a bounded read of
    an already-at-rest file.
    """
    plat = platform if platform is not None else sys.platform
    authoritative = _credentials_file_is_authoritative(plat)

    def _absent_or_empty() -> AccountAuthentication:
        return (
            AccountAuthentication.NOT_AUTHENTICATED
            if authoritative
            else AccountAuthentication.CANNOT_TELL
        )

    path = config_dir / _CREDENTIALS_FILENAME
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return _absent_or_empty()
    except OSError:
        return AccountAuthentication.CANNOT_TELL
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return AccountAuthentication.CANNOT_TELL
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if oauth is None:
        return _absent_or_empty()
    if not isinstance(oauth, dict):
        return AccountAuthentication.CANNOT_TELL
    token = oauth.get("accessToken")
    if token is None:
        return _absent_or_empty()
    if not isinstance(token, str):
        return AccountAuthentication.CANNOT_TELL
    if token:
        return AccountAuthentication.AUTHENTICATED
    return _absent_or_empty()


def _refuse_conflicting_config_dirs(env: Mapping[str, str]) -> None:
    """Raise when the test seam and Claude Code's own relocation disagree.

    ``TRAILHEAD_CLAUDE_DIR`` is a trailhead-only seam; ``CLAUDE_CONFIG_DIR`` is the
    variable Claude Code itself honors.  When both name the same directory that is
    one consistent statement of intent and nothing is refused.  When they name
    DIFFERENT directories there is no reading that serves the user: the plugin
    state and the registration markers would land in the seam's dir while the
    launched session reads the other one, producing a session that starts, is
    trusted, and carries none of trailhead's plugins.  Refusing loudly is the only
    honest outcome.
    """
    seam = (env.get("TRAILHEAD_CLAUDE_DIR") or "").strip()
    relocation = (env.get("CLAUDE_CONFIG_DIR") or "").strip()
    if not seam or not relocation:
        return
    if Path(seam) == Path(relocation):
        return
    if os.path.realpath(seam) == os.path.realpath(relocation):
        return
    raise HarnessError(
        "TRAILHEAD_CLAUDE_DIR and CLAUDE_CONFIG_DIR name different Claude config "
        f"directories: TRAILHEAD_CLAUDE_DIR={seam} CLAUDE_CONFIG_DIR={relocation}. "
        "Plugin state and trailhead's registration markers must live in the same "
        "directory the launched session reads, so trailhead will not guess which "
        "one you meant. Unset TRAILHEAD_CLAUDE_DIR (it is a test-only override) or "
        "set both to the same directory."
    )


#: Characters an account value may never carry: the C0 controls (NUL among
#: them), DEL, and the C1 controls.
_ACCOUNT_FORBIDDEN_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _account_home(env: Mapping[str, str]) -> Path:
    """The home a declared account's leading ``~`` expands against.

    Reads only ``HOME``/``USERPROFILE``, falling back to the real home — the same
    home resolution ``claude_config_file`` uses, so the two agree by construction.

    Two variables are deliberately NOT consulted. ``CLAUDE_CONFIG_DIR`` is an
    account statement, not a home, and reading it would let the ambient account
    steer where a declared one resolves. ``TRAILHEAD_CLAUDE_DIR`` is a
    trailhead-only seam Claude Code has never heard of, so a session launched
    into a path derived from it would read a directory nothing wrote.
    """
    home = env.get("HOME") or env.get("USERPROFILE")
    return Path(home) if home else Path.home()


def _account_dir(account: str, env: Mapping[str, str]) -> Path:
    """Resolve a declared *account* to an absolute config dir, or raise.

    A leading ``~`` expands against *env*'s home rather than the machine's, so a
    caller's declaration never resolves through the running user's password-db
    entry. Every other form must already be absolute: a relative value resolves
    against whichever working directory the launching process happens to have,
    and the directory it names is one Claude Code would then create and read as
    an empty, unauthenticated account.

    A control character is refused before either check. The value becomes a path
    the caller resolves and an operand of a process spawn, and both answer a NUL
    by RAISING — so without this the seam's promised fail-closed refusal is a raw
    :class:`ValueError` from somewhere with no idea what an account is. Only that
    range is refused: an account directory may be spelled in any script.
    """
    found = _ACCOUNT_FORBIDDEN_CHARS.search(account)
    if found:
        raise HarnessError(
            f"session_launch_env_set: account {account!r} contains the control "
            f"character {found.group()!r}. An account names a directory a session "
            "reads and a value a process is spawned with, and neither can carry one."
        )
    expanded = account
    if account == "~" or account.startswith("~/"):
        expanded = str(_account_home(env)) + account[1:]
    path = Path(expanded)
    if not path.is_absolute():
        raise HarnessError(
            f"session_launch_env_set: account {account!r} is not an absolute path. "
            "An account names a directory the launched session reads, so it must "
            "resolve identically from any working directory."
        )
    return path


def claude_config_file(env: Mapping[str, str] | None) -> Path:
    """Resolve Claude Code's global config *file* (``~/.claude.json``) from *env*.

    Precedence is ``CLAUDE_CONFIG_DIR`` then ``HOME``/``USERPROFILE``, falling back
    to the real home — and deliberately **not** ``TRAILHEAD_CLAUDE_DIR``. That seam
    is a trailhead-only test redirect Claude Code has never heard of: it relocates
    the config *directory*, and a file resolved through it would be a path Claude
    Code never reads, so a trust write there would be dead while the suite that set
    the seam passed green over it.

    Exported (see ``trailhead.harness.__all__``) because camp's launch-time trust
    pre-seed writes this same file and must agree on the path — one implementation
    rather than two copies kept in step by a parity test.
    """
    env = env or {}
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(config_dir) / _CONFIG_FILENAME
    home = env.get("HOME") or env.get("USERPROFILE")
    base = Path(home) if home else Path.home()
    return base / _CONFIG_FILENAME


# -- confined session start ---------------------------------------------------
#
# ``session_start`` renders a resolved launch profile into session-only settings
# passed inline by ``--settings`` (inline, so no file exists for the session to edit).
# Session settings MERGE with the user-level ``settings.json`` rather than replace it:
# lists union, and a denial wins over an allowance. This section is built around what
# that forces:
#
# * A profile can only NARROW. Writes are narrowed by DERIVING ``denyWrite`` from every
#   user-level writable root that is not one of the profile's roots. A user-level root
#   that CONTAINS a profile root cannot be denied without denying the profile root too
#   (the denial wins), so that launch is refused. Reads and network destinations
#   cannot be narrowed this way, so a profile that asks for one the user-level
#   configuration forbids is refused too.
# * A user-level ``sandbox.excludedCommands`` entry runs OUTSIDE the sandbox and cannot be
#   removed by a session (lists union), so each one is also denied as a ``Bash()``
#   permission rule: a denial beats any allowance at every scope, so the command is
#   refused rather than run unconfined.
# * The directory the session starts in is writable and trusted, so a session could
#   rewrite the project settings, hooks, agents and MCP config a later launch honours.
#   ``<directory>/.claude`` and ``<directory>/.mcp.json`` are therefore always denied.
# * Anything unreadable about the user-level configuration is a refusal, never a guess.
#
# Path conventions differ by key: ``sandbox.filesystem.*`` takes absolute or ``~/``
# paths, ``permissions.deny`` ``Read()`` rules take ``//abs/path``. The user-level file
# is ``$CLAUDE_CONFIG_DIR/settings.json`` for a bound account, else ``~/.claude/settings.json``.

_SETTINGS_FILENAME = "settings.json"
_READ_RULE_RE = re.compile(r"^Read\((?P<spec>[^)]*)\)$")

#: Where Claude Code reads managed settings files (``managed-settings.json`` then every
#: ``managed-settings.d/*.json`` in alphabetical order), by platform
#: (https://code.claude.com/docs/en/managed-settings.md). Managed settings outrank the
#: session's own, so a denial there that covers a path the profile needs defeats the profile.
_MANAGED_SETTINGS_DIRS = {
    "linux": Path("/etc/claude-code"),
    "darwin": Path("/Library/Application Support/ClaudeCode"),
}
_MANAGED_FILENAME = "managed-settings.json"
_MANAGED_DROP_IN_DIR = "managed-settings.d"

#: Characters a ``Read()`` path glob may carry that this check cannot evaluate; a rule using
#: one is refused rather than guessed at (``*``, ``**`` and ``?`` are understood).
_UNSUPPORTED_GLOB_RE = re.compile(r"[\[\]{}\\]|^!")
_GLOB_RE = re.compile(r"[*?]")


def _resolve_settings_path(entry: str, home: Path, launch_dir: Path) -> Path:
    """A path as a settings entry spells it, made absolute and symlink-free.

    ``~`` expands against *home*; ``.`` and any other relative entry are the launch
    directory. Comparison is always on these resolved paths, by segment.
    """
    if entry == "~" or entry.startswith("~/"):
        path = home / entry[2:]
    else:
        path = Path(entry)
        if not path.is_absolute():
            path = launch_dir / path
    return Path(os.path.realpath(path))


def _covers(parent: Path, child: Path) -> bool:
    """True when *child* is *parent* or lies beneath it (whole path segments)."""
    return child == parent or parent in child.parents


def _host_allowed(host: str, allowed: list[str]) -> bool:
    for entry in allowed:
        if entry == host:
            return True
        if entry.startswith("*.") and host.endswith(entry[1:]):
            return True
    return False


_Source = tuple[Path, str]  # (settings file, "user-level" | "managed")

#: Boolean sandbox settings a launch must pin, with the value that widens the session. A
#: managed value outranks the session's ``--settings`` and cannot be overridden, so it refuses
#: the launch; a user-level one is overridden by rendering the safe value.
#: (https://code.claude.com/docs/en/settings.md#settings-precedence)
_SANDBOX_FLAGS = {
    "sandbox.enabled": False,
    "sandbox.allowUnsandboxedCommands": True,
    "sandbox.filesystem.disabled": True,
    "sandbox.network.allowLocalBinding": True,
    "sandbox.network.allowAllUnixSockets": True,
}
#: The flags above that the hub's own settings can pin off when only the user file sets them
#: (``enabled`` and ``allowUnsandboxedCommands`` are always rendered safe).
_PINNED_OFF = tuple(
    label for label in _SANDBOX_FLAGS if label not in ("sandbox.enabled", "sandbox.allowUnsandboxedCommands")
)
#: Tools that write files under ``Edit(path)`` permission rules, which are the only thing
#: the file tools obey: the Bash sandbox does not apply to them.
_FILE_WRITE_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")


class _ReadRule:
    """A ``Read()`` deny path, as a pattern over path segments that can say whether it covers a root.

    Segments are ``("lit", name)`` (compared exactly) or ``("glob", pattern)`` (``*``, ``?``,
    ``**`` for any number of segments). A rule covers a root when it matches the root or any
    ancestor of it, since a denial of a directory denies everything under it.
    """

    def __init__(self, segments: list[tuple[str, str]]) -> None:
        self.segments = segments

    def covers(self, root: Path) -> bool:
        return any(self._match(self.segments, root.parts[:k]) for k in range(1, len(root.parts) + 1))

    @classmethod
    def _match(cls, pattern: list[tuple[str, str]], parts: tuple[str, ...]) -> bool:
        if not pattern:
            return not parts
        (kind, text), rest = pattern[0], pattern[1:]
        if kind == "glob" and text == "**":
            return any(cls._match(rest, parts[i:]) for i in range(len(parts) + 1))
        if not parts:
            return False
        same = parts[0] == text if kind == "lit" else fnmatch.fnmatchcase(parts[0], text)
        return same and cls._match(rest, parts[1:])


def _read_rule_pattern(
    rule: str, spec: str, source: _Source, anchor: Path | None, home: Path, launch_dir: Path
) -> _ReadRule:
    """Resolve a ``Read(<spec>)`` deny rule the way Claude Code does, or raise.

    Claude Code (permissions docs, "How path patterns match"): ``//path`` is an absolute
    path; ``~/path`` is home-relative; ``/path`` is relative to the settings source (for user
    settings, the config dir holding the file); ``./path`` is relative to the working
    directory; a bare pattern matches at any depth under the working directory. Syntax is
    gitignore's. A source with no documented anchor (managed settings) cannot be resolved for
    a single-slash rule, and a glob this check cannot evaluate cannot be proven not to cover
    a profile root: both refuse the launch.
    """
    path, kind = source
    if _UNSUPPORTED_GLOB_RE.search(spec):
        raise HarnessError(
            f"{path}: the {kind} read rule {rule!r} uses a glob this check cannot evaluate "
            "(only '*', '**' and '?' are understood), so it cannot be proven not to cover a "
            "path the profile allows; rewrite it as Read(//absolute/path/**) or remove it; "
            "refusing the launch"
        )
    for tail in ("/**", "/*"):
        if spec.endswith(tail):
            spec = spec[: -len(tail)]
    floating = False
    if spec in ("", ".", "**", "*"):
        base, rest = launch_dir, ""
    elif spec.startswith("//"):
        base, rest = Path("/"), spec[2:]
    elif spec == "~" or spec.startswith("~/"):
        base, rest = home, spec[2:]
    elif spec.startswith("/"):
        if anchor is None:
            raise HarnessError(
                f"{path}: the {kind} read rule {rule!r} is a single-slash path, which is "
                "relative to the settings source, and that source's anchor is not documented, "
                "so it cannot be resolved; rewrite it as Read(//absolute/path/**); "
                "refusing the launch"
            )
        base, rest = anchor, spec[1:]
    elif spec.startswith("./"):
        base, rest = launch_dir, spec[2:]
    else:
        base, rest, floating = launch_dir, spec, True
    names = [n for n in rest.split("/") if n]
    if floating:
        return _ReadRule(
            [("lit", part) for part in launch_dir.parts]
            + [("glob", "**")]
            + [("glob", n) for n in names]
        )
    literal: list[str] = []
    while names and not _GLOB_RE.search(names[0]):
        literal.append(names.pop(0))
    anchor_dir = Path(os.path.realpath(base.joinpath(*literal)))
    segments = [("lit", part) for part in anchor_dir.parts]
    segments += [("glob", n) for n in names]
    return _ReadRule(segments)


class _UserSettings:
    """The parts of an account's settings a launch must not widen.

    Built from the user-level file; :meth:`absorb` folds in the managed files, whose lists
    merge with it the way Claude Code merges them. Every entry remembers the file it came
    from, so a refusal names the file to change.
    """

    def __init__(
        self, path: Path, home: Path, launch_dir: Path, *, kind: str = "user-level"
    ) -> None:
        self.path = path
        self.kind = kind
        data = self._load(path, kind)
        source: _Source = (path, kind)
        sandbox = data.get("sandbox", {})
        if not isinstance(sandbox, dict):
            raise HarnessError(f"{path}: 'sandbox' must be an object; refusing the launch")
        fs = self._section(path, sandbox, "filesystem")
        net = self._section(path, sandbox, "network")
        permissions = self._section(path, data, "permissions")
        self.sandbox_flags: dict[str, tuple[bool, _Source]] = {}
        for label in _SANDBOX_FLAGS:
            _, *parents, key = label.split(".")
            holder = {(): sandbox, ("filesystem",): fs, ("network",): net}[tuple(parents)]
            if key in holder:
                if not isinstance(holder[key], bool):
                    raise HarnessError(
                        f"{path}: {label!r} must be true or false; the sandbox cannot be "
                        "confirmed, refusing the launch"
                    )
                self.sandbox_flags[label] = (holder[key], source)
        self.managed_flags: dict[str, tuple[bool, _Source]] = (
            dict(self.sandbox_flags) if kind == "managed" else {}
        )
        self.unix_sockets: list[tuple[str, _Source]] = [
            (e, source)
            for e in self._strings(path, net, "allowUnixSockets", "sandbox.network.allowUnixSockets")
            or []
        ]
        self.file_grants: list[tuple[str, _Source]] = [
            (r, source)
            for r in self._strings(path, permissions, "allow", "permissions.allow") or []
            if r.split("(", 1)[0] in _FILE_WRITE_TOOLS
        ] + [
            (d, source)
            for d in self._strings(
                path, permissions, "additionalDirectories", "permissions.additionalDirectories"
            )
            or []
        ]
        allow_write = self._strings(path, fs, "allowWrite", "sandbox.filesystem.allowWrite")
        self.allow_write: list[tuple[str, _Source]] | None = (
            None if allow_write is None else [(e, source) for e in allow_write]
        )
        self.deny_write: list[tuple[str, _Source]] = [
            (e, source)
            for e in self._strings(path, fs, "denyWrite", "sandbox.filesystem.denyWrite") or []
        ]
        self.allowed_domains = self._strings(
            path, net, "allowedDomains", "sandbox.network.allowedDomains"
        )
        self.excluded_commands = (
            self._strings(path, sandbox, "excludedCommands", "sandbox.excludedCommands") or []
        )
        deny_read = self._strings(path, fs, "denyRead", "sandbox.filesystem.denyRead") or []
        rules = self._strings(path, permissions, "deny", "permissions.deny") or []
        self.deny_read: list[tuple[str, Callable[[Path], bool], _Source]] = []
        for entry in deny_read:
            denied = _resolve_settings_path(entry, home, launch_dir)
            self.deny_read.append((entry, lambda root, d=denied: _covers(d, root), source))
        for rule in rules:
            match = _READ_RULE_RE.match(rule)
            if match:
                pattern = _read_rule_pattern(
                    rule, match.group("spec"), source,
                    path.parent if kind == "user-level" else None, home, launch_dir,
                )
                self.deny_read.append((rule, pattern.covers, source))
        self.home, self.launch_dir = home, launch_dir

    def absorb(self, other: _UserSettings) -> None:
        """Merge *other*'s lists into this one: allowances and denials union."""
        if other.allow_write is not None:
            self.allow_write = [*(self.allow_write or []), *other.allow_write]
        self.deny_write += other.deny_write
        self.managed_flags.update(other.managed_flags)
        self.unix_sockets += other.unix_sockets
        self.file_grants += other.file_grants
        self.deny_read += other.deny_read
        self.excluded_commands += [c for c in other.excluded_commands if c not in self.excluded_commands]
        if other.allowed_domains is not None:
            self.allowed_domains = [*(self.allowed_domains or []), *other.allowed_domains]

    @staticmethod
    def _load(path: Path, kind: str) -> dict:
        try:
            text = path.read_text()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise HarnessError(f"{path}: cannot read {kind} settings ({exc}); refusing the launch") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HarnessError(f"{path}: malformed {kind} settings ({exc}); refusing the launch") from exc
        if not isinstance(data, dict):
            raise HarnessError(f"{path}: {kind} settings must be a JSON object; refusing the launch")
        return data

    @staticmethod
    def _section(path: Path, parent: dict, key: str) -> dict:
        value = parent.get(key, {})
        if not isinstance(value, dict):
            raise HarnessError(f"{path}: {key!r} must be an object; refusing the launch")
        return value

    @staticmethod
    def _strings(path: Path, parent: dict, key: str, label: str) -> list[str] | None:
        if key not in parent:
            return None
        value = parent[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise HarnessError(f"{path}: {label!r} must be a list of strings; refusing the launch")
        return value


def _managed_settings(home: Path, launch_dir: Path, platform: str | None = None) -> list[_UserSettings]:
    """The managed settings files in the order Claude Code merges them; none when absent.

    A file or drop-in directory that exists but cannot be read, or a file that is not a JSON
    object, refuses the launch: what policy applies cannot be established.
    """
    plat = platform if platform is not None else sys.platform
    base = _MANAGED_SETTINGS_DIRS["darwin" if plat == "darwin" else "linux"]
    paths = [base / _MANAGED_FILENAME]
    try:
        names = sorted(os.listdir(base / _MANAGED_DROP_IN_DIR))
    except (FileNotFoundError, NotADirectoryError):
        names = []
    except OSError as exc:
        raise HarnessError(
            f"{base / _MANAGED_DROP_IN_DIR}: cannot read the managed settings directory ({exc}); "
            "refusing the launch"
        ) from exc
    paths += [base / _MANAGED_DROP_IN_DIR / n for n in names if n.endswith(".json") and not n.startswith(".")]
    return [_UserSettings(p, home, launch_dir, kind="managed") for p in paths]


def _scratch_dirs() -> set[Path]:
    """System temp, symlink-free: ``/tmp``, ``/private/tmp`` and the platform temp dir."""
    return {Path(os.path.realpath(p)) for p in ("/tmp", "/private/tmp", tempfile.gettempdir())}


def _tmux_socket_dir(env: dict[str, str], uid: int) -> Path:
    """The directory tmux keeps its default sockets in: ``<TMUX_TMPDIR or /tmp>/tmux-<uid>``,
    symlink-free (``/tmp`` is ``/private/tmp`` on macOS)."""
    return Path(os.path.realpath(Path(env.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{uid}"))


def _derive_write_confinement(
    user: _UserSettings, roots: list[Path], tmux_socket_dir: Path
) -> list[str]:
    """Check the profile's write roots against *user*; return the derived ``denyWrite``.

    The launch directory counts as user-allowed: Claude Code's sandbox always permits
    writes to the session's working directory, whatever ``allowWrite`` lists. System
    temp (``/tmp``, ``/private/tmp``, the platform temp dir) is shared scratch, not a
    confinement target, so a user entry resolving to it is never derived into the
    denial: a deny beats an allow, and it would break the session's own sandbox temp.

    *tmux_socket_dir* sits inside that scratch, so a session could unlink the sockets of
    the tmux server it runs in (or the operator's default one). It is denied whenever it
    lies under a user-allowed root or the launch directory; outside those it is already
    unwritable, so nothing is added there and the list never names a path the user did
    not allow. A directory that contains a profile root is never denied, since the denial
    would win over the root.

    A user with no ``allowWrite`` list at all allows only the launch directory, so a profile
    root outside it is refused too (and the refusal names the key to add); the profile's
    own ``allowWrite`` would otherwise widen writes past what the user permits.

    Raises :class:`HarnessError` naming the offending entry when a profile root is
    not covered by the user's writable list or the launch directory, when a user-level writable root contains
    a profile root (it cannot be narrowed without denying the profile root), or when
    a user-level write denial covers a profile root.
    """
    for entry, (source_path, kind) in user.deny_write:
        denied = _resolve_settings_path(entry, user.home, user.launch_dir)
        covered = next((root for root in roots if _covers(denied, root)), None)
        if covered is not None:
            raise HarnessError(
                f"{source_path}: the {kind} write denial {entry!r} covers a path this "
                f"profile writes. Remove or narrow it in {source_path} so it no longer covers "
                f"{covered}; refusing the launch"
            )
    allowed = [
        (e, _resolve_settings_path(e, user.home, user.launch_dir), src)
        for e, src in user.allow_write or []
    ]
    for entry, resolved, (source_path, kind) in allowed:
        if any(resolved in root.parents for root in roots):
            raise HarnessError(
                f"{source_path}: the {kind} writable root {entry!r} contains a path this "
                "profile writes, so it cannot be narrowed without denying that path; "
                "refusing the launch"
            )
    for root in roots:
        if not _covers(user.launch_dir, root) and not any(
            _covers(resolved, root) for _, resolved, _ in allowed
        ):
            raise HarnessError(
                f"the profile writes {root}, which {user.path} does not allow "
                "(a profile may narrow the user-level settings, never widen them); "
                + (
                    f"it sets no 'sandbox.filesystem.allowWrite' list, so only the working "
                    f"directory {user.launch_dir} is writable. "
                    if user.allow_write is None
                    else ""
                )
                + f"Add {root} to 'sandbox.filesystem.allowWrite' in {user.path} to allow it; "
                "refusing the launch"
            )
    scratch = _scratch_dirs()
    deny: list[str] = []
    for _, resolved, _ in allowed:
        if (
            resolved in scratch
            or any(_covers(root, resolved) for root in roots)
            or str(resolved) in deny
        ):
            continue
        deny.append(str(resolved))
    if (
        (_covers(user.launch_dir, tmux_socket_dir) or any(_covers(r, tmux_socket_dir) for _, r, _ in allowed))
        and not any(_covers(tmux_socket_dir, root) for root in roots)
        and str(tmux_socket_dir) not in deny
    ):
        deny.append(str(tmux_socket_dir))
    return deny


def _helper_code(header_command: tuple[str, ...]) -> list[tuple[Path, bool]]:
    """The headers helper's code as ``(path, is_directory)``: ``bin/trailhead`` as named and
    where it resolves, the checkout's ``bin`` directory, ``trailhead`` package and
    ``pyproject.toml``."""
    binary = Path(header_command[0])
    lexical = Path(os.path.realpath(binary.parent)) / binary.name
    resolved = Path(os.path.realpath(binary))
    checkout = resolved.parent.parent
    return [
        (lexical, False),
        (resolved, False),
        (resolved.parent, True),
        (checkout / "trailhead", True),
        (checkout / "pyproject.toml", False),
    ]


def _edit_denials(
    user: _UserSettings,
    header_command: tuple[str, ...],
    env: Mapping[str, str],
    state_dirs: list[Path],
) -> list[str]:
    """``Edit`` deny rules over everything the headers helper runs or reads from, and Outpost's state.

    The Bash sandbox does not apply to Claude's file tools; they obey only permission rules
    (https://code.claude.com/docs/en/sandboxing.md, "What runs outside the sandbox"), and an
    ``Edit`` rule covers every built-in tool that edits files, ``Write`` and ``NotebookEdit``
    included (permissions.md, "What Read and Edit rules cover"). A deny from any scope beats
    an allow from any scope, ``--settings`` included (permissions.md, "Settings precedence"),
    so a user or managed ``Edit``/``Write`` allow or ``additionalDirectories`` entry cannot
    reach these paths. The helper's checkout and *state_dirs* are always denied; PATH directories
    only when a visible grant could reach them, since the hub cannot otherwise edit outside its
    own directory, and a symlinked entry is denied at its real location too.
    """
    dirs: list[Path] = []
    files: list[Path] = []
    for path, is_dir in _helper_code(header_command):
        (dirs if is_dir else files).append(path)
    dirs += [files[0].parent, *state_dirs]
    if user.file_grants:
        for entry in env.get("PATH", "").split(os.pathsep):
            if os.path.isabs(entry):
                dirs += [Path(entry), Path(os.path.realpath(entry))]
    rules: list[str] = []
    for rule in [*(f"Edit(/{p}/**)" for p in dirs), *(f"Edit(/{p})" for p in files)]:
        if rule not in rules:
            rules.append(rule)
    return rules


def _refuse_unconfinable_sandbox(user: _UserSettings) -> None:
    """Refuse settings the hub's own ``--settings`` cannot override.

    Managed settings outrank ``--settings``, so a managed ``sandbox.enabled: false``,
    ``allowUnsandboxedCommands: true``, ``filesystem.disabled: true``, ``allowLocalBinding`` or
    ``allowAllUnixSockets`` leaves the session able to run commands outside the sandbox, with
    the helper's code and Outpost's state in reach. Unix-socket allow-lists merge across scopes,
    so a non-empty one at any scope cannot be narrowed. A managed value that a later managed
    file reverses is not in force and does not refuse.
    """
    for label, (value, (path, kind)) in user.managed_flags.items():
        if value is _SANDBOX_FLAGS[label]:
            raise HarnessError(
                f"{path}: the {kind} setting {label} is {str(value).lower()}, which the hub's own "
                "settings cannot override (managed settings outrank them) and which lets the "
                "session run outside the sandbox that keeps it from the headers helper's code. "
                f"Remove {label} from {path}; refusing the launch"
            )
    for entry, (path, kind) in user.unix_sockets:
        raise HarnessError(
            f"{path}: the {kind} setting sandbox.network.allowUnixSockets lists {entry!r}; lists "
            "merge across settings scopes, so the hub's own settings cannot narrow it, and a "
            "socket it names can lead out of the sandbox. "
            f"Remove sandbox.network.allowUnixSockets from {path}; refusing the launch"
        )


def _refuse_helper_code_under_write_roots(
    header_command: tuple[str, ...], env: Mapping[str, str], write_roots: list[Path]
) -> None:
    """Refuse a launch whose headers helper's code the session could rewrite.

    The helper runs outside the sandbox with the operator's access, so every file it executes
    must be out of the session's reach: ``bin/trailhead`` (as named, and where it resolves),
    the checkout's ``bin`` directory, ``trailhead`` package and ``pyproject.toml`` (the
    launcher reads its interpreter floor there), and every directory of the PATH it runs with.
    The helper is spawned by the harness, whose environment is the launched session's:
    ``session_start`` emits *env*'s ``PATH`` in ``env_set`` (and ``env_unset`` names no
    ``PATH``), so the PATH checked here is the one it resolves bash, grep, head, dirname,
    readlink and python3 through. An entry that is relative or empty, or whose lexical path or realpath
    overlaps a write root, is refused whether or not it exists yet: the session could create
    it. A path overlaps a write root when either contains the other, by whole segments.
    """
    for path, _ in _helper_code(header_command):
        for root in write_roots:
            if _covers(root, path) or _covers(path, root):
                raise HarnessError(
                    f"the headers helper's code {path} overlaps {root}, which the hub session may "
                    "write; the helper runs outside the sandbox with your access, so the session "
                    f"could replace it. Run trailhead from a checkout outside "
                    f"{root}, then start the hub again; refusing the launch"
                )
    if "PATH" not in env:
        return
    for entry in env["PATH"].split(os.pathsep):
        if not os.path.isabs(entry):
            raise HarnessError(
                f"PATH entry {entry!r} is not absolute; the headers helper runs outside the sandbox "
                "and resolves bash, grep, head, dirname, readlink and python3 through PATH, so a "
                "relative or empty entry lets the session plant them in its working directory. "
                "Remove it from the PATH Outpost runs with, then start the hub again; "
                "refusing the launch"
            )
        for path in {Path(entry), Path(os.path.realpath(entry))}:
            for root in write_roots:
                if _covers(root, path) or _covers(path, root):
                    raise HarnessError(
                        f"PATH entry {entry} ({path}) overlaps {root}, which the hub session may write; "
                        "the headers helper runs outside the sandbox and resolves bash, grep, head, "
                        "dirname, readlink and python3 through PATH, so the session could plant them "
                        f"there. Put a PATH outside {root} in the environment Outpost runs with, "
                        "then start the hub again; refusing the launch"
                    )


_GIT_ADMIN_NAMES = ("config", "hooks", "info")
_GIT_SCAN_DEPTH = 3
_GITDIR_POINTER_LIMIT = 4096


def _gitdir_of(dot_git: Path) -> Path | None:
    """The git directory a vault's ``.git`` entry stands for, or ``None``.

    A directory is itself the git directory; a symlink resolves to its target; a file is
    a ``gitdir: <path>`` pointer (relative paths resolve against the file's directory).
    Anything else, including a pointer that does not parse, is not a repository.
    """
    if dot_git.is_symlink():
        resolved = Path(os.path.realpath(dot_git))
        return resolved if resolved.is_dir() else None
    if dot_git.is_dir():
        return dot_git
    if not dot_git.is_file():
        return None
    try:
        with dot_git.open("rb") as f:
            line = f.read(_GITDIR_POINTER_LIMIT).decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    if not line or not line[0].startswith("gitdir:"):
        return None
    target = line[0][len("gitdir:"):].strip()
    if not target:
        return None
    resolved = Path(os.path.realpath(dot_git.parent / target))
    return resolved if resolved.is_dir() else None


def _common_dir_of(git_dir: Path) -> Path | None:
    pointer = git_dir / "commondir"
    try:
        target = pointer.read_text().strip()
    except OSError:
        return None
    if not target:
        return None
    resolved = Path(os.path.realpath(git_dir / target))
    return resolved if resolved.is_dir() else None


def _git_admin_denials(guarded: list[Path], writable: list[Path]) -> list[str]:
    """``denyWrite`` entries protecting the git administrative files under *guarded* roots.

    Every repository found within ``_GIT_SCAN_DEPTH`` directory levels of a guarded root
    (the root itself included) gets ``config``, ``hooks`` and ``info`` of its git directory
    denied. When ``.git`` is a pointer file or a symlink, the ``.git`` entry itself is
    denied too (so it cannot be repointed) along with the resolved git directory, and a
    linked worktree's common directory, which is where its ``config`` and ``hooks`` live.
    A git directory outside every *writable* root is not named: it is already unwritable.

    Each guarded root also gets the glob ``<root>/*/*/.git/<name>`` for each name, the
    shape of lore's ``vaults/<name>``, so a vault created after launch is covered without
    being enumerated. Repositories deeper than the scan bound and created after launch are
    not covered.
    """
    found: list[str] = []

    def add(path: Path | str) -> None:
        if str(path) not in found:
            found.append(str(path))

    def add_admin(git_dir: Path) -> None:
        if any(_covers(w, git_dir) for w in writable):
            for name in _GIT_ADMIN_NAMES:
                add(git_dir / name)

    for root in guarded:
        for current, dirnames, _ in os.walk(root):
            here = Path(current)
            depth = len(here.relative_to(root).parts)
            dot_git = here / ".git"
            git_dir = _gitdir_of(dot_git) if os.path.lexists(dot_git) else None
            if git_dir is not None:
                if git_dir != dot_git:
                    add(dot_git)
                add_admin(git_dir)
                common = _common_dir_of(git_dir)
                if common is not None:
                    add_admin(common)
            dirnames[:] = [] if depth >= _GIT_SCAN_DEPTH else [d for d in dirnames if d != ".git"]
        for name in _GIT_ADMIN_NAMES:
            add(f"{root}/*/*/.git/{name}")
    return found


def _check_reads_and_network(
    user: _UserSettings, roots: list[Path], hosts: tuple[str, ...]
) -> None:
    for entry, covers, (source_path, kind) in user.deny_read:
        covered = next((root for root in roots if covers(root)), None)
        if covered is not None:
            raise HarnessError(
                f"{source_path}: the {kind} read denial {entry!r} covers a path this "
                f"profile allows. Remove or narrow it in {source_path} so it no longer covers "
                f"{covered}; refusing the launch"
            )
    if user.allowed_domains is not None:
        for host in hosts:
            if not _host_allowed(host, user.allowed_domains):
                raise HarnessError(
                    f"the profile reaches {host!r}, which the network allow-list in "
                    f"{user.path} does not include (a profile may narrow it, never widen it)"
                )


def _seed_folder_trust(config_file: Path, directory: Path) -> None:
    """Mark *directory* trusted in *config_file* so the first-run trust prompt never fires.

    Read, update ``projects[<directory>].hasTrustDialogAccepted``, write a uniquely named
    temp file beside it, atomic rename (so concurrent launches never share a temp file and
    the file is never half-written); every other key survives. Two launches seeding at the
    same instant may still drop one entry (read-modify-write has no lock).
    An absent file is created; a malformed one refuses.
    """
    if not config_file.parent.is_dir():
        raise HarnessError(
            f"{config_file.parent} does not exist, so {config_file.name} cannot be written; "
            "refusing the launch"
        )
    try:
        text = config_file.read_text()
    except FileNotFoundError:
        data: object = {}
    except OSError as exc:
        raise HarnessError(f"{config_file}: cannot read ({exc}); refusing the launch") from exc
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HarnessError(f"{config_file}: malformed ({exc}); refusing the launch") from exc
    projects = data.get("projects", {}) if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(projects, dict):
        raise HarnessError(
            f"{config_file}: expected a JSON object with an object 'projects'; refusing the launch"
        )
    entry = projects.get(str(directory), {})
    if not isinstance(entry, dict):
        raise HarnessError(f"{config_file}: project entry for {directory} is not an object")
    projects[str(directory)] = {**entry, "hasTrustDialogAccepted": True}
    data["projects"] = projects
    try:
        mode = stat.S_IMODE(config_file.stat().st_mode)
    except FileNotFoundError:
        mode = 0o600
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(
            prefix=f".{config_file.name}.", suffix=".trailhead-tmp", dir=config_file.parent
        )
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), mode)
            json.dump(data, f, indent=2)
        os.replace(tmp, config_file)
    except OSError as exc:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)
        raise HarnessError(f"{config_file}: cannot write ({exc}); refusing the launch") from exc


class ClaudeCodeHarness(Harness):
    """Install agent-plugins into Claude Code via the ``claude plugin`` CLI."""

    name = "claude_code"

    @classmethod
    def detect(cls, env: dict[str, str]) -> bool:
        return _claude_dir(env).is_dir()

    def cli_binary_name(self) -> str | None:
        return "claude"

    def recognizes_process(self, comm: str, exe: str | None) -> bool:
        """The process is named ``claude`` AND runs a version-named file under a
        ``claude/versions`` directory (the installed layout; ``claude`` on PATH is a
        symlink to it, and ``/proc/<pid>/exe`` resolves that symlink)."""
        if comm != "claude" or not exe:
            return False
        versions = Path(exe).parent
        return versions.name == "versions" and versions.parent.name == "claude"

    # -- manifest -------------------------------------------------------------

    def generate_manifest(self, tools: list[str], composed_root: Path) -> None:
        """Write ONE consolidated Shape-A marketplace.json at composed_root/.claude-plugin/.

        The marketplace is named ``trailhead`` and has one ``plugins[]`` entry per
        tool.  Plugin order is deterministic (sorted).  The write is atomic:
        rendered to a sibling temp file first, then ``os.replace()``-ed into place,
        so a crash mid-write can never leave a torn shared marketplace.json.
        """
        for tool in tools:
            _validate_tool(tool)

        claude_plugin_dir = composed_root / ".claude-plugin"
        claude_plugin_dir.mkdir(parents=True, exist_ok=True)

        marketplace = {
            "name": "trailhead",
            "owner": {"name": "trailhead"},
            "description": "Trailhead-composed plugin marketplace.",
            "plugins": [
                {
                    "name": tool,
                    "source": f"./plugins/{tool}",
                    "description": _tool_description(tool),
                }
                for tool in sorted(tools)
            ],
        }

        out = claude_plugin_dir / "marketplace.json"
        fd, tmp_path = tempfile.mkstemp(
            dir=claude_plugin_dir, prefix=".marketplace-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(marketplace, indent=2))
            os.replace(tmp_path, out)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def manifest_name(self, composed_root: Path) -> str | None:
        """Return the display name of the generated marketplace, or None.

        None when the manifest is absent or unparseable — used by ``doctor`` for a
        read-only report, so a malformed file must never raise.
        """
        mkt = composed_root / ".claude-plugin" / "marketplace.json"
        if not mkt.exists():
            return None
        try:
            return json.loads(mkt.read_text()).get("name")
        except (OSError, json.JSONDecodeError):
            return None

    def manifest_exists(self, composed_root: Path) -> bool:
        """True if marketplace.json is present, regardless of whether it parses."""
        return (composed_root / ".claude-plugin" / "marketplace.json").exists()

    # -- registration state (on-disk truth) -----------------------------------

    def _marker_dir(self, env: dict[str, str] | None) -> Path:
        """Resolve the config dir the markers for this call belong to.

        Registration and per-tool install state are written by the ``claude``
        CLI into one config dir, so the markers that mirror that state must be
        keyed the same way — never by the composed tree, which is global and
        shared by every config dir on the machine.

        This is the single choke point for every method that shells the ``claude``
        CLI or reports installed state, so the conflicting-override refusal lives
        here: it covers ``register``, ``install_tool``, ``rewire_tool``,
        ``unregister_tool``, ``unregister_marketplace``, and the state readers
        ``doctor`` reads, in one place instead of five.
        """
        resolved = env if env is not None else dict(os.environ)
        _refuse_conflicting_config_dirs(resolved)
        return _claude_dir(resolved)

    def is_registered(self, composed_root: Path, *, env: dict[str, str] | None = None) -> bool:
        return (self._marker_dir(env) / _REGISTERED_MARKER).exists()

    def is_installed(
        self, tool: str, composed_root: Path, *, env: dict[str, str] | None = None
    ) -> bool:
        return (self._marker_dir(env) / f"{_INSTALLED_MARKER_PREFIX}{tool}").exists()

    def installed_tools(
        self, composed_root: Path, *, env: dict[str, str] | None = None
    ) -> list[str]:
        """Return the sorted tool names recorded installed in this config dir."""
        marker_dir = self._marker_dir(env)
        if not marker_dir.is_dir():
            return []
        return sorted(
            f.name[len(_INSTALLED_MARKER_PREFIX) :]
            for f in marker_dir.iterdir()
            if f.is_file() and f.name.startswith(_INSTALLED_MARKER_PREFIX)
        )

    def _write_marker(self, name: str, env: dict[str, str] | None) -> None:
        marker_dir = self._marker_dir(env)
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / name).write_text("{}")

    def _registration_entry(self, composed_root: Path, env: dict[str, str] | None) -> Path:
        """Path of this config dir's ledger entry inside *composed_root*.

        Named by a digest of the config-dir path so any absolute path becomes a
        safe single filename; the path itself is the file's content.
        """
        config_dir = str(self._marker_dir(env))
        digest = hashlib.sha256(config_dir.encode()).hexdigest()[:32]
        return composed_root / _REGISTRATIONS_SUBDIR / digest

    def composed_tree_in_use_elsewhere(
        self, composed_root: Path, *, env: dict[str, str] | None = None
    ) -> bool:
        """True if a config dir OTHER than the one *env* names is registered here.

        Only registrations trailhead itself recorded are visible: a config dir
        registered before this ledger existed does not hold the tree until its
        next ``trailhead install`` re-records it.
        """
        own = self._registration_entry(composed_root, env).name
        ledger = composed_root / _REGISTRATIONS_SUBDIR
        if not ledger.is_dir():
            return False
        return any(f.is_file() and f.name != own for f in ledger.iterdir())

    def _cli_env(self, env: dict[str, str] | None) -> dict[str, str]:
        """Environment for a ``claude plugin …`` subprocess, config dir pinned.

        The CLI writes marketplace registration and plugin content into the
        config dir ``CLAUDE_CONFIG_DIR`` selects, so the target is named
        explicitly rather than inherited from the ambient process environment —
        otherwise an install meant for one account lands in whichever dir the
        caller happened to be running under.

        The dir named is ``_marker_dir``'s — the same one the markers are written
        to, ``TRAILHEAD_CLAUDE_DIR`` included. That seam relocates the config
        *directory*, which is a directory Claude Code does read when named through
        ``CLAUDE_CONFIG_DIR``, so following it keeps trailhead's markers and the
        CLI's own state in one place instead of splitting them across two dirs.
        (Contrast ``claude_config_file``, which must NOT follow the seam: a config
        *file* resolved through it is a path Claude Code never reads.)

        Because following the seam here pins a LIVE subprocess, an environment that
        sets both the seam and ``CLAUDE_CONFIG_DIR`` to different directories is
        refused by ``_marker_dir`` rather than silently resolved — see
        :func:`_refuse_conflicting_config_dirs`.
        """
        base = dict(env) if env is not None else dict(os.environ)
        base["CLAUDE_CONFIG_DIR"] = str(self._marker_dir(env))
        return base

    # -- install / uninstall --------------------------------------------------

    def register(
        self, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        """Register the consolidated trailhead marketplace via the harness CLI.

        Shells ``claude plugin marketplace add --scope user <composed_root>``
        (idempotent) against the config dir *env* resolves, and writes that
        dir's ``.trailhead-registered`` marker only after the CLI call succeeds.
        The marketplace source is the global composed tree; the registration it
        creates belongs to one config dir.  That pairing is also recorded in the
        composed tree's registration ledger, so a teardown run under a different
        config dir can see this one still sourcing its plugins from here.

        A nonzero returncode from the injected *runner* raises ``HarnessError``:
        the marker (and, upstream, the provenance stamp `trailhead update`
        advances) must never claim a registration that never happened.
        ``_default_runner`` already raises via ``check=True``; this check
        exists for whatever *runner* a caller injects instead, which may
        return a failed ``CompletedProcess`` rather than raise.
        """
        _run = runner or _default_runner
        result = _run(
            ["claude", "plugin", "marketplace", "add", "--scope", "user", str(composed_root)],
            env=self._cli_env(env),
        )
        if getattr(result, "returncode", 0) != 0:
            raise HarnessError(
                f"claude plugin marketplace add failed for {composed_root} "
                f"(exit {result.returncode})"
            )
        self._write_marker(_REGISTERED_MARKER, env)
        entry = self._registration_entry(composed_root, env)
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(f"{self._marker_dir(env)}\n")

    def install_tool(
        self, tool: str, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        """Install a tool from the consolidated trailhead marketplace.

        Shells ``claude plugin install <tool>@trailhead --scope user`` against
        the config dir *env* resolves, and writes that dir's per-tool
        ``.trailhead-installed-<tool>`` marker only after success.

        Safe to run against a config dir that already has the plugin — the CLI
        reports "already installed" and exits 0 — which is what lets a config dir
        with no marker yet take this branch instead of the uninstall/install pair.

        A nonzero returncode from the injected *runner* raises ``HarnessError``:
        the marker (and, upstream, the provenance stamp `trailhead update`
        advances) must never claim a wire that never happened.
        ``_default_runner`` already raises via ``check=True``; this check
        exists for whatever *runner* a caller injects instead, which may return
        a failed ``CompletedProcess`` rather than raise.
        """
        _validate_tool(tool)
        _run = runner or _default_runner
        result = _run(
            ["claude", "plugin", "install", f"{tool}@trailhead", "--scope", "user"],
            env=self._cli_env(env),
        )
        if getattr(result, "returncode", 0) != 0:
            raise HarnessError(
                f"claude plugin install failed for {tool}@trailhead (exit {result.returncode})"
            )
        self._write_marker(f"{_INSTALLED_MARKER_PREFIX}{tool}", env)

    def rewire_tool(
        self, tool: str, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        """Refresh an already-installed tool after recomposition.

        Sequence: **uninstall THEN install** (NOT ``plugin update``, which is
        version-keyed and keeps stale content when the version is static).  The
        per-tool marker is cleared before the pair and rewritten only after install
        succeeds (self-heal — a failure mid-pair leaves the marker absent so the
        next wire re-attempts cleanly).  A failing uninstall (``not installed``) is
        tolerated; the install must still run.

        A nonzero returncode from the install call raises ``HarnessError``
        (see ``install_tool``) — the marker must never claim a wire that never
        happened, and the caller (``wire()``) turns this into a ``WireError``
        that a self-update apply's rollback depends on.
        """
        _validate_tool(tool)
        _run = runner or _default_runner

        marker = self._marker_dir(env) / f"{_INSTALLED_MARKER_PREFIX}{tool}"
        marker.unlink(missing_ok=True)

        cli_env = self._cli_env(env)
        try:
            _run(
                ["claude", "plugin", "uninstall", f"{tool}@trailhead", "--scope", "user"],
                env=cli_env,
            )
        except Exception:
            pass

        result = _run(
            ["claude", "plugin", "install", f"{tool}@trailhead", "--scope", "user"],
            env=cli_env,
        )
        if getattr(result, "returncode", 0) != 0:
            raise HarnessError(
                f"claude plugin install failed for {tool}@trailhead (exit {result.returncode})"
            )
        self._write_marker(f"{_INSTALLED_MARKER_PREFIX}{tool}", env)

    def unregister_tool(
        self, tool: str, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        """Uninstall ONE tool from the consolidated trailhead marketplace.

        Shells ``claude plugin uninstall <tool>@trailhead --scope user --keep-data
        --yes``.  ``--keep-data`` preserves the plugin's persistent data dir so an
        uninstall is *wiring only*; ``--yes`` keeps it non-interactive.  Does NOT
        remove the marketplace (shared across all tools — torn down once by
        :meth:`unregister_marketplace` after the last tool).  The per-tool marker is
        cleared in ``finally`` so a torn-down tree never reads as installed even if
        the CLI call raises.
        """
        _validate_tool(tool)
        _run = runner or _default_runner
        try:
            _run(
                [
                    "claude",
                    "plugin",
                    "uninstall",
                    f"{tool}@trailhead",
                    "--scope",
                    "user",
                    "--keep-data",
                    "--yes",
                ],
                env=self._cli_env(env),
            )
        finally:
            (self._marker_dir(env) / f"{_INSTALLED_MARKER_PREFIX}{tool}").unlink(missing_ok=True)

    def unregister_marketplace(
        self, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        """Remove the shared ``trailhead`` marketplace (inverse of :meth:`register`).

        Called **once** after every tool has been uninstalled — NEVER per-tool,
        since a single marketplace is shared across all tools.  Shells
        ``claude plugin marketplace remove trailhead --scope user`` and clears the
        global marker in ``finally`` so a half-removed state never reads as
        registered.  The config dir's entry in the composed tree's registration
        ledger is dropped there too — it no longer sources plugins from the tree,
        so it no longer holds it against deletion.
        """
        _run = runner or _default_runner
        try:
            _run(
                ["claude", "plugin", "marketplace", "remove", "trailhead", "--scope", "user"],
                env=self._cli_env(env),
            )
        finally:
            (self._marker_dir(env) / _REGISTERED_MARKER).unlink(missing_ok=True)
            self._registration_entry(composed_root, env).unlink(missing_ok=True)

    # -- user-level rulesets --------------------------------------------------
    #
    # Claude Code reads user-global agent guidance from ``~/.claude/rules/*.md``.
    # We write one file per ruleset name there; ``env`` is injectable so tests
    # redirect the Claude dir (``TRAILHEAD_CLAUDE_DIR``) and never touch the real
    # ``~/.claude`` (Axiom 6).

    def user_ruleset_path(self, name: str, *, env: dict[str, str] | None = None) -> Path:
        """Resolve ``<claude-dir>/rules/<name>.md``, refusing any escaping name.

        Every path-building entry point for a ruleset goes through here, so this
        is the one place the name has to be confined — and it is confined here
        rather than at the caller because the write lands under ``~/.claude``,
        outside any trailhead-owned tree, in files Claude Code loads into every
        session on the machine.
        """
        _validate_ruleset_name(name)
        _env = env if env is not None else dict(os.environ)
        rules_dir = _claude_dir(_env) / _RULES_SUBDIR
        target = rules_dir / f"{name}.md"
        # Belt and braces: the name pattern above already forbids separators, but
        # the resolved answer is re-checked so no future relaxation of the
        # pattern can quietly turn this into a write outside the rules dir.
        if not target.resolve().is_relative_to(rules_dir.resolve()):
            raise HarnessError(f"invalid ruleset name {name!r}: resolves outside {rules_dir}")
        return target

    def install_user_ruleset(
        self, name: str, content: str, *, env: dict[str, str] | None = None
    ) -> None:
        """Write ``~/.claude/rules/<name>.md`` idempotently and atomically.

        Mirrors ``lore/config/settings_writer.py`` ``_save``: same-mount temp file via
        ``mkstemp(dir=target.parent)`` + ``os.replace`` so the swap is atomic and
        the rename can't fail cross-filesystem; clean up the temp on any error.
        A re-run with byte-identical content is a true no-op (no write, no swap).
        The name is confined by ``user_ruleset_path`` before this method touches
        the filesystem, so neither the ``mkdir(parents=True)`` nor the temp file
        can be steered outside the rules dir.
        Read and write both pin utf-8: rulesets carry non-ASCII prose, and a
        locale-dependent codec on either side would break the drift compare.
        """
        target = self.user_ruleset_path(name, env=env)
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{name}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, str(target))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def user_ruleset_status(
        self, name: str, content: str, *, env: dict[str, str] | None = None
    ) -> str:
        target = self.user_ruleset_path(name, env=env)
        if not target.is_file():
            return "missing"
        return "current" if target.read_text(encoding="utf-8") == content else "stale"

    # -- session transcripts --------------------------------------------------
    #
    # Claude Code writes one JSONL transcript per session at
    # ``<claude-dir>/projects/<munged>/<session-id>.jsonl``, where ``<munged>`` is
    # the session's start-of-session working directory with BOTH ``/`` and ``.``
    # replaced by ``-`` (so ``/Users/x/.local`` → ``-Users-x--local``: the ``/.``
    # pair yields a DOUBLE dash).  That layout is Claude-Code-specific knowledge
    # and lives here only (Axiom 1); callers receive a resolved path or None.

    def session_transcript_path(
        self, session_id: str, workspace: Path, *, env: dict[str, str] | None = None
    ) -> Path | None:
        """Resolve the transcript for ``session_id`` under ``workspace``, or None.

        ``workspace`` must be the session's START cwd — the munge key is baked in
        when the session starts, so a caller that has since changed directory must
        still pass the launch dir.

        Returns None when the session id is not a usable path component or the
        transcript file does not exist.  Existence is checked rather than assumed:
        transcripts are subject to Claude Code's retention cleanup, and a path to
        a file that is gone is worse than an honest "unresolvable".
        """
        if not _is_session_id(session_id):
            return None
        _env = env if env is not None else dict(os.environ)
        munged = _projects_key(Path(workspace).resolve())
        candidate = _claude_dir(_env) / _PROJECTS_SUBDIR / munged / f"{session_id}.jsonl"
        return candidate if candidate.is_file() else None

    def session_transcript_stored_under(
        self, session_id: str, root: Path, *, env: dict[str, str] | None = None
    ) -> bool | None:
        """Whether the top-level transcript for ``session_id`` is stored under
        the projects key of ``root`` or of any directory beneath it.

        The stored key is a lossy munge, so it is first resolved against the
        directories that exist on disk: when any claims it, the answer is
        whether one of them is ``root`` or beneath it, which keeps a sibling like
        ``<parent>/ws-2`` out.  Only when none exists (the start directory was
        removed, or could not be listed) does the answer fall back to comparing
        the key against ``root``'s own.  A key that is neither ``root``'s own nor
        extends it with ``-`` cannot be stored under ``root`` and answers False
        without walking the filesystem.  A transcript keyed through a symlink
        alias from outside ``root`` therefore answers False.

        Known limit: a start directory under ``root`` that has since been removed
        can still answer False when an existing directory outside ``root`` munges
        to the same key (the session started in ``ws/2``, that directory was
        removed, and a sibling ``ws-2`` exists): the claimant rule wins and the
        fallback does not run.  A root that cannot be resolved (a symlink loop)
        answers False without that fallback.  Read-only; never raises.
        """
        if not _is_session_id(session_id):
            return False
        _env = env if env is not None else dict(os.environ)
        projects_dir = _claude_dir(_env) / _PROJECTS_SUBDIR
        try:
            resolved_root = Path(root).resolve()
            # Python 3.13 resolves a symlink loop without raising; a stat
            # surfaces it (ELOOP) while a merely-missing root still
            # falls through to the key comparison.
            try:
                resolved_root.stat()
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    return False
        except (OSError, RuntimeError):
            return False
        root_key = _projects_key(resolved_root)
        try:
            keys = [
                t.parent.name for t in projects_dir.glob(f"*/{session_id}.jsonl") if t.is_file()
            ]
        except OSError:
            return False
        for key in keys:
            if key != root_key and not key.startswith(root_key + "-"):
                continue
            claimants = _directories_munging_to(key)
            if claimants:
                if any(c.is_relative_to(resolved_root) for c in claimants):
                    return True
            else:
                return True
        return False

    # -- session transcript destination ----------------------------------------
    #
    # Composes the same ``<claude-dir>/projects/<munged>/<session-id>.jsonl``
    # layout as ``session_transcript_path`` above, but for a destination that may
    # not exist yet — the write side of an incoming transfer. The munge is
    # LOSSY (both '/' and '.' collapse to '-'), so two distinct workspaces can
    # legitimately compose to the same projects key (e.g. ``repo.a`` and
    # ``repo/a``). That collision is undetectable from a single call in
    # isolation, so this checks whatever is ALREADY at the computed key: if a
    # transcript is there recording a DIFFERENT cwd than ``workspace``, writing
    # to the composed path would silently mix two sessions' history, so this
    # raises rather than handing back that path.

    def session_transcript_destination(
        self, session_id: str, workspace: Path, *, env: dict[str, str] | None = None
    ) -> Path | None:
        """Compose the destination for ``session_id`` under ``workspace``, or None.

        Returns None when the session id is not a usable path component, or when
        ``workspace`` is not absolute (never resolved against this process's own
        cwd — mirroring how :meth:`session_transcripts` drops a relative
        recorded root rather than anchoring it to the caller).

        Raises :class:`HarnessError` when the computed destination's projects
        key already holds a transcript recorded against a DIFFERENT workspace —
        the reachable collision the lossy munge allows. A caller must treat that
        raise as a hard refusal, exactly like ``None``, and must never fall back
        to composing a destination path of its own.
        """
        if not _is_session_id(session_id):
            return None
        workspace = Path(workspace)
        if not workspace.is_absolute():
            return None
        resolved = workspace.resolve()
        _env = env if env is not None else dict(os.environ)
        munged = _projects_key(resolved)
        project_dir = _claude_dir(_env) / _PROJECTS_SUBDIR / munged
        if project_dir.is_dir():
            try:
                existing_paths = list(project_dir.glob("*.jsonl"))
            except OSError:
                existing_paths = []
            for existing in existing_paths:
                other_cwd, _ = _scan_transcript_head(existing)
                if other_cwd is not None and other_cwd.resolve() != resolved:
                    raise HarnessError(
                        f"session_transcript_destination: projects key {munged!r} "
                        "already holds a transcript for a different workspace; "
                        "refusing to collide two workspaces onto one destination"
                    )
        return project_dir / f"{session_id}.jsonl"

    # -- transcript workspace rewrite -------------------------------------------
    #
    # Claude Code's transcript structurally records a root in exactly ONE
    # top-level field per line: ``cwd``.  A user-message body or a summary's
    # ``lastPrompt`` can also contain an absolute path, but only as free prose —
    # rewriting those would corrupt the conversation record rather than relocate
    # it, so this transform touches ``cwd`` and nothing else.  The historical
    # prose in already-recorded turns therefore keeps the sending host's path; a
    # cosmetic residue, accepted knowingly.
    #
    # Streams line by line under the same byte discipline as the reader above
    # (``_CWD_SCAN_MAX_LINE_BYTES``): a transcript can run to hundreds of
    # megabytes and a single malformed line can be unbounded, so no single read
    # or write ever exceeds the cap.  Unlike the reader, this has no line-count
    # bound at all — a recorded root a thousand lines in is still rewritten.
    #
    # Writes to a temp file beside ``destination`` and only renames it into
    # place once the whole source has been read without a refusal, so a raised
    # ``HarnessError`` leaves ``destination`` exactly as it was — fail closed,
    # never a partial or unrewritten file left where a caller might read it.

    def rewrite_transcript_workspace(
        self,
        source: Path,
        destination: Path,
        old_root: Path,
        new_root: Path,
        *,
        keep_foreign_roots: bool = False,
    ) -> bool:
        """Rewrite every ``cwd`` in ``source`` from ``old_root`` to ``new_root``,
        streaming the result into ``destination``.

        A line that fails to decode as JSON, or decodes to something other than
        a dict with a string, absolute ``cwd``, is copied through verbatim — a
        relative ``cwd`` in particular is passed through unrewritten rather
        than refused. That pass-through is a live decision, not an oversight:
        this method reaches only conversations camp has already resolved a
        root for. ``session_transcripts`` collapses a relative recorded ``cwd``
        to ``cwd=None`` (`trailhead/harness/claude_code.py:1150-1154`), which
        marks the conversation unresolved, and the sender drops an
        unresolved conversation before it ever streams a transcript here
        (`camp.transfer.conversations.send_workspace_conversations`).
        A non-camp caller of this harness-boundary method directly, without
        that same guard in front of it, would not have that guarantee.

        A line whose ``cwd`` is absolute but not under ``old_root`` — neither
        as written nor once resolved, the reading ``session_transcripts``
        gives a recorded root — raises
        :class:`HarnessError` naming ``source``, and nothing is written to
        ``destination`` — unless ``keep_foreign_roots``, which copies that
        line through verbatim instead.

        ``destination``'s parent directory must already exist — creating it
        is the caller's responsibility, not this transform's.

        Always returns ``True`` on completion — this harness always has a
        transcript-rewrite concept, unlike the base class's degrading default.
        """
        old_root = Path(old_root)
        new_root = Path(new_root)
        resolved: dict[Path, Path] = {}
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
        )
        try:
            with source.open("rb") as f_in, os.fdopen(tmp_fd, "wb") as f_out:
                while True:
                    chunk = f_in.readline(_CWD_SCAN_MAX_LINE_BYTES + 1)
                    if not chunk:
                        break
                    if len(chunk) > _CWD_SCAN_MAX_LINE_BYTES:
                        f_out.write(chunk)
                        if not chunk.endswith(b"\n"):
                            _stream_rest_of_overlong_line(f_in, f_out)
                        continue
                    rewritten = _rewrite_transcript_line(
                        chunk,
                        old_root,
                        new_root,
                        source,
                        keep_foreign_roots=keep_foreign_roots,
                        resolved=resolved,
                    )
                    f_out.write(rewritten)
        except BaseException:
            os.unlink(tmp_name)
            raise
        os.replace(tmp_name, destination)
        return True

    # -- session resume -------------------------------------------------------
    #
    # ``claude --resume <session-id>`` re-enters a session.  (``-r`` is the
    # documented short alias; the long form is used here because the argv is read
    # by humans debugging a wrapper.)  The command MUST run with the working
    # directory set to the session's ORIGINAL start cwd — Claude Code indexes
    # sessions per project directory, and resuming from anywhere else fails with
    # "No conversation found".  Setting that cwd is the caller's half of the
    # contract; this method only supplies the tokens.
    #
    # Known limitation: some child/subagent transcripts are not resumable even
    # from the correct cwd (``--resume`` indexes top-level sessions).  Claude
    # Code's own error surfaces that at resume time, so nothing is filtered here.

    def session_resume(self, session_id: str) -> list[str] | None:
        """Return ``["claude", "--resume", <session-id>]``, or None.

        The session-id guard is the same one the transcript lookup applies: an id
        that is not a plain token is rejected outright rather than passed into an
        argv, so no id can ever contribute an extra argument or a shell-active
        character to the command a caller runs.
        """
        if not _is_session_id(session_id):
            return None
        return ["claude", "--resume", session_id]

    def session_start_hook_session_id(self, payload: str) -> str | None:
        """The ``session_id`` field of the JSON object Claude Code writes to a
        SessionStart hook's stdin, or None.

        The field is present for every SessionStart source — a new session, a
        resume, ``/clear``, and a compaction — so a hook sees the id the
        session is running under right now, including after ``/clear`` moves
        it to a fresh one. The same plain-token guard :meth:`session_resume`
        applies is applied here, so an id this answers is one it would resume.
        """
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        session_id = data.get("session_id")
        return session_id if _is_session_id(session_id) else None

    # -- session transcript enumeration ----------------------------------------
    #
    # Enumerates <claude-dir>/projects/*/*.jsonl — DEPTH 2 ONLY. Claude Code
    # nests per-session subagent and tool-result transcripts under
    # <session-id>/subagents/ and <session-id>/tool-results/; a recursive glob
    # would return those too, and on a real store they vastly outnumber genuine
    # top-level transcripts. Depth-2 is therefore not an optimization — it is
    # what makes "session transcripts" here mean the same top-level sessions
    # :meth:`session_resume` can actually re-enter.
    #
    # A project directory's NAME is not consulted for anything but locating the
    # file: it is a lossy munge of a launch cwd ('/' and '.' both collapse to
    # '-'), so it cannot be reversed into a real path. The cwd this method
    # returns always comes from :func:`_scan_transcript_head` reading the
    # transcript's own content, never from the directory name.

    def session_transcripts(
        self, workspace: Path | None = None, *, env: dict[str, str] | None = None
    ) -> list[SessionTranscript]:
        """Enumerate top-level transcripts under ``<claude-dir>/projects/``.

        See the base contract for the full semantics. Never raises: a missing
        projects dir yields ``[]``, and a transcript this harness cannot open,
        decode, or find a cwd inside still yields a row, with ``cwd=None``.
        """
        _env = env if env is not None else dict(os.environ)
        projects_dir = _claude_dir(_env) / _PROJECTS_SUBDIR
        if not projects_dir.is_dir():
            return []

        resolved_workspace = Path(workspace).resolve() if workspace is not None else None

        rows: list[SessionTranscript] = []
        for candidate in _iter_transcript_paths(projects_dir):
            if not candidate.is_file():
                continue
            session_id = candidate.stem
            if not _is_session_id(session_id):
                continue
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                continue

            raw_cwd, is_team_member = _scan_transcript_head(candidate)
            # A relative recorded cwd is dropped rather than resolved. Resolving
            # one anchors it at whatever directory camp happened to be invoked
            # from, which would make both the reported location and the launch
            # root a function of the caller's cwd — the very property that
            # disqualifies a relative entry from the launch allowlist. Reporting
            # no root is true; reporting a root that moves with the caller is not.
            cwd = (
                raw_cwd.resolve()
                if raw_cwd is not None and raw_cwd.is_absolute()
                else None
            )

            if resolved_workspace is not None and (
                cwd is None or not cwd.is_relative_to(resolved_workspace)
            ):
                continue

            rows.append(
                SessionTranscript(
                    session_id=session_id,
                    cwd=cwd,
                    modified_at=datetime.fromtimestamp(mtime, tz=timezone.utc),
                    resumable=not is_team_member,
                )
            )

        return rows

    # -- session retention setting ------------------------------------------
    #
    # Claude Code deletes transcripts older than the top-level
    # ``cleanupPeriodDays`` settings key; naming it here is what lets a caller
    # point a user at the right setting without this module handing out a
    # file path to edit.

    def session_retention_setting(self) -> str | None:
        """The settings key a user raises to keep transcripts longer."""
        return _CLEANUP_PERIOD_KEY

    # -- session launch environment & enumeration --------------------------
    #
    # ``claude agents --json`` lists this machine's Claude Code sessions.
    # ``--cwd`` is the CLI's native started-under PREFIX filter (verified
    # empirically 2026-08-14) — exactly the "rooted under" scoping the base
    # seam documents, so it is passed straight through rather than re-derived.

    def session_launch_env_unset(self) -> list[str]:
        """Env var names a launching caller must scrub before spawning.

        See :data:`_LAUNCH_ENV_UNSET` for why each name is here.
        """
        return list(_LAUNCH_ENV_UNSET)

    def session_launch_env_set(
        self, account: str | None, *, env: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Bind a launched session to *account* by naming its config dir.

        Claude Code expresses an account as the directory holding its
        credentials, ``settings.json``, ``plugins/`` and ``.claude.json``, named
        by ``CLAUDE_CONFIG_DIR`` — so the whole binding is that one assignment,
        and this is the only place on the launch path that knows its spelling.

        The returned value IS that directory, verbatim: ``CLAUDE_CONFIG_DIR``
        names the config dir itself, not a base some suffix hangs off. It is what
        :func:`_claude_dir` answers and what :func:`claude_config_file` appends
        ``.claude.json`` to.

        ``account=None`` — the caller declared nothing — contributes NO
        assignment, because no VALUE reproduces the state Claude Code starts from
        when the variable is unset. Unset, it reads the config dir ``~/.claude``
        AND the config file ``~/.claude.json``; ``$HOME`` names the wrong config
        dir; ``$HOME/.claude`` names the right one but moves the config file to
        ``~/.claude/.claude.json``, orphaning the real one. The default is
        therefore stated as the variable's ABSENCE, and the name is in
        :data:`_LAUNCH_ENV_UNSET` so a launching caller scrubs it. The two halves
        are one contract: an empty mapping alone would let an ambient value
        survive, which is the failure this seam exists to remove.

        Any ``CLAUDE_CONFIG_DIR`` already in *env* is ignored in both branches: it
        is the ambient value being replaced, not an input.

        Raises :class:`HarnessError` on a relative ``account``, and on one that
        disagrees with a ``TRAILHEAD_CLAUDE_DIR`` set in *env* — the resolution
        goes through :func:`_refuse_conflicting_config_dirs` rather than around
        it, so this method cannot become a fourth config-dir answer that skips
        the choke point. The default branch asserts no value and so contradicts
        nothing; refusing there would block every undeclared launch on a
        condition no group declaration could clear.
        """
        if account is None:
            return {}
        source = env if env is not None else dict(os.environ)
        config_dir = str(_account_dir(account, source))
        _refuse_conflicting_config_dirs({**source, "CLAUDE_CONFIG_DIR": config_dir})
        return {"CLAUDE_CONFIG_DIR": config_dir}

    def session_start(
        self,
        profile: ResolvedProfile,
        directory: Path,
        prompt: str | None,
        *,
        interactive: bool,
        account: str | None,
        env: dict[str, str] | None = None,
        uid: int | None = None,
        broker: BrokerLaunch | None = None,
        platform: str | None = None,
    ) -> SessionStart:
        """Render *profile* into a confined, never-prompting ``claude`` launch.

        See the section comment above ``_resolve_settings_path`` for how session
        settings merge with the account's user-level settings and what that forces.

        Posture: ``--permission-mode dontAsk`` (a refused action is reported to the
        model and the session carries on); headless adds ``-p`` and
        ``--permission-prompts none`` so nothing waits on an answer. The sandbox is
        enabled with ``failIfUnavailable`` so a sandbox that cannot start fails loudly
        instead of leaving ``dontAsk`` to refuse everything quietly.

        Network narrowing is not enforced: session lists merge with the user-level list,
        so ``allowedDomains`` can never narrow it. The profile's hosts are only checked
        against the user's list (a host it does not cover refuses the launch) and, when
        the profile names any, passed along; a profile naming none writes no key.

        Account binding is :meth:`session_launch_env_set` / :meth:`session_launch_env_unset`
        verbatim. The only side effect is the folder-trust record in the account's
        ``.claude.json`` (``projects[<realpath of directory>].hasTrustDialogAccepted``),
        written after every refusal check has passed. Never execs.

        Git administrative files: for every role in the profile's ``git_guarded_roles`` (the
        hub's is the lore state root, which holds each vault as a git working tree),
        ``config``, ``hooks`` and ``info`` of every repository under it join ``denyWrite``
        (see :func:`_git_admin_denials`). Claude Code's own protection of ``.git/config``
        and ``.git/hooks`` covers only the session's working directory, so without this a
        prompt-injected hub could plant a git filter, ``core.fsmonitor``, ``core.pager``,
        ``alias.*`` or a hook that Outpost, ``lore sync`` or the operator's own git would
        later run unsandboxed. Objects, refs, the index, ``COMMIT_EDITMSG`` and logs stay
        writable, so committing records through the ``lore`` CLI works. Repositories are
        enumerated at launch, and the glob ``<root>/*/*/.git/<name>`` covers a vault
        created later at ``vaults/<name>``; a repository created later anywhere else, or
        deeper than the scan bound, is not covered. Anything that must write a vault's
        ``.git/config`` (``git config``, ``push --set-upstream``, ``remote add``, a fresh
        ``git init`` or clone) is refused inside the hub; the operator runs it outside.

        The tmux socket directory (``$TMUX_TMPDIR`` from *env*, else ``/tmp``, then
        ``tmux-<uid>``; *uid* defaults to the process's) joins ``denyWrite`` under the
        conditions :func:`_derive_write_confinement` states.

        *directory* must be an existing directory: a launcher that cannot enter it (tmux
        falls back to ``$HOME``) would run the session in a directory the sandbox always
        lets it write. A missing or non-directory *directory* is refused before anything
        is written.

        Broker server: a profile naming :data:`BROKER_SERVER` is rendered as an HTTP MCP
        server in a config file delivered by ``--mcp-config <path>`` with
        ``--strict-mcp-config``; the file comes back in ``SessionStart.files`` for the
        launcher to write, and its definition never appears on argv. The server's
        Authorization header comes from a headers helper, which the harness runs outside
        the Bash sandbox with the operator's access, so the file must be unreadable and
        unwritable by the session: it lives in *broker*'s config path, under Outpost's
        state directory, which the profile's ``token-store`` role denies for reading and
        this method denies for writing. The broker tools are allowed by exact name
        (``mcp__<server>__<tool>``) so ``dontAsk`` does not refuse them. A profile naming
        the server without a *broker* is refused.

        Network narrowing: ``sandbox.network.deniedDomains`` accumulates across settings
        scopes and narrows an allow-list, so every user-level allowed domain the profile
        neither names nor covers a named host with is denied. A user wildcard that covers
        a named host stays, since denying it would deny that host too.

        Settings sources: the user-level file and the platform's managed
        settings (``/etc/claude-code/managed-settings.json`` then ``managed-settings.d/*.json``
        on Linux, the same under ``/Library/Application Support/ClaudeCode`` on macOS; *platform*
        defaults to ``sys.platform``) are read and merged; a managed denial a profile root falls
        under refuses the launch like a user one. Project and local settings in the session directory are not read (the
        session cannot write ``<directory>/.claude``; the operator owns what is there), and
        server-managed or MDM policy is not visible to a file read. A user with no
        ``sandbox.filesystem.allowWrite`` allows only the working directory.

        Headers helper code: a launch whose helper (``bin/trailhead``, the ``trailhead``
        package, ``pyproject.toml`` beside it, or any entry of PATH) overlaps any path the
        session may write (profile roots, the session directory, system temp) is refused,
        and so is a PATH entry that is relative or empty, since the helper runs outside the
        sandbox with the operator's access. A broker launch emits the PATH it checked in
        ``env_set`` so the session runs with exactly that PATH.

        File tools and the sandbox: the Bash sandbox does not apply to Claude's file tools, so a
        user or managed ``Edit``/``Write`` allow or ``additionalDirectories`` entry could let the
        hub rewrite the helper's code. A broker launch therefore renders ``Edit`` deny rules over
        the helper's code and Outpost's state (see :func:`_edit_denials`); deny beats allow across
        scopes. Managed ``sandbox.enabled: false``, ``allowUnsandboxedCommands: true``,
        ``filesystem.disabled``, ``allowLocalBinding``, ``allowAllUnixSockets``, or any
        ``allowUnixSockets`` entry refuse the launch (see :func:`_refuse_unconfinable_sandbox`);
        the same flags set true in the user file are rendered false.

        Raises :class:`HarnessError` for a *directory* that is not an existing directory,
        a profile naming the broker server with no *broker*, a profile that would widen
        the user-level settings, an unreadable or malformed user-level settings or
        ``.claude.json``, a profile that may prompt, and a prompt that would parse as a flag.
        """
        if not profile.profile.never_prompt:
            raise HarnessError("session_start only starts never-prompt profiles")
        if prompt and prompt.startswith("-"):
            raise HarnessError("the prompt starts with '-', so it would be read as a flag")
        if not Path(directory).is_dir():
            raise HarnessError(
                f"the hub home {directory} is not an existing directory; "
                "run `ranger setup --account <A>` for this hub's account to create it"
            )
        wants_broker = BROKER_SERVER in profile.profile.mcp_servers
        if wants_broker and broker is None:
            raise HarnessError(
                f"the profile names the {BROKER_SERVER!r} broker server but no broker session "
                "was given; refusing to start a session without it"
            )
        source = env if env is not None else dict(os.environ)
        env_set = self.session_launch_env_set(account, env=source)
        env_unset = self.session_launch_env_unset()
        config_dir = env_set.get("CLAUDE_CONFIG_DIR")
        home = _account_home(source)
        launch_dir = Path(os.path.realpath(directory))
        user = _UserSettings(
            (Path(config_dir) if config_dir else home / ".claude") / _SETTINGS_FILENAME,
            home,
            launch_dir,
        )
        for managed in _managed_settings(home, launch_dir, platform):
            user.absorb(managed)
        _refuse_unconfinable_sandbox(user)
        roots = [Path(os.path.realpath(p)) for p in profile.writable]
        if wants_broker:
            _refuse_helper_code_under_write_roots(
                broker.header_command, source, [*roots, launch_dir, *sorted(_scratch_dirs())]
            )
            if "PATH" in source:
                env_set = {**env_set, "PATH": source["PATH"]}
        deny_write = [
            str(launch_dir / ".claude"),
            str(launch_dir / ".mcp.json"),
            *([os.path.realpath(Path(broker.config_path).parent)] if wants_broker else []),
            *_derive_write_confinement(
                user, roots, _tmux_socket_dir(source, os.getuid() if uid is None else uid)
            ),
            *_git_admin_denials(
                [Path(os.path.realpath(profile.paths[r])) for r in profile.profile.git_guarded_roles],
                roots,
            ),
        ]
        hosts = profile.profile.network_allowed_hosts
        _check_reads_and_network(user, roots, hosts)

        denied_reads = [Path(os.path.realpath(p)) for p in profile.read_denied]
        settings: dict = {
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "allowUnsandboxedCommands": False,
                "filesystem": {
                    "allowWrite": [str(r) for r in roots],
                    "denyWrite": deny_write,
                    "denyRead": [str(p) for p in denied_reads],
                },
            },
            "permissions": {
                "deny": [f"Read(/{p}/**)" for p in denied_reads]
                + (
                    _edit_denials(
                        user,
                        broker.header_command,
                        source,
                        [*denied_reads, Path(os.path.realpath(Path(broker.config_path).parent))],
                    )
                    if wants_broker
                    else []
                )
                + [f"Bash({command})" for command in user.excluded_commands]
            },
            "enabledMcpjsonServers": [n for n in profile.profile.mcp_servers if n != BROKER_SERVER],
        }
        network: dict = {}
        if hosts:
            network["allowedDomains"] = list(hosts)
        denied_domains = [
            d for d in (user.allowed_domains or []) if not any(_host_allowed(h, [d]) for h in hosts)
        ]
        if denied_domains:
            network["deniedDomains"] = denied_domains
        if network:
            settings["sandbox"]["network"] = network
        for label in _PINNED_OFF:
            if user.sandbox_flags.get(label, (False,))[0]:
                _, *parents, key = label.split(".")
                holder = settings["sandbox"]
                for parent in parents:
                    holder = holder.setdefault(parent, {})
                holder[key] = False
        files: tuple[SessionFile, ...] = ()
        if wants_broker:
            config = {
                "mcpServers": {
                    broker.server: {
                        "type": "http",
                        "url": broker.url,
                        "headersHelper": broker.header_shell_command,
                    }
                }
            }
            files = (
                SessionFile(
                    path=broker.config_path,
                    content=json.dumps(config, sort_keys=True, indent=2) + "\n",
                    mode="0600",
                ),
            )
            settings["permissions"]["allow"] = [f"mcp__{broker.server}__{t}" for t in broker.tools]
        if profile.profile.model is not None:
            settings["model"] = profile.profile.model

        config_file = (
            Path(config_dir) / _CONFIG_FILENAME
            if config_dir
            else claude_config_file({k: v for k, v in source.items() if k != "CLAUDE_CONFIG_DIR"})
        )
        _seed_folder_trust(config_file, launch_dir)

        argv = ["claude"]
        if not interactive:
            argv.append("-p")
        argv += ["--settings", json.dumps(settings, sort_keys=True, separators=(",", ":"))]
        argv += ["--permission-mode", "dontAsk"]
        if files:
            argv += ["--mcp-config", files[0].path, "--strict-mcp-config"]
        if not interactive:
            argv += ["--permission-prompts", "none"]
        if profile.profile.effort is not None:
            argv += ["--effort", profile.profile.effort]
        if prompt:
            argv.append(prompt)
        return SessionStart(argv=argv, env_set=env_set, env_unset=env_unset, files=files)

    def session_launch_account_identity(
        self, account: str | None, *, env: dict[str, str] | None = None
    ) -> AccountIdentity:
        """Identity and configuration-existence for *account*, from ONE resolution.

        For a declared account, reuses :meth:`session_launch_env_set` itself to
        find the config dir — so a declaration this harness would refuse when
        actually binding a session (a relative path, a control character, a
        conflicting ``TRAILHEAD_CLAUDE_DIR``/``CLAUDE_CONFIG_DIR`` pair) raises
        here too, rather than reporting an identity the binding call would then
        contradict.

        For the default (``account=None``), :meth:`session_launch_env_set`
        contributes no assignment — the default is stated as an absence, not a
        value — so this resolves through :func:`claude_config_file` instead:
        the same ``CLAUDE_CONFIG_DIR`` → ``HOME``/``USERPROFILE`` → real-home
        precedence :func:`_account_home` already uses for the declared branch,
        and DELIBERATELY not ``_claude_dir``, which honours
        ``TRAILHEAD_CLAUDE_DIR`` — a directory neither the trust pre-seed nor
        the launched process itself ever reads for the default account. Using
        ``_claude_dir`` here would name an identity the launch does not
        actually land on.

        ``label`` is the config FILE path (``.claude.json``) — the one thing
        genuinely in the same namespace for both branches. A declared account's
        config file always lives inside its config directory, but the default's
        does not: ``$HOME/.claude.json`` sits beside ``$HOME/.claude``, not
        inside it, so a directory-shaped label would answer the two branches in
        different namespaces (the default's `HOME`, a declared account's config
        dir) and let two different accounts print the same string. The file
        path is also simply the thing that decides which account a session is
        on, so a declared account with ``account = "~"`` and the undeclared
        default legitimately share a label: both resolve to the same
        ``$HOME/.claude.json`` and so ARE the same account. ``has_config`` is
        whether that file exists.
        """
        if account is None:
            config_file = claude_config_file(env)
        else:
            binding = self.session_launch_env_set(account, env=env)
            config_file = claude_config_file(binding)
        return AccountIdentity(label=str(config_file), has_config=config_file.exists())

    def session_launch_account_authentication(
        self, account: str | None, *, env: dict[str, str] | None = None
    ) -> AccountAuthentication:
        """Read authentication from the account's credentials file.

        For a declared account, reuses :meth:`session_launch_env_set` to find
        the config dir — the same resolution the launch binding itself uses,
        so a declaration that binding would refuse (a relative path, a
        control character, a conflicting ``TRAILHEAD_CLAUDE_DIR`` /
        ``CLAUDE_CONFIG_DIR`` pair) raises here too, rather than reporting an
        authentication verdict for an account the launch would never bind to.

        For the default (``account=None``), resolves through
        :func:`_default_account_credentials_dir` — the same
        ``CLAUDE_CONFIG_DIR`` → ``HOME``/``USERPROFILE`` → real-home
        precedence :func:`claude_config_file` uses, and deliberately not
        ``_claude_dir``, for the same reason
        :meth:`session_launch_account_identity` avoids it: ``_claude_dir``
        honors ``TRAILHEAD_CLAUDE_DIR``, a trailhead-only test seam the real
        launched session never reads for the default account.

        See :func:`_read_account_authentication` for the signal itself.
        """
        source = env if env is not None else dict(os.environ)
        if account is None:
            config_dir = _default_account_credentials_dir(source)
        else:
            binding = self.session_launch_env_set(account, env=source)
            config_dir = Path(binding["CLAUDE_CONFIG_DIR"])
        return _read_account_authentication(config_dir)

    def eval_runner(
        self,
        *,
        plugin_dir: Path,
        case: str,
        runs: int,
        model: str,
        allow_tools: list[str],
        max_cost_usd: float,
        json_path: Path,
        output_dir: Path,
        env: dict[str, str],
        eval_dir: str | None = None,
        tmpdir: Path | None = None,
    ) -> EvalRunner:
        """``claude plugin eval`` for one case, with an allowlist-only environment.

        ``--allow-tools`` is variadic, so it is followed by another flag. Always
        invoke through a stub runner in tests — never the real ``claude``.

        ``eval_dir`` names the directory below the plugin that holds the cases
        (``--eval-dir``); omitted, the runner's own default applies.

        ``tmpdir`` becomes the child's ``TMPDIR``, the one variable set rather
        than inherited: the runner's sandbox could not write when its temp dir
        was under ``/tmp`` on the tested Linux host, so the caller supplies a
        directory elsewhere. The parent's ``TMPDIR`` is never forwarded.
        """
        argv = [
            "claude", "plugin", "eval", str(plugin_dir),
            "--case", case,
            "--scaffold",
            "--allow-tools", *allow_tools,
            "--ablation", "none",
            "--trust-plugin",
            "--runs", str(runs),
            "-j", str(min(runs, _EVAL_MAX_PARALLEL)),
            "--model", model,
            "--max-cost-usd", str(max_cost_usd),
            "--json", str(json_path),
            "--output-dir", str(output_dir) + "/",
            "--no-publish",
        ]
        if eval_dir is not None:
            argv += ["--eval-dir", eval_dir]
        child = {
            k: v
            for k, v in env.items()
            if k in _EVAL_ENV_ALLOWLIST or k.startswith(_EVAL_ENV_PREFIXES)
        }
        if tmpdir is not None:
            child["TMPDIR"] = str(tmpdir)
        return EvalRunner(argv=argv, env=child)

    def session_enumerate(self, workspace: Path | None = None) -> list[str]:
        """Return ``["claude", "agents", "--json"]``, plus ``--cwd <workspace>``.

        Raises :class:`HarnessError` on a ``workspace`` whose string form
        begins with ``-``: it would land in the value slot right after
        ``--cwd`` and read as a flag, silently unscoping the enumeration —
        or worse, being parsed as a real CLI flag. This is argv safety only,
        NOT filesystem validation: a nonexistent workspace still returns argv.
        Beyond the leading dash the value is
        passed through as given, so an argv from this seam is safe to exec but
        is NOT guaranteed free of shell-active characters the way a
        guard-checked ``session_id`` is — do not hand it to a shell.

        ``workspace`` is passed to ``--cwd`` unresolved (as given) while
        :meth:`parse_session_list` resolves each record's ``cwd`` before
        comparison — a caller wanting exact prefix-match behavior against a
        symlinked workspace root must pass an already-resolved path here.
        """
        args = ["claude", "agents", "--json"]
        if workspace is not None:
            as_arg = str(workspace)
            if as_arg.startswith("-"):
                raise HarnessError(
                    f"session_enumerate: workspace would read as a flag in "
                    f"argv: {as_arg!r}"
                )
            args += ["--cwd", as_arg]
        return args

    def parse_session_list(self, output: str) -> list[SessionRecord]:
        """Parse ``claude agents --json`` output into :class:`SessionRecord` values.

        See the base contract for the full failure semantics. Every raised
        :class:`HarnessError` names the offending field (or the decode failure)
        and includes a bounded excerpt of the raw output — bounded across the
        WHOLE payload, not per field, since a ``cwd`` carries an absolute path
        (often including the username) that must never spill unbounded into
        terminal scrollback or logs.
        """
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            raise HarnessError(
                f"claude agents --json: failed to decode output: {_excerpt(output)}"
            ) from None

        if not isinstance(data, list):
            raise HarnessError(
                f"claude agents --json: expected a JSON array, got "
                f"{type(data).__name__}: {_excerpt(output)}"
            )

        records = []
        for record in data:
            if not isinstance(record, dict):
                raise HarnessError(
                    f"claude agents --json: expected a JSON object per record, got "
                    f"{type(record).__name__}: {_excerpt(output)}"
                )

            session_id = record.get("sessionId")
            if not _is_session_id(session_id):
                raise HarnessError(
                    f"claude agents --json: record has missing or invalid "
                    f"'sessionId': {_excerpt(output)}"
                )

            raw_cwd = record.get("cwd")
            if not isinstance(raw_cwd, str) or not raw_cwd:
                raise HarnessError(
                    f"claude agents --json: record has missing or invalid "
                    f"'cwd': {_excerpt(output)}"
                )
            cwd = Path(raw_cwd).resolve()

            kind = record.get("kind")
            if not isinstance(kind, str) or not kind:
                raise HarnessError(
                    f"claude agents --json: record has missing or invalid "
                    f"'kind': {_excerpt(output)}"
                )

            name = record.get("name")
            if not isinstance(name, str):
                name = None

            pid = record.get("pid")
            if isinstance(pid, bool) or not isinstance(pid, int):
                pid = None

            started_at_raw = record.get("startedAt")
            started_at = None
            if isinstance(started_at_raw, (int, float)) and not isinstance(
                started_at_raw, bool
            ):
                try:
                    started_at = datetime.fromtimestamp(
                        started_at_raw / 1000, tz=timezone.utc
                    )
                except (ValueError, OverflowError):
                    # DEGRADE, don't raise.  ``startedAt`` is an OPTIONAL field,
                    # so the base contract maps an unusable value to None rather
                    # than discarding the record — and with it every well-formed
                    # record in the same payload.  The values that land here are
                    # exactly the schema drift the spec anticipates: a CLI that
                    # starts emitting epoch MICROS or NANOS instead of millis, or
                    # a bare Infinity/NaN (which ``json.loads`` accepts).
                    # Degrading turns that into "sessions with unknown start
                    # times" instead of "no sessions at all".
                    started_at = None

            records.append(
                SessionRecord(
                    session_id=session_id,
                    cwd=cwd,
                    kind=kind,
                    controllable=kind == "interactive",
                    name=name,
                    pid=pid,
                    started_at=started_at,
                )
            )

        return records
