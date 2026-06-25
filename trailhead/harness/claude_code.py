"""Claude Code harness implementation.

Wraps :mod:`trailhead.registry` (the Shape-A ``marketplace.json`` writer and the
``claude plugin …`` CLI calls) behind the generic :class:`~trailhead.harness.base.Harness`
interface.  Registration markers live in ``composed_root`` (written by
``registry`` after the CLI step succeeds):

- global ``.trailhead-registered`` — the marketplace has been added.
- per-tool ``.trailhead-installed-<tool>`` — the tool has been installed.

Detection: Claude Code is considered present when ``~/.claude`` exists.  Tests
may redirect this with the ``TRAILHEAD_CLAUDE_DIR`` env override.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from trailhead import registry
from trailhead.harness.base import Harness

_REGISTERED_MARKER = ".trailhead-registered"
_INSTALLED_MARKER_PREFIX = ".trailhead-installed-"

#: Subdir under the Claude dir holding user-level rulesets (``~/.claude/rules/``).
_RULES_SUBDIR = "rules"


def _claude_dir(env: dict[str, str]) -> Path:
    """Resolve Claude Code's config dir (``~/.claude``) from *env*.

    Honors the ``TRAILHEAD_CLAUDE_DIR`` override (tests redirect it here), then
    ``HOME``/``USERPROFILE``, falling back to the real home.  Single source of
    truth for both ``detect`` and the user-ruleset methods.
    """
    override = env.get("TRAILHEAD_CLAUDE_DIR")
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
        registry.generate_marketplace_json(tools, composed_root)

    # -- registration state ---------------------------------------------------

    def is_registered(self, composed_root: Path) -> bool:
        return (composed_root / _REGISTERED_MARKER).exists()

    def is_installed(self, tool: str, composed_root: Path) -> bool:
        return (composed_root / f"{_INSTALLED_MARKER_PREFIX}{tool}").exists()

    # -- install / uninstall --------------------------------------------------

    def register(self, composed_root: Path, *, runner=None) -> None:
        registry.register_marketplace(composed_root, runner=runner)

    def install_tool(self, tool: str, composed_root: Path, *, runner=None) -> None:
        registry.install_tool(tool, composed_root, runner=runner)

    def rewire_tool(self, tool: str, composed_root: Path, *, runner=None) -> None:
        registry.rewire_tool(tool, composed_root, runner=runner)

    def unregister_tool(self, tool: str, composed_root: Path, *, runner=None) -> None:
        registry.unregister_tool(tool, composed_root, runner=runner)

    def unregister_marketplace(self, composed_root: Path, *, runner=None) -> None:
        registry.unregister_marketplace(composed_root, runner=runner)

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

        Mirrors ``scripts/settings_writer.py`` ``_save``: same-mount temp file via
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
