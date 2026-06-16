"""Tests for inject.py — mid-session context-injection queue + drain (Slice 9).

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

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------


class TestEnqueue:
    def test_enqueue_writes_a_queue_file(self, tmp_path: Path):
        from inject import enqueue_doc, queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "# member doc\n")

        files = list(queue_dir_for(ws).iterdir())
        assert len(files) == 1
        assert files[0].read_text() == "# member doc\n"

    def test_multiple_enqueues_do_not_overwrite(self, tmp_path: Path):
        from inject import enqueue_doc, queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "doc-one")
        enqueue_doc(ws, "doc-two")

        files = list(queue_dir_for(ws).iterdir())
        assert len(files) == 2


# ---------------------------------------------------------------------------
# drain
# ---------------------------------------------------------------------------


def _drain(ws: Path) -> tuple[str, int]:
    """Run drain_queue capturing stdout; return (stdout, exit_code)."""
    from inject import drain_queue

    out = io.StringIO()
    import contextlib

    code = 0
    with contextlib.redirect_stdout(out):
        code = drain_queue(ws)
    return out.getvalue(), code


class TestDrain:
    def test_drain_emits_posttooluse_additional_context_json(self, tmp_path: Path):
        from inject import enqueue_doc

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
        from inject import enqueue_doc, queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "some doc")

        _drain(ws)

        # Queue is empty after a drain.
        qdir = queue_dir_for(ws)
        assert list(qdir.iterdir()) == []

    def test_drain_second_call_is_empty(self, tmp_path: Path):
        from inject import enqueue_doc

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "some doc")

        _drain(ws)
        stdout, code = _drain(ws)
        assert stdout == ""
        assert code == 0

    def test_drain_includes_all_queued_docs(self, tmp_path: Path):
        from inject import enqueue_doc

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "FIRST-DOC")
        enqueue_doc(ws, "SECOND-DOC")

        stdout, _ = _drain(ws)
        parsed = json.loads(stdout)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "FIRST-DOC" in ctx
        assert "SECOND-DOC" in ctx

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

import os
import subprocess

_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
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


class TestInjectCli:
    def test_cli_drain_empty_queue_no_output_exit_zero(self, tmp_path: Path):
        """camp inject --drain from a workspace with an empty queue → no output, exit 0."""
        ws = tmp_path / "ws"
        ws.mkdir()
        result = _run_cli(["inject", "--drain", "--workspace", str(ws)],
                          env={}, cwd=ws)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_cli_drain_with_doc_emits_json(self, tmp_path: Path):
        """camp inject --drain emits the PostToolUse JSON when the queue has a doc."""
        from inject import enqueue_doc

        ws = tmp_path / "ws"
        ws.mkdir()
        enqueue_doc(ws, "QUEUED-DOC-marker")

        result = _run_cli(["inject", "--drain", "--workspace", str(ws)],
                          env={}, cwd=ws)
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
        from inject import enqueue_doc, queue_dir_for

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
        from inject import queue_dir_for

        ws = tmp_path / "ws"
        ws.mkdir()

        qdir = queue_dir_for(ws)
        qdir.mkdir(parents=True, exist_ok=True)
        # A sub-directory in the queue dir; is_file() → False, filtered out.
        (qdir / "poison_dir.md").mkdir()

        result = _run_cli(["inject", "--drain", "--workspace", str(ws)],
                          env={}, cwd=ws)
        assert result.returncode == 0, (
            f"camp inject --drain should exit 0 even with only dirs in queue. "
            f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
        assert result.stdout == "", (
            f"camp inject --drain should produce no stdout with empty/dir-only queue. "
            f"Got: {result.stdout!r}"
        )
