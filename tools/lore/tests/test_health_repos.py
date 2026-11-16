"""Repositories behind a vault and the default-branch view of each, for the health report.

Real git repositories under ``tmp_path`` (hermetic git env), real camp group TOML
files in a ``tmp_path`` groups dir. The camp import, ``PATH``, the git timeout and
the output cap are injected through keyword seams.
"""

from __future__ import annotations

import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import build_history, load_script, make_bare_remote

repos = load_script("lore.health.repos")
layers = load_script("lore.vault.layers")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _hermetic(hermetic_git_env):
    return hermetic_git_env


def git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )
    return done.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def make_repo(path: Path, branches: dict[str, dict[str, str]], *, remote_tracking=()) -> Path:
    """A repo whose *branches* ({name: {file: text}}) exist as local branches.

    Names in *remote_tracking* are moved to ``refs/remotes/origin/<name>``, so the
    repo has them only as remote-tracking refs.
    """
    init_repo(path)
    commits = [
        {"id": name, "branch": name, "files": files} for name, files in branches.items()
    ]
    build_history(path, commits)
    for name in remote_tracking:
        git(path, "update-ref", f"refs/remotes/origin/{name}", f"refs/heads/{name}")
        git(path, "update-ref", "-d", f"refs/heads/{name}")
    return path


def write_group(groups_dir: Path, name: str, roots, scopes=(), *, body_extra="") -> Path:
    groups_dir.mkdir(parents=True, exist_ok=True)
    lines = [f'[group]\nname = "{name}"\n']
    for i, root in enumerate(roots):
        lines.append(f'\n[[members]]\nname = "m{i}"\nrepo_root = "{root}"\n')
    for scope, vault in scopes:
        lines.append(f'\n[[lore_scopes]]\nscope = "{scope}"\nname = "{vault}"\n')
    path = groups_dir / f"{name}.toml"
    path.write_text("".join(lines) + body_extra, encoding="utf-8")
    return path


def view_of(root: Path, **kw):
    out = repos.read_repository(root, **kw)
    assert isinstance(out, repos.Repository), out
    return out


def reason_of(root, **kw):
    out = repos.read_repository(root, **kw)
    assert isinstance(out, repos.Unreadable), out
    return out.reason


# ---------------------------------------------------------------------------
# load_camp_groups (the loader extracted from vault/layers.py)
# ---------------------------------------------------------------------------


def _camp_missing():
    raise ImportError("no camp")


def test_load_camp_groups_reads_every_group_in_the_dir(tmp_path):
    gd = tmp_path / "groups"
    write_group(gd, "a", [tmp_path / "ra"])
    write_group(gd, "b", [tmp_path / "rb"])
    out = layers.load_camp_groups(gd)
    assert out.reason is None
    assert [c["group"]["name"] for c in out.configs] == ["a", "b"]


def test_load_camp_groups_reports_camp_absent_when_the_import_fails(tmp_path):
    out = layers.load_camp_groups(tmp_path / "groups", import_camp=_camp_missing)
    assert (out.reason, out.configs) == (layers.CAMP_ABSENT, ())


def test_load_camp_groups_reports_unreadable_config_for_malformed_toml(tmp_path):
    gd = tmp_path / "groups"
    gd.mkdir()
    (gd / "bad.toml").write_text("[group\nname = ", encoding="utf-8")
    out = layers.load_camp_groups(gd)
    assert out.reason == layers.CONFIG_UNREADABLE
    assert out.error is not None


def test_load_camp_groups_reports_unreadable_config_for_non_utf8_toml(tmp_path):
    gd = tmp_path / "groups"
    gd.mkdir()
    (gd / "bad.toml").write_bytes(b"\xff\xfe\x00")
    out = layers.load_camp_groups(gd)
    assert out.reason == layers.CONFIG_UNREADABLE


def test_load_camp_groups_with_no_groups_dir_is_empty_not_a_failure(tmp_path):
    out = layers.load_camp_groups(tmp_path / "absent")
    assert (out.reason, out.configs) == (None, ())


def test_resolve_active_group_config_warns_and_degrades_on_malformed_toml(tmp_path, capsys):
    gd = tmp_path / "groups"
    gd.mkdir()
    (gd / "bad.toml").write_text("[group\n", encoding="utf-8")
    assert layers.resolve_active_group_config(gd, tmp_path) is None
    assert "camp group config error; degrading to personal-only recall" in capsys.readouterr().err


def test_resolve_active_group_config_warns_differently_for_an_unreadable_file(tmp_path, capsys):
    gd = tmp_path / "groups"
    gd.mkdir()
    (gd / "bad.toml").write_bytes(b"\xff\xfe\x00")
    assert layers.resolve_active_group_config(gd, tmp_path, degrade_target="x") is None
    assert "cannot read camp group config; degrading to x" in capsys.readouterr().err


def test_resolve_active_group_config_returns_the_group_containing_the_cwd(tmp_path):
    gd = tmp_path / "groups"
    a, b = tmp_path / "ra", tmp_path / "rb"
    a.mkdir()
    b.mkdir()
    write_group(gd, "g1", [a])
    write_group(gd, "g2", [b])
    cfg = layers.resolve_active_group_config(gd, b, camp_state_dir=tmp_path / "state")
    assert cfg["group"]["name"] == "g2"


# ---------------------------------------------------------------------------
# vault_repositories
# ---------------------------------------------------------------------------


def test_two_groups_binding_one_vault_give_that_vault_both_repositories(tmp_path):
    gd = tmp_path / "groups"
    a, b = tmp_path / "ra", tmp_path / "rb"
    write_group(gd, "g1", [a], [("repo", "v")])
    write_group(gd, "g2", [b], [("team", "v")])
    got = repos.vault_repositories(["v"], groups_dir=gd)
    assert got["v"].roots == (a.resolve(), b.resolve())
    assert got["v"].reason is None


def test_one_group_binding_two_vaults_gives_both_its_repositories(tmp_path):
    gd = tmp_path / "groups"
    a = tmp_path / "ra"
    write_group(gd, "g1", [a], [("repo", "v1"), ("team", "v2")])
    got = repos.vault_repositories(["v1", "v2"], groups_dir=gd)
    assert got["v1"].roots == got["v2"].roots == (a.resolve(),)


def test_the_mapping_follows_which_lore_scopes_names_appear(tmp_path):
    gd = tmp_path / "groups"
    a = tmp_path / "ra"
    write_group(gd, "g1", [a], [("repo", "bound")])
    got = repos.vault_repositories(["bound", "other"], groups_dir=gd)
    assert got["bound"].roots == (a.resolve(),)
    assert (got["other"].roots, got["other"].reason) == ((), repos.UNBOUND)
    write_group(gd, "g1", [a], [("repo", "other")])
    got = repos.vault_repositories(["bound", "other"], groups_dir=gd)
    assert (got["bound"].roots, got["bound"].reason) == ((), repos.UNBOUND)
    assert got["other"].roots == (a.resolve(),)


def test_a_repository_in_several_groups_is_listed_once_in_stable_order(tmp_path):
    gd = tmp_path / "groups"
    a, b = tmp_path / "ra", tmp_path / "rb"
    write_group(gd, "g1", [a, b], [("repo", "v")])
    write_group(gd, "g2", [b], [("team", "v")])
    got = repos.vault_repositories(["v"], groups_dir=gd)
    assert got["v"].roots == (a.resolve(), b.resolve())


def test_a_slashed_vault_name_matches_its_normalized_binding(tmp_path):
    gd = tmp_path / "groups"
    a = tmp_path / "ra"
    write_group(gd, "g1", [a], [("repo", "org/proj")])
    got = repos.vault_repositories(["org_proj"], groups_dir=gd)
    assert got["org_proj"].roots == (a.resolve(),)


def test_malformed_group_toml_gives_every_vault_config_unreadable(tmp_path):
    gd = tmp_path / "groups"
    gd.mkdir()
    (gd / "bad.toml").write_text("[group\n", encoding="utf-8")
    got = repos.vault_repositories(["v", "w"], groups_dir=gd)
    assert {n: (r.roots, r.reason) for n, r in got.items()} == {
        "v": ((), repos.CONFIG_UNREADABLE),
        "w": ((), repos.CONFIG_UNREADABLE),
    }


def test_an_unimportable_camp_gives_every_vault_camp_absent(tmp_path):
    got = repos.vault_repositories(["v"], groups_dir=tmp_path / "g", import_camp=_camp_missing)
    assert (got["v"].roots, got["v"].reason) == ((), repos.CAMP_ABSENT)


def test_a_tilde_root_expands_against_home(tmp_path):
    gd = tmp_path / "groups"
    write_group(gd, "g1", ["~/code/r"], [("repo", "v")])
    got = repos.vault_repositories(["v"], groups_dir=gd)
    assert got["v"].roots == (Path(os.environ["HOME"], "code", "r").resolve(),)


def test_a_relative_root_resolves_against_the_toml_dir_not_the_cwd(tmp_path, monkeypatch):
    gd = tmp_path / "groups"
    write_group(gd, "g1", ["../elsewhere/r"], [("repo", "v")])
    expected = (tmp_path / "elsewhere" / "r").resolve()
    first = repos.vault_repositories(["v"], groups_dir=gd)["v"].roots
    other = tmp_path / "unrelated"
    other.mkdir()
    monkeypatch.chdir(other)
    second = repos.vault_repositories(["v"], groups_dir=gd)["v"].roots
    assert first == second == (expected,)


def test_read_repositories_reads_each_root_once(tmp_path):
    seen = []

    def fake_read(root, **kw):
        seen.append(root)
        return repos.Unreadable(root, repos.MISSING)

    a, b = tmp_path / "a", tmp_path / "b"
    out = repos.read_repositories([a, b, a], read=fake_read)
    assert seen == [a, b]
    assert set(out) == {a, b}


# ---------------------------------------------------------------------------
# read_repository: which ref
# ---------------------------------------------------------------------------


def test_origin_head_pointing_at_trunk_reads_trunk(tmp_path):
    r = make_repo(
        tmp_path / "r",
        {"trunk": {"t.txt": "t"}, "main": {"m.txt": "m"}},
        remote_tracking=("trunk", "main"),
    )
    git(r, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    v = view_of(r)
    assert (v.ref, v.branch, v.local) == ("refs/remotes/origin/trunk", "trunk", False)
    assert "t.txt" in v.paths and "m.txt" not in v.paths


def test_no_origin_head_falls_back_to_origin_main(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"m.txt": "m"}}, remote_tracking=("main",))
    v = view_of(r)
    assert (v.ref, v.local) == ("refs/remotes/origin/main", False)


def test_origin_master_is_used_when_there_is_no_origin_main(tmp_path):
    r = make_repo(tmp_path / "r", {"master": {"x": "x"}}, remote_tracking=("master",))
    assert view_of(r).ref == "refs/remotes/origin/master"


def test_only_a_local_main_is_read_and_marked_local(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"m.txt": "m"}})
    v = view_of(r)
    assert (v.ref, v.branch, v.local) == ("refs/heads/main", "main", True)


def test_a_remote_tracking_default_wins_over_a_local_main(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"m.txt": "m"}})
    git(r, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
    git(r, "update-ref", "refs/heads/main", "refs/remotes/origin/main")
    build_history(r, [{"id": "ahead", "branch": "main", "parents": ["refs/heads/main"], "files": {"local-only.txt": "x"}}])
    v = view_of(r)
    assert v.ref == "refs/remotes/origin/main"
    assert "local-only.txt" not in v.paths


def test_only_a_feature_branch_is_no_default_branch(tmp_path):
    r = make_repo(tmp_path / "r", {"feature": {"f.txt": "f"}})
    assert reason_of(r) == repos.NO_DEFAULT_BRANCH


# ---------------------------------------------------------------------------
# read_repository: what is read
# ---------------------------------------------------------------------------


def test_the_read_is_the_ref_not_the_working_tree(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"keep.txt": "k", "gone.txt": "g"}})
    git(r, "checkout", "-q", "-f", "main")
    (r / "gone.txt").unlink()
    (r / "untracked.txt").write_text("u")
    v = view_of(r)
    assert "gone.txt" in v.paths
    assert "untracked.txt" not in v.paths


def test_a_file_only_on_a_local_branch_ahead_of_origin_main_is_not_in_the_view(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"a.txt": "a"}}, remote_tracking=("main",))
    build_history(r, [{"id": "wip", "branch": "wip", "parents": ["refs/remotes/origin/main"], "files": {"wip.txt": "w"}}])
    v = view_of(r)
    assert "a.txt" in v.paths and "wip.txt" not in v.paths


def test_directories_are_paths_too_and_top_level_entries_are_listed(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"tools/lore/x.py": "x", "README.md": "r"}})
    v = view_of(r)
    assert {"tools", "tools/lore", "tools/lore/x.py", "README.md"} <= v.paths
    assert v.top_level == frozenset({"tools", "README.md"})


def test_paths_with_spaces_and_non_ascii_names_survive_intact(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"a dir/b c.txt": "x", "caf\u00e9.txt": "y"}})
    v = view_of(r)
    assert {"a dir", "a dir/b c.txt", "caf\u00e9.txt"} <= v.paths


def test_a_gitlink_is_recorded_and_paths_beneath_it_count_as_present(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"README.md": "r"}})
    git(r, "checkout", "-q", "-f", "main")
    sha = git(r, "rev-parse", "main")
    git(r, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/sub")
    git(r, "commit", "-q", "-m", "add submodule")
    v = view_of(r)
    assert v.submodules == frozenset({"vendor/sub"})
    assert v.contains("vendor/sub")
    assert v.contains("vendor/sub/lib/deep.py")
    assert not v.contains("vendor/other/x.py")
    assert not v.contains("vendor/subway/x.py")
    assert v.contains("README.md") and not v.contains("nope.md")


# ---------------------------------------------------------------------------
# read_repository: fetch time
# ---------------------------------------------------------------------------


def clone_of(tmp_path: Path, files=None) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    make_bare_remote(remote)
    build_history(remote, [{"id": "c1", "branch": "main", "files": files or {"a.txt": "a"}}])
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    return remote, clone


def set_mtime(path: Path, epoch: int) -> None:
    os.utime(path, (epoch, epoch))


def test_fetch_head_mtime_is_reported_as_aware_utc(tmp_path):
    _, clone = clone_of(tmp_path)
    fh = clone / ".git" / "FETCH_HEAD"
    fh.write_text("")
    set_mtime(fh, 1_700_000_000)
    got = view_of(clone).fetched_at
    assert got == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    assert got.utcoffset().total_seconds() == 0


def test_no_fetch_head_anywhere_is_none(tmp_path):
    _, clone = clone_of(tmp_path)
    assert view_of(clone).fetched_at is None


def test_a_fetch_run_from_a_linked_worktree_is_the_time_reported_for_the_main_root(tmp_path):
    _, clone = clone_of(tmp_path)
    main_fh = clone / ".git" / "FETCH_HEAD"
    main_fh.write_text("")
    set_mtime(main_fh, 1_700_000_000)
    wt = tmp_path / "wt"
    git(clone, "worktree", "add", "-q", "-b", "feat", str(wt))
    git(wt, "fetch", "-q", "origin")
    wt_fh = clone / ".git" / "worktrees" / "wt" / "FETCH_HEAD"
    assert wt_fh.exists()
    set_mtime(wt_fh, 1_800_000_000)
    assert view_of(clone).fetched_at == datetime.fromtimestamp(1_800_000_000, timezone.utc)


def test_a_linked_worktree_root_reports_the_main_repos_fetch_time(tmp_path):
    _, clone = clone_of(tmp_path)
    main_fh = clone / ".git" / "FETCH_HEAD"
    main_fh.write_text("")
    set_mtime(main_fh, 1_700_000_000)
    wt = tmp_path / "wt"
    git(clone, "worktree", "add", "-q", "-b", "feat", str(wt))
    assert view_of(wt).fetched_at == datetime.fromtimestamp(1_700_000_000, timezone.utc)


def test_the_newest_fetch_head_wins_over_an_older_worktree_one(tmp_path):
    _, clone = clone_of(tmp_path)
    main_fh = clone / ".git" / "FETCH_HEAD"
    main_fh.write_text("")
    set_mtime(main_fh, 1_800_000_000)
    wt = tmp_path / "wt"
    git(clone, "worktree", "add", "-q", "-b", "feat", str(wt))
    git(wt, "fetch", "-q", "origin")
    set_mtime(clone / ".git" / "worktrees" / "wt" / "FETCH_HEAD", 1_700_000_000)
    assert view_of(clone).fetched_at == datetime.fromtimestamp(1_800_000_000, timezone.utc)


# ---------------------------------------------------------------------------
# read_repository: unreadable
# ---------------------------------------------------------------------------


def test_a_missing_directory_is_missing(tmp_path):
    assert reason_of(tmp_path / "nowhere") == repos.MISSING


def test_a_plain_directory_is_not_a_repository(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert reason_of(d) == repos.NOT_A_REPOSITORY


def test_a_plain_directory_inside_a_repository_is_not_a_repository(tmp_path):
    outer = make_repo(tmp_path / "outer", {"main": {"a": "a"}})
    inner = outer / "sub"
    inner.mkdir()
    assert reason_of(inner) == repos.NOT_A_REPOSITORY


def test_git_absent_from_path_is_git_failed(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"a": "a"}})
    empty = tmp_path / "emptybin"
    empty.mkdir()
    assert reason_of(r, env={"PATH": str(empty)}) == repos.GIT_FAILED


def test_a_git_that_outlasts_the_timeout_is_git_failed(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"a": "a"}})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "git"
    shim.write_text("#!/bin/sh\nexec sleep 5\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    assert reason_of(r, env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, timeout=0.5) == repos.GIT_FAILED


def test_a_tree_larger_than_the_output_cap_is_git_failed_and_a_larger_cap_reads_it(tmp_path):
    files = {f"dir/file-{i:03}.txt": "x" for i in range(50)}
    r = make_repo(tmp_path / "r", {"main": files})
    assert reason_of(r, max_output_bytes=200) == repos.GIT_FAILED
    assert len(view_of(r, max_output_bytes=1_000_000).paths) == 51


# ---------------------------------------------------------------------------
# read_repository: hostile input and environment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["refs/heads/secret", "refs/remotes/origin/../../heads/secret", "refs/remotes/upstream/main"],
)
def test_a_hostile_origin_head_is_not_used_and_the_next_fallback_is_read(tmp_path, target):
    r = make_repo(
        tmp_path / "r",
        {"main": {"real.txt": "r"}, "secret": {"secret.txt": "s"}},
        remote_tracking=("main",),
    )
    git(r, "update-ref", "refs/remotes/upstream/main", "refs/heads/secret")
    head = r / ".git" / "refs" / "remotes" / "origin" / "HEAD"
    head.write_text(f"ref: {target}\n")
    v = view_of(r)
    assert v.ref == "refs/remotes/origin/main"
    assert "real.txt" in v.paths and "secret.txt" not in v.paths


def test_an_inherited_git_dir_pointing_elsewhere_does_not_change_what_is_read(tmp_path):
    mine = make_repo(tmp_path / "mine", {"main": {"mine.txt": "m"}})
    other = make_repo(tmp_path / "other", {"main": {"other.txt": "o"}})
    env = {**os.environ, "GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}
    v = view_of(mine, env=env)
    assert "mine.txt" in v.paths and "other.txt" not in v.paths


def test_inherited_git_variables_do_not_reach_git(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"a": "a"}})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "env.log"
    real = subprocess.run(["which", "git"], capture_output=True, text=True, check=True).stdout.strip()
    shim = bin_dir / "git"
    shim.write_text(f'#!/bin/sh\nenv | grep "^GIT_" >> "{log}"\nexec "{real}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "GIT_AUTHOR_NAME": "leak", "GIT_SSH_COMMAND": "leak"}
    view_of(r, env=env)
    seen = set(log.read_text().split())
    assert not any("leak" in line for line in seen)
    assert {"GIT_OPTIONAL_LOCKS=0", "GIT_NO_LAZY_FETCH=1", "GIT_TERMINAL_PROMPT=0"} <= seen


def test_git_is_given_no_stdin(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"a": "a"}})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "stdin.log"
    real = subprocess.run(["which", "git"], capture_output=True, text=True, check=True).stdout.strip()
    shim = bin_dir / "git"
    shim.write_text(f'#!/bin/sh\ncat >> "{seen}"\nexec "{real}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    read_end, write_end = os.pipe()
    os.write(write_end, b"leaked-stdin")
    os.close(write_end)
    saved = os.dup(0)
    os.dup2(read_end, 0)
    try:
        view_of(r, env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(read_end)
    assert not seen.exists() or seen.read_text() == ""


def test_a_failing_git_after_the_ref_resolved_is_git_failed(tmp_path):
    r = make_repo(tmp_path / "r", {"main": {"a": "a"}})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real = subprocess.run(["which", "git"], capture_output=True, text=True, check=True).stdout.strip()
    shim = bin_dir / "git"
    shim.write_text(
        f'#!/bin/sh\ncase "$*" in *ls-tree*) echo boom >&2; exit 1;; esac\nexec "{real}" "$@"\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    assert reason_of(r, env=env) == repos.GIT_FAILED


def test_reading_never_fetches(tmp_path):
    remote, clone = clone_of(tmp_path)
    before = git(clone, "for-each-ref")
    build_history(
        remote,
        [{"id": "c2", "branch": "main", "parents": ["refs/heads/main"], "files": {"new.txt": "n"}}],
    )
    remote_refs = git(remote, "for-each-ref")
    v = view_of(clone)
    assert "new.txt" not in v.paths and "a.txt" in v.paths
    assert git(clone, "for-each-ref") == before
    assert git(remote, "for-each-ref") == remote_refs
    assert v.fetched_at is None
    assert not (clone / ".git" / "FETCH_HEAD").exists()
