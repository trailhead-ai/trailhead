"""``lore sync`` — stage, commit, pull, and push EVERY configured vault.

The no-argument form covers all configured vaults; ``--vault <name>`` narrows to
one. Syncing only the ``default``-scope vault is not an option the CLI offers,
because record writes route by scope: from a repo bound to a product-scope vault,
``lore record create`` writes there while a ``default``-only sync commits nothing
of what was just written — and still prints "Committed / Pushed to origin". Every
per-vault line of output is therefore labeled with the vault it describes, so a
mismatch between where records land and where they are committed is visible at
the call site rather than discovered when a disk fails. (The one run-level line —
the closing reindex report — is unlabeled, because the index spans all vaults.)

**Sync is bidirectional: commit → pull → push, in that order.** The same vault
lives on multiple devices, so a push-only sync leaves each device blind to
records captured on the others — and once histories diverge, every push is
rejected while the error text blames the network. Local changes are committed
FIRST so the pull rebases real commits (never stashes a dirty tree), then
remote commits are integrated, then the combined history is pushed.

**Partial failure is per-vault, never fatal to the run.** A vault that is missing,
is not its own git toplevel, fails to stage/commit, or hits a rebase conflict is
reported and *skipped*; the remaining vaults are still synced and the command
exits 1 at the end. One broken vault must not be able to strand the others
uncommitted — that is the same silent-data-loss shape this module exists to close.

**Network failures stay soft** (exit 0 with a notice): a failed fetch or push
leaves the commit durable locally, and a later ``lore sync`` retries both. A
**failed rebase is hard** (exit 1) — most commonly a genuine conflict, which
``lore resolve <vault>`` exists to settle and which the notice names as the
remedy. The rebase is aborted before reporting, and the abort is verified: if the
vault somehow remains mid-rebase, the notice says so instead of promising a clean
state that does not exist (``lore resolve`` picks the vault up from either state,
so the remedy is the same one).

**A pull that landed commits triggers a search-index rebuild** at the end of the
run: the index is a derived projection of the vault tree, and records written on
another device have never been projected on this one — without the rebuild,
``lore search`` would silently miss exactly the records sync just fetched.

**``--pull-only`` is the non-destructive half of all this.** It fetches and
integrates origin's commits and does nothing else: no staging, no commit, no
push. Integration is further gated on a CLEAN working tree — the full sync may
rebase a dirty vault only because it commits first, and a pull-only run has no
such commit to rebase onto. A dirty vault is therefore fetched (which touches no
file) and reported as "N commit(s) behind", never rebased. That is what makes it
safe to run implicitly, which is exactly what :func:`implicit_pull` does: every
lore write path calls it first, throttled to one fetch ATTEMPT per vault per
:data:`FRESHNESS_WINDOW_SECONDS`, reporting on stderr only, and unable to fail
the write it precedes. See :func:`implicit_pull` for the three properties every
caller depends on.

**Only the tree-mutating half runs under the vault write lock.** ``git add -A`` →
``commit`` and the pull's ``rebase`` / ``reset --hard`` are held under
:func:`lore.locking.vault_write_lock`, because a concurrent ``move_record`` is a
copy → index-repoint → delete sequence and an unlocked ``git add -A`` can observe
the record at both endpoints. ``fetch`` and ``push`` are deliberately OUTSIDE it:
they never touch the working tree, and lore's lock is blocking with no timeout —
holding it across a network round-trip would let one hung remote starve every
local writer. Hold time is therefore bounded by local git work.

**The commit phase locks every SUCCESSFULLY-LOCKED target vault TOGETHER, not
one at a time.** ``cmd_sync`` stages+commits every target of the run (every
configured vault, or just ``--vault <name>`` when given) with each target's
:func:`lore.locking.vault_write_lock` entered into one shared
``contextlib.ExitStack``, in the same sorted-path order
:func:`lore.locking.vault_write_locks` itself uses, before touching any pull or
push. A per-vault-only lock would let a cross-vault ``move_record`` run its
whole copy → repoint → delete sequence strictly BETWEEN two different targets'
visits — sync would then commit the destination vault's new copy while the
source vault's own commit (dropping the now-moved record) never happens this
run, leaving the record readable as committed in both vaults until a later
sync catches up. Locking every target up front closes that window: whichever
of sync or the move starts first, the other waits for it to finish in full
before touching any of the shared vaults. Acquiring one target at a time
(rather than delegating to ``vault_write_locks`` as one opaque call) also means
a lock that fails to acquire is attributed to that exact vault, with no need to
infer it from the failing exception — see ``cmd_sync``'s own docstring.

**Caveat: a git operation that PROMPTS is unbounded.** A commit whose signing key
needs a gpg pinentry passphrase (or any git helper that waits on a human) blocks
inside the lock, and because the lock has no timeout every other vault writer
queues behind that prompt until it is answered. Under the batched commit phase
this blast radius is the WHOLE phase, not just one vault: a prompt stalling
ONE target's commit blocks every other configured vault's lock acquisition
too, since they're all held together for the phase's duration. The lock
helper's own ``lore: waiting for the vault write lock`` stderr notice —
printed on any wait past ~2 seconds — is the diagnostic that distinguishes
this from a hang.
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Callable

from .. import locking
from ..record import model as record_model
from .common import (
    SYNC_REFUSAL_MESSAGES,
    _git,
    _resolve_all_vaults,
    _resolve_lore_state_dir,
    _shared_vault_paths,
    _vault_has_upstream,
    _vault_head_branch,
    _vault_is_git_toplevel,
    _vault_mid_rebase,
    _vault_unpushed,
    _vault_upstream_ref,
    machine_state_key,
    vault_refusal_condition,
)
from .init import _SITES_DIR

DEFAULT_SYNC_MSG = "lore: sync vault"

#: The per-vault outcome when that vault's write lock is already held by a
#: concurrent writer at the moment the sweep/manual `lore sync` tries to
#: acquire it — a readable value, not a message string a caller has to parse
#: back out of stdout. A person at a terminal is not stranded behind the
#: holder: the vault is skipped for this run and reported, not waited on.
SYNC_IN_PROGRESS = "in-progress"

#: How many times :func:`_push_one` replays onto a moved history and re-pushes
#: before giving up. Configurable (see :func:`resolve_publish_retry_max`)
#: because how much a moved history is worth retrying against is an operator
#: call, not a universal constant — a low-traffic vault rarely needs more than
#: one replay, a busy multi-device vault may race the forge more often.
DEFAULT_PUBLISH_RETRY_MAX = 3

#: Outcomes :func:`_push_one` can produce beyond a plain successful push (which
#: reports :data:`PUBLISH_OK`) or the pre-existing offline/no-op paths (also
#: :data:`PUBLISH_OK` — this module's existing soft-network contract is
#: unchanged). Readable values, not message strings a later caller has to
#: parse back out of stdout/stderr — the same shape as ``SYNC_IN_PROGRESS``
#: above and ``vault_refusal_condition``'s ``SYNC_REFUSAL_*`` constants.
PUBLISH_OK = "ok"

#: The push was rejected, but the published history did NOT move — a
#: pre-receive hook, a protected branch, or a permission refusal, not a race.
#: No retry can clear this: the local commit is left in place (vault clean,
#: diverged) for a person, or a later sweep, to settle.
PUBLISH_HOLDING = "holding"

#: The published history moved on every attempt through the configured
#: maximum. Hard failure — distinct from a generic push failure so a caller
#: can tell "the forge just won't sit still" apart from "something is broken".
PUBLISH_RETRIES_EXHAUSTED = "retries-exhausted"

#: A judgment conflict at either replay site (the pull's rebase or the push's
#: moved-history replay) that the resolver could not settle without a
#: person — `cli.resolve.resolve_for_sweep` aborted the replay whole and left
#: the vault clean, diverged, and marked held. **Breaking change** (see
#: `CHANGELOG.md`): before this literal existed, exactly this vault state
#: reported `holding` and printed a `lore resolve` remedy; a caller keyed on
#: either signal for this case now sees `awaiting-person` instead, because a
#: resolver failure and a genuine judgment conflict are no longer the same
#: outcome (see :data:`PUBLISH_HOLDING` and the failure-reason constants
#: below). A caller that only checked "exit code != 0" is unaffected: both
#: outcomes still exit non-zero.
SYNC_AWAITING_PERSON = "awaiting-person"

#: A conflict no field-wise merge could settle, on a host that declares it
#: makes no vault content: `cli.resolve.resolve_for_sweep` took the published
#: history outright and the vault converged with nobody present (AC37). An
#: INTERNAL ending only — deliberately NOT a member of :data:`SYNC_OUTCOMES`:
#: the vault's reported outcome is plain "converged", because from the
#: owner's side nothing happened but records arriving a little later than
#: they otherwise would. It exists so this module can tell that ending apart
#: from :data:`PUBLISH_OK` without re-deriving it from git state: a discard
#: leaves nothing to push, so the run must not report `published`, and it
#: must not attempt a push of its own either.
PUBLISH_DISCARDED = "discarded"

#: Closed failure-reason literals reported ALONGSIDE a `holding` outcome —
#: never alone, and never in place of it (the row a person or a caller reads
#: stays "holding" either way; see the module's own council-review note on
#: this task). `FAILURE_POLICY` is a resolver failure: a record the graph
#: guards refuse, a `rebase --continue` that fails, the step ceiling, or an
#: unreadable index — `cli.resolve.ResolveError` raised from
#: `resolve_for_sweep` itself, never a judgment conflict (that is
#: :data:`SYNC_AWAITING_PERSON`, a DIFFERENT outcome entirely).
#: `FAILURE_REMOTE_REJECTION` is the forge's own doing: a push rejected where
#: the published history did NOT move (a pre-receive hook, a protected
#: branch, a permission refusal) — no resolver is ever consulted for this
#: one, because there is no conflict to merge. One is cleared by fixing this
#: host's data; the other is cleared by fixing the forge's policy or the
#: credential pushing to it — indistinguishable in the row's text, which is
#: exactly why the tag exists as a separate, closed, git/remote-text-free
#: fact rather than folded into prose nobody reads until it matters.
FAILURE_POLICY = "policy-failure"
FAILURE_REMOTE_REJECTION = "remote-rejection"
SYNC_FAILURE_REASONS = frozenset({FAILURE_POLICY, FAILURE_REMOTE_REJECTION})

#: The closed outcome vocabulary `lore sync --json` reports, one member per
#: vault entry in :func:`render_sync_json`'s document. Three members are the
#: exact literal values of this module's own outcome constants above
#: (:data:`SYNC_IN_PROGRESS`, :data:`PUBLISH_HOLDING`,
#: :data:`PUBLISH_RETRIES_EXHAUSTED`) — `cmd_sync` stores those constants
#: directly into its `outcomes` dict, so no separate mapping step exists for
#: them. "converged", "published", and "refused" are decided in `cmd_sync`
#: itself (there is no dedicated constant for each — see its outcome-derivation
#: comment) and asserted here as the literal strings a caller reads.
#: :data:`SYNC_AWAITING_PERSON` is the newest member — added, never retired
#: anything (see `CHANGELOG.md`). No value outside this set is ever emitted;
#: a caller may treat it as a closed enum.
SYNC_OUTCOMES = frozenset(
    {
        SYNC_IN_PROGRESS,
        "converged",
        "published",
        PUBLISH_HOLDING,
        "refused",
        PUBLISH_RETRIES_EXHAUSTED,
        SYNC_AWAITING_PERSON,
    }
)

#: The report schema, documented once and shown both on `lore sync --help`
#: (where the reader is choosing a form) and on `--json` itself — mirrors
#: `cli/resolve.py`'s `_REPORT_SCHEMA`.
_SYNC_REPORT_SCHEMA = (
    'The machine-readable report is: {"schema", "vaults":[{"vault","outcome"'
    '[,"condition"][,"reason"]}]}. "outcome" is one of "in-progress", '
    '"converged", "published", "holding", "refused", "retries-exhausted", '
    '"awaiting-person" — a CLOSED set; no other value is ever emitted. '
    '"condition" is present only when "outcome" is "refused", naming which '
    "SYNC_REFUSAL_CONDITIONS member (mid-rebase, mid-merge, detached-head, "
    'stale-lock) was found. "reason" is present only when "outcome" is '
    '"holding" AND the cause is known, naming which SYNC_FAILURE_REASONS '
    'member ("policy-failure": the resolver itself could not settle the '
    'vault; "remote-rejection": the forge rejected a push whose history did '
    "not move) applied — a caller can act on the two differently even though "
    "they render identically as prose. A vault that did not reach a "
    "determinate outcome this run — for ANY reason, including but not "
    "limited to a missing vault, a git error while staging, or a failed lock "
    "acquisition — has no entry; its failure is reported on stderr and "
    "reflected only in the exit code, exactly as without --json. A run that "
    "fails before any vault is even selected (an unreadable config, or an "
    "unknown --vault name) prints no document at all."
)


def render_sync_json(entries: list[tuple]) -> dict:
    """Render `lore sync --json`'s report payload.

    *entries* is ``[(vault_name, outcome, condition_or_None[, reason_or_None]), ...]``,
    in the order the vaults were synced — the trailing *reason* element is
    optional so every pre-existing 3-tuple call site keeps working unchanged.
    *outcome* must be a member of :data:`SYNC_OUTCOMES`; *condition* is
    non-``None`` only for a "refused" outcome; *reason* is non-``None`` only
    for a "holding" outcome and must be a member of :data:`SYNC_FAILURE_REASONS`.
    Mirrors `cli/resolve.py`'s `render_json` — a plain dict, printed with
    ``json.dumps(..., indent=2)`` by the caller.

    Raises:
        ValueError: if *outcome* is not a member of :data:`SYNC_OUTCOMES`, or
            *reason* is given but not a member of :data:`SYNC_FAILURE_REASONS`
            — the schema string promises callers a closed vocabulary in both
            cases, and this is what makes that promise a guarantee rather
            than a claim resting on review.
    """
    vaults = []
    for entry in entries:
        name, outcome, condition, *rest = entry
        reason = rest[0] if rest else None
        if outcome not in SYNC_OUTCOMES:
            raise ValueError(
                f"outcome {outcome!r} for vault {name!r} is not in SYNC_OUTCOMES: "
                f"{sorted(SYNC_OUTCOMES)}"
            )
        if reason is not None and reason not in SYNC_FAILURE_REASONS:
            raise ValueError(
                f"reason {reason!r} for vault {name!r} is not in SYNC_FAILURE_REASONS: "
                f"{sorted(SYNC_FAILURE_REASONS)}"
            )
        entry_dict: dict = {"vault": name, "outcome": outcome}
        if condition is not None:
            entry_dict["condition"] = condition
        if reason is not None:
            entry_dict["reason"] = reason
        vaults.append(entry_dict)
    return {"schema": _SYNC_REPORT_SCHEMA, "vaults": vaults}


def resolve_publish_retry_max(env: dict | None = None) -> int:
    """Resolve the max publish-retry attempt count, in precedence order.

    1. ``LORE_PUBLISH_RETRY_MAX``, if set to a valid positive integer string.
    2. The ``publish_retry_max`` key in ``config.json``, if present and a
       positive int (see :func:`lore.vault.config.read_publish_retry_max`).
    3. :data:`DEFAULT_PUBLISH_RETRY_MAX`.

    Mirrors :func:`lore.record_url.resolve_base`'s precedence shape. A
    non-positive or unparseable value at any layer is treated as absent rather
    than raising — a malformed override must not turn "keep retrying a moved
    history" into "never retry at all" (0) or a crash.

    Args:
        env: Optional ``{str: str}`` environment override, used both for
             reading ``LORE_PUBLISH_RETRY_MAX`` and forwarded to
             :func:`lore.vault.config.read_publish_retry_max` for XDG
             resolution. ``None`` reads the real process environment.
    """
    from ..vault import config as vault_config_mod

    source = os.environ if env is None else env
    env_value = source.get("LORE_PUBLISH_RETRY_MAX", "")
    if env_value:
        try:
            parsed = int(env_value)
        except ValueError:
            parsed = None
        if parsed is not None and parsed > 0:
            return parsed

    config_value = vault_config_mod.read_publish_retry_max(env=env)
    if config_value is not None and config_value > 0:
        return config_value

    return DEFAULT_PUBLISH_RETRY_MAX


def _make_emitters(name: str, width: int):
    """Return ``(say, say_err)`` writers that label output with the vault name.

    ``say`` labels its first line and indents continuations to the same column, so
    multiple ``say`` calls against ONE emitter pair read as a single block::

        trailhead:    Committed: lore: sync vault
                      Pushed to origin.

    ``cmd_sync`` gets that block shape only WITHIN one phase: it calls
    ``_make_emitters`` once for the batched commit phase (shared across every
    target) and once more, freshly, per vault for the pull/push phase — a
    vault's commit line and its push line therefore come from two different
    emitter pairs, each independently labeling its own first line, rather than
    one continuous block spanning both phases.

    ``say_err`` always repeats the full label and writes to stderr — an error line
    must identify its vault even when stderr is captured or read on its own, where
    the continuation indent would leave it anonymous.
    """
    label = f"{name}:".ljust(width)
    state = {"first": True}

    def say(text: str) -> None:
        prefix = label if state["first"] else " " * width
        state["first"] = False
        print(f"  {prefix} {text}")

    def say_err(text: str) -> None:
        print(f"  {label} {text}", file=sys.stderr)

    return say, say_err


#: Outcomes of :func:`_pull_one`. ``PULL_OK`` covers both "nothing to pull" and a
#: clean integration; ``PULL_OFFLINE`` means the fetch never reached the remote
#: (soft — and the push is skipped, the network already failed once this run);
#: ``PULL_FAILED`` means the integration failed (hard — most commonly a rebase
#: conflict, which ``lore resolve <vault>`` settles).
PULL_OK = "ok"
PULL_OFFLINE = "offline"
PULL_FAILED = "failed"

#: :func:`_pull_only_one`'s own outcome for a dirty tree it deliberately did not
#: rebase (see its docstring) — distinct from :data:`PULL_OK`, which a caller
#: must be able to tell apart from "nothing needed doing". A dirty tree failed
#: to integrate is not converged: something is uncommitted, and only the full
#: `lore sync` (which commits before it rebases) can clear it.
PULL_DIRTY = "dirty"

#: :func:`_pull_one`'s three conflict-handoff outcomes, reachable only when
#: called with ``resolve_conflicts=True`` (the full sync's own call, never
#: ``_pull_only_one``'s — see :func:`_hand_off_to_resolver`). ``PULL_RESOLVED``
#: means the resolver settled the vault AND already pushed it (its own finish
#: tail does that) — distinct from :data:`PULL_OK`, which never publishes, so
#: the caller knows not to attempt a redundant "did we have anything to
#: publish" push cycle of its own. ``PULL_AWAITING_PERSON`` mirrors
#: :data:`SYNC_AWAITING_PERSON` by value — a separate name here documents
#: which layer produced it.
#: :data:`PULL_DISCARDED` mirrors :data:`PUBLISH_DISCARDED` the same way:
#: the resolver kept the published history and discarded this host's local
#: commits, so — unlike :data:`PULL_RESOLVED` — nothing was published and the
#: caller must not run a push cycle of its own.
PULL_RESOLVED = "resolved"
PULL_AWAITING_PERSON = SYNC_AWAITING_PERSON
PULL_DISCARDED = PUBLISH_DISCARDED


def _fetch_origin(vault: Path, say_err, *, pull_only: bool = False) -> bool:
    """Fetch ``origin``. Returns ``True`` on success, reporting on failure.

    A failed fetch is always SOFT — the network is not the vault. The notice
    differs only in what it promises about the rest of the run: the full sync
    also skips the push (it just proved the remote unreachable), while a
    pull-only run has no push to skip and reports the consequence that actually
    matters to its caller — the vault may be stale.
    """
    rc_fetch, _, stderr_fetch = _git(vault, "fetch", "origin")
    if rc_fetch == 0:
        return True
    if pull_only:
        say_err("notice: fetch failed — the vault may be stale; nothing was integrated")
    else:
        say_err("notice: fetch failed — skipping pull and push; records stay committed locally")
    say_err(f"  fetch error: {stderr_fetch}")
    say_err("  re-run `lore sync` when online")
    return False


#: ``git status``/``git add`` pathspec-magic exclusions applied to every UNSCOPED
#: probe of a vault's tree: the write-lock sidecar (merely taking the lock
#: create-or-opens it) and the ``outpost/`` daemon-config carve-out (deliberately
#: never committed — see :func:`_vault_is_dirty`). Excluding via pathspec magic
#: means a vault whose OWN ``.gitignore`` was scaffolded before ``outpost/`` was
#: added to :data:`config.installer._GITIGNORE_PATTERNS` still reads clean,
#: without depending on that vault ever being re-scaffolded.
_STATUS_EXCLUDE_PATHSPECS = (
    f":(exclude){locking.VAULT_LOCK_NAME}",
    ":(exclude)outpost/",
)


def _vault_is_dirty(vault: Path) -> bool:
    """Return ``True`` iff ``vault``'s working tree has changes to commit.

    Excludes the same two paths :data:`_STATUS_EXCLUDE_PATHSPECS` (and
    :func:`_stage_and_commit_one`) excludes: the write-lock sidecar — merely
    taking the lock create-or-opens ``.lore.lock``, so counting it would make
    every locked vault read as dirty — and the ``outpost/`` daemon-config
    carve-out, which is deliberately never committed. Excluding ``outpost/``
    here, not just at the commit-scope allow-list, matters because this
    function gates whether ``--pull-only`` integrates at all
    (:func:`_pull_only_one`) and whether the implicit pull's own pull-only call
    does the same: without it, ANY vault carrying an `outpost/` directory —
    which is every daemon-managed vault — reads as permanently dirty and never
    integrates.
    """
    rc, out, _ = _git(vault, "status", "--porcelain", "--", ".", *_STATUS_EXCLUDE_PATHSPECS)
    return rc != 0 or bool(out.strip())


def _commits_behind(vault: Path) -> int:
    """Return how many commits ``vault``'s upstream has that HEAD does not."""
    ref = _vault_upstream_ref(vault)
    if ref is None:
        return 0
    rc, out, _ = _git(vault, "rev-list", "--count", f"HEAD..{ref}")
    return int(out) if rc == 0 and out.isdigit() else 0


def _hand_off_to_resolver(
    vault: Path, name: str, say, say_err, *, shared: bool
) -> str:
    """Drive a conflicted vault through the resolver instead of reporting a
    remedy. Called only AFTER the plain rebase that hit the conflict has
    already been aborted (both replay sites abort first, unconditionally,
    exactly as before this task).

    Returns the outcome:

    - :data:`PUBLISH_OK` — the resolver settled the vault field-wise and its
      own finish tail already pushed it.
    - :data:`SYNC_AWAITING_PERSON` — a judgment conflict left the vault held
      for a person, clean and diverged.
    - :data:`PUBLISH_DISCARDED` — this host declares it makes no vault
      content, so the resolver kept the published history and discarded the
      local commits with it. Nothing was published, nothing is held, and
      nothing is said: the vault converged with nobody present.
    - :data:`PUBLISH_HOLDING` — the resolver itself could not reach either
      ending: a :class:`resolve.ResolveError` (a record the graph guards
      refuse, a ``rebase --continue`` that fails, the step ceiling, an
      unreadable index), or a :class:`vault.config.VaultConfigError` from
      the host's own author declaration being present but not a bool, which
      the resolver refuses rather than coerces and which it reads on exactly
      this path. Both are this host's data needing a fix, which is what
      :data:`FAILURE_POLICY` names, so both record it.

    Never raises either of those two named types. If one left
    the vault mid-rebase, that is aborted too before reporting — there is no
    person present to hand a traceback to, so this function's whole job is to
    always produce one of the four determinate endings above. The failure's
    reason and detail are written to :func:`resolve_state.mark_failed`'s
    marker (a named, durable location under ``state_dir("lore")/resolve``) in
    ADDITION to the stderr line, so they survive past this one run's terminal
    — the council review's "goes to the terminal and the host's log" promise,
    and the channel `cmd_sync` reads the reason back from.
    """
    from . import resolve as resolve_mod
    from ..vault import config as vault_config_mod

    # The resolution's own finish tail pushes, and a push refused by the forge
    # records its own reason there. Read what is on disk before handing over,
    # so a marker this attempt writes can be told from one an earlier run left.
    marker_before = resolve_mod.resolve_state.read_failed_marker(vault)
    try:
        report = resolve_mod.resolve_for_sweep(vault, name, shared=shared)
    except (resolve_mod.ResolveError, vault_config_mod.VaultConfigError) as exc:
        say_err(f"error: the resolver could not settle this conflict: {exc}")
        if _vault_mid_rebase(vault):
            try:
                resolve_mod._abort_replay(vault)
            except resolve_mod.ResolveError as abort_exc:
                say_err(f"  and the vault is still mid-rebase: {abort_exc}")
        marker_after = resolve_mod.resolve_state.read_failed_marker(vault)
        if marker_after is None or marker_after == marker_before:
            # Nothing inside the resolution classified this, so it is the
            # resolver's own failure. A reason the push recorded during this
            # attempt is the more specific fact and is left standing: the
            # forge refusing a settled push is the forge's doing, and sending
            # its reader to fix this host's data would be wrong.
            resolve_mod.resolve_state.mark_failed(
                vault, reason=FAILURE_POLICY, detail=str(exc)
            )
        return PUBLISH_HOLDING

    resolve_mod.resolve_state.clear_failed_marker(vault)
    if report.get("discarded"):
        # A host that declares it makes no vault content — the resolver kept
        # the published history and nothing of this host's is left to say.
        # Deliberately silent: the owner of such a host is never handed a
        # version-control decision, and a line here would be one.
        return PUBLISH_DISCARDED
    if report["held"]:
        say_err(
            "notice: a judgment conflict needs a person — "
            f"{resolve_mod.resolve_state.resolve_remedy(vault)} once ready"
        )
        return SYNC_AWAITING_PERSON

    say("Settled automatically and published.")
    return PUBLISH_OK


def _pull_only_one(vault: Path, say, say_err) -> tuple[str, int]:
    """Fetch ``vault`` and integrate ONLY if that costs the working tree nothing.

    The non-destructive half of :func:`_pull_and_push_one`: no staging, no
    commit, no push. Integration is delegated to :func:`_pull_one` (so the
    rebase, its abort, and the ``lore resolve`` remedy all have exactly one
    implementation) but is gated on a CLEAN tree.

    **A dirty tree is reported, never rebased.** The full sync can rebase a dirty
    vault because it commits first; a pull-only run has no such commit to rebase
    onto, and ``git rebase`` against uncommitted changes either refuses or
    (worse, for a caller that asked for nothing destructive) stashes them. So the
    fetch still runs — it touches no file — and the operator is told how far
    behind the vault is, which is the whole actionable content of the pull they
    did not get. **This returns :data:`PULL_DIRTY`, never :data:`PULL_OK`** — a
    dirty tree that could not be integrated is not "nothing needed doing", and a
    caller that collapsed the two would report a vault holding uncommitted work
    as converged.
    """
    rc_remote, remote_url, _ = _git(vault, "remote", "get-url", "origin")
    if rc_remote != 0 or not remote_url:
        return PULL_OK, 0

    if not _fetch_origin(vault, say_err, pull_only=True):
        return PULL_OFFLINE, 0

    if _vault_is_dirty(vault):
        behind = _commits_behind(vault)
        if behind:
            say_err(
                f"notice: {behind} commit(s) behind origin — the working tree is not "
                "clean, so nothing was integrated; run `lore sync` to commit and pull"
            )
        return PULL_DIRTY, 0

    return _pull_one(vault, say, say_err, already_fetched=True)


def _pull_one(
    vault: Path, say, say_err, *, already_fetched: bool = False,
    resolve_conflicts: bool = False, name: str = "", shared: bool = False,
) -> tuple[str, int]:
    """Fetch ``vault`` and integrate origin's commits. Returns ``(state, commits_pulled)``.

    ``already_fetched`` skips the fetch for a caller that has already run one this
    invocation (:func:`_pull_only_one`, which must fetch BEFORE it knows whether
    the tree is clean enough to integrate) — a second fetch would be a wasted
    network round-trip against a ref database that cannot have moved since.

    ``resolve_conflicts`` (with ``name``/``shared``) is the full sync's own
    opt-in to handing a rebase conflict to :func:`_hand_off_to_resolver`
    instead of just reporting it — see that function and
    :data:`PULL_RESOLVED` / :data:`PULL_AWAITING_PERSON`. ``_pull_only_one``
    never passes it: ``--pull-only`` must never stage, commit, or push, and
    the resolver's own finish tail does all three.

    Quiet no-ops: no origin remote (push reports it), detached HEAD (push
    reports it), a remote that does not have this branch yet (the first push
    creates it), and an already-up-to-date upstream.

    **A branch with no upstream still pulls against ``origin/<branch>`` when that
    ref exists.** Two devices can both be at their "first push": the loser's push
    is rejected non-fast-forward, and ``--set-upstream`` never records an upstream
    on a FAILED push — so a pull keyed on ``@{u}`` alone would skip forever while
    every push keeps failing. Rebasing onto the fetched ``origin/<branch>`` is
    what lets the next push converge.

    **An unborn branch adopts the remote branch outright.** A freshly ``git
    init``-ed vault wired to an existing remote has no commits to rebase, so
    without this it would print "Nothing to commit", pull nothing, and exit 0 —
    a silent no-op on exactly the new-device shape sync exists for. The caller
    commits BEFORE pulling, so an unborn HEAD here implies a clean tree and
    ``reset --hard origin/<branch>`` cannot discard local records. When the
    remote has no branch of the same name to adopt, that is reported, not
    skipped.

    **Integration is a rebase, never a merge**: sync commits are machine-made and
    content-independent, so replaying them keeps vault history linear instead of
    accumulating a merge bubble per device pair. On failure the rebase is
    ABORTED before reporting — a mid-rebase vault would break every subsequent
    record write, which is worse than the missed pull being reported — and the
    abort is verified via :func:`_vault_mid_rebase` so the remedy printed
    matches the state the vault is actually in.
    """
    rc_remote, remote_url, _ = _git(vault, "remote", "get-url", "origin")
    if rc_remote != 0 or not remote_url:
        return PULL_OK, 0

    if not already_fetched and not _fetch_origin(vault, say_err):
        return PULL_OFFLINE, 0

    if _vault_has_upstream(vault):
        upstream = "@{u}"
    else:
        branch = _vault_head_branch(vault)
        if branch is None:
            # No commit under HEAD: an unborn branch (fresh `git init`) still
            # resolves a symbolic ref; a detached HEAD does not.
            rc_sym, unborn, _ = _git(vault, "symbolic-ref", "--quiet", "--short", "HEAD")
            if rc_sym != 0 or not unborn:
                return PULL_OK, 0
            rc_ref, _, _ = _git(
                vault, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{unborn}"
            )
            if rc_ref != 0:
                say_err(
                    f"notice: vault has no commits and origin has no {unborn!r} branch — "
                    "nothing to pull; check out the branch the remote uses, or clone the vault"
                )
                return PULL_OK, 0
            rc_n, n_out, _ = _git(vault, "rev-list", "--count", f"origin/{unborn}")
            # Tree mutation — locked (the fetch above was not).
            with locking.vault_write_lock(vault):
                rc_reset, _, stderr_reset = _git(
                    vault, "reset", "--hard", f"origin/{unborn}"
                )
            if rc_reset != 0:
                say_err(f"error: could not adopt origin/{unborn}: {stderr_reset}")
                return PULL_FAILED, 0
            adopted = int(n_out) if rc_n == 0 and n_out else 0
            say(f"Pulled {adopted} commit(s) from origin.")
            return PULL_OK, adopted
        rc_ref, _, _ = _git(
            vault, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"
        )
        if rc_ref != 0:
            return PULL_OK, 0
        upstream = f"origin/{branch}"

    rc_count, count_out, _ = _git(vault, "rev-list", "--count", f"HEAD..{upstream}")
    if rc_count != 0 or not count_out or count_out == "0":
        return PULL_OK, 0
    behind = int(count_out)

    # Tree mutation — locked, so a concurrent record write can never see a
    # half-replayed tree. The abort is part of the same critical section: the
    # vault must not be observable mid-rebase.
    with locking.vault_write_lock(vault):
        rc_rebase, stdout_rebase, stderr_rebase = _git(vault, "rebase", upstream)
        if rc_rebase != 0:
            # Abort unconditionally: whether the rebase stopped on a conflict or
            # never started, the vault must come back to its pre-pull state before
            # anything is reported. Verified below rather than trusted.
            _git(vault, "rebase", "--abort")
    if rc_rebase != 0:
        if not resolve_conflicts:
            say_err("error: rebase onto origin failed — pull skipped")
            say_err(f"  rebase error: {stderr_rebase or stdout_rebase}")
            from . import resolve_state as resolve_state_mod

            remedy = resolve_state_mod.resolve_remedy(vault)
            if _vault_mid_rebase(vault):
                say_err(f"  the vault is STILL mid-rebase; to settle it, {remedy}")
            else:
                say_err(f"  to settle the conflict, {remedy}")
            return PULL_FAILED, 0

        # Breaking change (see CHANGELOG.md): the conflict is handed to the
        # resolver instead of reported. The plain rebase above has already
        # been aborted unconditionally, exactly as before this task.
        outcome = _hand_off_to_resolver(vault, name, say, say_err, shared=shared)
        if outcome == PUBLISH_OK:
            return PULL_RESOLVED, 0
        if outcome == PUBLISH_DISCARDED:
            # The published history was kept and this host's own commits were
            # discarded with it — the vault gained exactly the commits it was
            # behind by, so they count toward the caller's reindex decision
            # just as an ordinary pull's do.
            return PULL_DISCARDED, behind
        if outcome == SYNC_AWAITING_PERSON:
            return PULL_AWAITING_PERSON, 0
        return PULL_FAILED, 0

    say(f"Pulled {behind} commit(s) from origin.")
    return PULL_OK, behind


def _refetch_discriminator(vault: Path, branch: str) -> tuple[bool, bool]:
    """Re-fetch ``origin`` and classify a just-rejected push. Never reads stderr.

    Returns ``(fetch_ok, advanced)`` where ``advanced`` means
    ``refs/remotes/origin/<branch>`` moved past its pre-fetch value:

    - ``(True, True)``  — the published history genuinely moved; a replay can
      clear the rejection.
    - ``(False, False)`` — the forge itself was unreachable; the rejection was
      never about history at all.
    - ``(True, False)`` — the forge answered and its history did NOT move, so
      the rejection has some other cause (a hook, a protected branch,
      permission) that no amount of retrying will ever clear.

    The discrimination is entirely observable state — a fetch exit code and a
    ref comparison — never git or remote *text*: lore's stderr embeds the
    remote URL verbatim, which is a credential in a team-synced vault, and
    this function's result must never depend on parsing it.
    """
    ref = f"refs/remotes/origin/{branch}"
    rc_before, before, _ = _git(vault, "rev-parse", "--quiet", "--verify", ref)
    before_sha = before if rc_before == 0 else None
    rc_fetch, _, _ = _git(vault, "fetch", "origin")
    fetch_ok = rc_fetch == 0
    rc_after, after, _ = _git(vault, "rev-parse", "--quiet", "--verify", ref)
    after_sha = after if rc_after == 0 else None
    advanced = fetch_ok and before_sha != after_sha
    return fetch_ok, advanced


def _push_one(
    vault: Path,
    say,
    say_err,
    *,
    committed: bool,
    max_attempts: int | None = None,
    on_replay: Callable[[int], None] | None = None,
    name: str = "",
    shared: bool = False,
    hand_off: bool = True,
) -> tuple[int, str, int]:
    """Push ``vault`` to origin, replaying onto a moved history when rejected.

    Returns ``(exit_code, ending, attempts_used)``. ``ending`` is
    :data:`PUBLISH_OK` on a clean push (including every existing no-op —
    nothing to push, no origin remote, detached HEAD, a genuinely unreachable
    forge, and now also a moved-history replay the resolver settled and
    already pushed itself), :data:`PUBLISH_HOLDING` when the push was
    rejected for a reason no retry clears (``name``/``shared`` are unused
    here) OR the resolver itself failed on a replay conflict
    (:data:`FAILURE_POLICY`, recorded on the resolver's own failed-vault
    marker — see :func:`_hand_off_to_resolver`), :data:`SYNC_AWAITING_PERSON`
    when a replay conflict is a judgment call the resolver held for a person,
    :data:`PUBLISH_DISCARDED` when a replay conflict on a host that authors
    nothing was settled by keeping the published history — the commit this
    push was carrying went with the rest of the local divergence, so there is
    nothing left to publish and nothing went wrong —
    or :data:`PUBLISH_RETRIES_EXHAUSTED` when the published history kept
    moving past ``max_attempts`` (default :func:`resolve_publish_retry_max`).
    ``attempts_used`` counts only the moved-history case — the one condition
    a retry can actually clear.

    ``hand_off`` (default ``True``) gates whether a moved-history replay
    conflict is handed to the resolver at all — the caller's explicit opt
    OUT, not an implicit one inferred from ``name``/``shared``. ``resolve.py``'s
    ``_finish`` passes ``hand_off=False``: its own push is ALREADY the tail of
    a resolution the caller is driving (a person's `lore resolve`, or the
    sweep's own `resolve_for_sweep`), and a replay conflict reached from
    inside that tail must not start ANOTHER resolution recursively — it
    reports the pre-existing plain ``PUBLISH_HOLDING`` ending instead, exactly
    as this branch behaved before this task.

    ``on_replay``, when given, is called once per successful replay with the
    number of commits that replay's rebase integrated from origin — the same
    quantity :func:`_pull_one` returns for an ordinary pull. A vault whose push
    loses a race fetches and rebases a peer's commits onto its own tree exactly
    like a pull does, so those commits must count toward the caller's reindex
    decision the same way a pull's commits do; a caller that ignores
    ``on_replay`` gets the previous behavior unchanged.

    Skipped silently when the vault is clean AND already in sync with its
    upstream — the common case across a multi-vault sync, where an unconditional
    push would spend one network round-trip per vault to say "Everything
    up-to-date". ``_vault_unpushed`` answers that from the local ref database.

    **A branch with no upstream is pushed with ``--set-upstream``.** A bare
    ``git push origin`` refuses outright in that state ("The current branch has no
    upstream branch", exit 128) and, crucially, never sets one either — so
    without this the vault would fail identically on every future sync while the
    error text blamed the network. Setting upstream on the first push is what
    makes the condition converge. A push rejected for a moved history never
    records an upstream either, so a retry after replaying must still pass
    ``--set-upstream`` again until one attempt actually lands.

    A missing origin is reported only when this run committed something, so the
    per-vault line names the vault whose new commit is now unbacked; a clean
    remote-less vault stays quiet rather than re-reporting a standing condition on
    every sync (``lore status`` is the surface that reports it standing).

    **On rejection, :func:`_refetch_discriminator` classifies the cause before
    deciding whether to retry** — never by parsing stderr (see that function's
    docstring). A genuinely unreachable forge falls back to the pre-existing
    soft "committed locally; push failed" no-op, unchanged and consuming no
    attempt. A moved history is replayed under the vault's write lock (a tree
    mutation, exactly like :func:`_pull_one`'s rebase) and the push retried;
    exhausting ``max_attempts`` this way is a distinct, HARD ending
    (:data:`PUBLISH_RETRIES_EXHAUSTED`, exit 1) rather than the generic soft
    push-failure notice, so a caller can tell "the forge just won't sit still"
    apart from "the network is down". A rejection where the history did NOT
    move is :data:`PUBLISH_HOLDING` — HARD (exit 1), consumes no attempt, and
    leaves the local commit exactly where it landed for a person (or a later
    sweep) to settle; see the module's outcome constants. Both
    :data:`PUBLISH_HOLDING` producers (this one and the replay-conflict path
    below) exit non-zero identically — a person must act either way.
    """
    rc_remote, remote_url, _ = _git(vault, "remote", "get-url", "origin")
    if rc_remote != 0 or not remote_url:
        if committed:
            say("No origin remote — skipping push.")
        return 0, PUBLISH_OK, 0

    if not committed and not _vault_unpushed(vault):
        return 0, PUBLISH_OK, 0

    if max_attempts is None:
        max_attempts = resolve_publish_retry_max()

    attempts_used = 0
    while True:
        branch = _vault_head_branch(vault)
        if branch is None:
            # Detached HEAD: there is no branch to track, and guessing a refspec
            # would push to a name the operator never chose. Report, don't guess.
            say_err("notice: detached HEAD — skipping push; check out a branch and re-run")
            return 0, PUBLISH_OK, attempts_used

        push_args = ["push", "origin"]
        if not _vault_has_upstream(vault):
            push_args = ["push", "--set-upstream", "origin", branch]

        rc_push, _, _stderr_push = _git(vault, *push_args)
        if rc_push == 0:
            say("Pushed to origin.")
            from . import resolve_state as resolve_state_mod

            resolve_state_mod.clear_failed_marker(vault)
            return 0, PUBLISH_OK, attempts_used

        fetch_ok, advanced = _refetch_discriminator(vault, branch)
        if not fetch_ok:
            say_err("notice: committed locally; push failed — re-run `lore sync` when online")
            return 0, PUBLISH_OK, attempts_used

        if not advanced:
            say_err(
                "notice: the push did not go through and the published history "
                "did not move — needs a person; re-run `lore sync` later"
            )
            from . import resolve_state as resolve_state_mod

            resolve_state_mod.mark_failed(
                vault, reason=FAILURE_REMOTE_REJECTION,
                detail="the push was rejected and the published history did not move",
            )
            return 1, PUBLISH_HOLDING, attempts_used

        attempts_used += 1
        if attempts_used >= max_attempts:
            say_err(
                f"error: publish retries exhausted after {attempts_used} attempt(s) "
                "— the published history kept moving; re-run `lore sync`"
            )
            return 1, PUBLISH_RETRIES_EXHAUSTED, attempts_used

        # Count what the replay is about to integrate BEFORE rebasing — the
        # same measurement `_pull_one` takes of its own rebase, and for the
        # same reason: once the rebase replays local commits on top,
        # `HEAD..origin/<branch>` no longer names the commits that just landed.
        rc_count, count_out, _ = _git(
            vault, "rev-list", "--count", f"HEAD..origin/{branch}"
        )
        replayed = int(count_out) if rc_count == 0 and count_out.isdigit() else 0

        # Tree mutation — locked, exactly like `_pull_one`'s rebase. The abort
        # is part of the same critical section: the vault must not be
        # observable mid-rebase.
        with locking.vault_write_lock(vault):
            rc_rebase, _stdout_rebase, _stderr_rebase = _git(
                vault, "rebase", f"origin/{branch}"
            )
            if rc_rebase != 0:
                # Abort unconditionally, in the same critical section — the
                # vault must never be left observable mid-rebase, exactly like
                # `_pull_one`'s own conflict-abort contract.
                _git(vault, "rebase", "--abort")
        if rc_rebase != 0:
            if not hand_off:
                # This push is ALREADY the tail of a resolution the caller is
                # driving (`resolve.py`'s `_finish`, reached from a person's
                # `lore resolve` or the sweep's own `resolve_for_sweep`) — a
                # replay conflict reached from inside that tail must not
                # start ANOTHER resolution recursively. Pre-task behavior,
                # unchanged.
                say_err("error: replaying onto the moved history failed — publish skipped")
                return 1, PUBLISH_HOLDING, attempts_used
            # Breaking change (see CHANGELOG.md): the conflict is handed to
            # the resolver instead of reported. The plain rebase above has
            # already been aborted unconditionally, exactly as before this
            # task.
            outcome = _hand_off_to_resolver(vault, name, say, say_err, shared=shared)
            if outcome == PUBLISH_OK:
                return 0, PUBLISH_OK, attempts_used
            if outcome == PUBLISH_DISCARDED:
                # This host authors nothing: the commit this push was carrying
                # was discarded with the rest of the local divergence, so there
                # is nothing left to publish and nothing went wrong. The
                # commits the vault gained count exactly as a replay's do.
                if on_replay is not None and replayed:
                    on_replay(replayed)
                return 0, PUBLISH_DISCARDED, attempts_used
            if outcome == SYNC_AWAITING_PERSON:
                return 1, SYNC_AWAITING_PERSON, attempts_used
            return 1, PUBLISH_HOLDING, attempts_used

        if on_replay is not None and replayed:
            on_replay(replayed)


#: The root file lore scaffolds that IS committed. ``.lore.lock`` is deliberately
#: absent — the write lock sidecar and never part of the commit scope.
_COMMIT_SCOPE_ROOT_FILES = (".gitignore",)

def _untracked_allowlist_pathspecs(vault: Path) -> list[str]:
    """The allow-listed pathspecs an UNTRACKED addition may be staged under in
    *vault*: every record kind directory, the ``sites/`` free-write zone, and
    the scaffolded root ``.gitignore`` — filtered to what actually exists on
    disk.

    This bounds only NEW, untracked content. Tracked content is never bounded
    by this list — see :func:`_stage_and_commit_one`, which stages every
    tracked modification and deletion unscoped so a tracked file outside this
    allow-list (an adopted repo's root ``README.md``, say) is never silently
    left uncommittable, and a wholesale-removed kind directory's deletion is
    never invisible because the directory no longer exists to name here.

    ``outpost/`` and ``.lore.lock`` are never named, so neither can ever be
    staged as a new addition regardless of what a vault's own ``.gitignore``
    says. The filter to existing paths is required, not an optimization: ``git
    add -A -- <pathspec ...>`` exits 128 and stages NOTHING AT ALL the moment
    one named pathspec is absent from disk, and most real vaults do not carry
    every record kind.
    """
    candidates = sorted(record_model.KINDS) + [_SITES_DIR, *_COMMIT_SCOPE_ROOT_FILES]
    return [name for name in candidates if (vault / name).exists()]


def _stray_untracked_paths(status_lines: list[str], allowed_tops: set[str]) -> list[str]:
    """Untracked paths in *status_lines* whose top-level component is not one of
    *allowed_tops* — content that will never be staged and must be reported
    rather than silently swallowed. Tracked lines (any code other than ``??``)
    are never strays: they are always in scope (see
    :func:`_stage_and_commit_one`)."""
    strays = []
    for line in status_lines:
        if not line.startswith("??"):
            continue
        path = line[3:].strip()
        top = path.split("/", 1)[0]
        if top not in allowed_tops:
            strays.append(path)
    return strays


def _probe_vault_status(vault: Path) -> tuple[int, list[str], list[str], list[str], str]:
    """Run one UNSCOPED status probe. Returns ``(rc, all_lines, committable_lines,
    strays, stderr)``. ``committable_lines`` is every line that staging will
    actually pick up — every tracked change, plus untracked additions under
    :func:`_untracked_allowlist_pathspecs`. ``strays`` is untracked content
    outside that allow-list, which staging will never touch.
    """
    rc, status_out, stderr = _git(vault, "status", "--porcelain", "--", ".", *_STATUS_EXCLUDE_PATHSPECS)
    if rc != 0:
        return rc, [], [], [], stderr
    lines = [ln for ln in status_out.splitlines() if ln.strip()]
    allowed = set(_untracked_allowlist_pathspecs(vault))
    strays = _stray_untracked_paths(lines, allowed)
    stray_set = set(strays)
    committable = [
        ln for ln in lines
        if not (ln.startswith("??") and ln[3:].strip() in stray_set)
    ]
    return rc, lines, committable, strays, stderr


def _stage_and_commit_one(vault: Path, message: str, say, say_err) -> tuple[int, bool]:
    """Stage + commit one vault's tracked content and allow-listed new content
    under its write lock.

    Returns ``(exit_code, committed)``.

    **Tracked content is always in scope.** Every tracked modification and
    deletion is staged unscoped (``git add -u -- .`` semantics) — a vault's
    ``.gitignore`` and ``outpost/`` carve-out excepted via
    :data:`_STATUS_EXCLUDE_PATHSPECS`, which the dirtiness judgment below uses
    too, so "clean" means clean and a change confined to ``outpost/`` reads as
    "nothing to commit" rather than a dirty tree that ``git add`` then silently
    drops. **Only untracked ADDITIONS are bounded** — to
    :func:`_untracked_allowlist_pathspecs` (every record kind directory,
    ``sites/``, and the scaffolded root ``.gitignore``) — which is what the
    bounding was ever for: keeping ``outpost/`` and stray junk out of a commit,
    not dropping content already in the vault's history. An untracked path
    outside that allow-list is never staged and never silently swallowed either
    — see :func:`_stray_untracked_paths` — it is named in a notice instead.

    Probed twice, deliberately, though :func:`cmd_sync` — this function's only
    caller — already holds every target's lock before calling in, so neither
    probe here ever finds a vault genuinely un-locked. The REPEAT under ``with
    locking.vault_write_lock(vault)`` below (a reentrant no-op against the lock
    cmd_sync already holds) exists because staging what the first probe saw
    would only be safe if this vault's tree — and its set of kind directories —
    can't change between the two reads: true for a standalone caller taking the
    lock fresh here, and still asserted defensively even though cmd_sync's own
    batched acquisition already rules out a concurrent cross-vault
    ``move_record`` doing exactly that in between.

    No network runs in here — see the module docstring.
    """

    rc, _lines, committable, _strays, stderr = _probe_vault_status(vault)
    if rc != 0:
        say_err(f"error: git status failed: {stderr} — skipped")
        return 1, False
    if not committable:
        say("Nothing to commit — vault is clean.")
        return 0, False

    with locking.vault_write_lock(vault):
        rc, _lines, committable, strays, stderr = _probe_vault_status(vault)
        if rc != 0:
            say_err(f"error: git status failed: {stderr} — skipped")
            return 1, False

        if strays:
            say_err(
                "notice: untracked and outside the commit scope — never staged: "
                + ", ".join(strays)
            )

        if not committable:
            say("Nothing to commit — vault is clean.")
            return 0, False

        # `git add -u -- .` refuses outright on an unborn branch ("pathspec '.'
        # did not match any file(s) known to git", exit 128) — with zero
        # commits there is by definition nothing tracked yet for `-u` to
        # update, so it is skipped rather than treated as a real failure. A
        # never-committed vault (`lore vault add --path <existing repo>`
        # before its first sync) is exactly this shape.
        rc_head, _, _ = _git(vault, "rev-parse", "--verify", "-q", "HEAD")
        if rc_head == 0:
            rc, _, stderr = _git(vault, "add", "-u", "--", ".")
            if rc != 0:
                say_err(f"error: git add failed: {stderr} — skipped")
                return 1, False
        allowlisted = _untracked_allowlist_pathspecs(vault)
        if allowlisted:
            rc, _, stderr = _git(vault, "add", "-A", "--", *allowlisted)
            if rc != 0:
                say_err(f"error: git add failed: {stderr} — skipped")
                return 1, False
        # Belt-and-braces: `.lore.lock` is never staged by either `add` above —
        # this unstages it anyway in case a future pathspec ever collided with
        # the lock sidecar's name. `reset` never errors on an ignored or
        # never-staged path.
        rc, _, stderr = _git(vault, "reset", "-q", "--", locking.VAULT_LOCK_NAME)
        if rc != 0:
            say_err(f"error: git reset (unstaging lock file) failed: {stderr} — skipped")
            return 1, False
        # Never pass -S or --no-gpg-sign; honor the adopter's commit.gpgsign.
        rc, _, stderr = _git(vault, "commit", "-m", message)
        if rc != 0:
            say_err(f"error: git commit failed: {stderr} — skipped")
            return 1, False

    say(f"Committed: {message}")
    return 0, True


def _pull_and_push_one(
    vault: Path, say, say_err, *, committed: bool, name: str = "", shared: bool = False,
) -> tuple[int, int, str, bool]:
    """Pull then push one vault, given whether this run just committed to it.

    ``name``/``shared`` are forwarded to both replay sites'
    :func:`_hand_off_to_resolver` call — see :data:`PULL_RESOLVED`,
    :data:`PULL_AWAITING_PERSON`, and :data:`SYNC_AWAITING_PERSON` below for
    the new endings this adds on top of the ones already documented here.

    Returns ``(exit_code, commits_pulled, ending, published)``. ``commits_pulled``
    includes commits integrated by the publish retry's replay
    (:func:`_push_one`'s ``on_replay``), not just :func:`_pull_one`'s own pull —
    a vault whose push loses a race replays a peer's commits onto its tree just
    as surely as a pull does, and the caller's reindex decision must not miss
    them. ``ending`` is
    :func:`_push_one`'s outcome (:data:`PUBLISH_OK` for every path that never
    reaches a push attempt — a failed or skipped pull). Kept separate from
    :func:`_stage_and_commit_one` so :func:`cmd_sync` can run every target's
    stage+commit phase under ONE combined lock (see its docstring) before
    running each target's network-touching pull/push tail separately, one
    vault at a time.

    ``published`` is ``True`` only when this call actually landed a commit on
    the forge — the signal :func:`cmd_sync` needs to tell its "converged"
    outcome (nothing was ahead, or nothing left this machine) apart from
    "published" (something was ahead and it landed), which :data:`PUBLISH_OK`
    alone cannot say: it also covers every existing no-op (nothing to push, no
    origin remote, an unreachable forge). Derived the same way
    :func:`_push_one`'s own skip check does — from :func:`_vault_unpushed`,
    never from git or remote text — read once before the push attempt (had
    anything to publish at all) and once after (did it land): a push that
    never reaches the forge (offline, no remote, detached HEAD) leaves the
    vault still unpushed afterward, so ``published`` stays ``False`` exactly
    where the existing soft-network contract already treats it as a no-op.

    **An offline fetch that leaves local work unpublished is `holding`, not
    `converged`.** A vault that just committed (or already carried an unpushed
    commit) and then found the forge unreachable is hoarding work on this
    machine — the exact condition the design doc wants visible, never silently
    reported as nothing-left-to-do. This is a decided exception to the general
    "offline stays quiet" rule: quiet is right when there is genuinely nothing
    this host is holding, wrong when there is. `_vault_unpushed` is checked
    BEFORE any push attempt runs (none does, on this path) — the same read
    :data:`had_something_to_publish` below uses once :func:`_push_one` is
    reached.
    """
    pull_state, pulled = _pull_one(
        vault, say, say_err, resolve_conflicts=True, name=name, shared=shared
    )
    if pull_state == PULL_FAILED:
        # The resolver itself could not settle the conflict — a policy
        # failure, not a judgment call. `_hand_off_to_resolver` already wrote
        # the failed-vault marker `cmd_sync` reads for the `reason` tag.
        return 1, 0, PUBLISH_HOLDING, False
    if pull_state == PULL_AWAITING_PERSON:
        return 1, 0, SYNC_AWAITING_PERSON, False
    if pull_state == PULL_DISCARDED:
        # A host that authors nothing kept the published history. Nothing of
        # this host's is left to publish, so no push cycle runs — the vault
        # is converged, and `published` stays False.
        return 0, pulled, PUBLISH_DISCARDED, False
    if pull_state == PULL_RESOLVED:
        # Settled field-wise and already pushed by the resolver's own finish
        # tail — no separate push attempt needed.
        return 0, pulled, PUBLISH_OK, True
    if pull_state == PULL_OFFLINE:
        if committed or _vault_unpushed(vault):
            return 1, 0, PUBLISH_HOLDING, False
        return 0, 0, PUBLISH_OK, False

    had_something_to_publish = committed or _vault_unpushed(vault)
    replayed_total = 0

    def _record_replay(n: int) -> None:
        nonlocal replayed_total
        replayed_total += n

    rc, ending, _attempts = _push_one(
        vault, say, say_err, committed=committed, on_replay=_record_replay,
        name=name, shared=shared,
    )
    published = (
        had_something_to_publish
        and ending == PUBLISH_OK
        and not _vault_unpushed(vault)
    )
    return rc, pulled + replayed_total, ending, published


# ---------------------------------------------------------------------------
# The implicit pull — freshness-window-throttled, stderr-only, never fatal
# ---------------------------------------------------------------------------

#: How long a vault stays "fresh enough" after a fetch ATTEMPT. Every write path
#: pulls implicitly, and a write is not a network operation the operator asked
#: for — five minutes keeps a multi-device vault current within a working
#: rhythm while capping the cost of a burst of writes at one fetch per vault.
FRESHNESS_WINDOW_SECONDS = 300

#: Freshness-stamp directory under ``state_dir("lore")``.
FETCH_STAMP_DIRNAME = "fetch"


def fetch_stamp_root() -> Path:
    """Return ``state_dir("lore")/fetch`` — the freshness-stamp directory."""
    return _resolve_lore_state_dir() / FETCH_STAMP_DIRNAME


def fetch_stamp_path(vault_root: str | Path) -> Path:
    """Return the freshness stamp for *vault_root*, confined to the stamp root.

    Keyed on ``common.machine_state_key`` and confined with
    ``layers.assert_within_root`` — the same shape ``cli.resolve_state.marker_path``
    uses for its own machine-local per-vault file. The digest keeps two vaults
    sharing a basename on separate stamps, so a fetch against one never suppresses
    the other's implicit pull; the confinement check keeps a symlink planted at the
    stamp's name from redirecting the write outside the stamp root.

    Raises:
        layers.LayerConfinementError: if the stamp path escapes the stamp root.
    """
    from ..vault import layers as layers_mod

    root = fetch_stamp_root()
    candidate = root / machine_state_key(vault_root)
    layers_mod.assert_within_root(candidate, root)
    return candidate


def _fetch_is_fresh(vault_root: str | Path) -> bool:
    """Return ``True`` iff a fetch was ATTEMPTED against *vault_root* recently.

    Attempted, not succeeded: an offline session must pay one network timeout
    per window, not one per write. The stamp is therefore written before the
    fetch runs and never rolled back on failure.
    """
    try:
        age = time.time() - fetch_stamp_path(vault_root).stat().st_mtime
    except OSError:
        return False
    return 0 <= age < FRESHNESS_WINDOW_SECONDS


def _stamp_fetch_attempt(vault_root: str | Path) -> None:
    """Record that a fetch is being attempted against *vault_root*, now."""
    path = fetch_stamp_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def implicit_pull(vault_root: str | Path) -> None:
    """Run a throttled, stderr-only ``--pull-only`` for a vault about to be written.

    The cadence half of sync: every write path calls this so a multi-device vault
    converges on write instead of only when someone remembers to sync, WITHOUT a
    write ever becoming a network operation the caller has to reason about.
    Three properties make that safe, and every caller depends on all three:

    - **Advisory.** Nothing here can fail the write. A conflict, an unreachable
      remote, a broken git state — all are reported and stepped over; the pull is
      an optimization, and the write's own success is unrelated to it.
    - **Silent on stdout.** All output goes to stderr, because ``record create``'s
      stdout is exactly one ``RECORD_ID`` line that callers parse.
    - **Throttled on ATTEMPT.** See :func:`_fetch_is_fresh` — an offline session
      pays one timeout per window per vault, not one per write.

    MUST be called before the caller takes any vault write lock or opens an index
    transaction: this fetches (network, unbounded) and may reindex (which takes
    every configured vault's lock), neither of which belongs inside a write's own
    critical section.
    """
    vault = Path(vault_root)
    name = vault.name

    def say(text: str) -> None:
        print(f"  lore: {name}: {text}", file=sys.stderr)

    try:
        if _fetch_is_fresh(vault):
            return
        _stamp_fetch_attempt(vault)
        state, pulled = _pull_only_one(vault, say, say)
    except Exception as exc:  # noqa: BLE001 — advisory: a write must not fail on this
        print(f"  lore: {name}: notice: implicit pull skipped ({exc})", file=sys.stderr)
        return

    if state != PULL_OK or not pulled:
        return

    from .areas import run_reindex

    count, error = run_reindex()
    if error is not None:
        print(
            f"  lore: {name}: notice: search reindex failed after pull — "
            f"run `lore reindex` ({error})",
            file=sys.stderr,
        )
    else:
        print(f"  lore: {name}: Reindexed {count} record(s) after pull.", file=sys.stderr)


def _select_targets(vault_filter: str | None) -> tuple[list, int]:
    """Resolve the vaults to sync. Returns ``(targets, exit_code)``.

    A non-empty ``targets`` always pairs with exit code 0. An unreadable config or
    an unknown ``--vault`` name yields ``([], 1)`` with the diagnostic already
    printed — both are refusals, never a silent fallback to syncing ``default``
    alone, which would recreate the very gap this command closes.
    """
    from ..vault import config as vault_config_mod

    targets, error = _resolve_all_vaults()
    if error is not None:
        print(f"error: {error}", file=sys.stderr)
        print("  Aborting — refusing to sync a partial vault set.", file=sys.stderr)
        return [], 1

    if vault_filter is None:
        return targets, 0

    wanted = vault_config_mod.normalize_vault_name(vault_filter)
    selected = [(n, p) for n, p in targets if n == wanted]
    if not selected:
        known = ", ".join(n for n, _ in targets) or "(none)"
        print(f"error: unknown vault: {vault_filter!r}", file=sys.stderr)
        print(f"  configured vaults: {known}", file=sys.stderr)
        return [], 1
    return selected, 0


def cmd_sync(args) -> int:
    """Sync every configured vault, or just ``--vault <name>``.

    Exit code is 1 if ANY vault hit a hard failure, 0 otherwise — the run always
    attempts every selected vault first, so one failure never strands the rest.

    **The stage+commit phase runs under every target's write lock held
    TOGETHER, not one target at a time.** A single ``lore sync`` run visits
    several vaults sequentially; locking each one independently (the original
    shape) only keeps a concurrent writer from interleaving WITHIN one
    vault's own stage/commit call. It does nothing to stop a cross-vault
    ``move_record`` (which holds source+destination together — see
    ``locking.vault_write_locks`` and ``record.store.move_record``) from
    running its ENTIRE copy -> repoint -> delete sequence strictly between
    two different targets' visits — e.g. sync sees the source vault clean
    before the move starts, the move completes, then sync commits the
    destination vault. That leaves the source vault's own commit (the one
    that would drop the now-deleted record) never taken this run, while the
    destination vault's commit for the SAME record already landed — the
    record then reads as committed in both vaults until a later sync happens
    to revisit the source. Acquiring every target's lock up front (sorted
    order, matching ``move_record``'s own discipline, so this can never
    deadlock against it) closes that: a move starting after this phase begins
    must wait for the WHOLE phase (every target) to finish, and a move
    already in flight blocks the phase from starting until it releases both
    its locks — either way, every target's post-move state is only ever
    visible together, never split across this run. Pull/push stay outside it
    and per-vault, one at a time, exactly as before — see
    :func:`_pull_and_push_one`.

    Locks are acquired ONE TARGET AT A TIME into a single ``ExitStack`` (sorted
    order, same as ``vault_write_locks``), not via that helper's one opaque
    all-or-nothing call — a target whose lock fails to acquire is skipped
    (attributed exactly, no exception-message guessing needed) while every
    OTHER target still gets locked together and committed; it never strands
    the rest of the batch just because one vault's lock is broken.

    When any vault pulled commits, the derived search index is rebuilt ONCE at
    the end (it is global across vaults, so per-vault rebuilds would be wasted
    work). A reindex failure is soft: the pulled text is already on disk and
    wins, and ``lore search`` reports its own staleness until `lore reindex`.

    **This is also the flow ``lore flush``'s sync tail reuses.** Every input is
    read off *args* with ``getattr`` — ``vault``, ``message``, ``pull_only`` —
    and nothing here touches the parser, so the tail calls this function directly
    with a small namespace object, once per writable vault (``cli.flush``'s
    ``_flush_sync_tail``; per-vault, because a bare run would cover ``shared:
    true`` vaults too and a shared vault must never be committed or pushed by an
    agent-actuated write). Keep it that way: coupling this function to argparse,
    or making any of those three attributes mandatory, breaks that caller.

    **The commit-phase lock acquisition is non-blocking by default, blocking
    only when the caller opts in.** ``blocking = getattr(args, "blocking",
    True)`` — the sweep/manual ``lore sync`` CLI path sets ``blocking=False``
    explicitly (see ``add_sync_subparser``) so a person at a terminal is never
    stranded behind another writer holding the vault; a contended vault raises
    ``BlockingIOError`` at the ExitStack acquisition, is reported as
    :data:`SYNC_IN_PROGRESS`, skipped for the WHOLE run (commit phase and
    pull/push phase both), and counted a SUCCESS, not a failure — exit 0.
    ``_flush_sync_tail``'s ``SimpleNamespace`` carries no ``blocking`` attribute
    at all, so ``getattr`` defaults it to ``True``: the tail keeps the original
    always-blocks behavior, because a flush that gave up on its own sync tail
    would report success while leaving the just-flushed record unpushed.
    """
    targets, rc = _select_targets(getattr(args, "vault", None))
    if rc != 0:
        return rc

    message = getattr(args, "message", None) or DEFAULT_SYNC_MSG
    pull_only = bool(getattr(args, "pull_only", False))
    blocking = bool(getattr(args, "blocking", True))
    width = max(len(name) for name, _ in targets) + 1  # + ':'

    say_map = {name: _make_emitters(name, width) for name, _ in targets}
    commit_rc: dict[str, int] = {}
    committed_map: dict[str, bool] = {}
    outcomes: dict[str, str] = {}
    refusal_conditions: dict[str, str] = {}
    failure_reasons: dict[str, str] = {}
    valid_targets: list[tuple[str, Path]] = []

    for name, vault in targets:
        say, say_err = say_map[name]
        vault_path = Path(vault)
        if not vault_path.exists():
            say_err(f"error: vault not found: {vault_path} — skipped")
            commit_rc[name] = 1
            continue
        if not _vault_is_git_toplevel(vault_path):
            say_err(
                f"error: not its own git toplevel: {vault_path} — skipped\n"
                "         (vault may be a subdirectory of a larger repo, or not a git repo)"
            )
            commit_rc[name] = 1
            continue
        refusal = vault_refusal_condition(vault_path)
        if refusal is not None:
            say_err(
                f"error: refused — {SYNC_REFUSAL_MESSAGES[refusal]}: "
                f"{vault_path} — skipped"
            )
            commit_rc[name] = 1
            outcomes[name] = "refused"
            refusal_conditions[name] = refusal
            continue
        valid_targets.append((name, vault_path))

    # A lock acquisition failure (e.g. a read-only vault root, or the lock
    # path occupied by something other than a file) must not crash the whole
    # run, AND must not strand every OTHER target unattempted just because
    # one vault's lock is broken. Acquiring each target's lock ONE AT A TIME
    # into a single shared ExitStack (rather than delegating to
    # ``locking.vault_write_locks`` as one opaque all-or-nothing call) gives
    # exact attribution for free — the vault whose lock just failed to
    # acquire is whichever one this loop iteration is on, no need to infer it
    # from ``OSError.filename`` — while still holding every SUCCESSFULLY
    # locked target together for the whole commit phase, in the same sorted
    # order ``vault_write_locks`` itself uses, so this still can't deadlock
    # against a cross-vault ``move_record``.
    sorted_targets = sorted(valid_targets, key=lambda t: locking.vault_lock_sort_key(t[1]))
    if pull_only:
        # Nothing to stage, nothing to commit — so nothing here needs a lock.
        # ``_pull_only_one`` still takes the write lock around its own rebase.
        for name, _vault_path in sorted_targets:
            commit_rc[name] = 0
        sorted_targets = []
    try:
        with ExitStack() as stack:
            locked: list[tuple[str, Path]] = []
            for name, vault_path in sorted_targets:
                try:
                    stack.enter_context(
                        locking.vault_write_lock(vault_path, blocking=blocking)
                    )
                except BlockingIOError:
                    # Contention, not breakage — `BlockingIOError` is an
                    # `OSError` subclass, so this must be caught FIRST or the
                    # generic handler below misreports "in progress" as
                    # "broken". Never reached when blocking=True: the plain
                    # blocking flock below never raises this.
                    #
                    # No `commit_rc[name]` entry is set here (unlike the
                    # `pull_only` branch above, which must set one — it never
                    # populates `outcomes` at all): the pull/push loop below
                    # checks `outcomes` BEFORE it ever reads `commit_rc`, so
                    # this vault is skipped there on the `outcomes` entry
                    # alone. Mutation-checked: setting `commit_rc[name] = 0`
                    # here changed nothing observable.
                    say, _say_err = say_map[name]
                    say(f"sync already in progress for {name!r} — skipped")
                    outcomes[name] = SYNC_IN_PROGRESS
                    continue
                except OSError as exc:
                    say_err = say_map[name][1]
                    say_err(f"error: failed to acquire vault lock: {exc} — skipped")
                    commit_rc[name] = 1
                    continue
                locked.append((name, vault_path))

            for name, vault_path in locked:
                say, say_err = say_map[name]
                rc_one, committed = _stage_and_commit_one(vault_path, message, say, say_err)
                commit_rc[name] = rc_one
                committed_map[name] = committed
    except OSError as exc:
        # Every target that reached the point of being locked or committed
        # above already has a recorded commit_rc entry (set as the loops run,
        # not just at the end) — this can only fire releasing a lock during
        # the ExitStack's own unwind, after all guarded work already landed.
        # The work is safe; only the unlock itself is in question, and that's
        # not attributable to any one target's commit outcome, so leave
        # commit_rc alone and just surface it.
        print(f"notice: error releasing a vault lock after sync: {exc}", file=sys.stderr)

    failed: list[str] = []
    total_pulled = 0
    for name, vault in targets:
        if outcomes.get(name) == SYNC_IN_PROGRESS:
            # Already reported; skipped for the WHOLE run, not just the commit
            # phase — pull/push would re-take this same vault's lock (inside
            # `_pull_one`'s rebase/reset, always blocking) and strand the
            # terminal exactly where the non-blocking commit phase just
            # refused to.
            continue
        if commit_rc.get(name, 1) != 0:
            failed.append(name)
            continue
        # Fresh emitters for the pull/push phase: the commit phase above may
        # already have printed this vault's labeled first line, and reusing
        # that closure's `state["first"]` here would print an unlabeled
        # continuation instead of a new labeled block (see `_make_emitters`).
        say, say_err = _make_emitters(name, width)
        if pull_only:
            state, pulled = _pull_only_one(Path(vault), say, say_err)
            # `--pull-only` never publishes, so there are three reachable
            # outcomes, not two: the pre-existing "holding" shape
            # (`PULL_FAILED` — a conflict this run could not integrate, vault
            # left clean and diverged); a NEW "holding" shape (`PULL_DIRTY` —
            # the tree had something uncommitted this run deliberately did not
            # touch, so it is not converged either, exactly the same exit-code
            # rule as a refused vault: a person must run the full sync);
            # "converged" (`PULL_OK` — fetched and, if anything was behind,
            # integrated cleanly); and `PULL_OFFLINE`, which gets NO entry at
            # all — the loop could not determine an outcome, so it must not
            # claim one, and the exit code for this vault stays 0.
            if state == PULL_OFFLINE:
                rc_one = 0
            elif state in (PULL_FAILED, PULL_DIRTY):
                rc_one = 1
                outcomes[name] = "holding"
            else:
                rc_one = 0
                outcomes[name] = "converged"
        else:
            shared = str(Path(vault).resolve()) in _shared_vault_paths()
            rc_one, pulled, ending, published = _pull_and_push_one(
                Path(vault), say, say_err, committed=committed_map.get(name, False),
                name=name, shared=shared,
            )
            if ending == SYNC_AWAITING_PERSON:
                # A judgment conflict the resolver held for a person — clean,
                # diverged, distinct from a resolver or forge FAILURE (see
                # below): there is nothing broken here for a person to fix,
                # only a choice for them to make.
                outcomes[name] = SYNC_AWAITING_PERSON
            elif ending in (PUBLISH_HOLDING, PUBLISH_RETRIES_EXHAUSTED):
                # Readable value, not a message string to parse back out —
                # same shape as `outcomes[name] = SYNC_IN_PROGRESS` above.
                outcomes[name] = ending
                if ending == PUBLISH_HOLDING:
                    from . import resolve as resolve_mod

                    marker = resolve_mod.resolve_state.read_failed_marker(Path(vault))
                    if marker is not None:
                        failure_reasons[name] = marker["reason"]
            elif published:
                outcomes[name] = "published"
            else:
                outcomes[name] = "converged"
                # A determinate, non-failing ending: whatever failure this
                # vault once had is over, so the durable half of that report
                # stops being current. Left in place it would outlive the
                # failure indefinitely, and the holding endings that write no
                # marker of their own read back whatever is on disk — which
                # is how a months-old reason reaches a later, unrelated hold.
                # The published ending clears it in `_push_one`, on the push
                # itself.
                from . import resolve_state as resolve_state_mod

                resolve_state_mod.clear_failed_marker(Path(vault))
        total_pulled += pulled
        if rc_one != 0:
            failed.append(name)

    if total_pulled:
        from .areas import run_reindex

        count, error = run_reindex()
        if error is not None:
            print(
                f"notice: search reindex failed after pull — run `lore reindex` ({error})",
                file=sys.stderr,
            )
        else:
            print(f"  Reindexed {count} record(s) after pull.")

    rc_final = 1 if failed else 0

    if bool(getattr(args, "json", False)):
        # Printed LAST and unconditionally — an addition to the prose above,
        # never a replacement for it (see `cmd_sync`'s docstring on
        # `--json`). Only vaults that reached one of `SYNC_OUTCOMES`'s seven
        # determinate outcomes this run get an entry; a vault that did not —
        # for any reason (never existed, not its own git toplevel, a bare git
        # error while staging, a failed lock acquisition, ...) — has no
        # outcome to report and is reflected only in the exit code and
        # stderr, exactly as without `--json`.
        entries = [
            (name, outcomes[name], refusal_conditions.get(name), failure_reasons.get(name))
            for name, _vault in targets
            if name in outcomes
        ]
        print(json.dumps(render_sync_json(entries), indent=2))

    if failed:
        print(
            f"error: {len(failed)} of {len(targets)} vault(s) failed to sync: "
            f"{', '.join(failed)}",
            file=sys.stderr,
        )
    return rc_final


def add_sync_subparser(sub) -> None:
    """Register the ``sync`` command parser."""
    p_sync = sub.add_parser(
        "sync", help="Stage, commit, pull, and push every configured vault",
        description=(
            "Stage, commit, pull, and push every configured vault. "
            "Breaking change: a conflict at either replay site (the pull's "
            "rebase, the push's moved-history replay) is now handed to the "
            "resolver instead of being reported with a `lore resolve` "
            "remedy and a non-zero exit. A settleable conflict now publishes "
            "(exit 0, no remedy printed); an unsettleable one now exits "
            "non-zero as `awaiting-person`, not `holding` — see CHANGELOG.md."
        ),
    )
    p_sync.add_argument(
        "--message", "-m", default=None,
        help=f"Commit message (default: {DEFAULT_SYNC_MSG!r})",
    )
    p_sync.add_argument(
        "--vault", default=None,
        help="Sync only this vault (default: every configured vault)",
    )
    p_sync.add_argument(
        "--pull-only", action="store_true",
        help="Fetch and integrate origin only — never stage, commit, or push",
    )
    p_sync.add_argument(
        "--json", action="store_true",
        help=f"Also print a machine-readable per-vault outcome report. {_SYNC_REPORT_SCHEMA}",
    )
    # Explicit opt-in to the non-blocking commit-phase lock acquisition — see
    # `cmd_sync`'s docstring. `_flush_sync_tail`'s SimpleNamespace carries no
    # `blocking` attribute at all, so it keeps `cmd_sync`'s always-blocks
    # default instead of inheriting this parser default.
    p_sync.set_defaults(func=cmd_sync, blocking=False)
