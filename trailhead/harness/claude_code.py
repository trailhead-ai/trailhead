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

from pathlib import Path

from trailhead import registry
from trailhead.harness.base import Harness

_REGISTERED_MARKER = ".trailhead-registered"
_INSTALLED_MARKER_PREFIX = ".trailhead-installed-"


class ClaudeCodeHarness(Harness):
    """Install agent-plugins into Claude Code via the ``claude plugin`` CLI."""

    name = "claude_code"

    @classmethod
    def detect(cls, env: dict[str, str]) -> bool:
        override = env.get("TRAILHEAD_CLAUDE_DIR")
        if override:
            return Path(override).is_dir()
        home = env.get("HOME") or env.get("USERPROFILE")
        base = Path(home) if home else Path.home()
        return (base / ".claude").is_dir()

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
