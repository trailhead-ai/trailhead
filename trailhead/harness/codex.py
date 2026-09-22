"""Codex harness implementation — the read path of the trailhead Harness seam.

This module owns everything Codex-specific about being recognised through the
trailhead :class:`~trailhead.harness.base.Harness` seam (Axiom 1). Nothing
Codex-specific lives outside this file — the shared install/compose/wire/
doctor path talks to it only through the generic interface. Nothing in this
module writes into a Codex home: every function here only reads Codex's own
on-disk state, or returns argv/env for a caller to spawn.

Home resolution
----------------
Codex's own home directory is resolved by ``codex_home``: ``CODEX_HOME`` when
set (must be absolute), else ``HOME``/``USERPROFILE`` joined with ``.codex`` —
the same variable Codex itself reads, so trailhead needs no Codex-only test
seam the way it does for Claude Code's ``TRAILHEAD_CLAUDE_DIR``. This resolver
NEVER falls back to :func:`pathlib.Path.home`: a caller must inject an
environment (real or test-pinned) explicitly, so a missing injection fails
loudly instead of silently reading (or, worse, writing under) the operator's
real Codex home. Every other resolver in this module — transcript listing,
transcript resolution, the lister subprocess, the launch-env binding — goes
through this one choke point.

Detection
---------
``CodexHarness.detect`` is true when either signal is present: a ``codex``
executable on the given environment's ``PATH``, or a ``config.toml`` file
under the resolved Codex home. A bare, empty home directory (no
``config.toml``) is NOT detected — Codex has never been configured there.

Transcript store and the session-id guard
-------------------------------------------
Rollouts live at ``<codex-home>/sessions/YYYY/MM/DD/rollout-<timestamp>-
<thread-id>[_<rollout-id>].jsonl`` (optionally ``.jsonl.zst``). A thread id
extracted from a filename is accepted only when it also passes
``_is_session_id`` — a single, inert path component (mirroring the guard
Claude Code's harness applies to its own session ids) — before it is ever
joined onto the sessions root or handed to a caller's argv; neither a
traversal segment (``..``) nor a path separator can reach either. A rollout
whose id fails the guard yields no row at all, never a row with a rejected id.

``session_transcripts`` recovers each row's ``cwd`` with a single bounded
read: ``_read_session_meta_payload`` performs one capped ``readline`` on the
rollout's first line, so an oversized or newline-less line — or a corrupt or
hostile file — stops there without ever touching a second line. A ``.zst``
file, an unreadable file, or one whose first line isn't a decodable
``session_meta`` envelope still yields a row, with ``cwd=None``, rather than
raising or being skipped.

Live enumeration
----------------
Codex ships no non-interactive session lister of its own (``codex agents`` is
a TUI browser), so ``session_enumerate`` returns the argv that runs
:mod:`trailhead.harness.codex_sessions` as a subprocess (``sys.executable -m
trailhead.harness.codex_sessions``). That module resolves the Codex home from
its own process environment through the same ``codex_home`` choke point and
prints one JSON array of the threads whose
``<home>/thread-writer-locks/<thread-id>.lock`` is currently held; its
docstring owns the fail-closed liveness contract. ``parse_session_list``
decodes that subprocess's stdout under the base contract.

Session launch
--------------
``session_launch`` returns ``["codex", "--cd", <workspace>]`` — Codex's
interactive launch offers no flag for a caller-chosen session id, session
name, or an additional settings file, so ``session_id``/``session_name``/
``settings_path`` are validated for argv safety (the same inert-token guard
``session_id`` gets everywhere else in this seam) and then ignored; the argv
never varies with them. ``session_launch_modality`` reports
``tty-required``: Codex's CLI is an interactive terminal program.

``session_launch_env_unset`` names ``CODEX_HOME`` (the variable this seam
itself uses to redirect a session's home) plus every environment variable a
running Codex session has been observed injecting into its own child
processes, as a floor that may grow in a later Codex version.

``session_launch_env_set`` binds a launched session to an account by naming
its Codex home directory. An account is a ``~``-relative or absolute path,
expanded against the given environment's ``HOME``/``USERPROFILE`` (never the
machine's) exactly the way Claude Code's own declared-account resolution
works, refused for a relative value or a control character, and refused when
the environment's own ambient ``CODEX_HOME`` already names a different
directory — naming both, so a caller sees which one to fix.

Install surface
----------------
Every registration/install method on this class (``generate_manifest``,
``register``, ``install_tool``, ``rewire_tool``, ``unregister_tool``,
``unregister_marketplace``, ``is_registered``, ``is_installed``,
``installed_tools``) is a no-op that reports nothing installed. ``trailhead
doctor`` therefore reports Codex as detected with nothing installed — the
honest state this seam's read path can report today, never a silent write to
a harness that can't yet read it, and never a raised error that would abort
``trailhead install``/``trailhead update`` on any machine with Codex detected.

Every other seam method (identity, authentication) stays at the base class's
default too; this module implements detection, the transcript store, live
enumeration, and the launch quartet, and nothing beyond them.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from trailhead.harness.base import (
    MODALITY_TTY_REQUIRED,
    Harness,
    HarnessError,
    Modality,
    SessionRecord,
    SessionTranscript,
)
from trailhead.harness.claude_code import _excerpt

#: The file whose presence under a Codex home means Codex has been configured
#: there — read by :meth:`CodexHarness.detect` as the "home has state" signal.
_CODEX_CONFIG_FILENAME = "config.toml"

#: The executable name looked up on the given environment's ``PATH`` — the
#: "Codex CLI is installed" signal for :meth:`CodexHarness.detect`.
_CODEX_EXECUTABLE_NAME = "codex"

#: The directory under ``HOME``/``USERPROFILE`` Codex uses as its home when
#: ``CODEX_HOME`` is unset — :func:`codex_home`'s fallback.
_CODEX_HOME_SUBDIR = ".codex"

#: The directory under a Codex home holding rollout transcripts, laid out as
#: ``sessions/YYYY/MM/DD/rollout-<timestamp>-<thread-id>[_<rollout-id>].jsonl``
#: (optionally ``.jsonl.zst``).
_SESSIONS_SUBDIR = "sessions"

#: A Codex thread id must be a single, inert path COMPONENT before it is
#: joined onto the sessions root — mirrors the guard Claude Code's harness
#: applies to its own session ids (`trailhead/harness/claude_code.py:136`), so
#: neither a traversal segment (``..``) nor a path separator can ever be
#: joined onto a store path or handed to a caller's argv.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: A rollout filename, stripped of its ``.jsonl``/``.jsonl.zst`` extension:
#: ``rollout-<YYYY-MM-DDTHH-MM-SS>-<thread-id>[_<rollout-id>]``. The thread id
#: is captured as everything up to the first underscore after the timestamp —
#: observed thread ids are UUID-shaped and never contain one.
_ROLLOUT_STEM_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-([^_]+)(?:_.+)?$"
)

#: Byte cap on the bounded first-line read `_read_session_meta_payload`
#: performs to recover a rollout's ``session_meta`` payload. A rollout can run
#: to hundreds of megabytes; this caps the single read at a size no genuine
#: metadata line should ever approach, so a corrupt or hostile file cannot
#: force an unbounded read.
_SESSION_META_MAX_LINE_BYTES = 65_536

#: Env var names a launching caller must scrub before spawning a Codex
#: session, returned by :meth:`CodexHarness.session_launch_env_unset`.
#: ``CODEX_HOME`` is the variable this seam itself uses to redirect a
#: session's home (never injected by Codex itself, but scrubbed here for the
#: same reason Claude Code scrubs ``CLAUDE_CONFIG_DIR``: a caller must assert
#: the default rather than inherit whatever the launching process carried).
#: The remaining six are variables an interactive Codex session has been
#: observed injecting into its own child processes — a floor, not an
#: exhaustive guarantee, per the base contract.
_LAUNCH_ENV_UNSET = [
    "CODEX_HOME",
    "CODEX_CI",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_VERSION",
]

#: Characters an account value may never carry: the C0 controls (NUL among
#: them), DEL, and the C1 controls. Mirrors Claude Code's own account guard.
_ACCOUNT_FORBIDDEN_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _account_dir(account: str, env: dict[str, str]) -> Path:
    """Resolve a declared *account* to an absolute Codex home directory, or raise.

    A leading ``~`` expands against *env*'s ``HOME``/``USERPROFILE`` rather
    than the machine's, so a caller's declaration never resolves through the
    running user's password-db entry. Every other form must already be
    absolute: a relative value resolves against whichever working directory
    the launching process happens to have.

    A control character is refused before either check, for the same reason
    Claude Code's own ``_account_dir`` refuses one: the value becomes a path
    the caller resolves and an operand of a process spawn.
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
        home = env.get("HOME") or env.get("USERPROFILE") or ""
        expanded = home + account[1:]
    path = Path(expanded)
    if not path.is_absolute():
        raise HarnessError(
            f"session_launch_env_set: account {account!r} is not an absolute path. "
            "An account names the directory a launched session reads as its Codex "
            "home, so it must resolve identically from any working directory."
        )
    return path


def _refuse_conflicting_codex_home(account_dir: Path, env: dict[str, str]) -> None:
    """Raise when *env*'s ambient ``CODEX_HOME`` names a different directory
    than the one a declared account just resolved to.

    Compared both textually and by realpath, mirroring Claude Code's
    ``_refuse_conflicting_config_dirs``: a caller may spell the same directory
    two different ways (a symlink, a trailing slash) and that is not a
    conflict, only two different values naming the same place are.
    """
    ambient = (env.get("CODEX_HOME") or "").strip()
    if not ambient:
        return
    if Path(ambient) == account_dir:
        return
    if os.path.realpath(ambient) == os.path.realpath(str(account_dir)):
        return
    raise HarnessError(
        f"session_launch_env_set: account {str(account_dir)!r} disagrees with the "
        f"ambient CODEX_HOME={ambient!r}. A launched session reads whichever "
        "directory CODEX_HOME names, so trailhead will not guess which one you "
        "meant."
    )


def _is_session_id(session_id: object) -> bool:
    """Whether ``session_id`` is a plain token safe to use as a path or an argv.

    Mirrors Claude Code's ``_is_session_id`` guard: an id is either inert
    enough to be joined onto the sessions root AND handed to a caller's argv,
    or it is not a session id this harness recognizes at all.
    """
    return isinstance(session_id, str) and _SESSION_ID_RE.match(session_id) is not None


def _iter_rollouts(sessions_dir: Path):
    """Yield ``(path, thread_id)`` for every rollout file under ``sessions_dir``.

    Walks both plain and ``.zst``-compressed rollouts at a fixed ``*/*/*``
    depth — the ``YYYY/MM/DD`` layout Codex itself writes — never a recursive
    glob. A non-file, or a filename :func:`_parse_rollout_session_id` rejects,
    is skipped.
    """
    for pattern in ("*/*/*/rollout-*.jsonl", "*/*/*/rollout-*.jsonl.zst"):
        for path in sessions_dir.glob(pattern):
            if not path.is_file():
                continue
            session_id = _parse_rollout_session_id(path)
            if session_id is not None:
                yield path, session_id


def _parse_rollout_session_id(path: Path) -> str | None:
    """Parse the thread id out of a rollout filename, or ``None``.

    Strips the ``.jsonl``/``.jsonl.zst`` extension, matches the fixed
    ``rollout-<timestamp>-<thread-id>[_<rollout-id>]`` shape, and rejects the
    extracted id unless it also passes :func:`_is_session_id` — a filename
    that parses but yields a traversal or separator-bearing id names no row.
    """
    name = path.name
    if name.endswith(".jsonl.zst"):
        stem = name[: -len(".jsonl.zst")]
    elif name.endswith(".jsonl"):
        stem = name[: -len(".jsonl")]
    else:
        return None
    match = _ROLLOUT_STEM_RE.match(stem)
    if match is None:
        return None
    candidate = match.group(1)
    return candidate if _is_session_id(candidate) else None


def _read_session_meta_payload(path: Path) -> dict | None:
    """Read and parse the leading ``session_meta`` envelope's ``payload``.

    Performs exactly one bounded read — ``readline`` capped at
    ``_SESSION_META_MAX_LINE_BYTES + 1`` bytes — so an oversized or
    newline-less first line stops there without ever touching a second line.
    Returns ``None`` when the file cannot be opened, the first line exceeds
    the cap, is not valid JSON, is not a dict, is not tagged
    ``"type": "session_meta"``, or carries a non-dict ``payload``.
    """
    try:
        with path.open("rb") as f:
            chunk = f.readline(_SESSION_META_MAX_LINE_BYTES + 1)
    except OSError:
        return None
    if not chunk or len(chunk) > _SESSION_META_MAX_LINE_BYTES:
        return None
    try:
        envelope = json.loads(chunk)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, dict) or envelope.get("type") != "session_meta":
        return None
    payload = envelope.get("payload")
    return payload if isinstance(payload, dict) else None


def _extract_rollout_cwd(path: Path) -> Path | None:
    """Extract the recorded start-of-session ``cwd`` from a rollout, or ``None``.

    Always ``None`` for a ``.zst`` file — this seam never decompresses one —
    and for any file whose leading ``session_meta`` payload is missing or
    carries no string ``cwd``.
    """
    if path.name.endswith(".zst"):
        return None
    payload = _read_session_meta_payload(path)
    if payload is None:
        return None
    cwd = payload.get("cwd")
    return Path(cwd) if isinstance(cwd, str) and cwd else None


def _resolve_rollout_path(session_id: str, sessions_dir: Path) -> Path | None:
    """Resolve ``session_id`` to its rollout file under ``sessions_dir``, or ``None``.

    Returns ``None`` when the id is not a usable path component, the sessions
    directory does not exist, or no rollout filename parses to that id.
    """
    if not _is_session_id(session_id):
        return None
    if not sessions_dir.is_dir():
        return None
    for path, candidate_id in _iter_rollouts(sessions_dir):
        if candidate_id == session_id:
            return path
    return None


def codex_home(env: dict[str, str]) -> Path:
    """Resolve Codex's home directory from *env* — the single choke point every
    other Codex-home-relative resolver in this seam calls through.

    Precedence: ``CODEX_HOME`` (the variable Codex itself reads) when set, else
    ``HOME``/``USERPROFILE`` joined with ``.codex``. Deliberately never falls
    back to :func:`pathlib.Path.home` — every caller must inject an
    environment, so a test that forgets to pin one fails loudly (via the
    ``HarnessError`` below) rather than silently resolving the operator's real
    Codex home.

    Raises:
        HarnessError: if ``CODEX_HOME`` is set but not an absolute path (a
            relative value would resolve differently depending on the
            launching process's working directory — mirroring how Claude
            Code's declared-account resolution refuses a relative value), or
            if neither ``CODEX_HOME`` nor ``HOME``/``USERPROFILE`` is set.
    """
    override = env.get("CODEX_HOME")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise HarnessError(
                f"CODEX_HOME={override!r} is not an absolute path. Codex resolves "
                "its home from this directory exactly as given, so a relative "
                "value would resolve differently depending on the launching "
                "process's working directory."
            )
        return path
    home = env.get("HOME") or env.get("USERPROFILE")
    if not home:
        raise HarnessError(
            "cannot resolve the Codex home: neither CODEX_HOME nor HOME/USERPROFILE "
            "is set in the given environment."
        )
    return Path(home) / _CODEX_HOME_SUBDIR


def _workspace_argv_value(caller: str, workspace: Path) -> str:
    """Return *workspace* as an argv value, or raise if it would read as a flag.

    Argv safety only, not filesystem validation: a value beginning with ``-``
    would be parsed as an option by the program it is handed to. *caller*
    prefixes the error so it names the seam method that refused.
    """
    as_arg = str(workspace)
    if as_arg.startswith("-"):
        raise HarnessError(f"{caller}: workspace would read as a flag in argv: {as_arg!r}")
    return as_arg


def _codex_on_path(env: dict[str, str]) -> bool:
    """True if an executable named ``codex`` is on the given env's ``PATH``.

    Mirrors the executable check ``trailhead.pathint`` uses for its own shims
    (``is_file()`` and ``os.access(..., os.X_OK)``) rather than shelling out to
    ``shutil.which``, which reads the real process environment instead of the
    injected one this seam is built to test against.
    """
    for entry in env.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / _CODEX_EXECUTABLE_NAME
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return True
    return False


class CodexHarness(Harness):
    """Recognise Codex through the trailhead harness seam.

    See the module docstring for what's implemented (detection, the
    transcript store, live enumeration, the launch quartet) versus vacuous
    (every install/registration method).
    """

    name = "codex"

    @classmethod
    def detect(cls, env: dict[str, str]) -> bool:
        if _codex_on_path(env):
            return True
        return (codex_home(env) / _CODEX_CONFIG_FILENAME).is_file()

    # -- manifest ---------------------------------------------------------

    def generate_manifest(self, tools: list[str], composed_root: Path) -> None:
        return None

    # -- registration state (on-disk truth) --------------------------------

    def is_registered(self, composed_root: Path, *, env: dict[str, str] | None = None) -> bool:
        return False

    def is_installed(
        self, tool: str, composed_root: Path, *, env: dict[str, str] | None = None
    ) -> bool:
        return False

    def installed_tools(
        self, composed_root: Path, *, env: dict[str, str] | None = None
    ) -> list[str]:
        return []

    # -- install / uninstall ------------------------------------------------

    def register(
        self, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        return None

    def install_tool(
        self, tool: str, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        return None

    def rewire_tool(
        self, tool: str, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        return None

    def unregister_tool(
        self, tool: str, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        return None

    def unregister_marketplace(
        self, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        return None

    # -- session transcript enumeration ------------------------------------

    def session_transcripts(
        self, workspace: Path | None = None, *, env: dict[str, str] | None = None
    ) -> list[SessionTranscript]:
        """Enumerate rollouts under ``<codex-home>/sessions/*/*/*/``.

        See the base contract for the full semantics. Never raises for a
        missing store, or for an individual rollout this harness cannot open,
        decode, or find a cwd inside — that rollout still yields a row, with
        ``cwd=None``.
        """
        _env = env if env is not None else dict(os.environ)
        sessions_dir = codex_home(_env) / _SESSIONS_SUBDIR
        if not sessions_dir.is_dir():
            return []

        resolved_workspace = Path(workspace).resolve() if workspace is not None else None

        rows: list[SessionTranscript] = []
        for candidate, session_id in _iter_rollouts(sessions_dir):
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                continue

            raw_cwd = _extract_rollout_cwd(candidate)
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

    def session_transcript_path(
        self, session_id: str, workspace: Path, *, env: dict[str, str] | None = None
    ) -> Path | None:
        """Resolve the rollout for ``session_id``, or ``None``.

        ``workspace`` is accepted for interface parity with the base contract
        but unused: Codex keys its store by date, not by launch cwd, so no
        workspace scoping applies to a single-id lookup.
        """
        _env = env if env is not None else dict(os.environ)
        sessions_dir = codex_home(_env) / _SESSIONS_SUBDIR
        return _resolve_rollout_path(session_id, sessions_dir)

    # -- session launch ------------------------------------------------------

    def session_launch(
        self,
        workspace: Path,
        session_id: str,
        *,
        session_name: str | None = None,
        settings_path: Path | None = None,
    ) -> list[str]:
        """Return ``["codex", "--cd", str(workspace)]``.

        Codex's interactive launch offers no flag for a caller-chosen session
        id, session name, or an additional settings file, so all three are
        validated as inert argv tokens and then ignored — the returned argv
        never varies with them. See the module docstring's "Session launch"
        section for the full contract.
        """
        if not _is_session_id(session_id):
            raise HarnessError(f"session_launch: invalid session_id: {session_id!r}")
        if session_name is not None and not _is_session_id(session_name):
            raise HarnessError(f"session_launch: invalid session_name: {session_name!r}")
        if settings_path is not None:
            text = str(settings_path)
            if not text or text.startswith("-"):
                raise HarnessError(
                    f"session_launch: invalid settings_path: {settings_path!r}"
                )
        return ["codex", "--cd", _workspace_argv_value("session_launch", workspace)]

    def session_launch_modality(self) -> Modality:
        """Codex launch requires a TTY (interactive terminal)."""
        return MODALITY_TTY_REQUIRED

    def session_launch_env_unset(self) -> list[str]:
        """Env var names a launching caller must scrub before spawning.

        See :data:`_LAUNCH_ENV_UNSET` for why each name is here.
        """
        return list(_LAUNCH_ENV_UNSET)

    def session_launch_env_set(
        self, account: str | None, *, env: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Bind a launched session to *account* by naming its Codex home.

        ``account=None`` — the caller declared nothing — contributes NO
        assignment: Codex's own default (``CODEX_HOME`` unset, falling back
        to ``HOME``/``.codex``) is expressed as the variable's ABSENCE, and
        the name is in :data:`_LAUNCH_ENV_UNSET` so a launching caller
        scrubs it.

        For a declared account, resolves it via :func:`_account_dir` (raising
        on a relative value or a control character), refuses one that
        disagrees with an ambient ``CODEX_HOME`` already in *env* (via
        :func:`_refuse_conflicting_codex_home`), and returns
        ``{"CODEX_HOME": <resolved dir>}``.
        """
        if account is None:
            return {}
        source = env if env is not None else dict(os.environ)
        account_dir = _account_dir(account, source)
        _refuse_conflicting_codex_home(account_dir, source)
        return {"CODEX_HOME": str(account_dir)}

    # -- live session enumeration -------------------------------------------

    def session_enumerate(self, workspace: Path | None = None) -> list[str]:
        """Return the argv that runs :mod:`trailhead.harness.codex_sessions`.

        Raises :class:`HarnessError` on a ``workspace`` whose string form
        begins with ``-`` — it would land in the value slot right after
        ``--workspace`` and read as a flag. This is argv safety only, not
        filesystem validation, mirroring
        :meth:`~trailhead.harness.claude_code.ClaudeCodeHarness.session_enumerate`.
        """
        argv = [sys.executable, "-m", "trailhead.harness.codex_sessions"]
        if workspace is not None:
            argv += ["--workspace", _workspace_argv_value("session_enumerate", workspace)]
        return argv

    def parse_session_list(self, output: str) -> list[SessionRecord]:
        """Parse :mod:`trailhead.harness.codex_sessions`'s JSON array output.

        See the base contract for the full failure semantics. ``pid`` is
        always ``None`` — the lock this seam's liveness signal comes from
        carries no pid — and ``controllable`` is always ``False``: this
        harness gives no session a remote-attach capability, regardless of
        ``kind``. ``startedAt`` is the rollout's ISO 8601 ``session_meta``
        timestamp (as ``_read_session_meta_payload`` recovers it), not the epoch-millis shape Claude
        Code's own listing uses, so a wrong-typed or unparseable value
        degrades to ``None`` rather than raising.
        """
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            raise HarnessError(
                f"codex_sessions: failed to decode output: {_excerpt(output)}"
            ) from None

        if not isinstance(data, list):
            raise HarnessError(
                f"codex_sessions: expected a JSON array, got "
                f"{type(data).__name__}: {_excerpt(output)}"
            )

        records: list[SessionRecord] = []
        for record in data:
            if not isinstance(record, dict):
                raise HarnessError(
                    f"codex_sessions: expected a JSON object per record, got "
                    f"{type(record).__name__}: {_excerpt(output)}"
                )

            session_id = record.get("sessionId")
            if not _is_session_id(session_id):
                raise HarnessError(
                    f"codex_sessions: record has missing or invalid "
                    f"'sessionId': {_excerpt(output)}"
                )

            raw_cwd = record.get("cwd")
            if not isinstance(raw_cwd, str) or not raw_cwd:
                raise HarnessError(
                    f"codex_sessions: record has missing or invalid "
                    f"'cwd': {_excerpt(output)}"
                )
            cwd = Path(raw_cwd).resolve()

            kind = record.get("kind")
            if not isinstance(kind, str) or not kind:
                raise HarnessError(
                    f"codex_sessions: record has missing or invalid "
                    f"'kind': {_excerpt(output)}"
                )

            name = record.get("name")
            if not isinstance(name, str):
                name = None

            started_at_raw = record.get("startedAt")
            started_at = None
            if isinstance(started_at_raw, str) and started_at_raw:
                try:
                    started_at = datetime.fromisoformat(
                        started_at_raw.replace("Z", "+00:00")
                    )
                except ValueError:
                    started_at = None

            records.append(
                SessionRecord(
                    session_id=session_id,
                    cwd=cwd,
                    kind=kind,
                    controllable=False,
                    name=name,
                    pid=None,
                    started_at=started_at,
                )
            )

        return records
