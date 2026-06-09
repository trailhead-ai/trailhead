"""Tests for group_resolve.py — cwd/--group resolution.

Test contract (Slice 1):
- U1 ambiguity-matrix: marker-first resolution is unambiguous across ≥6 cwd positions.
- Resolves group from inside a member repo (slug=None fleet-view fallback).
- Resolves group AND slug from inside a /.claude/worktrees/<slug> path.
- --group override beats cwd.
- cwd ∉ any group + no --group → explicit legible error.
- A repo in two groups → error naming both groups + the repo.
- D-E: group name containing ../  or a separator → named confinement error.
- The central state path equals state_dir("camp")/<group>/ for a CAMP_STATE_DIR-
  overridden fixture (proving the override flows through).
- No group config file → legible first-run scaffold/point message.
- Eager overlap validation surfaces a two-group overlap.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers — build synthetic group config dicts
# ---------------------------------------------------------------------------


def _make_group_config(
    name: str,
    repo_roots: list[Path],
    *,
    bootstrap: list[list[str]] | None = None,
) -> dict:
    """Build a parsed group config dict (as group_config.load_group would return)."""
    members = []
    for i, rr in enumerate(repo_roots):
        member = {
            "name": f"repo{i}",
            "repo_root": str(rr),
            "bootstrap": (bootstrap[i] if bootstrap and i < len(bootstrap) else []),
        }
        members.append(member)
    return {
        "group": {"name": name},
        "members": members,
        "branch_pattern": "worktree-{slug}",
    }


# ---------------------------------------------------------------------------
# U1 ambiguity matrix — marker-first resolution (D-G)
# ---------------------------------------------------------------------------


class TestU1AmbiguityMatrix:
    """Marker-first resolution is unambiguous across all cwd positions."""

    @pytest.fixture()
    def fake_repo(self, tmp_path: Path) -> Path:
        """A fake repo root (simulated; no real git)."""
        repo = tmp_path / "myrepo"
        repo.mkdir()
        return repo

    @pytest.fixture()
    def fake_worktree(self, fake_repo: Path) -> Path:
        """A worktree directory under the fake repo."""
        wt = fake_repo / ".claude" / "worktrees" / "my-feature"
        wt.mkdir(parents=True)
        return wt

    @pytest.fixture()
    def group_configs(self, fake_repo: Path) -> list[dict]:
        """Single group config listing fake_repo as a member."""
        return [_make_group_config("mygroup", [fake_repo])]

    def test_1_worktree_root(self, fake_worktree: Path, group_configs: list[dict]) -> None:
        """Position 1: cwd = worktree root → (group, slug) resolved."""
        from group_resolve import resolve_from_cwd

        group, slug = resolve_from_cwd(fake_worktree, group_configs)
        assert group == "mygroup"
        assert slug == "my-feature"

    def test_2_one_level_deep(self, fake_worktree: Path, group_configs: list[dict]) -> None:
        """Position 2: cwd = one level deep inside worktree → (group, slug) resolved."""
        from group_resolve import resolve_from_cwd

        deep = fake_worktree / "tools" / "camp"
        deep.mkdir(parents=True)
        group, slug = resolve_from_cwd(deep, group_configs)
        assert group == "mygroup"
        assert slug == "my-feature"

    def test_3_deep_path(self, fake_worktree: Path, group_configs: list[dict]) -> None:
        """Position 3: cwd = deep path inside worktree → (group, slug) resolved."""
        from group_resolve import resolve_from_cwd

        deep = fake_worktree / "apps" / "foo" / "lib" / "bar"
        deep.mkdir(parents=True)
        group, slug = resolve_from_cwd(deep, group_configs)
        assert group == "mygroup"
        assert slug == "my-feature"

    def test_4_detached_repo_root(self, fake_repo: Path, group_configs: list[dict]) -> None:
        """Position 4: cwd = repo root (no .claude/worktrees/ segment) →
        group resolved, slug=None (fleet-view fallback, NOT an error).
        """
        from group_resolve import resolve_from_cwd

        group, slug = resolve_from_cwd(fake_repo, group_configs)
        assert group == "mygroup"
        assert slug is None

    def test_5_renamed_worktree_dir(self, fake_repo: Path, group_configs: list[dict]) -> None:
        """Position 5: the on-disk dir name differs from the git branch name.
        Resolution uses the PATH SEGMENT name, not the branch name.
        """
        from group_resolve import resolve_from_cwd

        # e.g. worktree dir is "ISSUE-123" (mixed case, not the branch worktree-issue-123)
        wt = fake_repo / ".claude" / "worktrees" / "ISSUE-123"
        wt.mkdir(parents=True)
        group, slug = resolve_from_cwd(wt, group_configs)
        assert group == "mygroup"
        assert slug == "ISSUE-123"  # slug is the path segment name, as-is

    def test_6_no_group_tmp(self, group_configs: list[dict], tmp_path: Path) -> None:
        """Position 6: /tmp (no group member) → legible error, not a silent wrong-group."""
        from group_resolve import GroupResolutionError, resolve_from_cwd

        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()
        with pytest.raises(GroupResolutionError, match="no group resolved from cwd"):
            resolve_from_cwd(unrelated, group_configs)

    def test_7_nested_worktrees_innermost_wins(
        self, tmp_path: Path
    ) -> None:
        """Position 7: nested worktrees (a path containing TWO .claude/worktrees/<slug>
        segments).

        The resolver must pick the segment whose PARENT matches a configured member
        repo_root. If both match, the INNERMOST segment wins.
        """
        from group_resolve import resolve_from_cwd

        # outer_repo is a group member
        outer_repo = tmp_path / "outer_repo"
        outer_repo.mkdir()
        # inner_repo is also a group member (simulating a repo checked out inside a worktree)
        inner_repo = (
            outer_repo / ".claude" / "worktrees" / "outer-slug" / "inner_repo"
        )
        inner_repo.mkdir(parents=True)
        # inner_repo has its own worktree
        inner_wt = inner_repo / ".claude" / "worktrees" / "inner-slug"
        inner_wt.mkdir(parents=True)

        configs = [
            _make_group_config("outer_group", [outer_repo]),
            _make_group_config("inner_group", [inner_repo]),
        ]

        # cwd inside inner_wt → innermost segment wins, matches inner_group
        deep = inner_wt / "src"
        deep.mkdir()
        group, slug = resolve_from_cwd(deep, configs)
        assert group == "inner_group"
        assert slug == "inner-slug"

    def test_7b_nested_only_outer_matches(
        self, tmp_path: Path
    ) -> None:
        """Position 7b: nested path but only outer repo is in any group.
        The resolver should match the outer group (the only match).
        """
        from group_resolve import resolve_from_cwd

        outer_repo = tmp_path / "outer_repo"
        outer_repo.mkdir()
        inner_repo = (
            outer_repo / ".claude" / "worktrees" / "outer-slug" / "inner_repo"
        )
        inner_repo.mkdir(parents=True)
        inner_wt = inner_repo / ".claude" / "worktrees" / "inner-slug"
        inner_wt.mkdir(parents=True)

        configs = [_make_group_config("outer_group", [outer_repo])]
        deep = inner_wt / "src"
        deep.mkdir()
        group, slug = resolve_from_cwd(deep, configs)
        assert group == "outer_group"
        assert slug == "outer-slug"


# ---------------------------------------------------------------------------
# --group override
# ---------------------------------------------------------------------------


def test_group_override_beats_cwd(tmp_path: Path) -> None:
    """--group override returns the requested group regardless of cwd."""
    from group_resolve import resolve_group_override

    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    repo_b = tmp_path / "repo_b"
    repo_b.mkdir()
    configs = [
        _make_group_config("group_a", [repo_a]),
        _make_group_config("group_b", [repo_b]),
    ]
    # cwd is inside repo_a, but --group group_b is requested
    group_cfg = resolve_group_override("group_b", configs)
    assert group_cfg["group"]["name"] == "group_b"


def test_group_override_unknown_group_errors(tmp_path: Path) -> None:
    """--group override with an unknown group name → legible error."""
    from group_resolve import GroupResolutionError, resolve_group_override

    configs = [_make_group_config("group_a", [tmp_path / "repo_a"])]
    with pytest.raises(GroupResolutionError, match="group_b"):
        resolve_group_override("group_b", configs)


# ---------------------------------------------------------------------------
# cwd ∉ any group + no --group
# ---------------------------------------------------------------------------


def test_no_group_legible_error(tmp_path: Path) -> None:
    """cwd ∉ any group + no --group → explicit 'no group resolved from cwd, pass --group'."""
    from group_resolve import GroupResolutionError, resolve_from_cwd

    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    configs = [_make_group_config("group_a", [repo_a])]

    unrelated = tmp_path / "unrelated_dir"
    unrelated.mkdir()
    with pytest.raises(GroupResolutionError) as exc_info:
        resolve_from_cwd(unrelated, configs)
    assert "no group resolved from cwd" in str(exc_info.value)
    assert "pass --group" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Repo in two groups → error naming both groups + the repo
# ---------------------------------------------------------------------------


def test_repo_in_two_groups_error(tmp_path: Path) -> None:
    """A repo listed in two groups → error naming both groups + the repo."""
    from group_resolve import GroupResolutionError, validate_no_overlap

    shared_repo = tmp_path / "shared_repo"
    shared_repo.mkdir()
    configs = [
        _make_group_config("group_a", [shared_repo]),
        _make_group_config("group_b", [shared_repo]),
    ]
    with pytest.raises(GroupResolutionError) as exc_info:
        validate_no_overlap(configs)
    msg = str(exc_info.value)
    assert "group_a" in msg
    assert "group_b" in msg
    assert str(shared_repo) in msg


def test_repo_in_two_groups_surfaces_on_cwd_resolve(tmp_path: Path) -> None:
    """Overlap is detected at resolve time too, not only at validate_no_overlap."""
    from group_resolve import GroupResolutionError, resolve_from_cwd

    shared_repo = tmp_path / "shared_repo"
    shared_repo.mkdir()
    configs = [
        _make_group_config("group_a", [shared_repo]),
        _make_group_config("group_b", [shared_repo]),
    ]
    with pytest.raises(GroupResolutionError) as exc_info:
        resolve_from_cwd(shared_repo, configs)
    msg = str(exc_info.value)
    assert "group_a" in msg
    assert "group_b" in msg


# ---------------------------------------------------------------------------
# D-E: group-name confinement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "../traversal",
        "foo/bar",
        "foo\\bar",
        "a/b/../c",
        "foo\x00bar",
    ],
)
def test_group_name_confinement_rejects_path_traversal(
    tmp_path: Path, bad_name: str
) -> None:
    """D-E: group names with path separators / '..' / null → named confinement error."""
    from group_resolve import GroupConfinementError, validate_group_name

    with pytest.raises(GroupConfinementError):
        validate_group_name(bad_name)


def test_group_name_valid(tmp_path: Path) -> None:
    """A valid group name passes confinement validation."""
    from group_resolve import validate_group_name

    validate_group_name("my-group")
    validate_group_name("trailhead")
    validate_group_name("ai_tooling")


# ---------------------------------------------------------------------------
# Central state path uses state_dir("camp")/<group>/
# ---------------------------------------------------------------------------


def test_central_state_path_uses_resolver(tmp_path: Path) -> None:
    """The central state path = state_dir("camp")/<group>/ via resolver env= injection.

    Uses CAMP_STATE_DIR override to prove no path derives from __file__.
    """
    from group_resolve import central_state_dir

    override = tmp_path / "custom_state"
    env = {"CAMP_STATE_DIR": str(override), "HOME": str(tmp_path)}
    result = central_state_dir("mygroup", env=env, platform="linux")
    assert result == override / "mygroup"
    # Must NOT be relative to __file__
    import group_resolve as gr_mod
    module_path = Path(gr_mod.__file__).resolve()
    assert not str(result).startswith(str(module_path.parent))


def test_central_state_path_no_file_anchor(tmp_path: Path) -> None:
    """central_state_dir without override uses the resolver, not __file__."""
    from group_resolve import central_state_dir

    env = {"HOME": str(tmp_path)}
    result = central_state_dir("mygroup", env=env, platform="linux")
    # On linux without XDG override: ~/.local/state/camp/mygroup
    expected = tmp_path / ".local" / "state" / "camp" / "mygroup"
    assert result == expected
