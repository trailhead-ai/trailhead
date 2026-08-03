"""Tests for camp.bookmark.store — the global bookmark store.

Test contract:
- The store lives at state_dir("camp")/bookmarks.json; the file is created 0o600
  and its lockfile sits beside it.
- Read/query API: get_by_ref (hit/miss), list_bookmarks (empty / ref-ordered),
  find_by_workspace (hit/miss) — the surface every consumer builds on.
- upsert replaces an existing ref in place rather than duplicating it.
- delete_by_ref reports whether it removed anything and leaves siblings alone.
- Writes are atomic: a failure mid-write leaves the PRIOR store intact and drops
  no temp files.
- A corrupt/truncated store raises BookmarkStoreError naming the file — never a
  raw JSONDecodeError traceback.

Every test injects CAMP_STATE_DIR so the real ~/.local/state/camp is never touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

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


def _record(ref: str, *, group: str = "g", slug: str = "s", **kw) -> dict:
    base = {
        "ref": ref,
        "group": group,
        "slug": slug,
        "session_id": f"sess-{ref}",
        "transcript_path": f"/transcripts/{ref}.jsonl",
        "note": "",
        "created_at": "2026-08-03T00:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Placement + file mode
# ---------------------------------------------------------------------------


def test_store_path_is_under_camp_state_dir(env: dict[str, str], tmp_path: Path) -> None:
    from camp.bookmark.store import store_path

    assert store_path(env=env) == tmp_path / "state" / "bookmarks.json"


def test_upsert_creates_store_with_owner_only_mode(env: dict[str, str]) -> None:
    from camp.bookmark.store import store_path, upsert

    upsert(_record("alpha"), env=env)
    path = store_path(env=env)
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600


def test_upsert_exercises_the_lockfile(env: dict[str, str]) -> None:
    from camp.bookmark.store import lock_path, upsert

    assert not lock_path(env=env).exists()
    upsert(_record("alpha"), env=env)
    assert lock_path(env=env).is_file()


# ---------------------------------------------------------------------------
# Read / query API
# ---------------------------------------------------------------------------


def test_get_by_ref_returns_the_record(env: dict[str, str]) -> None:
    from camp.bookmark.store import get_by_ref, upsert

    upsert(_record("alpha", note="hi"), env=env)
    got = get_by_ref("alpha", env=env)
    assert got is not None
    assert got["ref"] == "alpha"
    assert got["note"] == "hi"


def test_get_by_ref_miss_returns_none(env: dict[str, str]) -> None:
    from camp.bookmark.store import get_by_ref, upsert

    upsert(_record("alpha"), env=env)
    assert get_by_ref("nope", env=env) is None


def test_get_by_ref_on_absent_store_returns_none(env: dict[str, str]) -> None:
    from camp.bookmark.store import get_by_ref

    assert get_by_ref("alpha", env=env) is None


def test_list_bookmarks_empty_when_store_absent(env: dict[str, str]) -> None:
    from camp.bookmark.store import list_bookmarks

    assert list_bookmarks(env=env) == []


def test_list_bookmarks_is_ordered_by_ref(env: dict[str, str]) -> None:
    from camp.bookmark.store import list_bookmarks, upsert

    for ref in ("charlie", "alpha", "bravo"):
        upsert(_record(ref, slug=ref), env=env)
    assert [b["ref"] for b in list_bookmarks(env=env)] == ["alpha", "bravo", "charlie"]


def test_find_by_workspace_matches_group_and_slug(env: dict[str, str]) -> None:
    from camp.bookmark.store import find_by_workspace, upsert

    upsert(_record("alpha", group="g1", slug="s1"), env=env)
    upsert(_record("bravo", group="g2", slug="s1"), env=env)
    got = find_by_workspace("g2", "s1", env=env)
    assert got is not None and got["ref"] == "bravo"


def test_find_by_workspace_miss_returns_none(env: dict[str, str]) -> None:
    from camp.bookmark.store import find_by_workspace, upsert

    upsert(_record("alpha", group="g1", slug="s1"), env=env)
    assert find_by_workspace("g1", "other", env=env) is None


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def test_upsert_replaces_existing_ref_in_place(env: dict[str, str]) -> None:
    from camp.bookmark.store import get_by_ref, list_bookmarks, upsert

    upsert(_record("alpha", session_id="old"), env=env)
    upsert(_record("alpha", session_id="new"), env=env)
    assert len(list_bookmarks(env=env)) == 1
    assert get_by_ref("alpha", env=env)["session_id"] == "new"


def test_delete_by_ref_removes_and_reports_true(env: dict[str, str]) -> None:
    from camp.bookmark.store import delete_by_ref, get_by_ref, upsert

    upsert(_record("alpha"), env=env)
    upsert(_record("bravo"), env=env)
    assert delete_by_ref("alpha", env=env) is True
    assert get_by_ref("alpha", env=env) is None
    assert get_by_ref("bravo", env=env) is not None


def test_delete_by_ref_miss_reports_false(env: dict[str, str]) -> None:
    from camp.bookmark.store import delete_by_ref, upsert

    upsert(_record("alpha"), env=env)
    assert delete_by_ref("nope", env=env) is False


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------


def test_failed_write_leaves_prior_store_intact(env: dict[str, str]) -> None:
    from camp.bookmark import store as store_mod

    store_mod.upsert(_record("alpha"), env=env)
    before = store_mod.store_path(env=env).read_text()

    with patch.object(store_mod.json, "dump", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            store_mod.upsert(_record("bravo"), env=env)

    assert store_mod.store_path(env=env).read_text() == before
    assert store_mod.get_by_ref("bravo", env=env) is None


def test_failed_write_drops_no_temp_files(env: dict[str, str]) -> None:
    from camp.bookmark import store as store_mod

    store_mod.upsert(_record("alpha"), env=env)
    with patch.object(store_mod.json, "dump", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            store_mod.upsert(_record("bravo"), env=env)

    leftovers = [p.name for p in store_mod.store_path(env=env).parent.glob("*.tmp")]
    assert leftovers == []


def test_corrupt_store_raises_named_error(env: dict[str, str]) -> None:
    from camp.bookmark.store import BookmarkStoreError, list_bookmarks, store_path

    path = store_path(env=env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"bookmarks": {"alpha": ')  # truncated mid-write

    with pytest.raises(BookmarkStoreError) as exc:
        list_bookmarks(env=env)
    assert str(path) in str(exc.value)


def test_store_of_wrong_json_shape_raises_named_error(env: dict[str, str]) -> None:
    from camp.bookmark.store import BookmarkStoreError, list_bookmarks, store_path

    path = store_path(env=env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(["not", "an", "object"]))

    with pytest.raises(BookmarkStoreError) as exc:
        list_bookmarks(env=env)
    assert str(path) in str(exc.value)


def test_store_dir_is_created_owner_only(env: dict[str, str]) -> None:
    """The store's parent (the camp state dir) is created owner-only, matching the
    rest of camp's central state — a 0o600 file under a world-readable dir would
    still leak every ref, group, and slug by name."""
    from camp.bookmark.store import store_path, upsert

    upsert(_record("alpha"), env=env)
    assert store_path(env=env).parent.stat().st_mode & 0o777 == 0o700
