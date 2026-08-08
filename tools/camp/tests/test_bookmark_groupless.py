"""`camp resume` / `camp bookmark ls` / `camp bookmark rm` work from ANY cwd.

The whole point of a ref is that it is looked up without knowing which group it
belongs to. These three commands therefore must work from a plain shell that sits
outside every camp group directory — the case where no group resolves from cwd and
the invocation falls through to the spine dispatcher.

`camp bookmark` (bare capture) is the deliberate exception: it bookmarks the
CURRENT workspace, so it is cwd-scoped by definition and still refuses outside one.

Every test runs the real CLI as a subprocess with CAMP_CONFIG_DIR / CAMP_STATE_DIR
redirected at tmp_path, from a cwd that belongs to no group, so nothing about the
developer's own camp install can make it pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _write_group_toml(groups_dir: Path, name: str, *, binary: str | None = None) -> None:
    body = (
        f'[group]\nname = "{name}"\n\n'
        '[[members]]\nname = "repo_a"\nrepo_root = "/nonexistent/repo"\n\n'
        '[branch]\npattern = "worktree-{slug}"\n'
    )
    if binary is not None:
        body += f'\n[harness]\nbinary = "{binary}"\n'
    (groups_dir / f"{name}.toml").write_text(body)


@pytest.fixture()
def groupless(tmp_path: Path) -> dict:
    """A configured group + one healthy bookmark, and a cwd in no group at all."""
    from camp.bookmark.store import upsert
    from camp.group.manifest import workspace_dir

    config_dir = tmp_path / "camp-config"
    (config_dir / "groups").mkdir(parents=True)
    state_dir = tmp_path / "camp-state"
    state_dir.mkdir()
    _write_group_toml(config_dir / "groups", "demo")

    inner_env = {"CAMP_STATE_DIR": str(state_dir), "CAMP_CONFIG_DIR": str(config_dir)}
    workspace = workspace_dir("demo", "alpha", env=inner_env)
    workspace.mkdir(parents=True, exist_ok=True)
    transcript = tmp_path / "sess-alpha.jsonl"
    transcript.write_text("{}\n")
    upsert(
        {
            "ref": "alpha",
            "group": "demo",
            "slug": "alpha",
            "session_id": "sess-alpha",
            "transcript_path": str(transcript),
            "note": "",
            "created_at": "2026-08-03T00:00:00Z",
            "updated_at": "2026-08-03T00:00:00Z",
        },
        env=inner_env,
    )

    outside = tmp_path / "outside"
    outside.mkdir()

    env = {**os.environ, **inner_env, "CAMP_SHELL_INTEGRATION": "1"}
    return {
        "env": env,
        "cwd": outside,
        "workspace": workspace,
        "inner_env": inner_env,
        "config_dir": config_dir,
    }


def _camp(groupless: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=groupless["env"],
        cwd=str(groupless["cwd"]),
    )


class TestNoGroupResolvesFromCwd:
    """Sanity: the fixture's cwd really is outside every group."""

    def test_a_cwd_scoped_verb_still_reports_no_group(self, groupless):
        r = _camp(groupless, "pwd")
        assert r.returncode != 0
        assert "no group resolved from cwd" in r.stderr


class TestBookmarkLsFromAnywhere:
    def test_ls_exits_zero_and_lists_the_bookmark(self, groupless):
        r = _camp(groupless, "bookmark", "ls")
        assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
        assert "alpha" in r.stdout
        assert "demo/alpha" in r.stdout

    def test_ls_does_not_demand_a_group(self, groupless):
        r = _camp(groupless, "bookmark", "ls")
        assert "no group resolved" not in r.stderr


class TestBookmarkRmFromAnywhere:
    def test_rm_removes_the_named_bookmark(self, groupless):
        from camp.bookmark.store import get_by_ref

        r = _camp(groupless, "bookmark", "rm", "alpha")
        assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
        assert get_by_ref("alpha", env=groupless["inner_env"]) is None

    def test_rm_of_an_unknown_ref_names_the_ref_not_the_group(self, groupless):
        r = _camp(groupless, "bookmark", "rm", "nope")
        assert r.returncode != 0
        assert "nope" in r.stderr
        assert "no group resolved" not in r.stderr


class TestResumeFromAnywhere:
    def test_resume_emits_the_two_line_contract(self, groupless):
        r = _camp(groupless, "resume", "alpha")
        assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
        lines = r.stdout.splitlines()
        assert len(lines) == 2
        assert Path(lines[0]) == groupless["workspace"].resolve()

    def test_resume_of_an_unknown_ref_names_the_ref_not_the_group(self, groupless):
        r = _camp(groupless, "resume", "nope")
        assert r.returncode != 0
        assert "no bookmark named 'nope'" in r.stderr

    def test_resume_uses_the_harness_of_the_bookmarks_own_group(self, groupless):
        """The argv comes from the harness of the group the bookmark was captured
        in — read off the record — not from whatever group the shell is near."""
        _write_group_toml(
            groupless["config_dir"] / "groups", "demo", binary="some-other-agent"
        )
        r = _camp(groupless, "resume", "alpha")
        assert r.returncode != 0
        assert "resume unsupported for this harness" in r.stderr


class TestBareCaptureStaysCwdScoped:
    def test_bare_bookmark_outside_a_workspace_still_needs_a_group(self, groupless):
        """Capture names no target: it bookmarks the workspace it is run from, so
        it has nothing to act on outside one."""
        r = _camp(groupless, "bookmark")
        assert r.returncode != 0
        assert "no group resolved from cwd" in r.stderr


class TestHelpAndSchemaUnaffected:
    def test_camp_list_still_works_from_the_groupless_cwd(self, groupless):
        r = _camp(groupless, "list", "--json")
        assert r.returncode == 0, f"stderr={r.stderr!r}"
        json.loads(r.stdout)
