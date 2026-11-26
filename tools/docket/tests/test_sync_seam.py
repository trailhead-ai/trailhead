"""The change pipeline against real lore and real git, end to end, with a bare repository as the remote.

Each host is a clone of one bare remote with its own lore config, state and HOME, so nothing here
reaches an operator's vaults. The runner hands docket the real lore CLI and real git.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _docket_cli import PLUGIN_ROOT
from trailhead.tests._git_support import make_hermetic_git_env

from docket import changes, cli

pytestmark = pytest.mark.seam_real

LORE = PLUGIN_ROOT.parents[2] / "lore" / "plugins" / "lore" / "cli" / "lore"


def _git(env, *argv, cwd=None):
    return subprocess.run(["git", *argv], check=True, capture_output=True, text=True, env=env, cwd=cwd)


class Host:
    def __init__(self, root: Path, remote: Path, *, seed: bool, name: str):
        self.root = root
        self.vault = root / "vault"
        root.mkdir(parents=True)
        base = dict(os.environ, HOME=str(root / "home"), XDG_STATE_HOME=str(root / "state"),
                    XDG_CONFIG_HOME=str(root / "config"), LORE_PUBLISH_DISABLE="1", LORE_EMAIL="t@e.st")
        self.env = make_hermetic_git_env(base, root)
        cfg = root / "config" / "lore"
        cfg.mkdir(parents=True)
        (cfg / "config.json").write_text(json.dumps(
            {"vaults": [{"name": name, "scope": "default", "path": str(self.vault)}]}))
        if seed:
            _git(self.env, "init", "-q", "-b", "main", str(self.vault))
            (self.vault / "task").mkdir()
            (self.vault / "task" / "README.md").write_text("vault\n")
            (self.vault / ".gitignore").write_text("*.lock\n")
            _git(self.env, "-C", str(self.vault), "add", "-A")
            _git(self.env, "-C", str(self.vault), "commit", "-q", "-m", "init")
            _git(self.env, "-C", str(self.vault), "remote", "add", "origin", str(remote))
            _git(self.env, "-C", str(self.vault), "push", "-q", "-u", "origin", "main")
        else:
            _git(self.env, "clone", "-q", str(remote), str(self.vault))

    def runner(self, argv):
        argv = list(argv)
        if argv[0] == "lore":
            argv = [sys.executable, str(LORE), *argv[1:]]
        return subprocess.run(argv, capture_output=True, text=True, env=self.env, timeout=120)

    def note(self, title: str) -> str:
        proc = subprocess.run(
            [sys.executable, str(LORE), "record", "create", "--vault", "main", "--kind", "blob", "--title", title,
             "--keyword", "note"], input="body\n", capture_output=True, text=True, env=self.env)
        assert proc.returncode == 0, proc.stderr
        return "main/blob/" + title.lower().replace(" ", "-")

    def docket(self, monkeypatch, capsys, *argv, runner=None):
        for name, command in changes.make_commands(runner or self.runner).items():
            monkeypatch.setitem(cli.COMMANDS, name, command)
        capsys.readouterr()
        code = cli.main(list(argv))
        return code, json.loads(capsys.readouterr().out)

    def list_on_disk(self):
        path = self.vault / "sites" / "docket" / "lists" / "main.json"
        return [e["slug"] for e in json.loads(path.read_text())["entries"]]


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote.git"
    env = make_hermetic_git_env(os.environ, tmp_path)
    _git(env, "init", "-q", "--bare", "-b", "main", str(path))
    return path


def remote_list(remote: Path, env) -> list[str]:
    shown = _git(env, "--git-dir", str(remote), "show", "main:sites/docket/lists/main.json").stdout
    return [e["slug"] for e in json.loads(shown)["entries"]]


def test_an_add_is_synced_and_the_list_file_is_on_the_remote(tmp_path, remote, monkeypatch, capsys):
    host = Host(tmp_path / "a", remote, seed=True, name="main")
    ref = host.note("First note")
    code, doc = host.docket(monkeypatch, capsys, "add", "--", ref)
    assert (code, doc["outcome"]) == (0, "saved"), doc
    assert doc["live_count"] == {"main": 1}
    assert remote_list(remote, host.env) == ["first-note"]


def test_a_remote_edit_that_lands_between_the_syncs_makes_the_change_lost_to_remote(
    tmp_path, remote, monkeypatch, capsys
):
    a = Host(tmp_path / "a", remote, seed=True, name="main")
    b = Host(tmp_path / "b", remote, seed=False, name="main")
    ref_a, ref_b = a.note("From a"), b.note("From b")
    synced = []

    def b_runner(argv):
        proc = b.runner(argv)
        if list(argv)[:2] == ["lore", "sync"] and not synced:
            synced.append(True)
            code, doc = a.docket(monkeypatch, capsys, "add", "--", ref_a)
            assert (code, doc["outcome"]) == (0, "saved"), doc
        return proc

    code, doc = b.docket(monkeypatch, capsys, "add", "--", ref_b, runner=b_runner)
    assert (code, doc["outcome"]) == (4, "lost-to-remote"), doc
    assert doc["lists"] == {"main": [{"kind": "blob", "slug": "from-a"}]}
    assert b.list_on_disk() == ["from-a"]
    assert remote_list(remote, b.env) == ["from-a"]
