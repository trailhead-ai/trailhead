"""Claude Code harness implementation.

This module owns **everything** Claude-Code-specific about installing trailhead's
composed plugin trees: the Shape-A ``marketplace.json`` writer, the
``claude plugin …`` CLI calls, and the on-disk registration markers.  Nothing
Claude-Code-specific lives outside this file (Axiom 1) — the shared
install/compose/wire/doctor/uninstall path talks to it only through the generic
:class:`~trailhead.harness.base.Harness` interface.

Registration markers (single source of truth)
----------------------------------------------
Markers live in ``composed_root`` (NOT inside ``plugins/<tool>/``, which the
atomic promote ``rmtree``s on every re-wire).  They are this harness's on-disk
truth, written only AFTER the corresponding CLI step succeeds:

- global ``.trailhead-registered`` — the marketplace has been added.  A
  skip-optimisation for :meth:`register` (the call itself is idempotent).
- per-tool ``.trailhead-installed-<tool>`` — the tool has been installed.  A
  self-heal signal for the ``wire()`` loop: a missing marker means
  :meth:`install_tool` should run; a present marker means :meth:`rewire_tool`.

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
it is only exercised in live-session dogfood runs.  This harness NEVER writes to
``~/.claude/plugins/`` directly — the CLI manages ``known_marketplaces.json`` and
the plugin cache; this harness only generates ``marketplace.json`` under the
``state_dir``-rooted ``composed_root``.

Shape-A marketplace.json
-------------------------
The generated marketplace.json follows Shape A (validated live via
``claude plugin validate``): one marketplace named ``trailhead`` with one
``plugins[]`` entry per tool, each sourced at ``./plugins/<tool>``.  All tools
share one marketplace root (``composed_root``); plugin trees live at
``composed_root/plugins/<tool>/``.

Detection: Claude Code is considered present when ``~/.claude`` exists.  Tests
may redirect this with the ``TRAILHEAD_CLAUDE_DIR`` env override.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from trailhead.harness.base import (
    MODALITY_TTY_REQUIRED,
    Harness,
    HarnessError,
    Modality,
    SessionRecord,
    SessionTranscript,
)

_REGISTERED_MARKER = ".trailhead-registered"
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


#: Bounded head-scan limits for extracting a session's start cwd out of a
#: transcript (see :func:`_extract_transcript_cwd`). Real transcripts run to
#: hundreds of megabytes, so this seam must never read one in full:
#: ``_CWD_SCAN_MAX_LINES`` bounds how many leading records are inspected, and
#: ``_CWD_SCAN_MAX_LINE_BYTES`` skips any single record too large to be a
#: plausible cwd-bearing header line rather than paying to decode it.
_CWD_SCAN_MAX_LINES = 12
_CWD_SCAN_MAX_LINE_BYTES = 1_000_000


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


def _extract_transcript_cwd(path: Path) -> Path | None:
    """Bounded head-scan for the cwd recorded near the start of a transcript.

    Reads at most ``_CWD_SCAN_MAX_LINES`` lines, JSON-decoding each in turn,
    and returns the first decoded dict's string ``cwd``. A line longer than
    ``_CWD_SCAN_MAX_LINE_BYTES`` is skipped without being decoded, so a huge
    early line can never win over a plain one further down within the window.

    The cap bounds the READ, not just the decode: no single call takes more
    than ``_CWD_SCAN_MAX_LINE_BYTES + 1`` bytes, so a corrupt transcript
    carrying no newline at all costs bounded memory instead of its full size.

    Returns ``None`` — never raises — when the file cannot be opened, no line
    within the window decodes to a JSON object, or no decoded object carries a
    non-empty string ``cwd``. This is the seam's only source of an
    "unreadable/undecodable transcript" outcome; the caller turns that into a
    row with ``cwd=None`` rather than dropping the row.
    """
    try:
        with path.open("rb") as f:
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
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    cwd = record.get("cwd")
                    if isinstance(cwd, str) and cwd:
                        return Path(cwd)
    except OSError:
        return None
    return None


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


#: Env markers Claude Code sets for its own child sessions.  Verified twice
#: (v2.1.229 / v2.1.232) that a session launched with ``CLAUDE_CODE_CHILD_SESSION``
#: inherited runs and connects remote-control but NEVER appears in
#: ``claude agents --json`` — and the leaked env also carries the parent's live
#: session access token, so scrubbing this list is a credential-hygiene
#: requirement, not merely an enumeration fix.  This is a FLOOR, not an
#: exhaustive guarantee (see the base contract).  Applying the scrub is the
#: exec-owning caller's job, done at spawn time — this seam only names them.
_LAUNCH_ENV_UNSET = [
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ACCESS_TOKEN",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
]

#: The user-level settings file under the Claude dir, and the top-level key in it
#: that sets how many days a session transcript is kept before cleanup.  Claude
#: Code's own default when the key is absent is 30 days (minimum accepted: 1).
_SETTINGS_FILENAME = "settings.json"
_CLEANUP_PERIOD_KEY = "cleanupPeriodDays"
_DEFAULT_CLEANUP_PERIOD_DAYS = 30

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


class ClaudeCodeHarness(Harness):
    """Install agent-plugins into Claude Code via the ``claude plugin`` CLI."""

    name = "claude_code"

    @classmethod
    def detect(cls, env: dict[str, str]) -> bool:
        return _claude_dir(env).is_dir()

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

    def is_registered(self, composed_root: Path) -> bool:
        return (composed_root / _REGISTERED_MARKER).exists()

    def is_installed(self, tool: str, composed_root: Path) -> bool:
        return (composed_root / f"{_INSTALLED_MARKER_PREFIX}{tool}").exists()

    def installed_tools(self, composed_root: Path) -> list[str]:
        """Return the sorted tool names recorded installed under composed_root."""
        if not composed_root.is_dir():
            return []
        return sorted(
            f.name[len(_INSTALLED_MARKER_PREFIX) :]
            for f in composed_root.iterdir()
            if f.is_file() and f.name.startswith(_INSTALLED_MARKER_PREFIX)
        )

    # -- install / uninstall --------------------------------------------------

    def register(self, composed_root: Path, *, runner=None) -> None:
        """Register the consolidated trailhead marketplace via the harness CLI.

        Shells ``claude plugin marketplace add --scope user <composed_root>``
        (idempotent) and writes the global ``.trailhead-registered`` marker only
        after the CLI call succeeds.
        """
        _run = runner or _default_runner
        _run(
            ["claude", "plugin", "marketplace", "add", "--scope", "user", str(composed_root)]
        )
        (composed_root / _REGISTERED_MARKER).write_text("{}")

    def install_tool(self, tool: str, composed_root: Path, *, runner=None) -> None:
        """Install a tool from the consolidated trailhead marketplace.

        Shells ``claude plugin install <tool>@trailhead --scope user`` and writes
        the per-tool ``.trailhead-installed-<tool>`` marker only after success.
        """
        _validate_tool(tool)
        _run = runner or _default_runner
        _run(["claude", "plugin", "install", f"{tool}@trailhead", "--scope", "user"])
        (composed_root / f"{_INSTALLED_MARKER_PREFIX}{tool}").write_text("{}")

    def rewire_tool(self, tool: str, composed_root: Path, *, runner=None) -> None:
        """Refresh an already-installed tool after recomposition.

        Sequence: **uninstall THEN install** (NOT ``plugin update``, which is
        version-keyed and keeps stale content when the version is static).  The
        per-tool marker is cleared before the pair and rewritten only after install
        succeeds (self-heal — a failure mid-pair leaves the marker absent so the
        next wire re-attempts cleanly).  A failing uninstall (``not installed``) is
        tolerated; the install must still run.
        """
        _validate_tool(tool)
        _run = runner or _default_runner

        marker = composed_root / f"{_INSTALLED_MARKER_PREFIX}{tool}"
        marker.unlink(missing_ok=True)

        try:
            _run(["claude", "plugin", "uninstall", f"{tool}@trailhead", "--scope", "user"])
        except Exception:
            pass

        _run(["claude", "plugin", "install", f"{tool}@trailhead", "--scope", "user"])
        marker.write_text("{}")

    def unregister_tool(self, tool: str, composed_root: Path, *, runner=None) -> None:
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
                ]
            )
        finally:
            (composed_root / f"{_INSTALLED_MARKER_PREFIX}{tool}").unlink(missing_ok=True)

    def unregister_marketplace(self, composed_root: Path, *, runner=None) -> None:
        """Remove the shared ``trailhead`` marketplace (inverse of :meth:`register`).

        Called **once** after every tool has been uninstalled — NEVER per-tool,
        since a single marketplace is shared across all tools.  Shells
        ``claude plugin marketplace remove trailhead --scope user`` and clears the
        global marker in ``finally`` so a half-removed state never reads as
        registered.
        """
        _run = runner or _default_runner
        try:
            _run(
                ["claude", "plugin", "marketplace", "remove", "trailhead", "--scope", "user"]
            )
        finally:
            (composed_root / _REGISTERED_MARKER).unlink(missing_ok=True)

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
        munged = str(Path(workspace).resolve()).replace("/", "-").replace(".", "-")
        candidate = _claude_dir(_env) / _PROJECTS_SUBDIR / munged / f"{session_id}.jsonl"
        return candidate if candidate.is_file() else None

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
    # returns always comes from :func:`_extract_transcript_cwd` reading the
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

            raw_cwd = _extract_transcript_cwd(candidate)
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
                )
            )

        return rows

    # -- session retention ----------------------------------------------------
    #
    # Claude Code deletes transcripts older than the top-level
    # ``cleanupPeriodDays`` settings key (minimum 1); when the key is unset its
    # own default is 30 days.  The key may also appear in project, local, and
    # managed settings, which override the user file — but those are per-project
    # and this seam is asked machine-globally (`camp sessions --recoverable` spans
    # every workspace), so the USER settings file is the one source read
    # here.  A project that shortens its own window is therefore reported
    # optimistically; the warning is advisory, and over-warning every project
    # from one project's setting would be worse.

    def session_retention_days(self, *, env: dict[str, str] | None = None) -> int | None:
        """Return the transcript-retention window in days (never ``None``).

        Anything unreadable, absent, or not a positive int falls back to Claude
        Code's documented 30-day default: a caller asking for a retention hint
        must not be handed an exception, and "no setting" genuinely means 30.
        """
        _env = env if env is not None else dict(os.environ)
        settings = _claude_dir(_env) / _SETTINGS_FILENAME
        try:
            data = json.loads(settings.read_text())
        except (OSError, ValueError):
            return _DEFAULT_CLEANUP_PERIOD_DAYS
        if not isinstance(data, dict):
            return _DEFAULT_CLEANUP_PERIOD_DAYS
        value = data.get(_CLEANUP_PERIOD_KEY)
        # bool is an int subclass — `true` is a malformed value, not a 1-day window.
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return _DEFAULT_CLEANUP_PERIOD_DAYS
        return value

    def session_retention_setting(self) -> str | None:
        """The settings key a user raises to keep transcripts longer."""
        return _CLEANUP_PERIOD_KEY

    # -- session launch & enumeration ------------------------------------------
    #
    # ``claude agents --json`` lists this machine's Claude Code sessions.
    # ``--cwd`` is the CLI's native started-under PREFIX filter (verified
    # empirically 2026-08-14) — exactly the "rooted under" scoping the base
    # seam documents, so it is passed straight through rather than re-derived.
    #
    # ``claude --remote-control --session-id <id>`` starts a brand-new session
    # under the caller-chosen id (confirmed by an operator TTY check on
    # 2026-08-14: the CLI honors a supplied id and it enumerates under exactly
    # that id in ``claude agents --json``; the CLI's own help text calls the
    # flag "reattach", but a fresh id observably creates). ``--name <name>``
    # sets the session's visible name — both the ``name`` field in
    # ``claude agents --json`` and the label the claude.ai/code clients render
    # (verified live 2026-08-19: both flags together create a new session
    # under the supplied id carrying the supplied name). Without ``--name``,
    # the derived name is the relay default (hostname-prefixed) on the client
    # surface and the cwd basename plus two hex characters locally.
    #
    # Deliberately absent from the argv, each for a verified reason:
    #
    # - no ``--bg`` — it silently discards ``--remote-control`` and yields
    #   ``kind: background`` instead of a controllable session.
    # - no workspace path — Claude Code roots a launched session on the
    #   process's cwd, which the exec-owning caller sets; ``workspace`` is
    #   accepted for the seam signature and is unused here.

    def session_launch(
        self,
        workspace: Path,
        session_id: str,
        *,
        session_name: str | None = None,
    ) -> list[str]:
        """Return ``["claude", "--remote-control", "--session-id", <session-id>]``,
        plus ``["--name", <session-name>]`` when a name is requested.

        Raises :class:`HarnessError` on a malformed ``session_id`` — see the
        base contract's DIVERGES note: this is the one seam here that raises
        instead of degrading to ``None`` on bad input, since launch is
        constant-valued and ``None`` is reserved for "cannot launch at all".

        ``session_name`` is held to the same inert-token predicate as
        ``session_id``: it lands in the same argv, so a separator, an empty
        string, or a leading dash is the same flag-injection surface and
        raises the same way.
        """
        if not _is_session_id(session_id):
            raise HarnessError(f"session_launch: invalid session_id: {session_id!r}")
        argv = ["claude", "--remote-control", "--session-id", session_id]
        if session_name is not None:
            if not _is_session_id(session_name):
                raise HarnessError(
                    f"session_launch: invalid session_name: {session_name!r}"
                )
            argv += ["--name", session_name]
        return argv

    def session_launch_modality(self) -> Modality:
        """Claude Code launch requires a TTY (interactive terminal)."""
        return MODALITY_TTY_REQUIRED

    def session_launch_env_unset(self) -> list[str]:
        """Env var names a launching caller must scrub before spawning.

        See :data:`_LAUNCH_ENV_UNSET` for why each name is here.
        """
        return list(_LAUNCH_ENV_UNSET)

    def session_enumerate(self, workspace: Path | None = None) -> list[str]:
        """Return ``["claude", "agents", "--json"]``, plus ``--cwd <workspace>``.

        Raises :class:`HarnessError` on a ``workspace`` whose string form
        begins with ``-``: it would land in the value slot right after
        ``--cwd`` and read as a flag, silently unscoping the enumeration —
        or worse, being parsed as a real CLI flag. This is argv safety only,
        NOT filesystem validation: a nonexistent workspace still returns argv,
        matching :meth:`session_launch`. Beyond the leading dash the value is
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
