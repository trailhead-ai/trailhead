"""Tests for inject.py — mid-session context-injection queue + drain.

The claude-hook strategy enqueues a member doc to <workspace>/.camp/inject_queue/
(one file per enqueue so multiple camp enters before a drain are not lost). The
hidden `camp inject --drain` reads the queue, emits the Claude Code PostToolUse
additionalContext JSON contract to stdout, then clears the queue. An empty queue
emits NOTHING (exit 0). The drain is resilient — on any internal error it exits 0
with no output so it never crashes a tool call.

Test contract:
- camp inject --drain with a queued doc → valid PostToolUse additionalContext JSON
  (parse it, assert the doc content is inside additionalContext); queue cleared.
- camp inject --drain with an empty queue → no output, exit 0.
- multiple enqueues before a drain → all docs present in the drained output.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------


class TestEnqueue:
    def test_enqueue_writes_a_queue_file(self, tmp_path: Path):
        from camp.launch.inject import enqueue_doc, queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "# member doc\n")

        files = list(queue_dir_for(ws).iterdir())
        assert len(files) == 1
        assert files[0].read_text() == "# member doc\n"

    def test_multiple_enqueues_do_not_overwrite(self, tmp_path: Path):
        from camp.launch.inject import enqueue_doc, queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "doc-one")
        enqueue_doc(ws, "doc-two")

        files = list(queue_dir_for(ws).iterdir())
        assert len(files) == 2

    def test_enqueue_unique_even_on_same_time_ns(self, tmp_path: Path):
        """BUG 8: if two enqueues collide on the same time_ns, the uuid suffix
        still keeps filenames unique — no overwrite."""
        import unittest.mock as mock
        from camp.launch.inject import enqueue_doc, queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()
        with mock.patch("camp.launch.inject.time.time_ns", return_value=42):
            enqueue_doc(ws, "doc-one")
            enqueue_doc(ws, "doc-two")

        files = list(queue_dir_for(ws).iterdir())
        assert len(files) == 2
        bodies = {f.read_text() for f in files}
        assert bodies == {"doc-one", "doc-two"}


# ---------------------------------------------------------------------------
# drain
# ---------------------------------------------------------------------------


def _drain(ws: Path) -> tuple[str, int]:
    """Run drain_queue capturing stdout; return (stdout, exit_code)."""
    from camp.launch.inject import drain_queue

    out = io.StringIO()
    import contextlib

    code = 0
    with contextlib.redirect_stdout(out):
        code = drain_queue(ws)
    return out.getvalue(), code


class TestDrain:
    def test_drain_emits_posttooluse_additional_context_json(self, tmp_path: Path):
        from camp.launch.inject import enqueue_doc

        ws = tmp_path / "ws"
        ws.mkdir()
        doc = "# MyRepo CLAUDE.md\n\nactivated.\n"
        enqueue_doc(ws, doc)

        stdout, code = _drain(ws)
        assert code == 0
        parsed = json.loads(stdout)
        assert parsed["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert doc in parsed["hookSpecificOutput"]["additionalContext"]

    def test_drain_clears_queue(self, tmp_path: Path):
        from camp.launch.inject import enqueue_doc, queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "some doc")

        _drain(ws)

        # Queue is empty after a drain.
        qdir = queue_dir_for(ws)
        assert list(qdir.iterdir()) == []

    def test_drain_second_call_is_empty(self, tmp_path: Path):
        from camp.launch.inject import enqueue_doc

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "some doc")

        _drain(ws)
        stdout, code = _drain(ws)
        assert stdout == ""
        assert code == 0

    def test_drain_includes_all_queued_docs(self, tmp_path: Path):
        from camp.launch.inject import enqueue_doc

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "FIRST-DOC")
        enqueue_doc(ws, "SECOND-DOC")

        stdout, _ = _drain(ws)
        parsed = json.loads(stdout)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "FIRST-DOC" in ctx
        assert "SECOND-DOC" in ctx

    def test_drain_emits_docs_in_enqueue_order(self, tmp_path: Path):
        """BUG 8: docs must surface in enqueue order A, B, C — not uuid-sorted order."""
        from camp.launch.inject import enqueue_doc

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "DOC-A")
        enqueue_doc(ws, "DOC-B")
        enqueue_doc(ws, "DOC-C")

        stdout, _ = _drain(ws)
        parsed = json.loads(stdout)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert ctx.index("DOC-A") < ctx.index("DOC-B") < ctx.index("DOC-C")

    def test_drain_empty_queue_no_output(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()

        stdout, code = _drain(ws)
        assert stdout == ""
        assert code == 0

    def test_drain_no_workspace_no_output(self, tmp_path: Path):
        """A workspace with no .camp/inject_queue at all → no output, exit 0."""
        ws = tmp_path / "does-not-exist"

        stdout, code = _drain(ws)
        assert stdout == ""
        assert code == 0


# ---------------------------------------------------------------------------
# CLI route: camp inject --drain
# ---------------------------------------------------------------------------

import os  # noqa: E402
import subprocess  # noqa: E402

_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"


def _run_cli(args: list[str], *, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    base = {**os.environ}
    base.update(env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=base,
        cwd=str(cwd),
    )


# ---------------------------------------------------------------------------
# resolve_group_slug_from_cwd — pure path arithmetic, no group-config load
# ---------------------------------------------------------------------------


def _state_env(state_dir: Path) -> dict[str, str]:
    return {"CAMP_STATE_DIR": str(state_dir)}


class TestResolveGroupSlugFromCwd:
    def test_resolves_group_and_slug_from_member_worktree_cwd(self, tmp_path: Path):
        from camp.launch.inject import resolve_group_slug_from_cwd

        state = tmp_path / "state"
        member_dir = state / "mygroup" / "worktrees" / "my-slug" / "myrepo"
        member_dir.mkdir(parents=True)

        assert resolve_group_slug_from_cwd(member_dir, state) == ("mygroup", "my-slug")

    def test_resolves_from_a_deep_member_subdir(self, tmp_path: Path):
        from camp.launch.inject import resolve_group_slug_from_cwd

        state = tmp_path / "state"
        deep = state / "mygroup" / "worktrees" / "my-slug" / "myrepo" / "sub" / "dir"
        deep.mkdir(parents=True)

        assert resolve_group_slug_from_cwd(deep, state) == ("mygroup", "my-slug")

    def test_resolves_from_the_workspace_root_itself(self, tmp_path: Path):
        """No member segment needed — 3 parts (group/worktrees/slug) is enough."""
        from camp.launch.inject import resolve_group_slug_from_cwd

        state = tmp_path / "state"
        ws = state / "mygroup" / "worktrees" / "my-slug"
        ws.mkdir(parents=True)

        assert resolve_group_slug_from_cwd(ws, state) == ("mygroup", "my-slug")

    def test_returns_none_outside_state_dir(self, tmp_path: Path):
        from camp.launch.inject import resolve_group_slug_from_cwd

        state = tmp_path / "state"
        nowhere = tmp_path / "nowhere" / "deep"
        nowhere.mkdir(parents=True)

        assert resolve_group_slug_from_cwd(nowhere, state) is None

    def test_returns_none_when_not_shaped_group_worktrees_slug(self, tmp_path: Path):
        from camp.launch.inject import resolve_group_slug_from_cwd

        state = tmp_path / "state"
        stray = state / "mygroup" / "not-worktrees" / "my-slug" / "myrepo"
        stray.mkdir(parents=True)

        assert resolve_group_slug_from_cwd(stray, state) is None


# ---------------------------------------------------------------------------
# central_queue_dir — the queue root, relocated outside the workspace dir
# ---------------------------------------------------------------------------


class TestCentralQueueDir:
    def test_central_queue_dir_is_not_inside_the_workspace_dir(self, tmp_path: Path):
        from camp.launch.inject import central_queue_dir

        state = tmp_path / "state"
        qdir = central_queue_dir("mygroup", "my-slug", env=_state_env(state))
        workspace_dir = state / "mygroup" / "worktrees" / "my-slug"

        assert workspace_dir not in qdir.parents
        assert qdir != workspace_dir

    def test_central_queue_dir_created_owner_only(self, tmp_path: Path):
        import stat

        from camp.launch.inject import central_queue_dir

        state = tmp_path / "state"
        qdir = central_queue_dir("mygroup", "my-slug", env=_state_env(state))

        assert stat.S_IMODE(qdir.stat().st_mode) == 0o700


# ---------------------------------------------------------------------------
# CLI drain resolves the queue root from cwd via (group, slug), not a walk-up
# ---------------------------------------------------------------------------


class TestInjectCliCwdResolution:
    def test_drain_from_member_worktree_cwd_finds_the_central_queue(self, tmp_path: Path):
        from camp.launch.inject import central_queue_dir, enqueue_doc

        state = tmp_path / "state"
        member_dir = state / "mygroup" / "worktrees" / "my-slug" / "myrepo"
        member_dir.mkdir(parents=True)
        env = _state_env(state)
        enqueue_doc(central_queue_dir("mygroup", "my-slug", env=env), "CWD-RESOLVE-marker")

        result = _run_cli(["inject", "--drain"], env=env, cwd=member_dir)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert "CWD-RESOLVE-marker" in parsed["hookSpecificOutput"]["additionalContext"]

    def test_drain_from_deep_member_subdir_finds_the_central_queue(self, tmp_path: Path):
        from camp.launch.inject import central_queue_dir, enqueue_doc

        state = tmp_path / "state"
        deep = state / "mygroup" / "worktrees" / "my-slug" / "myrepo" / "sub" / "dir"
        deep.mkdir(parents=True)
        env = _state_env(state)
        enqueue_doc(central_queue_dir("mygroup", "my-slug", env=env), "DEEP-RESOLVE-marker")

        result = _run_cli(["inject", "--drain"], env=env, cwd=deep)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert "DEEP-RESOLVE-marker" in parsed["hookSpecificOutput"]["additionalContext"]

    def test_drain_clears_the_central_queue(self, tmp_path: Path):
        from camp.launch.inject import central_queue_dir, enqueue_doc, queue_dir_for

        state = tmp_path / "state"
        member_dir = state / "mygroup" / "worktrees" / "my-slug" / "myrepo"
        member_dir.mkdir(parents=True)
        env = _state_env(state)
        qroot = central_queue_dir("mygroup", "my-slug", env=env)
        enqueue_doc(qroot, "doc")

        _run_cli(["inject", "--drain"], env=env, cwd=member_dir)
        assert list(queue_dir_for(qroot).iterdir()) == []

    def test_drain_with_cwd_outside_any_state_dir_no_output(self, tmp_path: Path):
        state = tmp_path / "state"
        nowhere = tmp_path / "nowhere" / "deep"
        nowhere.mkdir(parents=True)

        result = _run_cli(["inject", "--drain"], env=_state_env(state), cwd=nowhere)
        assert result.returncode == 0
        assert result.stdout == ""


# ---------------------------------------------------------------------------
# FINDING 1 (security): a file planted straight into the OLD, task-reachable
# location must never be drained into agent context — the queue moved, and
# the old location is abandoned, not read.
# ---------------------------------------------------------------------------


class TestOldPredictableSiblingLocationIsNeverDrained:
    def test_file_planted_at_old_workspace_camp_location_is_not_drained(
        self, tmp_path: Path
    ) -> None:
        """Reproduces the closed bypass: a malicious task step (same OS user,
        full filesystem access, cwd = the member worktree) writes straight to
        `<workspace>/.camp/inject_queue/` — reachable from its cwd as simply
        `../.camp/inject_queue` — bypassing enqueue_doc/enqueue_notice and
        build_notice_body entirely. That file must never surface in a drain."""
        state = tmp_path / "state"
        workspace_dir = state / "mygroup" / "worktrees" / "my-slug"
        member_dir = workspace_dir / "myrepo"
        member_dir.mkdir(parents=True)

        old_queue_dir = workspace_dir / ".camp" / "inject_queue"
        old_queue_dir.mkdir(parents=True)
        (old_queue_dir / "evil.md").write_text("ATTACKER-INJECTED-marker")

        result = _run_cli(["inject", "--drain"], env=_state_env(state), cwd=member_dir)

        assert result.returncode == 0
        assert "ATTACKER-INJECTED-marker" not in result.stdout
        assert result.stdout == ""


class TestInjectCli:
    def test_cli_drain_empty_queue_no_output_exit_zero(self, tmp_path: Path):
        """camp inject --drain from a workspace with an empty queue → no output, exit 0."""
        ws = tmp_path / "ws"
        ws.mkdir()
        result = _run_cli(["inject", "--drain", "--workspace", str(ws)], env={}, cwd=ws)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_cli_drain_with_doc_emits_json(self, tmp_path: Path):
        """camp inject --drain emits the PostToolUse JSON when the queue has a doc."""
        from camp.launch.inject import enqueue_doc

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "QUEUED-DOC-marker")

        result = _run_cli(["inject", "--drain", "--workspace", str(ws)], env={}, cwd=ws)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert parsed["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert "QUEUED-DOC-marker" in parsed["hookSpecificOutput"]["additionalContext"]


# ---------------------------------------------------------------------------
# Task B: error-resilience — drain_queue with a poisoned queue entry
# ---------------------------------------------------------------------------


class TestDrainResilience:
    def test_drain_resilient_to_read_error_exits_zero_no_stdout(self, tmp_path: Path):
        """drain_queue with a queue file whose read() raises → exit 0, no stdout.

        Injects the error by patching Path.read_text on the queued file to raise,
        which fires the outer except Exception branch. This proves the crash-proof
        safety net actually catches — without it, drain_queue would propagate the
        exception and the test would error.
        """
        import unittest.mock as mock
        from camp.launch.inject import enqueue_doc, queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "some doc")

        original_read_text = Path.read_text

        def failing_read_text(self, *args, **kwargs):
            if str(self).startswith(str(queue_dir_for(ws))):
                raise OSError("simulated read failure")
            return original_read_text(self, *args, **kwargs)

        with mock.patch.object(Path, "read_text", failing_read_text):
            stdout, code = _drain(ws)

        assert code == 0, f"drain_queue should exit 0 on error, got {code}"
        assert stdout == "", f"drain_queue should emit no stdout on error, got {stdout!r}"

    def test_drain_cli_exits_zero_no_stdout_when_queue_dir_only_has_subdirs(self, tmp_path: Path):
        """camp inject --drain exits 0 with no stdout when the queue only has
        subdirectories (is_file() filters them; the queue appears empty to drain).
        Verifies the CLI route's resilience contract end-to-end."""
        from camp.launch.inject import queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()

        qdir = queue_dir_for(ws)
        qdir.mkdir(parents=True, exist_ok=True)
        # A sub-directory in the queue dir; is_file() → False, filtered out.
        (qdir / "poison_dir.md").mkdir()

        result = _run_cli(["inject", "--drain", "--workspace", str(ws)], env={}, cwd=ws)
        assert result.returncode == 0, (
            f"camp inject --drain should exit 0 even with only dirs in queue. "
            f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
        assert result.stdout == "", (
            f"camp inject --drain should produce no stdout with empty/dir-only queue. "
            f"Got: {result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# build_notice_body — camp-authored, templated-fields-only notice bodies
# ---------------------------------------------------------------------------


class TestBuildNoticeBody:
    def test_body_contains_templated_fields(self):
        from camp.launch.inject import build_notice_body

        body = build_notice_body(
            member="myrepo",
            phase="activate",
            task="dep-install",
            consequence="Run `camp status` for details.",
        )
        assert "myrepo" in body
        assert "activate" in body
        assert "dep-install" in body
        assert "Run `camp status` for details." in body

    def test_body_omits_task_line_when_task_is_none(self):
        from camp.launch.inject import build_notice_body

        body = build_notice_body(
            member="myrepo",
            phase="activate",
            task=None,
            consequence="All done.",
        )
        assert "Task:" not in body

    def test_body_is_assembled_only_from_its_own_arguments(self):
        """Two calls differing only in `consequence` must differ ONLY in the
        substring supplied — proving the body is built from the template's
        fields and not from any other data source (e.g. a task's captured
        output that happened to be reachable at the call site)."""
        from camp.launch.inject import build_notice_body

        kwargs = dict(member="myrepo", phase="activate", task="dep-install")
        body_a = build_notice_body(**kwargs, consequence="AAA-marker")
        body_b = build_notice_body(**kwargs, consequence="BBB-marker")

        assert "AAA-marker" in body_a and "BBB-marker" not in body_a
        assert "BBB-marker" in body_b and "AAA-marker" not in body_b
        assert body_a.replace("AAA-marker", "BBB-marker") == body_b


# ---------------------------------------------------------------------------
# enqueue_notice + staleness guard (notices only — member docs are exempt)
# ---------------------------------------------------------------------------


class TestEnqueueNoticeAndStaleness:
    def test_enqueue_notice_writes_a_distinct_queue_file(self, tmp_path: Path):
        from camp.launch.inject import build_notice_body, enqueue_notice, queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()
        body = build_notice_body(
            member="myrepo", phase="activate", task=None, consequence="Ready."
        )
        enqueue_notice(ws, body)

        files = list(queue_dir_for(ws).iterdir())
        assert len(files) == 1
        assert files[0].read_text() == body

    def test_fresh_notice_is_delivered(self, tmp_path: Path):
        from camp.launch.inject import build_notice_body, enqueue_notice

        ws = tmp_path / "ws"
        ws.mkdir()
        body = build_notice_body(
            member="myrepo", phase="activate", task=None, consequence="FRESH-NOTICE-marker"
        )
        enqueue_notice(ws, body)

        stdout, code = _drain(ws)
        assert code == 0
        assert "FRESH-NOTICE-marker" in stdout

    def test_stale_notice_is_dropped_not_delivered(self, tmp_path: Path):
        """A notice whose file mtime is older than the staleness threshold is
        dropped from the drain output. Backdating the ONE file's mtime (not
        the process clock) is the safe way to simulate age."""
        import os
        from camp.launch.inject import (
            NOTICE_MAX_AGE_SECONDS,
            build_notice_body,
            enqueue_notice,
        )

        ws = tmp_path / "ws"
        ws.mkdir()
        body = build_notice_body(
            member="myrepo", phase="activate", task=None, consequence="STALE-NOTICE-marker"
        )
        qfile = enqueue_notice(ws, body)

        stale_time = qfile.stat().st_mtime - (NOTICE_MAX_AGE_SECONDS + 60)
        os.utime(qfile, (stale_time, stale_time))

        stdout, code = _drain(ws)
        assert code == 0
        assert "STALE-NOTICE-marker" not in stdout

    def test_stale_notice_is_still_cleared_from_queue(self, tmp_path: Path):
        """A stale notice is dropped as undelivered news, not left to linger."""
        import os
        from camp.launch.inject import (
            NOTICE_MAX_AGE_SECONDS,
            build_notice_body,
            enqueue_notice,
            queue_dir_for,
        )

        ws = tmp_path / "ws"
        ws.mkdir()
        body = build_notice_body(
            member="myrepo", phase="activate", task=None, consequence="STALE-CLEAR-marker"
        )
        qfile = enqueue_notice(ws, body)
        stale_time = qfile.stat().st_mtime - (NOTICE_MAX_AGE_SECONDS + 60)
        os.utime(qfile, (stale_time, stale_time))

        _drain(ws)

        assert list(queue_dir_for(ws).iterdir()) == []

    def test_member_doc_never_age_filtered_however_old(self, tmp_path: Path):
        """The staleness guard applies to notices only — a member doc enqueued
        by camp activate (enqueue_doc) is delivered however old its mtime."""
        import os
        from camp.launch.inject import NOTICE_MAX_AGE_SECONDS, enqueue_doc

        ws = tmp_path / "ws"
        ws.mkdir()
        qfile = enqueue_doc(ws, "ANCIENT-MEMBER-DOC-marker")

        ancient_time = qfile.stat().st_mtime - (NOTICE_MAX_AGE_SECONDS * 100)
        os.utime(qfile, (ancient_time, ancient_time))

        stdout, code = _drain(ws)
        assert code == 0
        assert "ANCIENT-MEMBER-DOC-marker" in stdout

    def test_ordering_preserved_across_member_doc_and_notice(self, tmp_path: Path):
        from camp.launch.inject import build_notice_body, enqueue_doc, enqueue_notice

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "MEMBER-DOC-FIRST")
        body = build_notice_body(
            member="myrepo", phase="activate", task=None, consequence="NOTICE-SECOND"
        )
        enqueue_notice(ws, body)
        enqueue_doc(ws, "MEMBER-DOC-THIRD")

        stdout, _ = _drain(ws)
        parsed = json.loads(stdout)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert ctx.index("MEMBER-DOC-FIRST") < ctx.index("NOTICE-SECOND") < ctx.index(
            "MEMBER-DOC-THIRD"
        )
