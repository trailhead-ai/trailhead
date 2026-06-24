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


#: Emitted by the base-class ``install_user_ruleset`` default when a harness has
#: no user-ruleset support.  Degrade VISIBLY: a no-op install must never leave a
#: user believing the ruleset was written (Axiom 2 — widen the seam with a safe,
#: honest default).  Pinned by ``test_harness.py``.
UNSUPPORTED_RULESET_NOTICE = (
    "trailhead: this harness has no user-level ruleset support; nothing was installed."
)


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

    # -- user-level rulesets --------------------------------------------------
    #
    # A *user-level ruleset* is harness-global agent guidance (e.g. lore's
    # write-prohibition rules) installed once per machine, NOT per project.  This
    # capability is deliberately CONCRETE with a safe default — unlike every
    # method above, it is NOT ``@abstractmethod``.  That asymmetry is the point:
    # a harness that can't express user rulesets shouldn't have to (Axiom 2 — to
    # use a capability the seam doesn't yet express, widen the seam with a safe
    # default rather than forcing every harness to implement it).
    #
    # The safe default DEGRADES VISIBLY: ``user_ruleset_path`` → ``None``,
    # ``user_ruleset_status`` → ``"unsupported"``, and ``install_user_ruleset``
    # writes nothing but emits ``UNSUPPORTED_RULESET_NOTICE`` so a user is never
    # left believing the ruleset installed.  Harnesses that DO support it (e.g.
    # Claude Code → ``~/.claude/rules/<name>.md``) override all three.

    def install_user_ruleset(self, name: str, content: str) -> None:
        """Install a user-level ruleset; default no-op that announces itself."""
        print(UNSUPPORTED_RULESET_NOTICE)

    def user_ruleset_path(self, name: str) -> Path | None:
        """Path to the installed ruleset, or ``None`` when unsupported."""
        return None

    def user_ruleset_status(self, name: str, content: str) -> str:
        """One of ``current`` / ``stale`` / ``missing`` / ``unsupported``."""
        return "unsupported"
