"""Assumption probe (ephemeral — delete before merge).

Resolves two Known Unknowns on task/the-handover-commits-ownership-moves-and-
the-sending-host-lets-go / task/the-peer-claims-ownership:

  (1) Does an ownership write on the receiving host survive `finish`'s
      detached provisioner (which rebuilds the manifest via
      carry_forward_owner)?
  (2) Is `reconcile_lock` correct-and-sufficient mutual exclusion for that
      claim write, given the provisioner it races is detached and takes the
      same lock?

Not part of the shipped suite. Delete this whole file after the controller
has recorded the verdict.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from camp.group.manifest import (
    ManifestError,
    carry_forward_owner,
    owner_of,
    read_central_manifest,
    reconcile_lock,
    write_central_manifest,
)


def _base_manifest(owner: str) -> dict:
    return {
        "schema_version": 1,
        "group": "g",
        "slug": "s",
        "branch": "worktree-s",
        "members": [],
        "owner": owner,
    }


# ---------------------------------------------------------------------------
# (1) Ordering-only case: claim commits strictly BEFORE the rebuild ever
# starts (no detached provisioner in flight yet). This is the phase position
# the plan is considering — claim precedes `finish`, so `finish`'s
# synchronous seed_pending_workspace-style rebuild (and any later detached
# rebuild) reads the claim's write, not a stale value. No lock needed here
# because there is no concurrent WRITE, only a later read+carry-forward.
# ---------------------------------------------------------------------------


def test_claim_before_rebuild_survives_with_no_lock_at_all(tmp_path: Path) -> None:
    ws_dir = tmp_path / "worktrees" / "s"
    ws_dir.mkdir(parents=True)
    mpath = ws_dir / "manifest.json"

    # Sender seeded the arriving workspace naming itself owner (begin phase).
    write_central_manifest(mpath, _base_manifest("sender-host"))

    # Claim commits — the peer's own name, via the deliberate opt-in — with
    # nothing else running concurrently.
    write_central_manifest(mpath, _base_manifest("peer-host"), allow_owner_change=True)

    # Now simulate the rebuild finish/the detached provisioner performs:
    # read prior owner, rebuild, carry forward.
    prior = read_central_manifest(mpath)
    prior_owner = owner_of(prior)
    rebuilt = {
        "schema_version": 1,
        "group": "g",
        "slug": "s",
        "branch": "worktree-s",
        "members": [{"name": "m", "provision_state": "ready"}],
    }
    carry_forward_owner(rebuilt, prior_owner)
    write_central_manifest(mpath, rebuilt)  # no allow_owner_change — must not raise

    final = read_central_manifest(mpath)
    assert owner_of(final) == "peer-host"


# ---------------------------------------------------------------------------
# (2) Real concurrency window: hold the rebuild mid-flight between its READ
# of prior_owner and its WRITE of the rebuilt manifest, and have the claim
# write land in that window. Two variants:
#   (a) claim writes WITHOUT taking reconcile_lock  -> demonstrate the race
#       is real (either a lost update or a refused write from
#       write_central_manifest's own guard — either way, unsafe).
#   (b) claim writes WHILE holding the SAME reconcile_lock the rebuild takes
#       -> demonstrate genuine mutual exclusion (the claim cannot even start
#       until the rebuild releases, so no interleaving is observable).
#
# Threads are sufficient to exercise real fcntl.flock() contention: each
# thread opens its own fd to the lockfile, and flock() locks are associated
# with the open file description, not the process or thread — two distinct
# opens of the same path from the same process still genuinely contend
# (documented in flock(2): "if a process uses open() to obtain more than one
# descriptor for the same file, these descriptors are treated
# independently"). This exercises the same primitive real concurrent
# processes would use.
# ---------------------------------------------------------------------------


def _run_rebuild_mid_flight(
    mpath: Path,
    ws_dir: Path,
    *,
    claim_may_proceed: threading.Event,
    claim_done: threading.Event,
    results: dict,
) -> None:
    """Mimic reconcile_worktree's manifest rebuild: read prior_owner, THEN
    (mid critical section, still holding the lock) let the claim run, THEN
    write the rebuilt manifest carrying the (now possibly stale) prior owner
    forward — exactly reconcile.py:635-776's shape."""
    with reconcile_lock(ws_dir):
        prior = read_central_manifest(mpath)
        prior_owner = owner_of(prior)

        # Open the window: let the claim attempt its write now, while we
        # still hold the lock.
        claim_may_proceed.set()
        claim_done.wait(timeout=5)

        rebuilt = {
            "schema_version": 1,
            "group": "g",
            "slug": "s",
            "branch": "worktree-s",
            "members": [{"name": "m", "provision_state": "ready"}],
        }
        carry_forward_owner(rebuilt, prior_owner)
        try:
            write_central_manifest(mpath, rebuilt)  # no allow_owner_change
            results["rebuild_outcome"] = "wrote"
        except ManifestError as e:
            results["rebuild_outcome"] = f"refused: {e}"


def test_unlocked_claim_races_the_rebuild_and_is_unsafe(tmp_path: Path) -> None:
    ws_dir = tmp_path / "worktrees" / "s"
    ws_dir.mkdir(parents=True)
    mpath = ws_dir / "manifest.json"
    write_central_manifest(mpath, _base_manifest("sender-host"))

    claim_may_proceed = threading.Event()
    claim_done = threading.Event()
    results: dict = {}

    rebuild_thread = threading.Thread(
        target=_run_rebuild_mid_flight,
        args=(mpath, ws_dir),
        kwargs=dict(
            claim_may_proceed=claim_may_proceed,
            claim_done=claim_done,
            results=results,
        ),
    )
    rebuild_thread.start()

    # Claim: no lock taken at all — races the rebuild's held critical
    # section directly, the way a claim phase with no locking discipline
    # would.
    assert claim_may_proceed.wait(timeout=5), "rebuild never reached its window"
    claim_outcome = None
    try:
        write_central_manifest(mpath, _base_manifest("peer-host"), allow_owner_change=True)
        claim_outcome = "wrote"
    except ManifestError as e:
        claim_outcome = f"refused: {e}"
    finally:
        claim_done.set()

    rebuild_thread.join(timeout=5)
    assert not rebuild_thread.is_alive()

    final_owner = owner_of(read_central_manifest(mpath))

    print(f"\n[unlocked] claim_outcome={claim_outcome!r} "
          f"rebuild_outcome={results.get('rebuild_outcome')!r} "
          f"final_owner={final_owner!r}")

    # The claim landed (write_central_manifest's own on-disk check only
    # compares against what THIS write's own path had at the moment it
    # started — there is no lock serializing it against the rebuild, so it
    # goes straight through).
    assert claim_outcome == "wrote"

    # The unsafety: the rebuild, holding the lock across its own read and
    # write, still carries forward the STALE prior_owner it read before the
    # claim landed, and write_central_manifest's guard (comparing against
    # on-disk state that has since changed to "peer-host") refuses it. This
    # is not a silent lost update — the guard converts it into a hard
    # failure: the detached provisioner's rebuild raises and the manifest
    # write in that phase never completes. Either way the outcome is unsafe:
    # the finish/provisioner path errors out instead of cleanly carrying the
    # claim forward.
    assert results.get("rebuild_outcome", "").startswith("refused:")
    # And because the rebuild refused, its write never landed — the peer's
    # claim is what's on disk. But this is the guard saving the DATA at the
    # cost of the rebuild raising uncaught in a detached background process
    # — an unhandled failure mode of its own, and still proof the unlocked
    # race is real and consequential.
    assert final_owner == "peer-host"


def test_claim_holding_reconcile_lock_is_excluded_from_the_rebuild(tmp_path: Path) -> None:
    ws_dir = tmp_path / "worktrees" / "s"
    ws_dir.mkdir(parents=True)
    mpath = ws_dir / "manifest.json"
    write_central_manifest(mpath, _base_manifest("sender-host"))

    claim_may_proceed = threading.Event()
    claim_done = threading.Event()
    results: dict = {}
    order: list[str] = []

    rebuild_thread = threading.Thread(
        target=_run_rebuild_mid_flight,
        args=(mpath, ws_dir),
        kwargs=dict(
            claim_may_proceed=claim_may_proceed,
            claim_done=claim_done,
            results=results,
        ),
    )
    rebuild_thread.start()

    assert claim_may_proceed.wait(timeout=5), "rebuild never reached its window"

    # Claim now attempts the SAME lock the rebuild holds. It must block —
    # prove that by racing a flag flip against the acquire.
    claim_blocked = threading.Event()

    def _claim() -> None:
        with reconcile_lock(ws_dir):
            order.append("claim-acquired")
            write_central_manifest(mpath, _base_manifest("peer-host"), allow_owner_change=True)
        claim_done.set()

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()

    # The rebuild is still holding the lock (it is blocked on claim_done,
    # which only the claim sets — and the claim cannot even acquire the
    # lock yet). Give the claim thread a moment to attempt acquisition; it
    # must NOT have appended "claim-acquired" yet, because the rebuild still
    # holds the lock and is waiting on claim_done — a deadlock this test
    # deliberately avoids by having the rebuild NOT wait for claim_done
    # under the lock in this variant. Instead, release the rebuild's window
    # immediately since the whole point here is mutual exclusion, not
    # interleaving.
    time.sleep(0.3)
    assert "claim-acquired" not in order, (
        "claim acquired reconcile_lock while the rebuild still held it — "
        "no mutual exclusion"
    )

    # Now let the rebuild finish its critical section (release the lock).
    claim_done.set()
    rebuild_thread.join(timeout=5)
    claim_thread.join(timeout=5)

    assert not rebuild_thread.is_alive()
    assert not claim_thread.is_alive()
    assert order == ["claim-acquired"]

    # The rebuild, unraced (claim only ran after it released), carried the
    # original owner forward cleanly — no refusal.
    assert results.get("rebuild_outcome") == "wrote"

    # The claim then ran strictly after, and its write lands cleanly.
    final_owner = owner_of(read_central_manifest(mpath))
    assert final_owner == "peer-host"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-x", "-q"]))
