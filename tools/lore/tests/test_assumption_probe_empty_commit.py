"""EPHEMERAL assumption probe — NOT part of the permanent suite.

Resolves the unknown blocking task/a-deletion-wins-over-a-change-on-the-other-side:
once a delete/modify conflict is settled by staging the DELETION on top of a
replay step whose parent tree already lacks the record (the other side already
removed it), can `git rebase --continue` land an EMPTY commit, and if so, is
that exit distinguishable from a genuine replay failure using only exit code /
git state — no prose parsing?

Finding (git 2.54, plain non-interactive `git rebase <upstream>` — the exact
invocation `_start_rebase`/`_rebase_continue` issue): the now-empty commit is
silently DROPPED, not landed and not stopped on. `--continue` reports plain
success (rc 0, "Successfully rebased...") and folds straight through to the
next real step in the same call. There is no ambiguous/failure signal to
disambiguate at all on this path.

Delete this file (and nothing else) once the executor has read its verdict and
written the real behavioral tests for the task.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from test_resolve_core import _Fixture, _git, _commit


def _rebase_continue(vault: Path) -> subprocess.CompletedProcess:
    """Exactly the invocation `cli/resolve.py::_rebase_continue` issues."""
    import os
    env = dict(os.environ)
    env["GIT_EDITOR"] = "true"
    return subprocess.run(
        ["git", "-C", str(vault), "rebase", "--continue"],
        capture_output=True, text=True, env=env,
    )


def test_deletion_wins_on_top_of_an_already_deleted_tree_yields_an_empty_commit(
    tmp_path,
):
    """Build a real delete/modify collision, settle it by staging the removal,
    and observe git's own behaviour on `rebase --continue` against real git —
    not documentation.
    """
    fx = _Fixture(tmp_path)
    record_id = fx.create("task", "A Task")
    fx.publish()
    fx.clone_device_b()

    # Remote (device B) deletes the whole record — both files.
    (fx.other / f"{record_id}.md").unlink()
    (fx.other / f"{record_id}.json").unlink()
    fx.push_device_b("device B deleted the record")

    # Local (device A) modifies the record's body — a genuine delete/modify
    # collision when device A's commit is replayed onto device B's tip.
    (fx.vault / f"{record_id}.md").write_text("local prose changed\n", encoding="utf-8")
    _commit(fx.vault, "device A edited the body")

    _git(fx.vault, "fetch", "origin")
    _git(fx.vault, "rebase", f"origin/{fx.branch}")
    assert (fx.vault / ".git" / "rebase-merge").exists(), (
        "fixture must land mid-rebase with a delete/modify conflict pending"
    )

    status = _git(fx.vault, "status", "--porcelain=v1")
    assert f"{record_id}.md" in status.stdout, (
        f"expected a conflicted path for {record_id}.md, got:\n{status.stdout}"
    )

    # Settle the collision the way "deletion wins" would: stage the removal.
    # Both paths — the base tree BEFORE this step already lacks the record
    # (device B removed it upstream), so staging its removal reproduces a
    # tree identical to the parent commit's tree.
    # The sidecar (.json) was unchanged on device A and deleted on device B —
    # git auto-resolves that one to "deleted" with no conflict, exactly as
    # the existing body-only delete/modify fixture demonstrates. Only the
    # .md is left conflicted; settle it the way "deletion wins" would.
    rm = _git(fx.vault, "rm", "-f", "--", f"{record_id}.md")
    assert rm.returncode == 0, rm.stderr

    diff_cached = _git(fx.vault, "diff", "--cached", "--stat")
    print("staged diff --cached --stat:", repr(diff_cached.stdout))

    proc = _rebase_continue(fx.vault)
    still_mid = (fx.vault / ".git" / "rebase-merge").exists()
    conflicted_after = _git(fx.vault, "diff", "--name-only", "--diff-filter=U").stdout

    print("rc:", proc.returncode)
    print("stdout:", proc.stdout)
    print("stderr:", proc.stderr)
    print("still_mid (rebase-merge dir present):", still_mid)
    print("conflicted paths after --continue:", repr(conflicted_after))

    # ── the assumption, corrected against what git 2.54 actually does ──────
    #
    # git's own default (`--empty=drop`, undocumented-but-default for a plain
    # `git rebase <upstream>` — see `git rebase --help`) means the now-empty
    # commit is never landed AND never stopped on: it is silently dropped and
    # the rebase reports plain success. No empty commit reaches history, no
    # non-zero exit, no "no conflict to settle" ambiguity — because there is
    # no failure signal to disambiguate in the first place.
    assert proc.returncode == 0, (
        f"expected `rebase --continue` to succeed by silently dropping the "
        f"now-empty commit (git's default --empty=drop), not "
        f"(stdout={proc.stdout!r} stderr={proc.stderr!r})"
    )
    assert not still_mid, (
        "expected the rebase to have completed in this single --continue call "
        "(dropping a commit does not itself consume a rebase step the way a "
        "real conflict does)"
    )
    assert conflicted_after.strip() == "", "no conflicted paths should remain"

    # The dropped commit must not appear in history at all.
    log = _git(fx.vault, "log", "--oneline", f"origin/{fx.branch}..HEAD")
    assert "device A edited the body" not in log.stdout, (
        f"the emptied commit must not land in history, got:\n{log.stdout}"
    )

    # git's stderr on this path is exactly "Successfully rebased and updated
    # refs/heads/<branch>.\n" — no mention of "empty" anywhere. Recorded here
    # so the report can quote it precisely; NOT a text a caller should key on
    # (see recommendation).
    print("literal stderr on success:", repr(proc.stderr))


def test_a_second_step_still_pending_the_dropped_commit_advances_silently(tmp_path):
    """Same collision, but with a SECOND local commit still to replay after it.

    Isolates whether `--continue`'s silent-drop behaviour is an artifact of
    "that was the last step" (rebase simply finishes) or holds mid-rebase too
    — i.e. does git advance past the dropped step to the next one with rc 0
    and no trace, or does it stop needing a second `--continue`?
    """
    fx = _Fixture(tmp_path)
    record_id = fx.create("task", "A Task")
    other_id = fx.create("task", "Another Task")
    fx.publish()
    fx.clone_device_b()

    (fx.other / f"{record_id}.md").unlink()
    (fx.other / f"{record_id}.json").unlink()
    fx.push_device_b("device B deleted the record")

    # Local commit 1: edits the doomed record (will collide + become empty).
    (fx.vault / f"{record_id}.md").write_text("local prose changed\n", encoding="utf-8")
    _git(fx.vault, "add", "-A")
    _git(fx.vault, "commit", "-m", "device A edited the body")

    # Local commit 2: edits an UNRELATED record — a real step that must still
    # land after the dropped one.
    (fx.vault / f"{other_id}.md").write_text("second commit prose\n", encoding="utf-8")
    _git(fx.vault, "add", "-A")
    _git(fx.vault, "commit", "-m", "device A edited the other record")

    _git(fx.vault, "fetch", "origin")
    _git(fx.vault, "rebase", f"origin/{fx.branch}")
    assert (fx.vault / ".git" / "rebase-merge").exists()

    rm = _git(fx.vault, "rm", "-f", "--", f"{record_id}.md")
    assert rm.returncode == 0, rm.stderr

    proc = _rebase_continue(fx.vault)
    still_mid = (fx.vault / ".git" / "rebase-merge").exists()

    log = _git(fx.vault, "log", "--oneline", f"origin/{fx.branch}..HEAD")
    print("rc:", proc.returncode, "stdout:", proc.stdout, "stderr:", proc.stderr)
    print("still_mid after continue:", still_mid)
    print("replayed log:", log.stdout)

    # The dropped step is invisible: no error, no stop, no commit for it —
    # and the rebase is DONE in one `--continue` (both steps collapsed into
    # this single call), since dropping doesn't consume a --continue of its
    # own; git moves straight on to apply the next real commit.
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert not still_mid, "rebase should have completed, folding past the dropped step"
    assert "device A edited the body" not in log.stdout, (
        "the emptied commit must not appear in history — it was dropped, not committed"
    )
    assert "device A edited the other record" in log.stdout, (
        "the following real commit must still have landed"
    )
