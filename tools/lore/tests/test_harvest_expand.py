"""Slice 1 tests: harvest-pending expansion on `lore finish`.

TDD — written before implementation. Covers (council dispositions in brackets):

- (a) each of the 5 in-scope types (deferred/decision/dead-end/radar/lesson)
      expands into a correct note in the right dir with one-liner fields populated.
- (b) lesson uses the new lesson template.
- (c) gotcha is surfaced AND left in harvest-pending.md (not expanded) [scope].
- (d) a malformed/unmarked line is retained + warned (not silently consumed)
      [Reliability idempotency].
- (e) consumed entries are removed from harvest-pending.md by hash marker.
- (f) ONE commit covers session note + new notes + pending; explicit paths only —
      an UNRELATED dirty file in the vault is NOT swept in [Reliability scope].
- (g) commit-failure path: a failing pre-commit hook → no entry lost (notes on
      disk; pending still holds the entries → re-expand cleanly) [Reliability data-loss].
- (h) re-run is idempotent (no duplicate notes) [Reliability idempotency].

ALL fixtures SYNTHETIC — zero private tokens (council Security, public repo).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


def run_cli(args, env=None, cwd=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, cwd=cwd,
    )


def _git_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for d in ("sessions", "deferred", "decisions", "dead-ends", "radar", "lessons"):
        (vault / d).mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@e.st"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Tester"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
                   check=True, capture_output=True)
    return vault


# Synthetic harvest entries, one per type + a gotcha + a malformed line.
PENDING_BODY = """\
# Harvest pending

Staging area.

## 2026-06-04T10:00:00Z — some-agent — widget-worktree

- deferred: rewrite the gizmo loader. Trigger to revisit: when the gizmo count exceeds 100.  <!-- h:aaaaaaaaaaaa -->
- decision: chose the ring buffer over a linked list because lookups stay O(1). Reversibility: hard.  <!-- h:bbbbbbbbbbbb -->
- dead-end: tried caching the sprocket index in memory. Failed because the index outgrew the heap. Revive if: we shard the sprocket store.  <!-- h:cccccccccccc -->
- radar: the doodad library v3 release. Cadence: monthly. Why: v3 removes the legacy adapter we depend on.  <!-- h:dddddddddddd -->
- lesson: skipped the bounds check on the flange array. Why it matters: out-of-range writes corrupt the adjacent widget. Confidence: high.  <!-- h:eeeeeeeeeeee -->
- gotcha: the flux capacitor resets its counter on every reconnect. Where it bit: flux_capacitor.py:88.  <!-- h:ffffffffffff -->
- this is a malformed line with no type prefix and no hash marker
"""


def _seed_pending(vault: Path) -> Path:
    pending = vault / "harvest-pending.md"
    pending.write_text(PENDING_BODY)
    return pending


def _seed_session_note(vault: Path, worktree: str = "widget-worktree") -> Path:
    note = vault / "sessions" / f"2026-06-04-1000-{worktree}.md"
    note.write_text(
        "---\n"
        "type: session\n"
        "project: test-project\n"
        f"worktree: {worktree}\n"
        "branch: main\n"
        "started: 2026-06-04T10:00:00Z\n"
        "ended:\n"
        "subsystems: []\n"
        "phase: Orient\n"
        "session_id: sid-1\n"
        "status: active\n"
        "---\n\n"
        f"# Session: {worktree}\n\n"
        "## What we did\n\n"
        "## Decided\n\n"
        "## Deferred\n\n"
        "## Learned\n\n"
        "## Open questions\n\n"
        "## Harvest candidates\n\n"
        "- radar: the gadget API deprecation. Cadence: weekly. Why: the v1 endpoint sunsets soon.  <!-- h:1111aaaa1111 -->\n"
    )
    return note


def _commit_baseline(vault: Path):
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-m", "baseline"],
                   check=True, capture_output=True)


def _finish(vault: Path, tmp_path: Path, worktree: str = "widget-worktree"):
    fake_cwd = tmp_path / worktree
    fake_cwd.mkdir(exist_ok=True)
    return run_cli(["finish"], env={"LORE_VAULT": str(vault)}, cwd=str(fake_cwd))


def _notes_in(vault: Path, subdir: str) -> list[Path]:
    # Harvested notes land in <subdir>/YYYY-MM/ buckets; include both the bucket
    # level and any flat notes so assertions hold regardless of layout.
    d = vault / subdir
    return sorted(list(d.glob("*.md")) + list(d.glob("*/*.md")))


# ---------------------------------------------------------------------------
# (a) + (b): each in-scope type expands into a correct note in the right dir
# ---------------------------------------------------------------------------

class TestExpansionLandsNotes:
    def test_deferred_note_created_with_fields(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        result = _finish(vault, tmp_path)
        assert result.returncode == 0, result.stderr
        notes = _notes_in(vault, "deferred")
        assert len(notes) == 1, notes
        body = notes[0].read_text()
        assert "rewrite the gizmo loader" in body
        assert "when the gizmo count exceeds 100" in body
        assert "type: deferred" in body

    def test_decision_note_created_with_fields(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        _finish(vault, tmp_path)
        notes = _notes_in(vault, "decisions")
        assert len(notes) == 1, notes
        body = notes[0].read_text()
        assert "ring buffer" in body
        assert "lookups stay O(1)" in body
        assert "type: decision" in body

    def test_dead_end_note_created_with_fields(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        _finish(vault, tmp_path)
        notes = _notes_in(vault, "dead-ends")
        assert len(notes) == 1, notes
        body = notes[0].read_text()
        assert "caching the sprocket index" in body
        assert "outgrew the heap" in body
        assert "shard the sprocket store" in body
        assert "type: dead-end" in body

    def test_radar_note_created_with_fields(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        _finish(vault, tmp_path)
        notes = _notes_in(vault, "radar")
        # one from pending + one from the session-note Harvest candidates block
        bodies = [n.read_text() for n in notes]
        assert any("doodad library v3" in b for b in bodies), bodies
        assert any("gadget API deprecation" in b for b in bodies), bodies
        assert all("type: radar" in b for b in bodies)

    def test_lesson_note_created_with_fields_and_template(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        _finish(vault, tmp_path)
        notes = _notes_in(vault, "lessons")
        assert len(notes) == 1, notes
        body = notes[0].read_text()
        assert "type: lesson" in body
        assert "bounds check on the flange array" in body
        assert "corrupt the adjacent widget" in body
        # (b) uses the new lesson template's distinctive sections
        assert "## What we did wrong" in body
        assert "## How to prevent recurrence" in body


# ---------------------------------------------------------------------------
# (c) gotcha surfaced + left in pending, not expanded
# ---------------------------------------------------------------------------

class TestGotchaSurfaced:
    def test_gotcha_not_expanded_no_gotcha_dir_note(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        result = _finish(vault, tmp_path)
        # no note dir should contain the gotcha body
        for sub in ("deferred", "decisions", "dead-ends", "radar", "lessons"):
            for note in _notes_in(vault, sub):
                assert "flux capacitor resets its counter" not in note.read_text()
        # surfaced in the finish output
        out = result.stdout + result.stderr
        assert "gotcha" in out.lower()
        assert "flux capacitor" in out

    def test_gotcha_left_in_pending(self, tmp_path):
        vault = _git_vault(tmp_path)
        pending = _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        _finish(vault, tmp_path)
        assert "h:ffffffffffff" in pending.read_text()

    def test_session_note_gotcha_is_surfaced(self, tmp_path):
        """A gotcha in the session note's ## Harvest candidates is surfaced.

        Session-note gotchas are NOT added to consumed_hashes (the session note
        is committed verbatim — the entry stays in it), but the operator must
        see them in the finish report so they can manually patch the subsystem.
        """
        vault = _git_vault(tmp_path)
        # No pending entries — only a session-note gotcha.
        (vault / "harvest-pending.md").write_text("# Harvest pending\n\nStaging area.\n")
        note = vault / "sessions" / "2026-06-04-1000-widget-worktree.md"
        note.write_text(
            "---\n"
            "type: session\n"
            "project: test-project\n"
            "worktree: widget-worktree\n"
            "branch: main\n"
            "started: 2026-06-04T10:00:00Z\n"
            "ended:\n"
            "subsystems: []\n"
            "phase: Orient\n"
            "session_id: sid-2\n"
            "status: active\n"
            "---\n\n"
            "# Session: widget-worktree\n\n"
            "## What we did\n\n"
            "## Harvest candidates\n\n"
            "- gotcha: the sprocket cache silently drops entries when the TTL overflows. "
            "Where it bit: sprocket_cache.py:42.  <!-- h:cccc2222cccc -->\n"
        )
        _commit_baseline(vault)
        result = _finish(vault, tmp_path)
        out = result.stdout + result.stderr
        assert "gotcha" in out.lower(), f"expected 'gotcha' in output; got: {out!r}"
        assert "sprocket cache" in out, f"expected gotcha body in output; got: {out!r}"


# ---------------------------------------------------------------------------
# (d) malformed line retained + warned
# ---------------------------------------------------------------------------

class TestMalformedLineRetained:
    def test_malformed_line_kept_and_warned(self, tmp_path):
        vault = _git_vault(tmp_path)
        pending = _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        result = _finish(vault, tmp_path)
        assert "this is a malformed line with no type prefix" in pending.read_text()
        out = result.stdout + result.stderr
        assert "malformed" in out.lower() or "unmarked" in out.lower() or "warn" in out.lower()


# ---------------------------------------------------------------------------
# (e) consumed entries removed from pending
# ---------------------------------------------------------------------------

class TestConsumedEntriesRemoved:
    def test_expanded_hashes_removed_from_pending(self, tmp_path):
        vault = _git_vault(tmp_path)
        pending = _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        _finish(vault, tmp_path)
        text = pending.read_text()
        for h in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc",
                  "dddddddddddd", "eeeeeeeeeeee"):
            assert f"h:{h}" not in text, f"{h} should have been consumed"


# ---------------------------------------------------------------------------
# (f) ONE commit covers everything; explicit paths only — unrelated file not swept
# ---------------------------------------------------------------------------

class TestSingleCommitExplicitPaths:
    def test_one_commit_covers_note_and_new_notes(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        before = subprocess.run(
            ["git", "-C", str(vault), "rev-list", "--count", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        _finish(vault, tmp_path)
        after = subprocess.run(
            ["git", "-C", str(vault), "rev-list", "--count", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert int(after) == int(before) + 1, f"expected exactly one new commit ({before} -> {after})"
        # the single commit's tree includes session note + new notes + pending
        files = subprocess.run(
            ["git", "-C", str(vault), "show", "--name-only", "--pretty=format:", "HEAD"],
            capture_output=True, text=True,
        ).stdout
        assert "harvest-pending.md" in files
        assert "sessions/" in files
        assert "deferred/" in files

    def test_unrelated_dirty_file_not_swept_into_commit(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        # an unrelated dirty file present at finish time
        stray = vault / "decisions" / "unrelated-scratch.md"
        stray.write_text("scratch work, not part of the finish\n")
        _finish(vault, tmp_path)
        committed = subprocess.run(
            ["git", "-C", str(vault), "show", "--name-only", "--pretty=format:", "HEAD"],
            capture_output=True, text=True,
        ).stdout
        assert "unrelated-scratch.md" not in committed
        # and it is still dirty/untracked, not consumed
        status = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        assert "unrelated-scratch.md" in status


# ---------------------------------------------------------------------------
# (g) commit-failure path: no entry lost
# ---------------------------------------------------------------------------

class TestCommitFailureNoDataLoss:
    def test_failing_commit_leaves_entries_recoverable(self, tmp_path):
        vault = _git_vault(tmp_path)
        pending = _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        # Install a pre-commit hook that always fails.
        hooks_dir = vault / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

        result = _finish(vault, tmp_path)
        # entries must still be recoverable: the in-scope hashes remain in pending
        text = pending.read_text()
        for h in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc",
                  "dddddddddddd", "eeeeeeeeeeee"):
            assert f"h:{h}" in text, f"{h} lost after commit failure"
        # surface the failure to the operator
        out = result.stdout + result.stderr
        assert "commit" in out.lower() and ("fail" in out.lower() or "error" in out.lower())

    def test_retry_after_commit_failure_expands_cleanly(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        hooks_dir = vault / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        # first finish: commit fails
        _finish(vault, tmp_path)
        # repair: remove the failing hook, retry
        hook.unlink()
        result = _finish(vault, tmp_path)
        assert result.returncode == 0, result.stderr
        # exactly one note per in-scope type after recovery (no dups)
        assert len(_notes_in(vault, "deferred")) == 1
        assert len(_notes_in(vault, "decisions")) == 1
        assert len(_notes_in(vault, "dead-ends")) == 1
        assert len(_notes_in(vault, "lessons")) == 1
        # pending no longer holds the consumed hashes
        text = (vault / "harvest-pending.md").read_text()
        assert "h:aaaaaaaaaaaa" not in text


# ---------------------------------------------------------------------------
# (h) re-run idempotent — no duplicate notes
# ---------------------------------------------------------------------------

class TestIdempotentRerun:
    def test_second_finish_creates_no_duplicate_notes(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        _finish(vault, tmp_path)
        deferred_after_first = len(_notes_in(vault, "deferred"))
        # second finish (note already complete; pending already cleared)
        _finish(vault, tmp_path)
        assert len(_notes_in(vault, "deferred")) == deferred_after_first

    def test_rerun_with_same_pending_does_not_dup(self, tmp_path):
        # If pending still carries a hash (e.g. re-added), expansion must not
        # create a second note for an already-expanded hash.
        vault = _git_vault(tmp_path)
        pending = _seed_pending(vault)
        _seed_session_note(vault)
        _commit_baseline(vault)
        _finish(vault, tmp_path)
        # re-add the deferred entry to pending and re-run finish on a fresh note
        with pending.open("a") as f:
            f.write(
                "\n## 2026-06-04T11:00:00Z — some-agent — widget-worktree\n\n"
                "- deferred: rewrite the gizmo loader. Trigger to revisit: when the gizmo count exceeds 100.  <!-- h:aaaaaaaaaaaa -->\n"
            )
        _seed_session_note(vault, worktree="widget-worktree")
        # reactivate session note so finish runs again
        note = vault / "sessions" / "2026-06-04-1000-widget-worktree.md"
        txt = note.read_text().replace("status: complete", "status: active")
        note.write_text(txt)
        _finish(vault, tmp_path)
        assert len(_notes_in(vault, "deferred")) == 1


# ---------------------------------------------------------------------------
# date-bucketing: harvested notes land in <folder>/YYYY-MM/, dedup recurses
# ---------------------------------------------------------------------------

# The scripts dir must be importable so harvest's `from vault import ...`
# resolves the sibling module the same way the CLI wires it up.
_SCRIPTS_DIR = str(PLUGIN_ROOT / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import harvest  # noqa: E402

TEMPLATES_DIR = PLUGIN_ROOT / "templates"
TODAY = "2026-06-04"
BUCKET = "2026-06"


def _expand(vault: Path, pending_text: str, session_text: str = ""):
    return harvest.expand(
        vault=vault,
        pending_text=pending_text,
        session_text=session_text,
        templates_dir=TEMPLATES_DIR,
        today=TODAY,
        project="test-project",
    )


class TestHarvestBuckets:
    def _bare_vault(self, tmp_path: Path) -> Path:
        vault = tmp_path / "vault"
        for d in ("deferred", "decisions", "dead-ends", "radar", "lessons"):
            (vault / d).mkdir(parents=True, exist_ok=True)
        return vault

    def test_lesson_lands_in_month_bucket_not_flat(self, tmp_path):
        vault = self._bare_vault(tmp_path)
        pending = (
            "- lesson: skipped the bounds check on the flange array. "
            "Why it matters: out-of-range writes corrupt the adjacent widget. "
            "Confidence: high.  <!-- h:eeeeeeeeeeee -->\n"
        )
        result = _expand(vault, pending)
        assert len(result.written) == 1
        written = result.written[0]
        assert written.parent == vault / "lessons" / BUCKET, written
        # nothing flat in lessons/
        assert list((vault / "lessons").glob("*.md")) == []

    def test_decision_lands_in_month_bucket_not_flat(self, tmp_path):
        vault = self._bare_vault(tmp_path)
        pending = (
            "- decision: chose the ring buffer over a linked list because "
            "lookups stay O(1). Reversibility: hard.  <!-- h:bbbbbbbbbbbb -->\n"
        )
        result = _expand(vault, pending)
        assert len(result.written) == 1
        assert result.written[0].parent == vault / "decisions" / BUCKET
        assert list((vault / "decisions").glob("*.md")) == []

    def test_unique_path_dedupes_within_bucket(self, tmp_path):
        vault = self._bare_vault(tmp_path)
        # two lessons with the same lead → same stem → second gets -2,
        # BOTH inside the month bucket.
        pending = (
            "- lesson: the widget broke. Confidence: high.  <!-- h:1111111111aa -->\n"
            "- lesson: the widget broke. Confidence: low.  <!-- h:2222222222bb -->\n"
        )
        result = _expand(vault, pending)
        assert len(result.written) == 2
        bucket = vault / "lessons" / BUCKET
        names = {p.name for p in result.written}
        assert all(p.parent == bucket for p in result.written), result.written
        assert "2026-06-04-the-widget-broke.md" in names, names
        assert "2026-06-04-the-widget-broke-2.md" in names, names

    def test_dedup_is_bucket_aware_no_duplicate(self, tmp_path):
        # Regression: a hash already stamped in a BUCKETED note must be seen by
        # the dedup scan, so a re-harvest of the same hash writes nothing.
        vault = self._bare_vault(tmp_path)
        pending = (
            "- lesson: skipped the bounds check on the flange array. "
            "Confidence: high.  <!-- h:eeeeeeeeeeee -->\n"
        )
        first = _expand(vault, pending)
        assert len(first.written) == 1
        # the note is bucketed; a flat-only scan would miss it
        assert first.written[0].parent == vault / "lessons" / BUCKET
        second = _expand(vault, pending)
        assert second.written == [], second.written
        # still exactly one note total
        all_lessons = list((vault / "lessons" / BUCKET).glob("*.md"))
        assert len(all_lessons) == 1, all_lessons

    def test_second_expand_same_pending_writes_nothing_new(self, tmp_path):
        vault = self._bare_vault(tmp_path)
        pending = (
            "- deferred: rewrite the gizmo loader. "
            "Trigger to revisit: gizmo count exceeds 100.  <!-- h:aaaaaaaaaaaa -->\n"
            "- decision: chose the ring buffer. Reversibility: hard.  <!-- h:bbbbbbbbbbbb -->\n"
        )
        first = _expand(vault, pending)
        assert len(first.written) == 2
        second = _expand(vault, pending)
        assert second.written == [], second.written
