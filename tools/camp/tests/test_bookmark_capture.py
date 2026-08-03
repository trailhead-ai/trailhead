"""Tests for camp.bookmark.capture — the `camp bookmark` capture command.

Test contract:
- A capture from inside a workspace, with the session-id env var set and a
  transcript on disk, stores ref/group/slug/session-id/absolute transcript
  path/note/timestamps.
- Each precondition fails on its own terms: cwd not in a workspace, session-id env
  var absent, transcript unresolvable — each exits non-zero naming that precondition.
- The default ref is the workspace slug; the ref charset is enforced and the
  rejection names the offending character.
- A ref already held by a DIFFERENT workspace is refused and the existing record is
  left untouched; when the ref was defaulted the refusal hints --ref.
- Re-capturing the same workspace updates the record in place: new session id, note
  replaced, created_at preserved.

Everything is hermetic: CAMP_STATE_DIR and TRAILHEAD_CLAUDE_DIR are injected, so
neither the real camp state dir nor the real ~/.claude is read or written.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_GROUP = "demo"
_SLUG = "bookmark"


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    """Injected env: camp state dir + claude dir + a live session id."""
    return {
        "CAMP_STATE_DIR": str(tmp_path / "state"),
        "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude"),
        "CLAUDE_CODE_SESSION_ID": "sess-abc123",
    }


@pytest.fixture()
def group() -> dict:
    return {
        "group": {"name": _GROUP},
        "members": [{"name": "repo", "repo_root": "/nonexistent/repo"}],
    }


def _workspace(env: dict[str, str], slug: str = _SLUG) -> Path:
    ws = Path(env["CAMP_STATE_DIR"]) / _GROUP / "worktrees" / slug
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _seed_transcript(env: dict[str, str], workspace: Path, session_id: str) -> Path:
    munged = str(workspace.resolve()).replace("/", "-").replace(".", "-")
    d = Path(env["TRAILHEAD_CLAUDE_DIR"]) / "projects" / munged
    d.mkdir(parents=True, exist_ok=True)
    t = d / f"{session_id}.jsonl"
    t.write_text("{}\n")
    return t


@pytest.fixture()
def live_workspace(env: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    """A workspace with a transcript on disk, and cwd inside it."""
    ws = _workspace(env)
    transcript = _seed_transcript(env, ws, env["CLAUDE_CODE_SESSION_ID"])
    monkeypatch.chdir(ws)
    return ws, transcript


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_capture_stores_the_full_record(live_workspace, env: dict[str, str], group: dict) -> None:
    from camp.bookmark.capture import capture
    from camp.bookmark.store import get_by_ref

    _ws, transcript = live_workspace
    capture(group=group, ref=None, note="mid-refactor", env=env)

    record = get_by_ref(_SLUG, env=env)
    assert record is not None
    assert record["ref"] == _SLUG
    assert record["group"] == _GROUP
    assert record["slug"] == _SLUG
    assert record["session_id"] == "sess-abc123"
    assert record["transcript_path"] == str(transcript)
    assert Path(record["transcript_path"]).is_absolute()
    assert record["note"] == "mid-refactor"
    assert record["created_at"] and record["updated_at"]


def test_default_ref_is_the_workspace_slug(live_workspace, env: dict[str, str], group: dict) -> None:
    from camp.bookmark.capture import capture
    from camp.bookmark.store import list_bookmarks

    capture(group=group, ref=None, note=None, env=env)
    assert [b["ref"] for b in list_bookmarks(env=env)] == [_SLUG]


def test_explicit_ref_overrides_the_default(live_workspace, env: dict[str, str], group: dict) -> None:
    from camp.bookmark.capture import capture
    from camp.bookmark.store import get_by_ref

    capture(group=group, ref="tuesday", note=None, env=env)
    assert get_by_ref("tuesday", env=env) is not None


def test_missing_note_stores_empty_string(live_workspace, env: dict[str, str], group: dict) -> None:
    from camp.bookmark.capture import capture
    from camp.bookmark.store import get_by_ref

    capture(group=group, ref=None, note=None, env=env)
    assert get_by_ref(_SLUG, env=env)["note"] == ""


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


def test_cwd_outside_a_workspace_names_that_precondition(
    env: dict[str, str], group: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from camp.bookmark.capture import BookmarkError, capture

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    with pytest.raises(BookmarkError) as exc:
        capture(group=group, ref=None, note=None, env=env)
    assert "workspace" in str(exc.value)


def test_absent_session_id_env_var_names_that_precondition(
    live_workspace, env: dict[str, str], group: dict
) -> None:
    from camp.bookmark.capture import BookmarkError, capture

    env.pop("CLAUDE_CODE_SESSION_ID")
    with pytest.raises(BookmarkError) as exc:
        capture(group=group, ref=None, note=None, env=env)
    assert "CLAUDE_CODE_SESSION_ID" in str(exc.value)


def test_unresolvable_transcript_names_that_precondition(
    env: dict[str, str], group: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from camp.bookmark.capture import BookmarkError, capture

    monkeypatch.chdir(_workspace(env))  # workspace exists, transcript does not
    with pytest.raises(BookmarkError) as exc:
        capture(group=group, ref=None, note=None, env=env)
    assert "transcript" in str(exc.value)


def test_unresolvable_transcript_writes_nothing(
    env: dict[str, str], group: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from camp.bookmark.capture import BookmarkError, capture
    from camp.bookmark.store import list_bookmarks

    monkeypatch.chdir(_workspace(env))
    with pytest.raises(BookmarkError):
        capture(group=group, ref=None, note=None, env=env)
    assert list_bookmarks(env=env) == []


def test_harness_without_transcript_support_degrades_to_the_same_error(
    live_workspace, env: dict[str, str], group: dict
) -> None:
    """A harness that cannot resolve transcripts must not crash the capture."""
    from camp.bookmark.capture import BookmarkError, capture

    group["harness"] = {"binary": "some-other-harness"}
    with pytest.raises(BookmarkError) as exc:
        capture(group=group, ref=None, note=None, env=env)
    assert "transcript" in str(exc.value)


# ---------------------------------------------------------------------------
# Ref validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad,offender", [("Tuesday", "T"), ("has space", " "), ("a/b", "/")])
def test_invalid_ref_names_the_offending_character(
    live_workspace, env: dict[str, str], group: dict, bad: str, offender: str
) -> None:
    from camp.bookmark.capture import BookmarkError, capture

    with pytest.raises(BookmarkError) as exc:
        capture(group=group, ref=bad, note=None, env=env)
    assert repr(offender) in str(exc.value)


def test_ref_may_not_start_with_punctuation(live_workspace, env: dict[str, str], group: dict) -> None:
    from camp.bookmark.capture import BookmarkError, capture

    with pytest.raises(BookmarkError) as exc:
        capture(group=group, ref="-lead", note=None, env=env)
    assert "'-'" in str(exc.value)


def test_over_long_ref_is_rejected(live_workspace, env: dict[str, str], group: dict) -> None:
    from camp.bookmark.capture import BookmarkError, capture

    with pytest.raises(BookmarkError) as exc:
        capture(group=group, ref="a" * 65, note=None, env=env)
    assert "64" in str(exc.value)


def test_valid_ref_charset_is_accepted(live_workspace, env: dict[str, str], group: dict) -> None:
    from camp.bookmark.capture import capture
    from camp.bookmark.store import get_by_ref

    capture(group=group, ref="a0._-ok", note=None, env=env)
    assert get_by_ref("a0._-ok", env=env) is not None


# ---------------------------------------------------------------------------
# Collisions + re-capture
# ---------------------------------------------------------------------------


def test_default_ref_taken_by_another_group_hints_at_ref_flag(
    live_workspace, env: dict[str, str], group: dict
) -> None:
    from camp.bookmark.capture import BookmarkError, capture
    from camp.bookmark.store import upsert

    upsert(
        {
            "ref": _SLUG,
            "group": "other-group",
            "slug": _SLUG,
            "session_id": "sess-old",
            "transcript_path": "/old.jsonl",
            "note": "",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        env=env,
    )

    with pytest.raises(BookmarkError) as exc:
        capture(group=group, ref=None, note=None, env=env)
    assert "--ref" in str(exc.value)


def test_ref_held_by_another_workspace_leaves_the_record_untouched(
    live_workspace, env: dict[str, str], group: dict
) -> None:
    from camp.bookmark.capture import BookmarkError, capture
    from camp.bookmark.store import get_by_ref, upsert

    existing = {
        "ref": "taken",
        "group": "other-group",
        "slug": "other-slug",
        "session_id": "sess-old",
        "transcript_path": "/old.jsonl",
        "note": "keep me",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    upsert(existing, env=env)

    with pytest.raises(BookmarkError) as exc:
        capture(group=group, ref="taken", note="clobber", env=env)
    assert "other-group" in str(exc.value)
    assert get_by_ref("taken", env=env) == existing


def test_recapture_of_same_workspace_updates_in_place(
    live_workspace, env: dict[str, str], group: dict
) -> None:
    from camp.bookmark.capture import capture
    from camp.bookmark.store import get_by_ref, list_bookmarks

    ws, _ = live_workspace
    capture(group=group, ref=None, note="first", env=env)
    first = get_by_ref(_SLUG, env=env)

    env["CLAUDE_CODE_SESSION_ID"] = "sess-second"
    second_transcript = _seed_transcript(env, ws, "sess-second")
    capture(group=group, ref=None, note="second", env=env)

    updated = get_by_ref(_SLUG, env=env)
    assert len(list_bookmarks(env=env)) == 1
    assert updated["session_id"] == "sess-second"
    assert updated["transcript_path"] == str(second_transcript)
    assert updated["note"] == "second"  # silently replaced
    assert updated["created_at"] == first["created_at"]  # first capture wins


def test_recapture_without_a_note_clears_the_previous_note(
    live_workspace, env: dict[str, str], group: dict
) -> None:
    from camp.bookmark.capture import capture
    from camp.bookmark.store import get_by_ref

    capture(group=group, ref=None, note="first", env=env)
    capture(group=group, ref=None, note=None, env=env)
    assert get_by_ref(_SLUG, env=env)["note"] == ""


def test_recapture_under_a_new_ref_moves_the_workspace_bookmark(
    live_workspace, env: dict[str, str], group: dict
) -> None:
    """One workspace holds at most one bookmark — a new ref renames, never forks."""
    from camp.bookmark.capture import capture
    from camp.bookmark.store import get_by_ref, list_bookmarks

    capture(group=group, ref=None, note=None, env=env)
    capture(group=group, ref="renamed", note=None, env=env)

    assert [b["ref"] for b in list_bookmarks(env=env)] == ["renamed"]
    assert get_by_ref(_SLUG, env=env) is None
    assert get_by_ref("renamed", env=env)["slug"] == _SLUG


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_exits_zero_and_confirms(
    live_workspace, env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.capture import cmd_bookmark

    cmd_bookmark(["--note", "hello"], group, env)
    out = capsys.readouterr().out
    assert _SLUG in out


def test_cli_precondition_failure_exits_nonzero_without_traceback(
    env: dict[str, str], group: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    from camp.bookmark.capture import cmd_bookmark

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    with pytest.raises(SystemExit) as exc:
        cmd_bookmark([], group, env)
    assert exc.value.code != 0
    assert "camp bookmark:" in capsys.readouterr().err


def test_cli_corrupt_store_exits_nonzero_naming_the_file(
    live_workspace, env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.capture import cmd_bookmark
    from camp.bookmark.store import store_path

    path = store_path(env=env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{oh no")

    with pytest.raises(SystemExit) as exc:
        cmd_bookmark([], group, env)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert str(path) in err
    assert "Traceback" not in err


def test_cli_rejects_unknown_positional_argument(
    live_workspace, env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.capture import cmd_bookmark

    with pytest.raises(SystemExit) as exc:
        cmd_bookmark(["mystery"], group, env)
    assert exc.value.code != 0
    assert "mystery" in capsys.readouterr().err


def test_cli_ref_flag_requires_a_value(
    live_workspace, env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.capture import cmd_bookmark

    with pytest.raises(SystemExit) as exc:
        cmd_bookmark(["--ref"], group, env)
    assert exc.value.code != 0
    assert "--ref" in capsys.readouterr().err
