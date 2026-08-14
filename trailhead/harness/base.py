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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


class HarnessError(Exception):
    """Raised for unknown / unsupported harness names."""


#: Emitted by the base-class ``install_user_ruleset`` default when a harness has
#: no user-ruleset support.  Degrade VISIBLY: a no-op install must never leave a
#: user believing the ruleset was written (Axiom 2 — widen the seam with a safe,
#: honest default).  Pinned by ``test_harness.py``.
UNSUPPORTED_RULESET_NOTICE = (
    "trailhead: this harness has no user-level ruleset support; nothing was installed."
)

#: Fraction of a harness's transcript-retention window after which a session is
#: "approaching expiry".  Lives here, beside ``session_retention_days``, because
#: two independent surfaces warn off it — ``trailhead doctor`` and
#: ``camp bookmark ls`` — and a user reading both must see the same cutoff.
SESSION_RETENTION_WARNING_FRACTION = 0.8

#: Closed modality vocabulary for how a launched session can be reached again.
#: Callers compare against these constants, never against their own literal —
#: the string values are caller-visible wire-ish vocabulary and are pinned by
#: ``test_harness.py``.
MODALITY_TTY_REQUIRED = "tty-required"
MODALITY_DETACHED_GUI = "detached-gui"

#: The closed set of valid :data:`Modality` values.
MODALITIES: frozenset[str] = frozenset({MODALITY_TTY_REQUIRED, MODALITY_DETACHED_GUI})

#: A session's launch modality: whether re-entering it requires a TTY the
#: caller controls, or whether the harness owns its own detached GUI surface.
Modality = Literal["tty-required", "detached-gui"]


@dataclass(frozen=True)
class SessionRecord:
    """One session as enumerated by a harness.

    ``session_id`` is the record's identity — callers diff snapshots on
    ``session_id``, never on whole-record equality. ``started_at`` is always
    timezone-aware UTC, never naive. Harness-native fields beyond this set are
    dropped, never passed through.
    """

    session_id: str
    cwd: Path
    kind: str
    #: this session is of a class the harness can remote-address — NOT a
    #: liveness flag (every enumerated record is live) and conveys NO attach
    #: capability or authorization (no attach-authz model exists; a consumer
    #: building attach must define one first). Not a connection probe.
    controllable: bool
    name: str | None
    pid: int | None
    started_at: datetime | None


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
    #
    # All three accept an injectable ``env`` so a caller can redirect the harness
    # config dir; the defaults ignore it (there is nothing to resolve), but every
    # caller may pass it uniformly without knowing which harness it holds.
    #
    # ``name`` CONTRACT: a ruleset name is an opaque identifier, not a path — it
    # may never contain a path separator, ``..``, or a leading dot.  An
    # implementation that turns the name into a filesystem path writes OUTSIDE
    # any trailhead-owned tree (into the user's harness config dir), so it MUST
    # reject a name violating this before creating a directory or writing a byte,
    # raising ``HarnessError`` — which the CLI renders as a clean
    # ``trailhead: <message>``.  Callers must not rely on their own name-building
    # being the only guard.

    def install_user_ruleset(
        self, name: str, content: str, *, env: dict[str, str] | None = None
    ) -> None:
        """Install a user-level ruleset; default no-op that announces itself."""
        print(UNSUPPORTED_RULESET_NOTICE)

    def user_ruleset_path(self, name: str, *, env: dict[str, str] | None = None) -> Path | None:
        """Path to the installed ruleset, or ``None`` when unsupported."""
        return None

    def user_ruleset_status(
        self, name: str, content: str, *, env: dict[str, str] | None = None
    ) -> str:
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

    # -- session retention ----------------------------------------------------
    #
    # Harnesses delete their own session transcripts on a schedule.  A caller
    # holding a long-lived pointer at a transcript (camp's bookmarks) wants to
    # warn BEFORE that deletion, which needs the window — expressed here in days
    # and read from wherever the harness configures it (that location, and the
    # setting's name, are harness knowledge and stay in the harness module).
    #
    # Same degrading default as the two seams above: ``None`` means "this harness
    # has no retention window to report".  A caller must then skip its warning
    # silently — a guessed window would warn about deletions that never come.

    def session_retention_days(self, *, env: dict[str, str] | None = None) -> int | None:
        """Days a session transcript survives before the harness cleans it up.

        Returns ``None`` when the harness does not expire transcripts or cannot
        report the window.  Implementations return their own documented default
        when the setting is simply unset, and never raise for an unreadable or
        malformed config — a retention hint is advisory, and crashing a report
        over it is worse than not showing it.
        """
        return None

    def session_retention_setting(self) -> str | None:
        """Name of the setting controlling the retention window, or ``None``.

        A warning that a transcript is about to be deleted is only actionable if
        it says what to change — but the SPELLING of that setting is
        harness-specific knowledge, so a core caller asks for it here rather than
        naming any harness's key itself.  ``None`` means "not reportable", and a
        caller must then omit the remedy rather than invent one.
        """
        return None

    # -- session launch & enumeration ------------------------------------------
    #
    # Launching a session means starting a brand-new one (as opposed to resuming
    # an existing one via ``session_resume``). Enumeration lists sessions already
    # running. Both are CONCRETE with degrading defaults, following the same
    # convention as ``session_resume`` above — ``None`` means "this harness has
    # no such concept" for every method here EXCEPT ``session_launch`` (see its
    # docstring for that deliberate divergence).
    #
    # The seam never execs and never sets a child's cwd: rooting the child at
    # ``workspace`` and applying the env scrub returned by
    # ``session_launch_env_unset`` are the exec-owning caller's job, done at
    # exec time, not here.
    #
    # Both-or-neither invariants (enforced by ``test_harness.py``, not by this
    # class): a harness that overrides ``session_launch`` must override
    # ``session_launch_modality`` and ``session_launch_env_unset`` too — all
    # three non-``None`` together, or all three left at the base ``None``
    # together, never a partial trio. A non-``None`` modality must additionally
    # be a member of :data:`MODALITIES`, not merely non-``None``. Likewise,
    # ``session_enumerate`` and ``parse_session_list`` must be overridden
    # together or not at all. A half-implemented harness is worse than an
    # unimplemented one: it advertises a capability it cannot actually honor.

    def session_launch(self, workspace: Path, session_id: str) -> list[str] | None:
        """DIVERGES: raises :class:`HarnessError` on a malformed ``session_id``, where
        ``session_resume`` returns ``None`` for the same input.

        Returns the argv that starts a brand-new session, or ``None`` if the
        harness cannot launch sessions at all.

        The divergence above is from the ``None``-on-malformed-input
        convention used elsewhere in this module. A consumer who learned "check for ``None``, else
        use the argv" from ``session_resume`` and applies that uniformly here
        will hit an uncaught exception on their first bad id — most likely at
        the call site that hands this method a freshly-generated, unvalidated
        id. Elsewhere in this module, ``None`` from any of these seams means
        "the harness has no such concept" for a fixed, harness-level
        capability; ``session_launch`` is the single exception to that rule.

        ``session_launch`` is constant-valued per harness: for a given
        harness it never returns ``None`` for a particular ``session_id`` —
        ``None`` from a concrete override means only "this harness cannot
        launch sessions at all," never "not for this argument."

        Performs no filesystem validation of ``workspace`` — it does not
        check that ``workspace`` exists, is a directory, or is writable.

        A harness may legitimately ignore ``workspace`` entirely (Claude
        Code does — it roots a launched session on the process's cwd at exec
        time, not on any argument). Passing ``workspace=A`` while the caller
        ends up exec'ing at cwd ``B`` then yields valid-looking argv for the
        wrong location, with no error signal from this method. Every future
        harness author must see this before deciding to honor or ignore
        ``workspace``.

        The seam does not exec: the returned argv still needs the caller to
        root the child process at ``workspace`` and apply
        :meth:`session_launch_env_unset` at spawn time.
        """
        return None

    def session_launch_modality(self) -> Modality | None:
        """The modality a launched session requires, or ``None`` if unsupported.

        ``None`` means this harness has no launch concept at all — distinct
        from a harness that launches but has no meaningful modality to
        report (not currently possible in this vocabulary, but the ``None``
        here is reserved for "unsupported", not "not applicable").
        """
        return None

    def session_launch_env_unset(self) -> list[str] | None:
        """Env var names a launching caller must scrub before spawning, or ``None``.

        Launching a new session from inside an existing harness session
        leaks parent markers and credentials to the child. With Claude Code
        specifically, an unscrubbed child becomes invisible to enumeration
        and inherits the parent's session access token.

        The tmux case: a tmux server started by an agent propagates its
        launch-time environment to every pane subsequently opened in that
        server, so the scrub must happen at spawn — via ``env -u`` on the
        launch command or ``tmux set-environment -u`` on the target
        variables — not after the fact. A pre-existing, user-started tmux
        server is typically already clean, but the scrub is mandatory
        regardless of that; a caller must not skip it based on a guess about
        the server's provenance.

        The returned list is a FLOOR, not an exhaustive guarantee: a harness
        CLI may add new leaking variables in a later version, so a caller
        must not treat this list as proof of complete coverage.

        Returns ``None`` when the harness has no such concept (e.g. nothing
        to scrub, or launch isn't supported at all).
        """
        return None

    def session_enumerate(self, workspace: Path | None = None) -> list[str] | None:
        """Return raw, harness-native session listing output, or ``None``.

        ``workspace``, when given, scopes the listing with PREFIX semantics
        — "rooted under" — so a session launched in a member worktree
        beneath ``workspace`` is in scope, not just a session launched
        exactly at ``workspace`` itself.

        Returns ``None`` when the harness has no enumeration concept.
        Parsing the returned output into :class:`SessionRecord` values is
        :meth:`parse_session_list`'s job, not this method's.
        """
        return None

    def parse_session_list(self, output: str) -> list[SessionRecord] | None:
        """Parse :meth:`session_enumerate`'s raw output into records.

        Failure semantics, binding on every implementation:

        - Returns ``None`` ONLY from this base default — "this harness has
          no enumeration/parse concept." A concrete override must never
          return ``None``.
        - Returns ``[]`` ONLY for a well-formed, empty listing.
        - RAISES :class:`HarnessError` on output that cannot be decoded —
          never silently drops it and never returns ``None`` for that case.

        Result order preserves the harness's own output order. Every parsed
        ``session_id`` satisfies the same validity guard that
        :meth:`session_resume` applies to its ``session_id`` argument.
        """
        return None
