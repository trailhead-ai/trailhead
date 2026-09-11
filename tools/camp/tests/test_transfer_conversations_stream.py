"""Tests for the sender side of the conversations transfer channel —
`camp.transfer.conversations.send_conversation` /
`send_workspace_conversations`.

Test contract (all must RED before implementation, GREEN after):

- Every conversation `workspace_conversations` reports ROOTED HERE is
  streamed, addressed on the wire by the subpath the enumeration assigned it.
- A conversation the enumeration reports EXCLUDED (no row at all) is never
  streamed — the selection varies with the enumeration's own answer rather
  than being re-derived here.
- A conversation the enumeration reports UNRESOLVED is refused by name
  (`UnresolvedConversation`), not silently dropped.
- A conversation that owns a subagent subtree streams that subtree too, each
  nested file addressed by its path relative to the conversation's own
  directory — never an absolute path. A conversation with no subtree streams
  just its own transcript.
- A transcript whose content changes between the start and the end of the
  stream aborts with `TranscriptChanged`, distinguishable from a transport
  failure (`ProducerFailed`). Detection is a full-content digest, so a
  same-size/different-content mutation is caught.
- A workspace with no conversations rooted in it streams nothing and raises
  nothing.
- A producer failure surfaces as `ProducerFailed`, never as a successful
  transfer of a truncated transcript.
- The sender-side producer is reachable as a real standalone-script
  subprocess (`python3 <this module> <transcript> [nested-dir]`), not only
  by importing `write_conversation_archive` directly.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

import _bootstrap  # noqa: E402

_bootstrap.ensure_trailhead_importable()

_NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)

_UUID_ROOT = "aaaaaaaa-1111-4111-8111-111111111111"
_UUID_MEMBER = "bbbbbbbb-2222-4222-8222-222222222222"
_UUID_OUTSIDE = "cccccccc-3333-4333-8333-333333333333"
_UUID_UNRESOLVED = "dddddddd-4444-4444-8444-444444444444"


def _host():
    from camp.host.config import Host

    return Host(ssh="fake-peer", camp_bin="/opt/camp/bin/camp")


def _transcript(session_id: str, cwd, *, age_seconds: float = 60.0):
    from datetime import timedelta

    from trailhead.harness.base import SessionTranscript

    return SessionTranscript(
        session_id=session_id, cwd=cwd, modified_at=_NOW - timedelta(seconds=age_seconds)
    )


def _rows(workspace: Path, *, transcripts):
    from camp.transfer.conversations import workspace_conversations

    return workspace_conversations(
        workspace,
        transcripts=transcripts,
        live_records=[],
        groups=[{"group": {"name": "g"}}],
        env={"CAMP_STATE_DIR": str(workspace)},
        now=_NOW,
    )


def _make_transcript_file(claude_dir: Path, munged: str, session_id: str, cwd: Path) -> Path:
    project_dir = claude_dir / "projects" / munged
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    path.write_text('{"cwd": %r}\n' % str(cwd))
    return path


def _munge(path: Path) -> str:
    return str(path.resolve()).replace("/", "-").replace(".", "-")


def _consuming_stream_spawner(argv, env):
    """A `StreamSpawner` standing in for the peer: reads the whole producer
    stream to completion and exits 0, without any real ssh or receiving
    phase — that half is a separate task's build."""
    return subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'ok')"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _failing_stream_spawner(argv, env):
    return subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(); sys.exit(1)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class TestSelectionVariesWithEnumeration:
    def test_rooted_conversation_streamed_with_assigned_subpath(self, tmp_path: Path) -> None:
        from camp.transfer.conversations import send_workspace_conversations

        ws = tmp_path / "ws"
        ws.mkdir()
        claude_dir = tmp_path / "home" / ".claude"
        munged = _munge(ws)
        transcript_path = _make_transcript_file(claude_dir, munged, _UUID_ROOT, ws)

        rows = _rows(ws, transcripts=[_transcript(_UUID_ROOT, ws)])
        assert len(rows) == 1

        seen_subpaths = []

        def _locate(session_id, root):
            seen_subpaths.append(root)
            return transcript_path

        results = send_workspace_conversations(
            _host(),
            group="g",
            slug="s",
            workspace=ws,
            conversations=rows,
            locate_transcript=_locate,
            spawn=_consuming_stream_spawner,
        )

        assert [r[0] for r in results] == [_UUID_ROOT]
        assert seen_subpaths == [ws.resolve()]

    def test_excluded_conversation_never_streamed(self, tmp_path: Path) -> None:
        from camp.transfer.conversations import send_workspace_conversations

        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        claude_dir = tmp_path / "home" / ".claude"
        transcript_path = _make_transcript_file(claude_dir, _munge(ws), _UUID_ROOT, ws)

        rows = _rows(
            ws,
            transcripts=[
                _transcript(_UUID_ROOT, ws),
                _transcript(_UUID_OUTSIDE, outside),
            ],
        )
        # The outside session produced no row at all.
        assert [r.session_id for r in rows] == [_UUID_ROOT]

        called_with = []

        def _locate(session_id, root):
            called_with.append(session_id)
            return transcript_path

        send_workspace_conversations(
            _host(),
            group="g",
            slug="s",
            workspace=ws,
            conversations=rows,
            locate_transcript=_locate,
            spawn=_consuming_stream_spawner,
        )

        assert called_with == [_UUID_ROOT]
        assert _UUID_OUTSIDE not in called_with

    def test_unresolved_conversation_refused_by_name(self, tmp_path: Path) -> None:
        from camp.transfer.conversations import UnresolvedConversation, send_workspace_conversations

        ws = tmp_path / "ws"
        ws.mkdir()

        rows = _rows(ws, transcripts=[_transcript(_UUID_UNRESOLVED, None)])
        assert rows[0].unresolved is True

        def _locate(session_id, root):
            raise AssertionError("must not be called for an unresolved row")

        with pytest.raises(UnresolvedConversation) as exc_info:
            send_workspace_conversations(
                _host(),
                group="g",
                slug="s",
                workspace=ws,
                conversations=rows,
                locate_transcript=_locate,
                spawn=_consuming_stream_spawner,
            )

        assert _UUID_UNRESOLVED in str(exc_info.value)

    def test_empty_workspace_streams_nothing_and_raises_nothing(self, tmp_path: Path) -> None:
        from camp.transfer.conversations import send_workspace_conversations

        ws = tmp_path / "ws"
        ws.mkdir()
        rows = _rows(ws, transcripts=[])
        assert rows == ()

        def _locate(session_id, root):
            raise AssertionError("must not be called — nothing to locate")

        results = send_workspace_conversations(
            _host(),
            group="g",
            slug="s",
            workspace=ws,
            conversations=rows,
            locate_transcript=_locate,
            spawn=_consuming_stream_spawner,
        )

        assert results == ()


class TestNestedSubtree:
    def test_conversation_with_subagent_subtree_streams_it_relative_to_own_directory(
        self, tmp_path: Path
    ) -> None:
        from camp.transfer.conversations import build_conversation_archive_argv

        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text('{"cwd": "/ws"}\n')
        nested_dir = tmp_path / _UUID_ROOT
        (nested_dir / "subagents").mkdir(parents=True)
        nested_file = nested_dir / "subagents" / "agent-deadbeef.jsonl"
        nested_file.write_text('{"cwd": "/ws"}\n')

        argv = build_conversation_archive_argv(transcript_path, nested_dir)
        proc = subprocess.run(argv, capture_output=True, check=True)

        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r|") as tf:
            members = {m.name: m for m in tf}

        assert set(members) == {"transcript.jsonl", "subagents/agent-deadbeef.jsonl"}
        for name in members:
            assert not Path(name).is_absolute()

    def test_conversation_with_no_subtree_streams_just_its_own_transcript(
        self, tmp_path: Path
    ) -> None:
        from camp.transfer.conversations import build_conversation_archive_argv

        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text('{"cwd": "/ws"}\n')

        argv = build_conversation_archive_argv(transcript_path, None)
        proc = subprocess.run(argv, capture_output=True, check=True)

        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r|") as tf:
            members = {m.name for m in tf}

        assert members == {"transcript.jsonl"}

    def test_non_jsonl_tool_result_artifact_streams_relative_to_own_directory(
        self, tmp_path: Path
    ) -> None:
        from camp.transfer.conversations import build_conversation_archive_argv

        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text('{"cwd": "/ws"}\n')
        nested_dir = tmp_path / _UUID_ROOT
        tool_results = nested_dir / "tool-results"
        tool_results.mkdir(parents=True)
        (tool_results / "result-1.txt").write_text("plain text tool output\n")
        (tool_results / "result-2.json").write_text('{"ok": true}\n')

        argv = build_conversation_archive_argv(transcript_path, nested_dir)
        proc = subprocess.run(argv, capture_output=True, check=True)

        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r|") as tf:
            members = {m.name: tf.extractfile(m).read() for m in tf}

        assert set(members) == {
            "transcript.jsonl",
            "tool-results/result-1.txt",
            "tool-results/result-2.json",
        }
        assert not any(name.endswith(".jsonl") for name in members if name != "transcript.jsonl")
        assert members["tool-results/result-1.txt"] == b"plain text tool output\n"
        assert members["tool-results/result-2.json"] == b'{"ok": true}\n'

    def test_sibling_memory_directory_is_never_streamed(self, tmp_path: Path) -> None:
        from camp.transfer.conversations import build_conversation_archive_argv

        projects_key_dir = tmp_path / "projects" / "somekey"
        transcript_path = projects_key_dir / f"{_UUID_ROOT}.jsonl"
        transcript_path.parent.mkdir(parents=True)
        transcript_path.write_text('{"cwd": "/ws"}\n')

        nested_dir = projects_key_dir / _UUID_ROOT
        (nested_dir / "subagents").mkdir(parents=True)
        (nested_dir / "subagents" / "agent-1.jsonl").write_text('{"cwd": "/ws"}\n')

        memory_dir = projects_key_dir / "memory"
        memory_dir.mkdir()
        (memory_dir / "notes.md").write_text("project-scoped agent memory\n")

        argv = build_conversation_archive_argv(transcript_path, nested_dir)
        proc = subprocess.run(argv, capture_output=True, check=True)

        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r|") as tf:
            members = {m.name for m in tf}

        assert "memory/notes.md" not in members
        assert not any("memory" in name for name in members)
        assert members == {"transcript.jsonl", "subagents/agent-1.jsonl"}


class TestTornCopyDetection:
    def test_same_size_content_change_during_stream_raises_named_error(
        self, tmp_path: Path
    ) -> None:
        from camp.transfer.conversations import TranscriptChanged, send_conversation

        transcript_path = tmp_path / "transcript.jsonl"
        original = b'{"cwd": "/ws", "n": 1}\n'
        transcript_path.write_bytes(original)
        mutated = b'{"cwd": "/ws", "n": 2}\n'
        assert len(mutated) == len(original)
        assert mutated != original

        def _mutating_producer_spawn(argv):
            transcript_path.write_bytes(mutated)
            return subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'data')"],
                stdout=subprocess.PIPE,
            )

        with pytest.raises(TranscriptChanged) as exc_info:
            send_conversation(
                _host(),
                group="g",
                slug="s",
                session_id=_UUID_ROOT,
                subpath=PurePosixPath("."),
                transcript_path=transcript_path,
                spawn=_consuming_stream_spawner,
                producer_spawn=_mutating_producer_spawn,
            )

        assert _UUID_ROOT in str(exc_info.value)

    def test_producer_failure_surfaces_as_producer_failure_even_if_content_also_changed(
        self, tmp_path: Path
    ) -> None:
        from camp.host.transport import ProducerFailed
        from camp.transfer.conversations import TranscriptChanged, send_conversation

        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_bytes(b'{"cwd": "/ws", "n": 1}\n')

        def _mutating_failing_producer_spawn(argv):
            transcript_path.write_bytes(b'{"cwd": "/ws", "n": 2}\n')
            return subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.exit(1)"],
                stdout=subprocess.PIPE,
            )

        outcome = send_conversation(
            _host(),
            group="g",
            slug="s",
            session_id=_UUID_ROOT,
            subpath=PurePosixPath("."),
            transcript_path=transcript_path,
            spawn=_consuming_stream_spawner,
            producer_spawn=_mutating_failing_producer_spawn,
        )

        assert isinstance(outcome, ProducerFailed)


class TestProducerFailurePropagates:
    def test_producer_failure_is_not_reported_as_success(self, tmp_path: Path) -> None:
        from camp.host.transport import ProducerFailed
        from camp.transfer.conversations import send_conversation

        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_bytes(b'{"cwd": "/ws"}\n')

        def _boom_producer_spawn(argv):
            return subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.exit(3)"],
                stdout=subprocess.PIPE,
            )

        outcome = send_conversation(
            _host(),
            group="g",
            slug="s",
            session_id=_UUID_ROOT,
            subpath=PurePosixPath("."),
            transcript_path=transcript_path,
            spawn=_consuming_stream_spawner,
            producer_spawn=_boom_producer_spawn,
        )

        assert isinstance(outcome, ProducerFailed)
        assert outcome.exit_code == 3
