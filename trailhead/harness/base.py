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
    dropped, never passed through. ``cwd`` is the session's LAUNCH root — the
    directory it was started under — never its current working directory,
    which may have since changed.
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


@dataclass(frozen=True)
class SessionTranscript:
    """One on-disk session transcript, as enumerated from a harness's own store.

    This is a TRANSCRIPT, not a "resumable session" — the raw pool a harness
    reports here includes sessions that are still live and therefore not
    actually recoverable. Subtracting the live set to produce an
    operator-facing "what can I resume" listing is the CALLER's job; this
    seam makes no liveness judgment and its noun stays honest about that.

    ``session_id`` is the transcript's filename stem, already checked against
    the same validity guard :meth:`Harness.session_resume` applies to its own
    argument — every id this method yields is therefore already safe to pass
    straight into ``session_resume`` without a caller re-validating it.

    ``cwd`` is the session's START-of-session working directory, read from
    INSIDE the transcript — never inferred from where the transcript file
    happens to live on disk (a harness's on-disk layout for its store is that
    harness's own knowledge, and any munging it does to build a directory name
    is typically lossy and not safely reversible). ``None`` means the harness
    could not extract a cwd — an unreadable, undecodable, or cwd-less
    transcript — and a caller must report that as "unreadable", never guess a
    location for it.

    ``modified_at`` is always timezone-aware UTC, taken from the transcript
    file's own mtime — never naive, and never a timestamp parsed out of the
    transcript's own content (which this seam does not read for that
    purpose).
    """

    session_id: str
    cwd: Path | None
    modified_at: datetime


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
    # that offers to bring a dead session back needs the window to say why one is
    # no longer there — expressed here in days and read from wherever the harness
    # configures it (that location, and the setting's name, are harness knowledge
    # and stay in the harness module).
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

    def session_launch(
        self,
        workspace: Path,
        session_id: str,
        *,
        session_name: str | None = None,
    ) -> list[str] | None:
        """DIVERGES: raises :class:`HarnessError` on a malformed ``session_id``, where
        ``session_resume`` returns ``None`` for the same input.

        Returns the argv that starts a brand-new session, or ``None`` if the
        harness cannot launch sessions at all.

        ``session_name`` is the caller's requested human-visible name for the
        session — the label a harness's own client surfaces (a companion app,
        a web UI) display for it. It is a hint: a harness with no nameable
        sessions ignores it, and ``None`` means the caller has no preference,
        leaving the harness's own default naming in effect. A concrete
        override that honors it must validate it as an inert argv token with
        the same rigor as ``session_id`` and raise :class:`HarnessError` on a
        malformed value rather than passing it through.

        The divergence above is from the ``None``-on-malformed-input
        convention used elsewhere in this module. A consumer who learned "check for ``None``, else
        use the argv" from ``session_resume`` and applies that uniformly here
        will hit an uncaught exception on their first bad id — most likely at
        the call site that hands this method a freshly-generated, unvalidated
        id. The raise guards path/argv safety (the id must be a safe token
        before it reaches the launch argv), not id validity in any broader
        sense — a non-UUID like ``"sess-1"`` passes this guard and only fails
        later, at exec, if the harness's CLI itself rejects it. Elsewhere in
        this module, ``None`` from any of these seams means
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

        Returns ``None`` only when launch itself is unsupported. A
        launch-capable harness with nothing to scrub returns ``[]``, per the
        both-or-neither invariant above — ``None`` here means "launch
        unsupported", never "nothing to scrub".
        """
        return None

    def session_enumerate(self, workspace: Path | None = None) -> list[str] | None:
        """Return the argv that lists this harness's live sessions, or ``None``.

        Like ``session_resume`` and ``session_launch``, the seam owns the
        ARGV — this method never execs; a caller runs it and hands the
        output to :meth:`parse_session_list`.

        ``workspace``, when given, scopes the listing with PREFIX semantics
        — "rooted under" — so a session launched in a member worktree
        beneath ``workspace`` is in scope, not just a session launched
        exactly at ``workspace`` itself.

        An implementation MAY raise :class:`HarnessError` on a ``workspace``
        that is unsafe to place in its argv — one whose string form begins
        with ``-`` reads as a flag in the value slot of whatever option
        carries it. That is argv safety, not filesystem validation: like
        :meth:`session_launch`, this method never checks that ``workspace``
        exists. Note the asymmetry a caller must respect — ``session_id`` is
        guard-checked wherever it reaches an argv, so it is provably free of
        shell-active characters; ``workspace`` is not. The returned argv is
        safe to EXEC; it is not guaranteed safe to hand to a shell.

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
          Every raised error names the offending field (or the decode
          failure, which belongs to no single field) and carries a
          BOUNDED excerpt of the raw output — bounded across the whole
          payload, not per field, since a field such as ``cwd`` can carry a
          username-bearing path that must never spill unbounded into logs.
        - A record whose ``session_id``, ``cwd``, or ``kind`` is absent,
          null, or of the wrong type RAISES — those three are required, and
          neither a record's identity nor its location can be guessed. This
          is a positive rule, not merely the complement of the next one.
        - ``name``, ``pid``, and ``started_at`` map to ``None`` when absent,
          null, or of the wrong type — they are optional fields, and a
          malformed value degrades rather than raising. "Cannot be used"
          counts as malformed even when the type is right (an out-of-range
          ``started_at``, say): degrading costs one field, while raising
          would discard every well-formed record in the same payload.
        - An unrecognized ``kind`` is KEPT in the record, with
          ``controllable=False`` — a session of a kind this harness version
          doesn't yet classify is still a real, live session, not one to
          drop.

        Result order preserves the harness's own output order. Every parsed
        ``session_id`` satisfies the same validity guard that
        :meth:`session_resume` applies to its ``session_id`` argument.
        """
        return None

    # -- session transcript enumeration ----------------------------------------
    #
    # Enumerating a harness's session-transcript STORE is a different capability
    # from ``session_enumerate`` above: that method lists LIVE processes via the
    # harness's own CLI, so a session that has already exited is invisible to
    # it. This method instead reads the harness's on-disk transcript store
    # directly, so it can also see sessions that are no longer running. All
    # store-layout knowledge — where the store lives, how it is organized, what
    # counts as a "top-level" transcript versus a nested one, how a
    # start-of-session cwd is extracted from a transcript's contents — is
    # harness-specific and stays inside that harness's module (Axiom 1); the
    # core only ever receives resolved :class:`SessionTranscript` rows or
    # ``None``.
    #
    # Same degrading-default convention as every other seam in this module:
    # ``None`` means "this harness has no recovery concept at all," and a
    # caller must report that rather than assume an empty store. A concrete
    # override must NEVER raise for a missing or unreadable store: a store
    # that does not exist on disk yields ``[]``, and one individual transcript
    # this harness cannot open, decode, or find a cwd inside still yields a
    # row — with ``cwd=None`` — rather than being silently dropped. A concrete
    # override must also never read an entire transcript into memory to answer
    # this question; real transcripts run to hundreds of megabytes, and a
    # bounded read is part of the contract, not an incidental optimization.

    def session_transcripts(
        self, workspace: Path | None = None, *, env: dict[str, str] | None = None
    ) -> list[SessionTranscript] | None:
        """Enumerate this harness's on-disk session transcripts, or ``None``.

        Returns TRANSCRIPTS, not "resumable sessions" — the pool this method
        returns may include sessions that are still live. A caller wanting an
        operator-facing recoverable listing must subtract the live set itself
        (using whatever this harness's own live-enumeration seam reports);
        this method makes no liveness judgment of its own.

        ``workspace``, when given, scopes the listing to rows whose extracted
        ``cwd`` is equal to or under ``workspace`` — a SUBTREE test on
        RESOLVED paths, matching :meth:`session_enumerate`'s "rooted under"
        prefix semantics. This is never a match against how this harness's
        store happens to lay directories out on disk: a store's on-disk
        directory-naming scheme is typically a lossy encoding of a path (for
        example, collapsing more than one distinct source character to the
        same output character), so recovering a real path from a directory
        name is ambiguous and MUST NOT be relied on. A row whose ``cwd`` is
        ``None`` is out of scope for any workspace-scoped call, and in scope
        only for the unscoped (``workspace=None``) global call.

        ``env`` overrides the process environment (hermetic tests, and callers
        that already carry an injected env); ``None`` means ``os.environ``.

        Returns ``None`` when the harness has no transcript-store concept at
        all. A concrete override returns ``[]`` for a missing or empty store
        — never ``None`` — and never raises for an individual transcript it
        cannot read or parse; that transcript still yields a row, with
        ``cwd=None``.

        Ordering is UNSPECIFIED at this seam — a caller that needs a
        particular order (for example, most-recently-modified first) must
        sort the result itself rather than rely on the order returned here.
        """
        return None
