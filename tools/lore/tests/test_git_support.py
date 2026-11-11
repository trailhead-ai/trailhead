"""Contract of lore's git test support: the hermetic git env, the vault templates'
isolation, and the one-spawn history builder.

Each test varies the input that decides the property: an ambient git
environment that is hostile in one specific way, a template that was touched or
not, a history spec with or without a merge.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import (
    _vault_template,
    build_history,
    make_bare_remote,
    make_git_vault,
    make_hermetic_git_env,
    run_cli,
)


def _git(path: Path, *args: str, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, env=env)


def _hostile_config(path: Path, key: str) -> Path:
    section, name = key.split(".")
    path.write_text(f"[{section}]\n\t{name} = hostile\n")
    return path


def _run(env, cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)


# ── hermetic git env ───────────────────────────────────────────────────────


def test_the_system_gitconfig_is_not_read(tmp_path):
    system = _hostile_config(tmp_path / "system.cfg", "ambient.system")
    base = {**os.environ, "GIT_CONFIG_SYSTEM": str(system)}
    assert _run(base, tmp_path, "config", "--get", "ambient.system").stdout.strip() == "hostile"

    env = make_hermetic_git_env(base, tmp_path)

    assert _run(env, tmp_path, "config", "--get", "ambient.system").returncode == 1


def test_the_global_gitconfig_is_not_read(tmp_path):
    ambient = _hostile_config(tmp_path / "ambient.cfg", "ambient.global")
    base = {**os.environ, "GIT_CONFIG_GLOBAL": str(ambient)}
    assert _run(base, tmp_path, "config", "--get", "ambient.global").stdout.strip() == "hostile"

    env = make_hermetic_git_env(base, tmp_path)

    assert _run(env, tmp_path, "config", "--get", "ambient.global").returncode == 1


@pytest.mark.parametrize(
    ("var", "field"),
    [
        ("GIT_AUTHOR_NAME", "%an"),
        ("GIT_AUTHOR_EMAIL", "%ae"),
        ("GIT_COMMITTER_NAME", "%cn"),
        ("GIT_COMMITTER_EMAIL", "%ce"),
    ],
)
def test_a_commit_carries_the_fixed_identity_whatever_the_ambient_one_is(tmp_path, var, field):
    hostile = {
        "GIT_AUTHOR_NAME": "evil",
        "GIT_AUTHOR_EMAIL": "evil@x.y",
        "GIT_COMMITTER_NAME": "evil",
        "GIT_COMMITTER_EMAIL": "evil@x.y",
    }
    env = make_hermetic_git_env({**os.environ, **hostile}, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(env, repo, "init", "-q")
    assert _run(env, repo, "commit", "-q", "--allow-empty", "-m", "x").returncode == 0

    shown = _run(env, repo, "log", "-1", f"--format={field}").stdout.strip()

    assert shown != hostile[var]
    assert shown == make_hermetic_git_env({}, tmp_path)[var]


def test_commit_signing_is_off_even_when_the_ambient_environment_asks_for_it(tmp_path):
    base = {
        **os.environ,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "commit.gpgsign",
        "GIT_CONFIG_VALUE_0": "true",
    }
    env = make_hermetic_git_env(base, tmp_path)

    assert _run(env, tmp_path, "config", "--get", "commit.gpgsign").stdout.strip() == "false"


def test_hooks_do_not_run_even_when_the_repository_configures_some(tmp_path):
    hooks = tmp_path / "hostile-hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    env = make_hermetic_git_env(os.environ, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(env, repo, "init", "-q")
    _run(env, repo, "config", "--local", "core.hooksPath", str(hooks))
    assert _run({**env, "GIT_CONFIG_COUNT": "0"}, repo, "commit", "-q", "--allow-empty", "-m", "x").returncode == 1

    assert _run(env, repo, "commit", "-q", "--allow-empty", "-m", "x").returncode == 0


def test_a_directory_under_the_temp_dir_does_not_discover_an_enclosing_worktree(tmp_path):
    env = make_hermetic_git_env(os.environ, tmp_path)
    _run(env, tmp_path, "init", "-q")
    inner = tmp_path / "a" / "b"
    inner.mkdir(parents=True)

    assert _run(env, inner, "rev-parse", "--show-toplevel").returncode != 0
    assert _run({**env, "GIT_CEILING_DIRECTORIES": ""}, inner, "rev-parse", "--show-toplevel").returncode == 0


def test_a_git_dir_inherited_from_a_hook_does_not_redirect_git(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _run(os.environ, other, "init", "-q")
    base = {**os.environ, "GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}
    env = make_hermetic_git_env(base, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(env, repo, "init", "-q")

    top = _run(env, repo, "rev-parse", "--show-toplevel").stdout.strip()

    assert Path(top).resolve() == repo.resolve()


def test_the_fixture_applies_the_env_to_this_process_and_the_subprocesses_it_spawns(
    hermetic_git_env, tmp_path
):
    assert os.environ["GIT_CONFIG_GLOBAL"] == hermetic_git_env["GIT_CONFIG_GLOBAL"]
    out = subprocess.run(
        ["git", "config", "--get", "commit.gpgsign"], capture_output=True, text=True, cwd=tmp_path
    )
    assert out.stdout.strip() == "false"


# ── template isolation ─────────────────────────────────────────────────────


def test_a_copy_that_is_mutated_leaves_the_template_pristine_for_the_next_copy(
    hermetic_git_env, tmp_path
):
    first = make_git_vault(tmp_path / "first")
    _git(first, "config", "mutated.key", "yes")
    (first / "mutated.md").write_text("x\n")
    _git(first, "add", "-A")
    assert _git(first, "commit", "-m", "second").returncode == 0

    second = make_git_vault(tmp_path / "second")

    assert _git(second, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert _git(second, "config", "--local", "--get", "mutated.key").returncode == 1
    assert not (second / "mutated.md").exists()


def test_a_record_created_in_one_copy_is_not_in_the_next(hermetic_git_env, tmp_path):
    first = make_git_vault(tmp_path / "first")
    state = tmp_path / "state"
    state.mkdir()
    made = run_cli(["record", "create", "--kind", "task", "--title", "Seed"],
                   vault=first, state_dir=state, stdin_text="body\n")
    assert made.returncode == 0, made.stderr
    assert any(p.suffix == ".md" and p.name != "README.md" for p in first.rglob("*.md"))

    second = make_git_vault(tmp_path / "second")

    assert [p.name for p in second.rglob("*.md")] == ["README.md"]


def test_a_copy_does_not_inherit_hooks_or_local_config_planted_in_the_template(
    hermetic_git_env, tmp_path
):
    identity = ("reset@e.st", "Reset")
    template = _vault_template(identity, True)
    hook = template / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    _git(template, "config", "core.hooksPath", str(hook.parent))
    _git(template, "config", "planted.key", "1")

    vault = make_git_vault(tmp_path / "v", identity=identity)

    assert _git(vault, "config", "--local", "--get", "planted.key").returncode == 1
    assert _git(vault, "config", "--local", "--get", "core.hooksPath").returncode == 1
    assert list((vault / ".git" / "hooks").glob("*")) == []
    (vault / "n.md").write_text("n\n")
    _git(vault, "add", "-A")
    assert _git(vault, "commit", "-m", "n").returncode == 0


def test_a_bare_remote_copy_does_not_inherit_planted_local_config(hermetic_git_env, tmp_path):
    first = make_bare_remote(tmp_path / "one.git")
    _git(first, "config", "planted.key", "1")

    second = make_bare_remote(tmp_path / "two.git")

    assert _git(second, "config", "--local", "--get", "planted.key").returncode == 1


# ── history builder ────────────────────────────────────────────────────────


def _empty_repo(tmp_path: Path, name: str = "h") -> Path:
    return make_git_vault(tmp_path / name, commit=False)


def test_a_two_branch_spec_produces_exactly_those_commits_and_branches(hermetic_git_env, tmp_path):
    repo = _empty_repo(tmp_path)
    spec = [
        {"id": "a", "branch": "main", "message": "one", "files": {"a.md": "a\n"}},
        {"id": "b", "branch": "main", "message": "two", "files": {"b.md": "b\n"}},
        {"id": "c", "branch": "feature", "parents": ["a"], "message": "three", "files": {"c.md": "c\n"}},
    ]

    shas = build_history(repo, spec)

    assert set(shas) == {"a", "b", "c"}
    refs = _git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads").stdout.split("\n")
    assert sorted(r for r in refs if r) == sorted([f"main {shas['b']}", f"feature {shas['c']}"])
    assert _git(repo, "log", "--format=%s", "main").stdout.split() == ["two", "one"]
    assert _git(repo, "log", "--format=%s", "feature").stdout.split() == ["three", "one"]
    assert _git(repo, "show", f"{shas['c']}:c.md").stdout == "c\n"
    assert _git(repo, "rev-parse", f"{shas['c']}^").stdout.strip() == shas["a"]


def test_a_spec_with_a_merge_produces_a_merge_commit_with_both_parents(hermetic_git_env, tmp_path):
    repo = _empty_repo(tmp_path)
    spec = [
        {"id": "a", "branch": "main", "message": "base", "files": {"a.md": "a\n"}},
        {"id": "b", "branch": "main", "message": "main-side", "files": {"b.md": "b\n"}},
        {"id": "c", "branch": "feature", "parents": ["a"], "message": "feature-side", "files": {"c.md": "c\n"}},
        {"id": "m", "branch": "main", "parents": ["b", "c"], "message": "merge", "files": {"c.md": "c\n"}},
    ]

    shas = build_history(repo, spec)

    merges = _git(repo, "rev-list", "--merges", "main").stdout.split()
    assert merges == [shas["m"]]
    assert _git(repo, "rev-list", "--parents", "-1", shas["m"]).stdout.split() == [
        shas["m"], shas["b"], shas["c"],
    ]
    assert _git(repo, "show", f"{shas['m']}:b.md").stdout == "b\n"
    assert _git(repo, "show", f"{shas['m']}:c.md").stdout == "c\n"


def test_a_spec_without_a_merge_produces_no_merge_commit(hermetic_git_env, tmp_path):
    repo = _empty_repo(tmp_path)

    build_history(repo, [
        {"id": "a", "branch": "main", "message": "one", "files": {"a.md": "a\n"}},
        {"id": "b", "branch": "main", "message": "two", "files": {"b.md": "b\n"}},
    ])

    assert _git(repo, "rev-list", "--merges", "main").stdout == ""
    assert _git(repo, "rev-list", "--count", "main").stdout.strip() == "2"


# inert-gate: allow process-wide subprocess patch from before the rule; the code under test needs a runner= seam
def test_the_whole_history_is_built_in_a_single_git_spawn(hermetic_git_env, tmp_path, monkeypatch):
    repo = _empty_repo(tmp_path)
    spawns: list[list[str]] = []
    real_run = subprocess.run

    def counting_run(argv, *a, **k):
        spawns.append(list(argv))
        return real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", counting_run)

    build_history(repo, [
        {"id": str(i), "branch": "main", "message": f"c{i}", "files": {f"f{i}.md": f"{i}\n"}}
        for i in range(6)
    ])

    assert len(spawns) == 1 and spawns[0][:2] == ["git", "-C"] and "fast-import" in spawns[0]


def test_a_history_can_be_built_into_a_bare_remote_and_cloned(hermetic_git_env, tmp_path):
    remote = make_bare_remote(tmp_path / "r.git")
    shas = build_history(remote, [
        {"id": "a", "branch": "main", "message": "one", "files": {"a.md": "a\n"}},
        {"id": "b", "branch": "main", "message": "two", "files": {"b.md": "b\n"}},
    ])

    clone = tmp_path / "clone"
    assert subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(clone)]).returncode == 0

    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == shas["b"]



def test_a_commit_carries_the_author_the_spec_names_and_the_default_otherwise(hermetic_git_env, tmp_path):
    repo = _empty_repo(tmp_path)

    shas = build_history(repo, [
        {"id": "a", "branch": "main", "files": {"a.md": "a\n"}},
        {"id": "b", "branch": "main", "files": {"b.md": "b\n"}, "author": ("DeviceB", "b@e.st")},
    ])

    assert _git(repo, "log", "-1", "--format=%an <%ae> / %cn <%ce>", shas["a"]).stdout.strip() == (
        "Test <t@e.st> / Test <t@e.st>"
    )
    assert _git(repo, "log", "-1", "--format=%an <%ae> / %cn <%ce>", shas["b"]).stdout.strip() == (
        "DeviceB <b@e.st> / DeviceB <b@e.st>"
    )


def test_a_history_continues_the_tip_of_a_branch_the_repository_already_has(hermetic_git_env, tmp_path):
    repo = _empty_repo(tmp_path)
    first = build_history(repo, [{"id": "a", "branch": "main", "files": {"a.md": "a\n"}}])

    later = build_history(repo, [
        {"id": "b", "branch": "main", "parents": ["refs/heads/main"], "files": {"b.md": "b\n"}}
    ])

    assert _git(repo, "rev-parse", f"{later['b']}^").stdout.strip() == first["a"]
    assert _git(repo, "show", f"{later['b']}:a.md").stdout == "a\n"


def test_a_template_built_while_the_hermetic_env_is_active_still_carries_the_requested_identity(
    hermetic_git_env, tmp_path
):
    vault = make_git_vault(tmp_path / "v", identity=("x@e.st", "Xavier"))

    assert _git(vault, "log", "-1", "--format=%an <%ae>").stdout.strip() == "Xavier <x@e.st>"


def test_a_repository_built_for_a_template_follows_the_startup_env_not_the_hermetic_one(
    hermetic_git_env, tmp_path, monkeypatch
):
    import conftest as lore_conftest

    monkeypatch.setitem(lore_conftest._STARTUP_ENV, "GIT_CONFIG_COUNT", "1")
    monkeypatch.setitem(lore_conftest._STARTUP_ENV, "GIT_CONFIG_KEY_0", "init.defaultBranch")
    monkeypatch.setitem(lore_conftest._STARTUP_ENV, "GIT_CONFIG_VALUE_0", "trunk")
    bare = tmp_path / "r.git"

    lore_conftest._git_init(bare, "--bare")

    assert (bare / "HEAD").read_text().strip() == "ref: refs/heads/trunk"


def test_a_git_dir_in_the_startup_env_does_not_redirect_a_template_build_to_that_repo(tmp_path, monkeypatch):
    import conftest as lore_conftest

    host = tmp_path / "host"
    host.mkdir()
    _git(host, "init", "-q")
    before = (host / ".git" / "config").read_text()
    monkeypatch.setitem(lore_conftest._STARTUP_ENV, "GIT_DIR", str(host / ".git"))
    built = tmp_path / "built"

    lore_conftest._git_init(built)
    lore_conftest._run_git(built, "config", "user.email", "t@e.st")

    assert (host / ".git" / "config").read_text() == before
    assert _git(built, "config", "--local", "user.email").stdout.strip() == "t@e.st"
