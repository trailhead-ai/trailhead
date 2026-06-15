"""U4 assumption probe: state-dir-path-parsing resolver for the NEW unified layout.

Tests that a path-parsing resolver can correctly classify an 8-position cwd matrix
for the NEW layout where member worktrees live at:
    central_state_dir(group)/worktrees/<slug>/<member>/

Rather than the OLD layout:
    <repo_root>/.claude/worktrees/<slug>/

The probe resolver is defined inline here — it is a **hypothesis implementation**,
not the real Slice 2 implementation.  The test is EPHEMERAL; the Slice 2 executor
will write the real resolver + its own matrix tests and delete this file.

Cleanup: remove this file after Slice 2 lands.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Inline hypothesis resolver — state-dir-path-parsing approach
# ---------------------------------------------------------------------------
# This is the design under test.  We implement it here so we can PROVE
# it works before Slice 2 builds on it.  Slice 2 will replace this with
# a real implementation in group_resolve.py.


def _probe_resolve_from_cwd_new(
    cwd: Path,
    group_configs: list[dict[str, Any]],
    *,
    camp_state_dir: Path,
) -> tuple[str, str | None]:
    """Hypothesis resolver using state-dir path parsing (NEW layout).

    Algorithm:
    1. Check if cwd is under camp_state_dir/<group>/worktrees/<slug>/.
       If so, extract (group, slug) from the path — verify group is a known
       group name.
    2. Otherwise walk cwd upward for a member repo_root match → (group, None).
    3. If still no match → GroupResolutionError.

    camp_state_dir is the resolved value of state_dir("camp") — in production
    this comes from central_state_dir(group).parent (or equivalently from
    trailhead.paths.state_dir("camp", env=...)).  Tests pass it directly.

    The distinguishing mechanism for position 5 vs 6:
    - Position 5 (canonical member repo): the cwd walk hits a directory that
      matches a member's repo_root in group_configs → returns (group, None).
    - Position 6 (non-member dir): no state-dir prefix AND no repo_root match
      anywhere in the walk → raises GroupResolutionError.

    This is deterministic because the state_dir prefix is derived from a known
    root (env-injectable), not from scanning on-disk markers.
    """
    from group_resolve import GroupResolutionError, validate_group_name

    resolved_cwd = cwd.resolve()
    # camp_state_dir may not exist on disk in tests; resolve only if it exists.
    camp_state = camp_state_dir.resolve() if camp_state_dir.exists() else camp_state_dir

    # Step 1: try prefix match against camp_state.
    try:
        rel = resolved_cwd.relative_to(camp_state)
    except ValueError:
        rel = None

    if rel is not None:
        # cwd is under camp_state.  Structure: <group>/worktrees/<slug>/[<member>/[deep/]]
        rel_parts = rel.parts
        if len(rel_parts) >= 3 and rel_parts[1] == "worktrees":
            group_name = rel_parts[0]
            slug = rel_parts[2]
            # Verify the group name is actually a configured group (not stray dir).
            for cfg in group_configs:
                if cfg["group"]["name"] == group_name:
                    validate_group_name(group_name)
                    return (group_name, slug)
            # Under the state dir but no matching group → fall through to
            # repo_root walk (unusual; treat as non-workspace path).

    # Step 2: walk cwd upward for a member repo_root match → (group, None).
    current = resolved_cwd
    visited: set[Path] = set()
    while current not in visited:
        visited.add(current)
        for cfg in group_configs:
            for m in cfg["members"]:
                try:
                    declared = Path(m["repo_root"]).resolve()
                except (OSError, ValueError):
                    continue
                if current == declared:
                    return (cfg["group"]["name"], None)
        parent = current.parent
        if parent == current:
            break
        current = parent

    raise GroupResolutionError("camp: no group resolved from cwd, pass --group")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_group_config(
    name: str,
    repo_roots: list[Path],
    member_names: list[str] | None = None,
) -> dict:
    """Build a parsed group config dict matching load_group's output shape."""
    members = []
    for i, rr in enumerate(repo_roots):
        mname = (member_names[i] if member_names and i < len(member_names) else f"repo{i}")
        members.append({
            "name": mname,
            "repo_root": str(rr),
            "bootstrap": [],
        })
    return {
        "group": {"name": name},
        "members": members,
        "branch_pattern": "worktree-{slug}",
    }


# ---------------------------------------------------------------------------
# U4 8-position matrix — state-dir-path-parsing approach (NEW layout)
# ---------------------------------------------------------------------------


class TestU4ResolverMatrix:
    """State-dir-path-parsing resolver is unambiguous across all 8 cwd positions.

    NEW layout: state_dir("camp")/<group>/worktrees/<slug>/<member>/

    The probe takes camp_state_dir directly (simulating what the real resolver
    will get from central_state_dir(group).parent / trailhead.paths.state_dir).
    This avoids importing trailhead.paths in the test process — the env= path is
    wired through central_state_dir which IS importable via _SCRIPTS_DIR, but
    trailhead itself isn't on the camp test runner's sys.path.  Slice 2's real
    implementation will wire it through central_state_dir(group, env=env).
    """

    @pytest.fixture()
    def camp_state(self, tmp_path: Path) -> Path:
        """The camp state root: tmp_path/state (simulates state_dir("camp"))."""
        state_root = tmp_path / "state"
        state_root.mkdir()
        return state_root

    @pytest.fixture()
    def member_repos(self, tmp_path: Path) -> list[Path]:
        """Two canonical member repos (separate from the state dir)."""
        repo_a = tmp_path / "repos" / "repo-alpha"
        repo_b = tmp_path / "repos" / "repo-beta"
        repo_a.mkdir(parents=True)
        repo_b.mkdir(parents=True)
        return [repo_a, repo_b]

    @pytest.fixture()
    def workspace_dir(self, camp_state: Path) -> Path:
        """Workspace dir: camp_state/mygroup/worktrees/my-feature/"""
        ws = camp_state / "mygroup" / "worktrees" / "my-feature"
        ws.mkdir(parents=True)
        return ws

    @pytest.fixture()
    def group_configs(self, member_repos: list[Path]) -> list[dict]:
        return [_make_group_config(
            "mygroup",
            member_repos,
            member_names=["repo-alpha", "repo-beta"],
        )]

    def _resolve(
        self,
        cwd: Path,
        group_configs: list[dict],
        camp_state: Path,
    ) -> tuple[str, str | None]:
        return _probe_resolve_from_cwd_new(
            cwd, group_configs, camp_state_dir=camp_state
        )

    # ------------------------------------------------------------------
    # Position 1: workspace root itself → (group, slug)
    # ------------------------------------------------------------------

    def test_1_workspace_root(
        self, workspace_dir: Path, group_configs: list[dict], camp_state: Path
    ) -> None:
        """cwd = camp_state/<group>/worktrees/<slug>/ → (group, slug)"""
        group, slug = self._resolve(workspace_dir, group_configs, camp_state)
        assert group == "mygroup"
        assert slug == "my-feature"

    # ------------------------------------------------------------------
    # Position 2: <member> subdir → (group, slug)
    # ------------------------------------------------------------------

    def test_2_member_subdir(
        self, workspace_dir: Path, group_configs: list[dict], camp_state: Path
    ) -> None:
        """cwd = …/worktrees/<slug>/repo-alpha/ → (group, slug)"""
        member_dir = workspace_dir / "repo-alpha"
        member_dir.mkdir()
        group, slug = self._resolve(member_dir, group_configs, camp_state)
        assert group == "mygroup"
        assert slug == "my-feature"

    # ------------------------------------------------------------------
    # Position 3: deep path inside a member worktree → (group, slug)
    # ------------------------------------------------------------------

    def test_3_deep_path_inside_member(
        self, workspace_dir: Path, group_configs: list[dict], camp_state: Path
    ) -> None:
        """cwd = …/worktrees/<slug>/repo-alpha/src/foo/ → (group, slug)"""
        deep = workspace_dir / "repo-alpha" / "src" / "foo"
        deep.mkdir(parents=True)
        group, slug = self._resolve(deep, group_configs, camp_state)
        assert group == "mygroup"
        assert slug == "my-feature"

    # ------------------------------------------------------------------
    # Position 4: another member's subdir (multi-member, same slug) → (group, slug)
    # ------------------------------------------------------------------

    def test_4_another_member_subdir(
        self, workspace_dir: Path, group_configs: list[dict], camp_state: Path
    ) -> None:
        """cwd = …/worktrees/<slug>/repo-beta/lib/ → same (group, slug)"""
        other_member = workspace_dir / "repo-beta" / "lib"
        other_member.mkdir(parents=True)
        group, slug = self._resolve(other_member, group_configs, camp_state)
        assert group == "mygroup"
        assert slug == "my-feature"

    # ------------------------------------------------------------------
    # Position 5: canonical member repo (NOT under state dir) → (group, None)
    # ------------------------------------------------------------------

    def test_5_canonical_member_repo_returns_group_no_slug(
        self, member_repos: list[Path], group_configs: list[dict], camp_state: Path
    ) -> None:
        """cwd = the configured member repo_root itself → (group, None)

        This is the fleet-view fallback: camp knows which group owns this repo
        but there is no workspace/slug context.  MUST NOT raise; MUST return
        (group, None).
        """
        repo_root = member_repos[0]  # repo-alpha, a configured member
        group, slug = self._resolve(repo_root, group_configs, camp_state)
        assert group == "mygroup"
        assert slug is None

    def test_5b_deep_inside_canonical_member_repo(
        self, member_repos: list[Path], group_configs: list[dict], camp_state: Path
    ) -> None:
        """cwd = deep path inside a canonical member repo → (group, None)"""
        deep = member_repos[1] / "tools" / "camp" / "tests"
        deep.mkdir(parents=True)
        group, slug = self._resolve(deep, group_configs, camp_state)
        assert group == "mygroup"
        assert slug is None

    # ------------------------------------------------------------------
    # Position 6: non-member directory → GroupResolutionError
    # ------------------------------------------------------------------

    def test_6_non_member_dir_raises(
        self, tmp_path: Path, group_configs: list[dict], camp_state: Path
    ) -> None:
        """cwd = unrelated path → GroupResolutionError with legible message."""
        from group_resolve import GroupResolutionError

        unrelated = tmp_path / "some" / "unrelated" / "dir"
        unrelated.mkdir(parents=True)
        with pytest.raises(GroupResolutionError, match="no group resolved from cwd"):
            self._resolve(unrelated, group_configs, camp_state)

    def test_6b_state_dir_root_itself_raises(
        self, camp_state: Path, group_configs: list[dict]
    ) -> None:
        """cwd = camp_state itself (no group/worktrees/slug segments) → error.

        The resolver requires at least <group>/worktrees/<slug> relative to
        state_dir.  The state root alone carries no slug.
        """
        from group_resolve import GroupResolutionError

        with pytest.raises(GroupResolutionError, match="no group resolved from cwd"):
            self._resolve(camp_state, group_configs, camp_state)

    # ------------------------------------------------------------------
    # Position 7: renamed/sibling workspace slug dir → resolves to correct slug
    # ------------------------------------------------------------------

    def test_7_different_slug_same_group(
        self, camp_state: Path, group_configs: list[dict]
    ) -> None:
        """Two slugs in the same group: resolver extracts slug from path, not stale state."""
        slug_a = camp_state / "mygroup" / "worktrees" / "feature-alpha"
        slug_b = camp_state / "mygroup" / "worktrees" / "feature-beta"
        slug_a.mkdir(parents=True)
        slug_b.mkdir(parents=True)

        group_a, slug_resolved_a = self._resolve(slug_a, group_configs, camp_state)
        group_b, slug_resolved_b = self._resolve(slug_b, group_configs, camp_state)

        assert group_a == "mygroup"
        assert slug_resolved_a == "feature-alpha"
        assert group_b == "mygroup"
        assert slug_resolved_b == "feature-beta"

    def test_7b_deep_path_resolves_correct_slug(
        self, camp_state: Path, group_configs: list[dict]
    ) -> None:
        """Deep path inside a renamed slug resolves to that slug, not to a sibling."""
        slug_dir = camp_state / "mygroup" / "worktrees" / "RENAMED-SLUG"
        deep = slug_dir / "repo-alpha" / "src" / "deeply" / "nested"
        deep.mkdir(parents=True)

        group, slug = self._resolve(deep, group_configs, camp_state)
        assert group == "mygroup"
        assert slug == "RENAMED-SLUG"

    # ------------------------------------------------------------------
    # Position 8: workspace dir under a DEEP state dir → still parses correctly
    # ------------------------------------------------------------------

    def test_8_deep_state_dir_root(self, tmp_path: Path) -> None:
        """state_dir lives at a deeply-nested path; resolver still parses group+slug.

        This proves the resolver is anchored to the state_dir VALUE, not to
        any fixed depth assumption.
        """
        # State dir is deeply nested (simulating non-standard XDG or CAMP_STATE_DIR).
        deep_state = tmp_path / "a" / "b" / "c" / "d" / "state"
        deep_state.mkdir(parents=True)

        member_repo = tmp_path / "repos" / "deep-member"
        member_repo.mkdir(parents=True)
        configs = [_make_group_config("deep-group", [member_repo], member_names=["deep-member"])]

        ws = deep_state / "deep-group" / "worktrees" / "deep-slug" / "deep-member" / "src"
        ws.mkdir(parents=True)

        group, slug = _probe_resolve_from_cwd_new(ws, configs, camp_state_dir=deep_state)
        assert group == "deep-group"
        assert slug == "deep-slug"

    # ------------------------------------------------------------------
    # Ambiguity check: position 5 vs 6 are distinct (key design question)
    # ------------------------------------------------------------------

    def test_position_5_vs_6_are_unambiguous(
        self,
        tmp_path: Path,
        member_repos: list[Path],
        group_configs: list[dict],
        camp_state: Path,
    ) -> None:
        """The ONLY mechanism distinguishing 'canonical member repo' (pos 5)
        from 'non-member dir' (pos 6) is whether cwd walks upward to a
        configured repo_root.  This test proves both outcomes coexist without
        ambiguity when run against the same group_configs.
        """
        from group_resolve import GroupResolutionError

        # Position 5: repo_alpha IS a configured member
        group, slug = self._resolve(member_repos[0], group_configs, camp_state)
        assert group == "mygroup"
        assert slug is None

        # Position 6: an unrelated sibling dir is NOT a member
        unrelated = tmp_path / "not-a-member"
        unrelated.mkdir()
        with pytest.raises(GroupResolutionError):
            self._resolve(unrelated, group_configs, camp_state)
