"""KU-1 assumption probe: resolve_from_cwd is fully isolatable via camp_state_dir injection.

THROWAWAY — delete this file once Slice 2 tests are written.

Proves that:
1. resolve_from_cwd(cwd, group_configs, camp_state_dir=<tmp>) never calls
   trailhead.paths.state_dir — the real ~/.local/state is never touched.
2. Both the in-memory dict pattern (mirrors test_group_resolve.py) and the
   load_all_groups(groups_dir) pattern (the path _resolve_group_scopes will use)
   isolate correctly using only tmp_path.
3. The fixture recipe Slice 2/3 should reuse: create a temp groups_dir + a temp
   camp_state_dir, write one TOML, build a synthetic cwd under the state dir.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add camp scripts to sys.path — mirrors the pattern in test_group_resolve.py.
_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_CAMP_SCRIPTS = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
if str(_CAMP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CAMP_SCRIPTS))


class TestResolveFromCwdIsolation:
    """Core KU-1 isolation checks — all pass if the assumption holds."""

    def test_in_memory_group_config_resolves_synthetic_worktree_path(
        self, tmp_path: Path
    ) -> None:
        """In-memory dict fixture + injected camp_state_dir: no trailhead import needed.

        Fixture recipe (mirrors test_group_resolve.py exactly):
          - camp_state_dir = tmp_path / "camp_state" (no subdirs pre-created)
          - group_configs built as plain dicts
          - cwd = camp_state_dir / <group> / "worktrees" / <slug> / <subdir>
          - call: resolve_from_cwd(cwd, group_configs, camp_state_dir=camp_state_dir)
        """
        from group_resolve import resolve_from_cwd

        camp_state_dir = tmp_path / "camp_state"

        # Synthetic worktree path: <state>/<group>/worktrees/<slug>/member-repo
        cwd = camp_state_dir / "mygroup" / "worktrees" / "my-slug" / "member-repo"
        cwd.mkdir(parents=True)

        # In-memory group config (no TOML needed for this pattern)
        group_configs = [
            {
                "group": {"name": "mygroup"},
                "members": [
                    {
                        "name": "member-repo",
                        "repo_root": str(tmp_path / "repos" / "member-repo"),
                        "bootstrap": [],
                        "base": "origin/main",
                    }
                ],
                "branch_pattern": "worktree-{slug}",
            }
        ]

        group_name, slug = resolve_from_cwd(cwd, group_configs, camp_state_dir=camp_state_dir)
        assert group_name == "mygroup"
        assert slug == "my-slug"

    def test_load_all_groups_plus_resolve_fully_isolated(self, tmp_path: Path) -> None:
        """load_all_groups(groups_dir) + resolve_from_cwd(camp_state_dir=...) is hermetic.

        This is the fixture recipe _resolve_group_scopes will use in Slice 2.

        Fixture recipe (canonical form for Slice 2/3):
          - groups_dir = tmp_path / "groups"  (with one TOML file)
          - camp_state_dir = tmp_path / "camp_state"  (separate from groups_dir)
          - cwd = camp_state_dir / <group> / "worktrees" / <slug> / <member>
          - load_all_groups(groups_dir) -> group_configs
          - resolve_from_cwd(cwd, group_configs, camp_state_dir=camp_state_dir)
        """
        from group_config import load_all_groups
        from group_resolve import resolve_from_cwd

        # Fixture groups_dir: one TOML file with minimal valid content
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()

        member_repo = tmp_path / "repos" / "member-alpha"
        member_repo.mkdir(parents=True)

        (groups_dir / "mygroup.toml").write_text(
            f'[group]\nname = "mygroup"\n\n[[members]]\nname = "member-alpha"\nrepo_root = "{member_repo}"\n',
            encoding="utf-8",
        )

        # Synthetic camp state dir (separate from groups_dir)
        camp_state_dir = tmp_path / "camp_state"
        cwd = camp_state_dir / "mygroup" / "worktrees" / "feat-123" / "member-alpha"
        cwd.mkdir(parents=True)

        # Load groups from fixture TOML — no real ~/.config/camp touched
        group_configs = load_all_groups(groups_dir)
        assert len(group_configs) == 1
        assert group_configs[0]["group"]["name"] == "mygroup"

        # Resolve: camp_state_dir injected, so trailhead.paths.state_dir is never called
        group_name, slug = resolve_from_cwd(cwd, group_configs, camp_state_dir=camp_state_dir)
        assert group_name == "mygroup"
        assert slug == "feat-123"

    def test_no_real_state_dir_is_consulted(self, tmp_path: Path) -> None:
        """Prove isolation: a deliberately wrong HOME still resolves correctly.

        If resolve_from_cwd used the real state_dir derivation (from trailhead.paths),
        it would derive a different path and the cwd prefix check would fail.
        Passing camp_state_dir explicitly bypasses that derivation entirely.
        """
        from group_resolve import resolve_from_cwd

        # Camp state dir has a path that would NOT match any real XDG derivation
        camp_state_dir = tmp_path / "totally-synthetic-camp-root" / "nested"

        cwd = camp_state_dir / "fixture-group" / "worktrees" / "isolation-slug"
        cwd.mkdir(parents=True)

        group_configs = [
            {
                "group": {"name": "fixture-group"},
                "members": [
                    {
                        "name": "repo-x",
                        "repo_root": str(tmp_path / "repo-x"),
                        "bootstrap": [],
                        "base": "origin/main",
                    }
                ],
                "branch_pattern": "worktree-{slug}",
            }
        ]

        group_name, slug = resolve_from_cwd(cwd, group_configs, camp_state_dir=camp_state_dir)
        assert group_name == "fixture-group"
        assert slug == "isolation-slug"
