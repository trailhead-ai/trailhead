"""Tests for group_resolve.py — cwd/--group resolution.

Slice 2 reworks resolution from marker-scan (old `<repo>/.claude/worktrees/<slug>`
layout) to state-dir path parsing for the unified workspace layout:

    central_state_dir(group)/worktrees/<slug>/<member>/

Resolution algorithm (U4 — VALIDATED):
  1. cwd.relative_to(camp_state_dir) → if len(parts) >= 3 and parts[1] == "worktrees"
     → (group=parts[0], slug=parts[2]), verifying the group is configured.
  2. else walk cwd upward for a member repo_root match → (group, None).
  3. else GroupResolutionError.

The resolver resolves BOTH cwd and camp_state_dir with .resolve() before the
prefix check.  camp_state_dir is injectable (tests pass it directly; production
derives it from trailhead.paths.state_dir("camp", env=...)).

Test contract:
- 8-position state-dir matrix (workspace root, each member subdir, deep path,
  canonical member repo → (group, None), non-member → error, renamed slug, nested).
- --group override beats cwd.
- cwd ∉ any group + no --group → explicit legible error.
- A repo in two groups → error naming both groups + the repo.
- D-E: group name containing ../ or a separator → named confinement error.
- The central state path equals state_dir("camp")/<group>/ for a CAMP_STATE_DIR-
  overridden fixture (proving the override flows through).
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
    member_names: list[str] | None = None,
) -> dict:
    """Build a parsed group config dict (as group_config.load_group would return)."""
    members = []
    for i, rr in enumerate(repo_roots):
        mname = member_names[i] if member_names and i < len(member_names) else f"repo{i}"
        members.append(
            {
                "name": mname,
                "repo_root": str(rr),
                "bootstrap": [],
                "base": "origin/main",
            }
        )
    return {
        "group": {"name": name},
        "members": members,
        "branch_pattern": "worktree-{slug}",
    }


# ---------------------------------------------------------------------------
# U4 8-position matrix — state-dir-path-parsing resolution (NEW layout)
# ---------------------------------------------------------------------------


class TestStateDirResolverMatrix:
    """State-dir-path-parsing resolution is unambiguous across all 8 cwd positions.

    NEW layout: state_dir("camp")/<group>/worktrees/<slug>/<member>/

    camp_state_dir is injected directly (production derives it from
    trailhead.paths.state_dir("camp")), so these are pure path-logic tests with
    no trailhead import dependency.
    """

    @pytest.fixture()
    def camp_state(self, tmp_path: Path) -> Path:
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
        return [
            _make_group_config(
                "mygroup",
                member_repos,
                member_names=["repo-alpha", "repo-beta"],
            )
        ]

    def _resolve(
        self, cwd: Path, group_configs: list[dict], camp_state: Path
    ) -> tuple[str, str | None]:
        from group_resolve import resolve_from_cwd

        return resolve_from_cwd(cwd, group_configs, camp_state_dir=camp_state)

    # Position 1: workspace root itself → (group, slug)
    def test_1_workspace_root(
        self, workspace_dir: Path, group_configs: list[dict], camp_state: Path
    ) -> None:
        group, slug = self._resolve(workspace_dir, group_configs, camp_state)
        assert group == "mygroup"
        assert slug == "my-feature"

    # Position 2: <member> subdir → (group, slug)
    def test_2_member_subdir(
        self, workspace_dir: Path, group_configs: list[dict], camp_state: Path
    ) -> None:
        member_dir = workspace_dir / "repo-alpha"
        member_dir.mkdir()
        group, slug = self._resolve(member_dir, group_configs, camp_state)
        assert group == "mygroup"
        assert slug == "my-feature"

    # Position 3: deep path inside a member worktree → (group, slug)
    def test_3_deep_path_inside_member(
        self, workspace_dir: Path, group_configs: list[dict], camp_state: Path
    ) -> None:
        deep = workspace_dir / "repo-alpha" / "src" / "foo"
        deep.mkdir(parents=True)
        group, slug = self._resolve(deep, group_configs, camp_state)
        assert group == "mygroup"
        assert slug == "my-feature"

    # Position 4: another member's subdir (multi-member, same slug) → (group, slug)
    def test_4_another_member_subdir(
        self, workspace_dir: Path, group_configs: list[dict], camp_state: Path
    ) -> None:
        other_member = workspace_dir / "repo-beta" / "lib"
        other_member.mkdir(parents=True)
        group, slug = self._resolve(other_member, group_configs, camp_state)
        assert group == "mygroup"
        assert slug == "my-feature"

    # Position 5: canonical member repo (NOT under state dir) → (group, None)
    def test_5_canonical_member_repo_returns_group_no_slug(
        self, member_repos: list[Path], group_configs: list[dict], camp_state: Path
    ) -> None:
        repo_root = member_repos[0]
        group, slug = self._resolve(repo_root, group_configs, camp_state)
        assert group == "mygroup"
        assert slug is None

    def test_5b_deep_inside_canonical_member_repo(
        self, member_repos: list[Path], group_configs: list[dict], camp_state: Path
    ) -> None:
        deep = member_repos[1] / "tools" / "camp" / "tests"
        deep.mkdir(parents=True)
        group, slug = self._resolve(deep, group_configs, camp_state)
        assert group == "mygroup"
        assert slug is None

    # Position 6: non-member directory → GroupResolutionError
    def test_6_non_member_dir_raises(
        self, tmp_path: Path, group_configs: list[dict], camp_state: Path
    ) -> None:
        from group_resolve import GroupResolutionError

        unrelated = tmp_path / "some" / "unrelated" / "dir"
        unrelated.mkdir(parents=True)
        with pytest.raises(GroupResolutionError, match="no group resolved from cwd"):
            self._resolve(unrelated, group_configs, camp_state)

    def test_6b_state_dir_root_itself_raises(
        self, camp_state: Path, group_configs: list[dict]
    ) -> None:
        """cwd = camp_state itself (no group/worktrees/slug segments) → error."""
        from group_resolve import GroupResolutionError

        with pytest.raises(GroupResolutionError, match="no group resolved from cwd"):
            self._resolve(camp_state, group_configs, camp_state)

    def test_6c_unconfigured_group_under_state_dir_raises(
        self, camp_state: Path, group_configs: list[dict]
    ) -> None:
        """A <group>/worktrees/<slug> path whose group is NOT configured → error.

        A stray dir under the state dir must not resolve to a phantom group.
        """
        from group_resolve import GroupResolutionError

        stray = camp_state / "ghost-group" / "worktrees" / "some-slug"
        stray.mkdir(parents=True)
        with pytest.raises(GroupResolutionError, match="no group resolved from cwd"):
            self._resolve(stray, group_configs, camp_state)

    # Position 7: renamed/sibling slug dir → resolves to correct slug
    def test_7_different_slug_same_group(
        self, camp_state: Path, group_configs: list[dict]
    ) -> None:
        slug_a = camp_state / "mygroup" / "worktrees" / "feature-alpha"
        slug_b = camp_state / "mygroup" / "worktrees" / "feature-beta"
        slug_a.mkdir(parents=True)
        slug_b.mkdir(parents=True)

        group_a, slug_resolved_a = self._resolve(slug_a, group_configs, camp_state)
        group_b, slug_resolved_b = self._resolve(slug_b, group_configs, camp_state)

        assert (group_a, slug_resolved_a) == ("mygroup", "feature-alpha")
        assert (group_b, slug_resolved_b) == ("mygroup", "feature-beta")

    def test_7b_deep_path_resolves_correct_slug(
        self, camp_state: Path, group_configs: list[dict]
    ) -> None:
        slug_dir = camp_state / "mygroup" / "worktrees" / "RENAMED-SLUG"
        deep = slug_dir / "repo-alpha" / "src" / "deeply" / "nested"
        deep.mkdir(parents=True)

        group, slug = self._resolve(deep, group_configs, camp_state)
        assert group == "mygroup"
        assert slug == "RENAMED-SLUG"

    # Position 8: workspace dir under a DEEP state dir → still parses correctly
    def test_8_deep_state_dir_root(self, tmp_path: Path) -> None:
        from group_resolve import resolve_from_cwd

        deep_state = tmp_path / "a" / "b" / "c" / "d" / "state"
        deep_state.mkdir(parents=True)

        member_repo = tmp_path / "repos" / "deep-member"
        member_repo.mkdir(parents=True)
        configs = [
            _make_group_config("deep-group", [member_repo], member_names=["deep-member"])
        ]

        ws = (
            deep_state
            / "deep-group"
            / "worktrees"
            / "deep-slug"
            / "deep-member"
            / "src"
        )
        ws.mkdir(parents=True)

        group, slug = resolve_from_cwd(ws, configs, camp_state_dir=deep_state)
        assert group == "deep-group"
        assert slug == "deep-slug"

    def test_position_5_vs_6_are_unambiguous(
        self,
        tmp_path: Path,
        member_repos: list[Path],
        group_configs: list[dict],
        camp_state: Path,
    ) -> None:
        """canonical member repo (pos 5) vs non-member dir (pos 6) coexist
        without ambiguity against the same configs."""
        from group_resolve import GroupResolutionError

        group, slug = self._resolve(member_repos[0], group_configs, camp_state)
        assert group == "mygroup"
        assert slug is None

        unrelated = tmp_path / "not-a-member"
        unrelated.mkdir()
        with pytest.raises(GroupResolutionError):
            self._resolve(unrelated, group_configs, camp_state)


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

    state_root = tmp_path / "state"
    state_root.mkdir()
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    configs = [_make_group_config("group_a", [repo_a])]

    unrelated = tmp_path / "unrelated_dir"
    unrelated.mkdir()
    with pytest.raises(GroupResolutionError) as exc_info:
        resolve_from_cwd(unrelated, configs, camp_state_dir=state_root)
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
    """Overlap is detected at resolve time too (canonical member repo walk)."""
    from group_resolve import GroupResolutionError, resolve_from_cwd

    state_root = tmp_path / "state"
    state_root.mkdir()
    shared_repo = tmp_path / "shared_repo"
    shared_repo.mkdir()
    configs = [
        _make_group_config("group_a", [shared_repo]),
        _make_group_config("group_b", [shared_repo]),
    ]
    with pytest.raises(GroupResolutionError) as exc_info:
        resolve_from_cwd(shared_repo, configs, camp_state_dir=state_root)
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
    """The central state path = state_dir("camp")/<group>/ via resolver env= injection."""
    from group_resolve import central_state_dir

    override = tmp_path / "custom_state"
    env = {"CAMP_STATE_DIR": str(override), "HOME": str(tmp_path)}
    result = central_state_dir("mygroup", env=env, platform="linux")
    assert result == override / "mygroup"
    import group_resolve as gr_mod

    module_path = Path(gr_mod.__file__).resolve()
    assert not str(result).startswith(str(module_path.parent))


def test_central_state_path_no_file_anchor(tmp_path: Path) -> None:
    """central_state_dir without override uses the resolver, not __file__."""
    from group_resolve import central_state_dir

    env = {"HOME": str(tmp_path)}
    result = central_state_dir("mygroup", env=env, platform="linux")
    expected = tmp_path / ".local" / "state" / "camp" / "mygroup"
    assert result == expected


# ---------------------------------------------------------------------------
# camp_state_dir derived from env when not passed explicitly
# ---------------------------------------------------------------------------


def test_resolve_derives_state_dir_from_env(tmp_path: Path) -> None:
    """When camp_state_dir is not passed, the resolver derives it from env via
    trailhead.paths — proving the env= injection path the design lock-in allows."""
    from group_resolve import resolve_from_cwd

    state_root = tmp_path / "state"
    state_root.mkdir()
    repo = tmp_path / "repos" / "member"
    repo.mkdir(parents=True)
    configs = [_make_group_config("envgroup", [repo], member_names=["member"])]

    ws = state_root / "envgroup" / "worktrees" / "wt-slug" / "member"
    ws.mkdir(parents=True)

    env = {"CAMP_STATE_DIR": str(state_root), "HOME": str(tmp_path)}
    group, slug = resolve_from_cwd(ws, configs, env=env)
    assert group == "envgroup"
    assert slug == "wt-slug"
