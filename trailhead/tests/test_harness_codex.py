"""Tests for trailhead/harness/codex.py — registry entry, home resolution, detection.

Every test injects its own env (``CODEX_HOME``/``HOME``/``PATH``) rather than
relying on the ambient process environment, per this suite's isolation
contract (see ``conftest.py``). ``_forbid_real_home`` poisons
``Path.home()`` for this whole suite, so any resolver reached here that fell
through to it would fail loudly rather than silently reading a real home.
"""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trailhead.harness import HarnessError, get_harness, known_harness_names
from trailhead.harness.codex import CodexHarness, _is_session_id, codex_home


def _make_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class TestFactoryRegistration:
    def test_known_harness_names_includes_codex(self):
        assert "codex" in known_harness_names()

    def test_get_harness_returns_codex_instance(self):
        h = get_harness("codex")
        assert isinstance(h, CodexHarness)
        assert h.name == "codex"


class TestCodexHome:
    def test_prefers_codex_home_over_home(self, tmp_path):
        codex_dir = tmp_path / "explicit-codex-home"
        home_dir = tmp_path / "home"
        env = {"CODEX_HOME": str(codex_dir), "HOME": str(home_dir)}
        assert codex_home(env) == codex_dir

    def test_falls_back_to_home_dot_codex(self, tmp_path):
        home_dir = tmp_path / "home"
        env = {"HOME": str(home_dir)}
        assert codex_home(env) == home_dir / ".codex"

    def test_falls_back_to_userprofile_dot_codex(self, tmp_path):
        home_dir = tmp_path / "userprofile-home"
        env = {"USERPROFILE": str(home_dir)}
        assert codex_home(env) == home_dir / ".codex"

    def test_raises_on_relative_codex_home(self, tmp_path):
        env = {"CODEX_HOME": "relative/codex", "HOME": str(tmp_path / "home")}
        with pytest.raises(HarnessError):
            codex_home(env)

    def test_raises_when_neither_codex_home_nor_home_set(self):
        with pytest.raises(HarnessError):
            codex_home({})


class TestDetect:
    def test_true_with_codex_executable_on_path_and_no_home(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_executable(bin_dir / "codex")
        env = {"PATH": str(bin_dir)}
        assert CodexHarness.detect(env) is True

    def test_true_with_config_toml_under_codex_home_and_empty_path(self, tmp_path):
        codex_dir = tmp_path / "codex-home"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("")
        env = {"CODEX_HOME": str(codex_dir), "PATH": ""}
        assert CodexHarness.detect(env) is True

    def test_false_with_bare_home_directory_and_empty_path(self, tmp_path):
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        env = {"HOME": str(home_dir), "PATH": ""}
        assert CodexHarness.detect(env) is False

    def test_false_when_path_entry_has_non_executable_file_named_codex(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "codex").write_text("not executable")
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        env = {"PATH": str(bin_dir), "HOME": str(home_dir)}
        assert CodexHarness.detect(env) is False


class TestVacuousInstallSurface:
    """The registration/install-state methods answer negatively regardless of
    on-disk state a Claude-shaped fixture would report positively for — the
    vacuous contract observed through the seam, not an absent method."""

    def test_reports_nothing_installed_against_a_claude_shaped_composed_root(self, tmp_path):
        composed_root = tmp_path / "composed" / "codex"
        composed_root.mkdir(parents=True)
        claude_dir = tmp_path / "claude-shaped-config-dir"
        claude_dir.mkdir()
        (claude_dir / ".trailhead-registered").write_text("{}")
        (claude_dir / ".trailhead-installed-lore").write_text("{}")
        (claude_dir / ".trailhead-installed-camp").write_text("{}")
        env = {"TRAILHEAD_CLAUDE_DIR": str(claude_dir), "HOME": str(tmp_path / "home")}

        harness = CodexHarness()

        assert harness.is_registered(composed_root, env=env) is False
        assert harness.is_installed("lore", composed_root, env=env) is False
        assert harness.installed_tools(composed_root, env=env) == []


def _env(tmp_path: Path) -> dict[str, str]:
    """Every test in this file pins both ``CODEX_HOME`` and ``HOME`` under
    ``tmp_path`` — never the ambient environment, per this suite's isolation
    contract."""
    return {
        "CODEX_HOME": str(tmp_path / "codex-home"),
        "HOME": str(tmp_path / "home"),
    }


def _sessions_dir(env: dict[str, str]) -> Path:
    return Path(env["CODEX_HOME"]) / "sessions"


def _meta_line(cwd: str, **extra_payload_fields) -> str:
    """A ``session_meta``-tagged envelope, in the shape U1 observed."""
    payload = {
        "session_id": "thread-fixture",
        "id": "thread-fixture",
        "timestamp": "2024-01-01T00:00:00Z",
        "cwd": cwd,
        "source": "cli",
    }
    payload.update(extra_payload_fields)
    return json.dumps(
        {"timestamp": "2024-01-01T00:00:00Z", "ordinal": 0, "type": "session_meta", "payload": payload}
    )


def _write_rollout(
    env: dict[str, str], filename: str, lines: list[str], *, date=("2024", "01", "01")
) -> Path:
    d = _sessions_dir(env).joinpath(*date)
    d.mkdir(parents=True, exist_ok=True)
    path = d / filename
    path.write_text("\n".join(lines) + "\n")
    return path


class TestCodexSessionTranscripts:
    """Codex stores one rollout per session at
    <codex-home>/sessions/YYYY/MM/DD/rollout-<timestamp>-<thread-id>[_<rollout-id>].jsonl
    (optionally ``.jsonl.zst``). ``session_transcripts`` walks that layout."""

    def test_missing_store_returns_empty_list(self, tmp_path):
        env = _env(tmp_path)
        assert CodexHarness().session_transcripts(env=env) == []

    def test_empty_store_returns_empty_list(self, tmp_path):
        env = _env(tmp_path)
        _sessions_dir(env).mkdir(parents=True)
        assert CodexHarness().session_transcripts(env=env) == []

    def test_two_rollouts_yield_two_rows_with_ids_from_filenames(self, tmp_path):
        env = _env(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        id_a = "01a0c9d2-1038-7b11-ad91-9c36433a8ce7"
        id_b = "02b1d0e3-2049-8c22-be02-0d47544b9df8"
        _write_rollout(env, f"rollout-2024-01-01T12-00-00-{id_a}.jsonl", [_meta_line(str(ws))])
        _write_rollout(
            env, f"rollout-2024-01-01T13-00-00-{id_b}_rolloutsuffix.jsonl", [_meta_line(str(ws))]
        )
        rows = CodexHarness().session_transcripts(env=env)
        assert {r.session_id for r in rows} == {id_a, id_b}

    def test_cwd_is_read_from_metadata_payload(self, tmp_path):
        env = _env(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        id_a = "01a0c9d2-1038-7b11-ad91-9c36433a8ce7"
        _write_rollout(env, f"rollout-2024-01-01T12-00-00-{id_a}.jsonl", [_meta_line(str(ws))])
        rows = CodexHarness().session_transcripts(env=env)
        assert rows[0].cwd == ws.resolve()

    def test_undecodable_first_line_yields_cwd_none(self, tmp_path):
        env = _env(tmp_path)
        id_a = "01a0c9d2-1038-7b11-ad91-9c36433a8ce7"
        _write_rollout(env, f"rollout-2024-01-01T12-00-00-{id_a}.jsonl", ["not json at all"])
        rows = CodexHarness().session_transcripts(env=env)
        assert len(rows) == 1
        assert rows[0].cwd is None

    def test_compressed_rollout_yields_cwd_none(self, tmp_path):
        """The ``.zst`` file below holds a perfectly PARSEABLE session_meta
        line, plain-text, not actually compressed — proving cwd=None comes
        from the extension guard (this seam never decompresses) rather than
        from an incidental decode failure a real zstd frame would also
        produce."""
        env = _env(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        id_a = "01a0c9d2-1038-7b11-ad91-9c36433a8ce7"
        d = _sessions_dir(env) / "2024" / "01" / "01"
        d.mkdir(parents=True)
        path = d / f"rollout-2024-01-01T12-00-00-{id_a}.jsonl.zst"
        path.write_text(_meta_line(str(ws)) + "\n")
        rows = CodexHarness().session_transcripts(env=env)
        assert len(rows) == 1
        assert rows[0].session_id == id_a
        assert rows[0].cwd is None

    def test_non_metadata_first_line_yields_cwd_none(self, tmp_path):
        env = _env(tmp_path)
        id_a = "01a0c9d2-1038-7b11-ad91-9c36433a8ce7"
        line = json.dumps(
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "ordinal": 0,
                "type": "response_item",
                "payload": {"cwd": "/should/not/be/read"},
            }
        )
        _write_rollout(env, f"rollout-2024-01-01T12-00-00-{id_a}.jsonl", [line])
        rows = CodexHarness().session_transcripts(env=env)
        assert len(rows) == 1
        assert rows[0].cwd is None

    def test_first_line_past_byte_cap_still_yields_row_and_never_reads_second_line(self, tmp_path):
        from trailhead.harness.codex import _SESSION_META_MAX_LINE_BYTES

        env = _env(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        sentinel = tmp_path / "sentinel-second-line-cwd"
        id_a = "01a0c9d2-1038-7b11-ad91-9c36433a8ce7"
        d = _sessions_dir(env) / "2024" / "01" / "01"
        d.mkdir(parents=True)
        path = d / f"rollout-2024-01-01T12-00-00-{id_a}.jsonl"

        # A COMPLETE, well-formed session_meta line padded past the byte cap —
        # if the read were unbounded this would decode fine and yield ws's
        # cwd, so cwd=None below can only come from the cap actually biting.
        oversized_first_line = _meta_line(str(ws), padding="x" * (_SESSION_META_MAX_LINE_BYTES + 100))
        second_line = _meta_line(str(sentinel))
        path.write_text(oversized_first_line + "\n" + second_line + "\n")

        rows = CodexHarness().session_transcripts(env=env)
        assert len(rows) == 1
        assert rows[0].cwd is None

    def test_dotdot_id_yields_no_row(self, tmp_path):
        env = _env(tmp_path)
        d = _sessions_dir(env) / "2024" / "01" / "01"
        d.mkdir(parents=True)
        (d / "rollout-2024-01-01T12-00-00-...jsonl").write_text(
            _meta_line(str(tmp_path)) + "\n"
        )
        rows = CodexHarness().session_transcripts(env=env)
        assert rows == []

    def test_separator_bearing_id_yields_no_row(self, tmp_path):
        env = _env(tmp_path)
        d = _sessions_dir(env) / "2024" / "01" / "01"
        d.mkdir(parents=True)
        (d / "rollout-2024-01-01T12-00-00-back\\slash.jsonl").write_text(
            _meta_line(str(tmp_path)) + "\n"
        )
        rows = CodexHarness().session_transcripts(env=env)
        assert rows == []

    def test_modified_at_is_tz_aware_utc_matching_file_mtime(self, tmp_path):
        env = _env(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        id_a = "01a0c9d2-1038-7b11-ad91-9c36433a8ce7"
        path = _write_rollout(
            env, f"rollout-2024-01-01T12-00-00-{id_a}.jsonl", [_meta_line(str(ws))]
        )
        rows = CodexHarness().session_transcripts(env=env)
        assert rows[0].modified_at.tzinfo is not None
        assert rows[0].modified_at == datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

    def test_workspace_scoping_keeps_under_drops_sibling_and_drops_cwd_none(self, tmp_path):
        env = _env(tmp_path)
        ws = tmp_path / "workspace"
        ws.mkdir()
        nested = ws / "nested"
        nested.mkdir()
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        id_under = "01a0c9d2-1038-7b11-ad91-9c36433a8ce7"
        id_sibling = "02b1d0e3-2049-8c22-be02-0d47544b9df8"
        id_unrooted = "03c2e1f4-3050-9d33-cf13-1e58655c0e09"
        _write_rollout(env, f"rollout-2024-01-01T10-00-00-{id_under}.jsonl", [_meta_line(str(nested))])
        _write_rollout(env, f"rollout-2024-01-01T11-00-00-{id_sibling}.jsonl", [_meta_line(str(sibling))])
        _write_rollout(env, f"rollout-2024-01-01T12-00-00-{id_unrooted}.jsonl", ["not json"])

        rows = CodexHarness().session_transcripts(ws, env=env)

        assert {r.session_id for r in rows} == {id_under}


class TestCodexSessionTranscriptPath:
    def test_resolves_existing_rollout(self, tmp_path):
        env = _env(tmp_path)
        id_a = "01a0c9d2-1038-7b11-ad91-9c36433a8ce7"
        path = _write_rollout(
            env, f"rollout-2024-01-01T12-00-00-{id_a}.jsonl", [_meta_line(str(tmp_path))]
        )
        resolved = CodexHarness().session_transcript_path(id_a, tmp_path, env=env)
        assert resolved == path

    def test_none_for_unknown_id(self, tmp_path):
        """A DIFFERENT, real rollout is present — proving the lookup matches
        on id, not merely on "something exists in the store"."""
        env = _env(tmp_path)
        other_id = "01a0c9d2-1038-7b11-ad91-9c36433a8ce7"
        _write_rollout(
            env, f"rollout-2024-01-01T12-00-00-{other_id}.jsonl", [_meta_line(str(tmp_path))]
        )
        assert (
            CodexHarness().session_transcript_path("no-such-thread-id", tmp_path, env=env)
            is None
        )

    def test_none_for_dotdot_empty_and_separator_ids(self, tmp_path):
        env = _env(tmp_path)
        harness = CodexHarness()
        assert harness.session_transcript_path("..", tmp_path, env=env) is None
        assert harness.session_transcript_path("", tmp_path, env=env) is None
        assert harness.session_transcript_path("a/b", tmp_path, env=env) is None

    def test_none_for_dotdot_id_even_when_a_matching_rollout_exists(self, tmp_path):
        """Populates the store with a rollout whose FILENAME parses to the
        literal id ``..`` — proving the guard rejects the id itself, not
        merely that nothing on disk happened to match it."""
        env = _env(tmp_path)
        d = _sessions_dir(env) / "2024" / "01" / "01"
        d.mkdir(parents=True)
        (d / "rollout-2024-01-01T12-00-00-...jsonl").write_text(
            _meta_line(str(tmp_path)) + "\n"
        )
        assert CodexHarness().session_transcript_path("..", tmp_path, env=env) is None


class TestCodexSessionIdGuard:
    """The guard next exported for the live-session lister/parser task to
    import — direct behavioral coverage independent of any on-disk rollout."""

    @pytest.mark.parametrize("session_id", ["..", "", "a/b", "a\\b", ".hidden"])
    def test_rejects_traversal_separator_and_leading_dot_ids(self, session_id):
        assert _is_session_id(session_id) is False

    def test_accepts_uuid_shaped_thread_id(self):
        assert _is_session_id("01a0c9d2-1038-7b11-ad91-9c36433a8ce7") is True
