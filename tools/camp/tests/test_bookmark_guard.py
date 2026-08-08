"""Tests for camp.bookmark.guard — the `camp rm` bookmark delete guard.

Test contract:
- `camp rm` on a workspace that holds a bookmark exits nonzero, names every
  blocking bookmark (ref, note, age), and does so BEFORE any teardown runs.
- `--force` tears down first and removes the workspace's bookmark entries only
  AFTER teardown succeeded; a failed teardown leaves the entries intact, so the
  bookmark keeps rendering (as `workspace gone`) in `camp bookmark ls`.
- A workspace whose directory is already gone is never blocked — a re-attempted
  removal must not be wedged by the bookmark its first attempt left behind.

Every test injects CAMP_STATE_DIR so the real ~/.local/state/camp is never
touched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

GROUP_NAME = "demo"
SLUG = "ws-slug"


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    return {"CAMP_STATE_DIR": str(tmp_path / "state")}


@pytest.fixture()
def group(tmp_path: Path) -> dict:
    return {
        "group": {"name": GROUP_NAME},
        "members": [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a")}],
    }


def _workspace_dir(env: dict[str, str], slug: str = SLUG) -> Path:
    from camp.group.manifest import workspace_dir

    return workspace_dir(GROUP_NAME, slug, env=env)


def _seed_bookmark(
    env: dict[str, str],
    *,
    ref: str = "alpha",
    slug: str = SLUG,
    note: str = "mid-refactor",
    updated_at: str = "2026-08-03T00:00:00Z",
    workspace: bool = True,
) -> dict[str, Any]:
    """Store one bookmark pointing at (demo, *slug*), materializing its workspace."""
    from camp.bookmark.store import upsert

    ws = _workspace_dir(env, slug)
    if workspace:
        ws.mkdir(parents=True, exist_ok=True)
    transcript = Path(env["CAMP_STATE_DIR"]) / f"{ref}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("{}\n")
    return upsert(
        {
            "ref": ref,
            "group": GROUP_NAME,
            "slug": slug,
            "session_id": f"sess-{ref}",
            "transcript_path": str(transcript),
            "note": note,
            "created_at": updated_at,
            "updated_at": updated_at,
        },
        env=env,
    )


@pytest.fixture()
def teardown_spy(monkeypatch: pytest.MonkeyPatch):
    """Replace reconcile_break with a recording stub.

    Returns the call log; set ``log["status"]`` to ``"ok_with_errors"`` to
    simulate a teardown that failed partway.
    """
    from camp.provision import reconcile

    log: dict[str, Any] = {"calls": [], "status": "ok"}

    def fake_reconcile_break(group, slug, *, env=None, force=False):
        log["calls"].append({"slug": slug, "force": force})
        if log["status"] == "ok_with_errors":
            return {"status": "ok_with_errors", "removed": [], "errors": ["repo_a: boom"]}
        # A real successful teardown deletes the workspace dir.
        ws = _workspace_dir(env or {}, slug)
        if ws.exists():
            import shutil

            shutil.rmtree(ws)
        return {"status": "ok", "removed": ["repo_a"], "errors": []}

    monkeypatch.setattr(reconcile, "reconcile_break", fake_reconcile_break)
    return log


def _run_remove(group: dict, env: dict[str, str], *args: str) -> None:
    from camp.cli.lifecycle import _cmd_remove_group_cli

    _cmd_remove_group_cli([SLUG, *args], group, env, False)


# ---------------------------------------------------------------------------
# Guard query surface
# ---------------------------------------------------------------------------


def test_blocking_bookmarks_empty_when_none_stored(env: dict[str, str]) -> None:
    from camp.bookmark.guard import blocking_bookmarks

    _workspace_dir(env).mkdir(parents=True, exist_ok=True)
    assert blocking_bookmarks(GROUP_NAME, SLUG, env=env) == []


def test_blocking_bookmarks_finds_the_workspaces_bookmark(env: dict[str, str]) -> None:
    from camp.bookmark.guard import blocking_bookmarks

    _seed_bookmark(env)
    assert [b["ref"] for b in blocking_bookmarks(GROUP_NAME, SLUG, env=env)] == ["alpha"]


def test_blocking_bookmarks_ignores_other_workspaces(env: dict[str, str]) -> None:
    from camp.bookmark.guard import blocking_bookmarks

    _seed_bookmark(env, ref="other", slug="other-slug")
    assert blocking_bookmarks(GROUP_NAME, SLUG, env=env) == []


def test_blocking_bookmarks_empty_when_workspace_dir_is_gone(env: dict[str, str]) -> None:
    """A bookmark whose workspace is already gone blocks nothing.

    Otherwise a removal interrupted after teardown could never be re-attempted.
    """
    from camp.bookmark.guard import blocking_bookmarks

    _seed_bookmark(env, workspace=False)
    assert blocking_bookmarks(GROUP_NAME, SLUG, env=env) == []


# ---------------------------------------------------------------------------
# Guard fires before teardown
# ---------------------------------------------------------------------------


def test_remove_exits_nonzero_when_bookmarked(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    _seed_bookmark(env)

    with pytest.raises(SystemExit) as exc:
        _run_remove(group, env)
    assert exc.value.code != 0


def test_remove_guard_runs_before_teardown(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    _seed_bookmark(env)

    with pytest.raises(SystemExit):
        _run_remove(group, env)
    assert teardown_spy["calls"] == [], "the guard must reject before any teardown runs"


def test_remove_guard_message_lists_ref_note_and_age(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    _seed_bookmark(env, ref="alpha", note="mid-refactor")

    with pytest.raises(SystemExit):
        _run_remove(group, env)

    err = capsys.readouterr().err
    assert "alpha" in err, err
    assert "mid-refactor" in err, err
    assert "d" in err.split("alpha", 1)[1], f"an age column should follow the ref:\n{err}"
    assert "--force" in err, err


def test_remove_guard_keeps_stdout_empty(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    """A blocked removal writes nothing to stdout — the shell must stay put."""
    _seed_bookmark(env)

    with pytest.raises(SystemExit):
        _run_remove(group, env)
    assert capsys.readouterr().out == ""


def test_remove_guard_leaves_the_bookmark_stored(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.store import get_by_ref

    _seed_bookmark(env)
    with pytest.raises(SystemExit):
        _run_remove(group, env)
    assert get_by_ref("alpha", env=env) is not None


def test_remove_unbookmarked_workspace_is_not_blocked(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    _workspace_dir(env).mkdir(parents=True, exist_ok=True)

    _run_remove(group, env)
    assert [c["slug"] for c in teardown_spy["calls"]] == [SLUG]


def test_remove_reattempt_after_workspace_gone_is_not_blocked(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    """The bookmark survived an interrupted teardown; rm must still be runnable."""
    _seed_bookmark(env, workspace=False)

    _run_remove(group, env)
    assert [c["slug"] for c in teardown_spy["calls"]] == [SLUG]


# ---------------------------------------------------------------------------
# --dry-run: the guard must fire even though the early return never mutates
# ---------------------------------------------------------------------------


def test_dry_run_exits_nonzero_when_bookmarked(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    from camp.cli.lifecycle import _cmd_remove_group_cli

    _seed_bookmark(env)

    with pytest.raises(SystemExit) as exc:
        _cmd_remove_group_cli([SLUG], group, env, True)
    assert exc.value.code != 0


def test_dry_run_prints_the_refusal_message(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    from camp.cli.lifecycle import _cmd_remove_group_cli

    _seed_bookmark(env, ref="alpha", note="mid-refactor")

    with pytest.raises(SystemExit):
        _cmd_remove_group_cli([SLUG], group, env, True)

    err = capsys.readouterr().err
    assert "alpha" in err, err
    assert "mid-refactor" in err, err


def test_dry_run_blocked_by_bookmark_runs_no_teardown(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    from camp.cli.lifecycle import _cmd_remove_group_cli

    _seed_bookmark(env)

    with pytest.raises(SystemExit):
        _cmd_remove_group_cli([SLUG], group, env, True)
    assert teardown_spy["calls"] == [], "dry-run must never mutate, even when blocked"


def test_dry_run_unbookmarked_workspace_still_prints_would_remove(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    """When NOT blocked, dry-run keeps its existing non-mutating preview line."""
    from camp.cli.lifecycle import _cmd_remove_group_cli

    _workspace_dir(env).mkdir(parents=True, exist_ok=True)

    _cmd_remove_group_cli([SLUG], group, env, True)
    assert teardown_spy["calls"] == []
    err = capsys.readouterr().err
    assert "would remove" in err, err


# ---------------------------------------------------------------------------
# --force: teardown first, bookmark cleanup only after success
# ---------------------------------------------------------------------------


def test_force_tears_down_the_workspace(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    _seed_bookmark(env)

    _run_remove(group, env, "--force")
    assert [c["force"] for c in teardown_spy["calls"]] == [True]


def test_force_removes_the_bookmark_after_successful_teardown(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.store import get_by_ref

    _seed_bookmark(env)
    _run_remove(group, env, "--force")
    assert get_by_ref("alpha", env=env) is None


def test_force_leaves_other_workspaces_bookmarks_alone(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.store import get_by_ref

    _seed_bookmark(env)
    _seed_bookmark(env, ref="other", slug="other-slug")

    _run_remove(group, env, "--force")
    assert get_by_ref("other", env=env) is not None


def test_failed_teardown_leaves_the_bookmark_intact(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.store import get_by_ref

    _seed_bookmark(env)
    teardown_spy["status"] = "ok_with_errors"

    with pytest.raises(SystemExit) as exc:
        _run_remove(group, env, "--force")
    assert exc.value.code != 0
    assert get_by_ref("alpha", env=env) is not None


def test_bookmark_surviving_a_failed_teardown_renders_workspace_gone(
    env: dict[str, str], group: dict, teardown_spy, capsys: pytest.CaptureFixture
) -> None:
    """An entry left behind by a half-finished teardown is legible, not silent."""
    import shutil

    from camp.bookmark.render import render_bookmarks
    from camp.bookmark.store import list_bookmarks

    _seed_bookmark(env)
    teardown_spy["status"] = "ok_with_errors"
    with pytest.raises(SystemExit):
        _run_remove(group, env, "--force")

    # The interrupted teardown got as far as deleting the workspace dir.
    shutil.rmtree(_workspace_dir(env))

    out = render_bookmarks(list_bookmarks(env=env), env=env)
    assert "workspace gone" in out, out
