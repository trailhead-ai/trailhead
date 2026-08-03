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
reaches any CLI arg, marker filename, or ``source`` path.

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
from pathlib import Path

from trailhead.harness.base import Harness

_REGISTERED_MARKER = ".trailhead-registered"
_INSTALLED_MARKER_PREFIX = ".trailhead-installed-"

#: Subdir under the Claude dir holding user-level rulesets (``~/.claude/rules/``).
_RULES_SUBDIR = "rules"

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

#: Subdir under the Claude dir holding one directory of session transcripts per
#: project (``~/.claude/projects/<munged-cwd>/<session-id>.jsonl``).
_PROJECTS_SUBDIR = "projects"

#: A session id must be a single, inert path COMPONENT before it is joined onto
#: the transcripts root.  Anything else (``..``, a separator, an empty string)
#: would escape the projects dir, so it resolves to "unknown session" instead.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

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
        _env = env if env is not None else dict(os.environ)
        return _claude_dir(_env) / _RULES_SUBDIR / f"{name}.md"

    def install_user_ruleset(
        self, name: str, content: str, *, env: dict[str, str] | None = None
    ) -> None:
        """Write ``~/.claude/rules/<name>.md`` idempotently and atomically.

        Mirrors ``lore/config/settings_writer.py`` ``_save``: same-mount temp file via
        ``mkstemp(dir=target.parent)`` + ``os.replace`` so the swap is atomic and
        the rename can't fail cross-filesystem; clean up the temp on any error.
        A re-run with byte-identical content is a true no-op (no write, no swap).
        """
        target = self.user_ruleset_path(name, env=env)
        if target.is_file() and target.read_text() == content:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{name}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
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
        return "current" if target.read_text() == content else "stale"

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
        if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
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
        if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
            return None
        return ["claude", "--resume", session_id]
