"""Harness-profile seam — config-shaped harness profile (claude default).

A group-level optional [harness] config block declares how doc-surfacing,
context injection, and trust pre-seeding behave for the workspace:

    [harness]
    doc_files = ["AGENTS.md"]    # which doc(s) to surface (default ["CLAUDE.md"])
    inject    = "stdout"         # context-injection channel
    pretrust  = false            # opt out of the claude trust pre-seed
    cwd       = "{workspace}"    # resolved launch/trust dir

with {slug} / {workspace} substitution. When the block is ABSENT the baked-in
claude default applies.

resolve_harness_profile merges the [harness] block over the claude default ONCE
into a frozen HarnessProfile (doc_files + inject + pretrust + cwd); callers read
fields off it directly. camp does not launch the harness; activation is a
separate seam.

resolve_harness_profile answers what the CONFIG says; harness_for takes the next
step and turns that configured binary name into the trailhead harness object whose
seam answers transcript, resume, enumeration, and retention questions. Every camp
surface that needs a harness — launch confirmation, session enumeration, resume —
starts there.

harness_store_for takes one step further: a group names a HARNESS, but the
session queries every read surface performs (listing, resume, kill, remove)
address a CREDENTIAL STORE — the harness bound to whichever account, or none,
the group declared. It returns a HarnessStore carrying that binding's
environment alongside the harness, so a caller can hold one value per store
instead of re-deriving the binding at every call. camp names no credential
location of its own anywhere in this module; the store-to-environment
translation is asked of the harness, every time.

`_CLAUDE_DEFAULT["binary"]` / HarnessProfile.binary:
should_pretrust() → is_claude_launch() reads its basename to detect a `claude`
binary and scope the trust pre-seed. It is a single binary NAME (no argv: nothing
is launched).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only; trailhead may not be installed
    from trailhead.harness.base import Harness

# Baked-in claude default (applied when no [harness] block is configured). Only
# the binary name is load-bearing now — is_claude_launch() reads its basename to
# scope the trust pre-seed to claude launches.
_CLAUDE_DEFAULT = {
    "binary": "claude",
    "cwd": "{workspace}",
}


def _substitute(token: str, *, slug: str, workspace: str) -> str:
    return token.format(slug=slug, workspace=workspace)


@dataclass(frozen=True)
class HarnessProfile:
    """The fully-resolved harness profile: doc_files + inject + pretrust + cwd
    (plus the retained `binary` name that pretrust scoping reads).

    Built ONCE by resolve_harness_profile by merging a [harness] block over the
    baked-in claude default. `pretrust` gates the claude trust pre-seed
    (bring_up_workspace); it is only acted on for claude launches (see
    is_claude_launch).
    """

    binary: str
    cwd: str
    doc_files: list[str]
    inject: str  # "stdout" | "claude-hook"
    pretrust: bool  # opt-in to the claude trust pre-seed (see should_pretrust)

    def resolved_cwd(self, *, slug: str, workspace: Path | str) -> Path:
        """Single source of the substituted launch/trust cwd.

        Accepts workspace as Path or str; returns the resolved Path after
        {slug}/{workspace} substitution. Used by pretrust_workspace to determine
        the trust target without duplicating the substitution logic.
        """
        return Path(_substitute(self.cwd, slug=slug, workspace=str(workspace)))

    def is_claude_launch(self) -> bool:
        """True when the harness binary is `claude` (by basename).

        Keyed on Path(self.binary).name == "claude". A bare/empty binary is
        impossible in practice (resolve_harness_profile falls back to the "claude"
        default), and an empty string answers False rather than raising — the
        predicate honors its "answer, don't raise" contract for a directly-built
        profile.
        """
        return Path(self.binary).name == "claude"

    def should_pretrust(self) -> bool:
        """Whether camp should pre-seed the claude trust flag for this launch.

        The single, declarative decision the bring-up call site asks (so harness
        scoping lives on the profile, not as a separate guard at the call site).

        Fires when pretrust is opted-in AND ANY positive claude signal holds:
          - the harness binary's basename is `claude` (covers the bare default and
            an explicit `[harness] binary = "claude"` block), OR
          - the native claude-hook inject channel is selected — the declarative
            opt-in for a claude launched under a wrapper/renamed binary, where the
            basename check alone would false-negative.

        Using OR (not the inject signal alone) is deliberate: an explicit
        `[harness]` block without `inject` defaults inject to "stdout", so an
        inject-only gate would wrongly skip pretrust for a plain `["claude", …]`
        launch.
        """
        return self.pretrust and (self.is_claude_launch() or self.inject == "claude-hook")


def resolve_harness_profile(group: dict[str, Any]) -> HarnessProfile:
    """Merge the [harness] block over the claude default ONCE → a frozen profile.

    Per-field merge over _CLAUDE_DEFAULT for binary/cwd/doc_files: a partial
    block only overrides the fields it lists. The inject default is the one
    intentional asymmetry (preserved exactly):
      - NO [harness] block (bare claude default)  → "claude-hook" (native hook)
      - a [harness] block WITHOUT inject          → "stdout" (safe universal floor
        for a harness whose injection contract we have not opted into)
      - a configured inject value                 → that value (enum-validated by
        group_config at load time).

    `pretrust` has NO such asymmetry — it defaults to True everywhere. Harness
    scoping is NOT carried by the default; it lives in HarnessProfile.should_pretrust()
    (pretrust AND a positive claude signal), which is what prevents a non-claude
    [harness] block from getting a claude trust write.
    """
    harness = group.get("harness")
    inject = "claude-hook" if harness is None else harness.get("inject", "stdout")
    harness = harness or {}

    return HarnessProfile(
        binary=harness.get("binary") or _CLAUDE_DEFAULT["binary"],
        cwd=harness.get("cwd") or _CLAUDE_DEFAULT["cwd"],
        doc_files=list(harness["doc_files"]) if "doc_files" in harness else ["CLAUDE.md"],
        inject=inject,
        pretrust=harness.get("pretrust", True),
    )


def harness_for(group: dict) -> Harness | None:
    """Return the trailhead harness backing *group*, or None if unrecognized.

    ``None`` means camp cannot name a harness for this group at all. Callers
    degrade on it exactly as they degrade on a harness that answers ``None`` from
    the seam itself — a user cannot act on the difference, and neither outcome
    yields the transcript, argv, or retention window that was asked for.

    Both imports are deferred: camp ships as a standalone CLI, so a caller that
    runs without trailhead installed must fail inside a caller's own guard rather
    than at module import.
    """
    from trailhead.harness import HarnessError, get_harness

    binary = Path(resolve_harness_profile(group).binary).name
    try:
        return get_harness(binary)
    except HarnessError:
        return None


@dataclass(frozen=True)
class HarnessStore:
    """One (harness, credential store) camp can ask about sessions.

    ``account`` is the declaring group's ``[launch] account`` value, exactly as
    written in its config — ``None`` when the group declared none. ``env`` is
    the full environment session queries against this store must run under:
    the caller's own environment, scrubbed and then bound to ``account`` by
    ``harness.session_launch_env_unset``/``session_launch_env_set`` — the same
    two calls, in the same order, that a launch or resume applies to a pane, so
    a listing binds to exactly the directory a session on this store would
    actually use. See :func:`harness_store_for`, which is the only place this
    is built.

    Every ``Harness`` method BUT ``session_transcripts`` and
    ``session_retention_days`` is proxied straight through to ``harness`` via
    ``__getattr__``, so a caller holding one of these needs no isinstance check
    to use it as a harness. Those two are the exceptions because both take
    their own ``env`` keyword, and a caller iterating a pool of stores must get
    THIS store's binding no matter what it passes — that is the one property
    this type exists to guarantee.
    """

    harness: Any
    account: str | None
    env: dict[str, str]

    def __getattr__(self, item: str) -> Any:
        return getattr(self.harness, item)

    def session_transcripts(self, workspace: Path | None = None, *, env: dict[str, str] | None = None):
        return self.harness.session_transcripts(workspace, env=self.env)

    def session_retention_days(self, *, env: dict[str, str] | None = None) -> int | None:
        return self.harness.session_retention_days(env=self.env)


def harness_store_for(group: dict, *, env: dict[str, str] | None = None) -> HarnessStore | None:
    """The (harness, credential store) *group*'s declared account resolves to.

    ``None`` when camp cannot name a harness for *group* at all (the same
    degrading posture :func:`harness_for` takes), OR when the harness refuses
    to bind the declared account, OR states no launch support at all
    (``session_launch_env_set``/``session_launch_env_unset`` answering
    ``None``). All three degrade the SAME way here — contributing no
    candidate — because this is a read path: one group whose store camp
    cannot resolve must not blank the pool's answer for every other group's
    store, the same reasoning that makes an unrecognized harness contribute
    nothing rather than fail the whole lookup.

    Deliberately calls ``session_launch_env_set``/``session_launch_env_unset``
    directly rather than going through the launch engine's own account
    resolution: that path also PRINTS an operator-facing notice on a
    defaulted-binding failure, appropriate for a launch an operator just
    asked for, not for a background read every listing, kill, and resume
    performs against every declared store on the machine.

    The scrub is applied before the binding, matching the launch engine's own
    order: a harness may state its default as a variable's ABSENCE and a
    declared account as an assignment of that same name, and merging the
    binding over the raw environment would leave an ambient value in place
    exactly when the harness meant it removed.
    """
    harness = harness_for(group)
    if harness is None:
        return None

    resolved_env = dict(env) if env is not None else dict(os.environ)
    account = (group.get("launch") or {}).get("account")
    if not isinstance(account, str):
        account = None

    try:
        binding = harness.session_launch_env_set(account, env=resolved_env)
        scrub = harness.session_launch_env_unset()
    except Exception:  # noqa: BLE001 — a store camp cannot bind to contributes nothing
        return None
    if binding is None or scrub is None:
        return None

    store_env = {k: v for k, v in resolved_env.items() if k not in set(scrub)}
    store_env.update(binding)
    return HarnessStore(harness=harness, account=account, env=store_env)
