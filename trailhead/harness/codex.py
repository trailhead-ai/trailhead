"""Codex harness implementation — detection and home resolution skeleton.

This module owns everything Codex-specific about being recognised through the
trailhead :class:`~trailhead.harness.base.Harness` seam (Axiom 1). Nothing
Codex-specific lives outside this file — the shared install/compose/wire/
doctor path talks to it only through the generic interface.

Home resolution
----------------
Codex's own home directory is resolved by ``codex_home``: ``CODEX_HOME`` when
set (must be absolute), else ``HOME``/``USERPROFILE`` joined with ``.codex`` —
the same variable Codex itself reads, so trailhead needs no Codex-only test
seam the way it does for Claude Code's ``TRAILHEAD_CLAUDE_DIR``. This resolver
NEVER falls back to :func:`pathlib.Path.home`: a caller must inject an
environment (real or test-pinned) explicitly, so a missing injection fails
loudly instead of silently reading (or, worse, writing under) the operator's
real Codex home.

Detection
---------
``CodexHarness.detect`` is true when either signal is present: a ``codex``
executable on the given environment's ``PATH``, or a ``config.toml`` file
under the resolved Codex home. A bare, empty home directory (no
``config.toml``) is NOT detected — Codex has never been configured there.

Install-surface skeleton
-------------------------
Every registration/install method below is a vacuous, harness-agnostic-safe
default (no-op write, ``False``/``[]`` read) so this class can be instantiated
and registered before the create-phase (compose a manifest Codex can read,
register it, install/rewire/uninstall tools) is implemented. This makes
``trailhead doctor`` report Codex as detected with nothing installed, which is
the honest state until those methods are filled in — never a silent write to
a harness that can't yet read it, and never a raised error that would abort
``trailhead install``/``trailhead update`` on any machine with Codex detected.
Every other seam method (transcripts, live-session enumeration, launch,
account identity/authentication) stays at the base class's default too; this
skeleton adds only detection and registry presence.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from trailhead.harness.base import Harness, HarnessError, SessionTranscript

#: The file whose presence under a Codex home means Codex has been configured
#: there — read by :meth:`CodexHarness.detect` as the "home has state" signal.
_CODEX_CONFIG_FILENAME = "config.toml"

#: The executable name looked up on the given environment's ``PATH`` — the
#: "Codex CLI is installed" signal for :meth:`CodexHarness.detect`.
_CODEX_EXECUTABLE_NAME = "codex"

#: A directory under ``.codex`` Codex itself creates. See :mod:`trailhead.harness`.
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


def _is_session_id(session_id: object) -> bool:
    """Whether ``session_id`` is a plain token safe to use as a path or an argv.

    Mirrors Claude Code's ``_is_session_id`` guard: an id is either inert
    enough to be joined onto the sessions root AND handed to a caller's argv,
    or it is not a session id this harness recognizes at all.
    """
    return isinstance(session_id, str) and _SESSION_ID_RE.match(session_id) is not None


def _iter_rollout_paths(sessions_dir: Path):
    """Walk ``sessions_dir`` for rollout files, both plain and ``.zst``-compressed.

    Depth is fixed at ``*/*/*`` — the ``YYYY/MM/DD`` layout Codex itself
    writes — never a recursive glob.
    """
    yield from sessions_dir.glob("*/*/*/rollout-*.jsonl")
    yield from sessions_dir.glob("*/*/*/rollout-*.jsonl.zst")


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
    for candidate in _iter_rollout_paths(sessions_dir):
        if not candidate.is_file():
            continue
        if _parse_rollout_session_id(candidate) == session_id:
            return candidate
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

    See the module docstring for what's implemented (detection, home
    resolution) versus vacuous (every install/registration method) at this
    stage.
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
        for candidate in _iter_rollout_paths(sessions_dir):
            if not candidate.is_file():
                continue
            session_id = _parse_rollout_session_id(candidate)
            if session_id is None:
                continue
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
