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

    @abstractmethod
    def installed_tools(self, composed_root: Path) -> list[str]:
        """Return the sorted tool names currently installed under ``composed_root``.

        The enumeration counterpart to :meth:`is_installed`: ``doctor`` and
        ``uninstall`` discover which tools a composed tree holds through this method
        rather than re-deriving the harness's on-disk marker scheme themselves.
        """

    def manifest_name(self, composed_root: Path) -> str | None:
        """Display name of the harness manifest under ``composed_root``, or ``None``.

        Read-only report helper (used by ``doctor``).  The default has no named
        manifest; harnesses whose :meth:`generate_manifest` writes a named artifact
        override this.  Must never raise on a malformed/absent manifest.
        """
        return None

    def manifest_exists(self, composed_root: Path) -> bool:
        """Return True if a manifest file is present on disk, parseable or not.

        :meth:`manifest_name` returns ``None`` both when the manifest is absent AND
        when it exists but is malformed — a deliberate, already-tested collapse.
        This method exists so ``doctor`` can tell those two cases apart (existence
        only; it never parses). The default is False; harnesses whose
        :meth:`generate_manifest` writes a manifest file override this.
        """
        return False

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

    # -- session transcripts --------------------------------------------------
    #
    # A *session transcript* is the harness's on-disk record of one agent
    # session.  Same asymmetry as the user-ruleset trio above: CONCRETE with a
    # safe default, so a harness with no transcript concept doesn't have to
    # implement it.  Everything about a harness's transcript LAYOUT (config dir,
    # directory munging, file extension) lives in that harness's module — the
    # core only ever receives a resolved path or ``None``.
    #
    # The default degrades to ``None`` — "this harness cannot tell you where the
    # transcript is".  Callers must treat ``None`` as unresolvable and say so;
    # they must never synthesize a path of their own.

    def session_transcript_path(
        self, session_id: str, workspace: Path, *, env: dict[str, str] | None = None
    ) -> Path | None:
        """Resolve the on-disk transcript for ``session_id``, or ``None``.

        ``workspace`` is the session's START-OF-SESSION working directory (for
        camp, the workspace root) — harnesses that key their transcript layout on
        the launch cwd need it, and it must never be inferred from the CALLER's
        cwd, which has usually moved by capture time.

        Returns ``None`` when the harness has no transcript concept, when the
        transcript does not exist, or when ``session_id`` is not a usable path
        component.  Never raises for an absent transcript.

        ``env`` overrides the process environment (hermetic tests, and callers
        that already carry an injected env); ``None`` means ``os.environ``.
        """
        return None

    # -- session resume -------------------------------------------------------
    #
    # Resuming a session means re-entering it as a fresh foreground process.  The
    # seam owns the ARGV — the exact binary, flag spelling, and argument order are
    # harness-specific knowledge and live in that harness's module only (Axiom 1).
    # A caller receives an already-safe token list and passes it through
    # untouched; it must never assemble, edit, or re-quote one of its own.
    #
    # Same degrading default as the transcript seam: ``None`` means "this harness
    # cannot be resumed", which a caller must report rather than paper over.
    #
    # The seam does NOT exec.  Deciding where and how to run the argv (in-process,
    # via a shell wrapper, not at all) belongs to the caller; splitting it this way
    # is what lets a core that must never exec still offer resume.

    def session_resume(self, session_id: str) -> list[str] | None:
        """Return the argv that re-enters ``session_id``, or ``None``.

        The argv is a list of individually-quoted-free tokens suitable for a
        direct ``exec``: no shell is implied and no element needs further
        escaping.

        Returns ``None`` when the harness has no resume concept, or when
        ``session_id`` is not a shape the harness accepts — a malformed id must
        never be smuggled into an argv a caller will run.
        """
        return None
