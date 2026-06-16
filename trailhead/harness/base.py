"""Harness-installer interface for trailhead.

A *harness* is an AI code harness that trailhead can install agent-plugins into
(Claude Code today; Codex, OpenCode, Copilot, … in the future).  Each harness is
a "trailhead-plugin" — a concrete implementation of this interface.  ``install``
and ``uninstall`` are harness-agnostic; they compose the generic plugin trees
(:mod:`trailhead.compose` / :mod:`trailhead.wire`) and delegate the
harness-specific registration tail to a :class:`Harness`.

Authoring a new harness
-----------------------
Subclass :class:`Harness`, set the class attribute ``name`` to the canonical
harness key (snake_case, e.g. ``"codex"``), implement ``detect`` (does this
harness's config exist on the machine?) and the install/registration methods,
then register the class in :mod:`trailhead.harness` (the factory).  The
composed plugin trees are written for you by ``wire`` into
``composed_root = self.composed_root(state_dir)`` — your methods only have to
make that tree live in the harness and record what is installed.

Per-harness isolation
---------------------
Each harness composes into its OWN root (``state_dir/composed/<name>/``) with its
own registration markers, so multiple harnesses never collide.

Design axioms
-------------
This interface is the seam that makes trailhead harness-agnostic (Axiom 1) while
still taking full advantage of each harness (Axiom 2): harness-specific behavior
lives behind this class, never in the shared install/compose/wire path. To use a
harness capability the interface doesn't yet express, widen this seam (add a
method with a safe default) rather than branching on a harness name in the core.
See ``docs/vision.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class HarnessError(Exception):
    """Raised for unknown / unsupported harness names."""


class Harness(ABC):
    """Abstract installer for one AI code harness.

    Subclasses set the class attribute ``name`` (the canonical key) and implement
    the methods below.  All registration methods accept an injectable ``runner``
    (a ``callable(args: list[str], **kw)``) so tests never invoke a real CLI.
    """

    #: Canonical harness key, e.g. ``"claude_code"``.  Set on each subclass.
    name: str = ""

    # -- detection ------------------------------------------------------------

    @classmethod
    @abstractmethod
    def detect(cls, env: dict[str, str]) -> bool:
        """Return True if this harness's configuration is present on the machine."""

    # -- layout ---------------------------------------------------------------

    def composed_root(self, state_dir: Path) -> Path:
        """Per-harness composed-tree root under ``state_dir``.

        Default: ``state_dir/composed/<name>``.  Plugin trees land at
        ``<composed_root>/plugins/<tool>/``.
        """
        return state_dir / "composed" / self.name

    # -- manifest -------------------------------------------------------------

    @abstractmethod
    def generate_manifest(self, tools: list[str], composed_root: Path) -> None:
        """Write whatever manifest the harness needs to discover the plugin trees."""

    # -- registration state (on-disk truth) -----------------------------------

    @abstractmethod
    def is_registered(self, composed_root: Path) -> bool:
        """Return True if the marketplace/registry step has already run."""

    @abstractmethod
    def is_installed(self, tool: str, composed_root: Path) -> bool:
        """Return True if ``tool`` is currently installed (per-tool marker present)."""

    # -- install / uninstall --------------------------------------------------

    @abstractmethod
    def register(self, composed_root: Path, *, runner=None) -> None:
        """Register the composed tree with the harness (once per composed_root)."""

    @abstractmethod
    def install_tool(self, tool: str, composed_root: Path, *, runner=None) -> None:
        """Install ``tool`` into the harness from the composed tree."""

    @abstractmethod
    def rewire_tool(self, tool: str, composed_root: Path, *, runner=None) -> None:
        """Refresh an already-installed ``tool`` after recomposition."""

    @abstractmethod
    def unregister_tool(self, tool: str, composed_root: Path, *, runner=None) -> None:
        """Uninstall one ``tool`` from the harness (keeping the user's data)."""

    @abstractmethod
    def unregister_marketplace(self, composed_root: Path, *, runner=None) -> None:
        """Remove the harness registration for the composed tree (once, after all tools)."""
