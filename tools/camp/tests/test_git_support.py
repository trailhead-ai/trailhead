"""Contract of camp's git test support: the hermetic git env and the isolation of
the repo templates `init_git_repo` copies from.

Each test varies the input that decides the property: an ambient git
environment that is hostile in one specific way, or a template that was touched
or not.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from trailhead.tests._git_support import make_hermetic_git_env
from ._helpers import _build_git_repo, _template, init_git_repo


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _run(env, cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)


def _hostile_config(path: Path, key: str) -> Path:
    section, name = key.split(".")
    path.write_text(f"[{section}]\n\t{name} = hostile\n")
    return path


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
    first = tmp_path / "first"
    init_git_repo(first, origin=True)
    _git(first, "config", "mutated.key", "yes")
    _git(first, "remote", "add", "extra", str(first))
    (first / "mutated.txt").write_text("x\n")
    _git(first, "add", "-A")
    assert _git(first, "commit", "-m", "second").returncode == 0

    second = tmp_path / "second"
    init_git_repo(second, origin=True)

    assert _git(second, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert _git(second, "config", "--local", "--get", "mutated.key").returncode == 1
    assert _git(second, "remote").stdout.split() == ["origin"]
    assert not (second / "mutated.txt").exists()


def test_a_copy_does_not_inherit_hooks_or_local_config_planted_in_the_template(
    hermetic_git_env, tmp_path
):
    template = _template(origin=False)
    hook = template / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    _git(template, "config", "core.hooksPath", str(hook.parent))
    _git(template, "config", "planted.key", "1")
    try:
        repo = tmp_path / "r"
        init_git_repo(repo)

        assert _git(repo, "config", "--local", "--get", "planted.key").returncode == 1
        assert _git(repo, "config", "--local", "--get", "core.hooksPath").returncode == 1
        assert list((repo / ".git" / "hooks").glob("*")) == []
        (repo / "n.txt").write_text("n\n")
        _git(repo, "add", "-A")
        assert _git(repo, "commit", "-m", "n").returncode == 0
    finally:
        hook.unlink()
        _git(template, "config", "--unset", "planted.key")
        _git(template, "config", "--unset", "core.hooksPath")


def test_a_template_built_while_the_hermetic_env_is_active_still_carries_the_repo_identity(
    hermetic_git_env, tmp_path
):
    built = tmp_path / "built"

    _build_git_repo(built, origin=False)

    assert _git(built, "log", "-1", "--format=%an <%ae>").stdout.strip() == "Test <test@test.com>"
