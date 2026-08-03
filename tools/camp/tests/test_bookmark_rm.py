"""Tests for camp.bookmark.render — the `camp bookmark rm <ref>` command.

Test contract:
- `bookmark rm <ref>` removes exactly that entry and leaves siblings untouched.
- A nonexistent ref errors non-zero, naming the ref.

Every test injects CAMP_STATE_DIR so the real ~/.local/state/camp is never
touched.
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


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    return {"CAMP_STATE_DIR": str(tmp_path / "state")}


@pytest.fixture()
def group() -> dict:
    return {"group": {"name": "demo"}}


def _record(ref: str, *, group: str = "demo", slug: str | None = None) -> dict:
    slug = slug or ref
    return {
        "ref": ref,
        "group": group,
        "slug": slug,
        "session_id": f"sess-{ref}",
        "transcript_path": f"/nonexistent/{ref}.jsonl",
        "note": "",
        "created_at": "2026-08-03T00:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",
    }


def test_cmd_bookmark_rm_removes_exactly_that_ref(
    env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.render import cmd_bookmark_rm
    from camp.bookmark.store import get_by_ref, upsert

    upsert(_record("alpha"), env=env)
    upsert(_record("bravo"), env=env)

    cmd_bookmark_rm(["alpha"], group, env)

    assert get_by_ref("alpha", env=env) is None
    assert get_by_ref("bravo", env=env) is not None


def test_cmd_bookmark_rm_confirms_on_stdout(
    env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.render import cmd_bookmark_rm
    from camp.bookmark.store import upsert

    upsert(_record("alpha"), env=env)
    cmd_bookmark_rm(["alpha"], group, env)

    assert "alpha" in capsys.readouterr().out


def test_cmd_bookmark_rm_nonexistent_ref_errors_naming_it(
    env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.render import cmd_bookmark_rm

    with pytest.raises(SystemExit) as exc:
        cmd_bookmark_rm(["nope"], group, env)
    assert exc.value.code != 0
    assert "nope" in capsys.readouterr().err


def test_cmd_bookmark_rm_requires_a_ref_argument(
    env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.render import cmd_bookmark_rm

    with pytest.raises(SystemExit) as exc:
        cmd_bookmark_rm([], group, env)
    assert exc.value.code != 0


def test_cmd_bookmark_rm_rejects_unexpected_extra_argument(
    env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.render import cmd_bookmark_rm
    from camp.bookmark.store import upsert

    upsert(_record("alpha"), env=env)
    with pytest.raises(SystemExit) as exc:
        cmd_bookmark_rm(["alpha", "extra"], group, env)
    assert exc.value.code != 0
    assert "extra" in capsys.readouterr().err
