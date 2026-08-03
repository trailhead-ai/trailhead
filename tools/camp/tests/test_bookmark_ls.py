"""Tests for camp.bookmark.render — the `camp bookmark ls` command.

Test contract:
- Listing across multiple groups/workspaces sorts most-recent-first (by
  updated-at) and renders all four columns: ref, group/workspace, age, note.
- Age is derived from updated_at, not wall-clock guesswork.
- A bookmark whose transcript file is gone renders "transcript gone"; one whose
  workspace dir is gone renders "workspace gone". Neither is auto-removed from
  the store.
- Zero bookmarks prints the "no bookmarks yet" hint, never an empty table.

Every test injects CAMP_STATE_DIR so the real ~/.local/state/camp is never
touched.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    return {"CAMP_STATE_DIR": str(tmp_path / "state")}


@pytest.fixture()
def group() -> dict:
    return {"group": {"name": "demo"}}


def _workspace_dir(env: dict[str, str], group: str, slug: str) -> Path:
    from camp.group.manifest import workspace_dir

    ws = workspace_dir(group, slug, env=env)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _record(
    ref: str,
    *,
    group: str = "demo",
    slug: str | None = None,
    updated_at: str = "2026-08-03T00:00:00Z",
    note: str = "",
    transcript: Path | None = None,
) -> dict:
    slug = slug or ref
    return {
        "ref": ref,
        "group": group,
        "slug": slug,
        "session_id": f"sess-{ref}",
        "transcript_path": str(transcript) if transcript else f"/nonexistent/{ref}.jsonl",
        "note": note,
        "created_at": updated_at,
        "updated_at": updated_at,
    }


def _seed(
    env: dict[str, str],
    record: dict,
    *,
    workspace: bool = True,
    transcript: bool = True,
    transcript_dir: Path | None = None,
) -> dict:
    from camp.bookmark.store import upsert

    ws = _workspace_dir(env, record["group"], record["slug"]) if workspace else None
    if transcript:
        t = Path(record["transcript_path"])
        if not t.is_absolute() or "/nonexistent/" in str(t):
            fallback_dir = ws or transcript_dir or Path(env["CAMP_STATE_DIR"])
            t = fallback_dir / f"{record['ref']}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text("{}\n")
        record = dict(record, transcript_path=str(t))
    return upsert(record, env=env)


# ---------------------------------------------------------------------------
# Ordering + columns
# ---------------------------------------------------------------------------


def test_ls_sorts_most_recently_updated_first(env: dict[str, str]) -> None:
    from camp.bookmark.render import render_bookmarks
    from camp.bookmark.store import list_bookmarks_by_recency

    _seed(env, _record("old", group="g1", slug="s1", updated_at="2026-01-01T00:00:00Z"))
    _seed(env, _record("new", group="g2", slug="s2", updated_at="2026-06-01T00:00:00Z"))
    _seed(env, _record("mid", group="g3", slug="s3", updated_at="2026-03-01T00:00:00Z"))

    bookmarks = list_bookmarks_by_recency(env=env)
    assert [b["ref"] for b in bookmarks] == ["new", "mid", "old"]

    out = render_bookmarks(bookmarks, env=env)
    lines = out.splitlines()
    assert lines[1].split()[0] == "new"
    assert lines[2].split()[0] == "mid"
    assert lines[3].split()[0] == "old"


def test_ls_renders_all_four_columns(env: dict[str, str]) -> None:
    from camp.bookmark.render import render_bookmarks

    record = _seed(env, _record("alpha", group="demo", slug="alpha", note="mid-refactor"))
    out = render_bookmarks([record], env=env)

    header, row = out.splitlines()[:2]
    assert "REF" in header
    assert "alpha" in row
    assert "demo/alpha" in row
    assert "mid-refactor" in row


def test_ls_age_derives_from_updated_at(env: dict[str, str]) -> None:
    from camp.bookmark.render import render_bookmarks

    now = dt.datetime(2026, 8, 3, 2, 0, 0, tzinfo=dt.timezone.utc)
    record = _seed(env, _record("alpha", updated_at="2026-08-03T00:00:00Z"))
    out = render_bookmarks([record], env=env, now=now)

    row = out.splitlines()[1]
    assert "2h" in row


# ---------------------------------------------------------------------------
# Staleness markers
# ---------------------------------------------------------------------------


def test_ls_marks_missing_transcript(env: dict[str, str]) -> None:
    from camp.bookmark.render import render_bookmarks

    record = _seed(env, _record("alpha"), transcript=False)
    out = render_bookmarks([record], env=env)

    row = out.splitlines()[1]
    assert "transcript gone" in row


def test_ls_marks_missing_workspace(env: dict[str, str]) -> None:
    from camp.bookmark.render import render_bookmarks

    record = _seed(env, _record("alpha"), workspace=False)
    out = render_bookmarks([record], env=env)

    row = out.splitlines()[1]
    assert "workspace gone" in row


def test_ls_missing_transcript_or_workspace_is_not_auto_removed(env: dict[str, str]) -> None:
    from camp.bookmark.render import render_bookmarks
    from camp.bookmark.store import get_by_ref

    record = _seed(env, _record("alpha"), transcript=False)
    render_bookmarks([record], env=env)
    assert get_by_ref("alpha", env=env) is not None


def test_ls_healthy_bookmark_has_no_marker(env: dict[str, str]) -> None:
    from camp.bookmark.render import render_bookmarks

    record = _seed(env, _record("alpha"))
    out = render_bookmarks([record], env=env)

    row = out.splitlines()[1]
    assert "gone" not in row


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_ls_empty_prints_hint_not_empty_table(env: dict[str, str]) -> None:
    from camp.bookmark.render import render_bookmarks

    out = render_bookmarks([], env=env)
    assert "no bookmarks yet" in out
    assert "REF" not in out


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cmd_bookmark_ls_prints_hint_when_empty(
    env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.render import cmd_bookmark_ls

    cmd_bookmark_ls([], group, env)
    out = capsys.readouterr().out
    assert "no bookmarks yet" in out


def test_cmd_bookmark_ls_prints_global_bookmarks(
    env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.render import cmd_bookmark_ls

    _seed(env, _record("alpha", group="other-group", slug="alpha"))
    cmd_bookmark_ls([], group, env)
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "other-group" in out


def test_cmd_bookmark_ls_rejects_unexpected_argument(
    env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.render import cmd_bookmark_ls

    with pytest.raises(SystemExit) as exc:
        cmd_bookmark_ls(["mystery"], group, env)
    assert exc.value.code != 0
    assert "mystery" in capsys.readouterr().err
