"""No lore command touches a read-only vault's folder.

A read-only vault's folder is changed by hand outside lore, so no lore command —
reader, writer, sweep or maintenance — may create, change or remove anything in
it. That includes the ``.lock`` sidecars lore's own writers take and the
``.git/`` writes (``FETCH_HEAD``, remote-ref logs, the refreshed index) a git
call makes without any lore lock.

The command list is read off lore's real argument parser — every top-level
subcommand and each one's subcommands — so a command added later joins the test
without anyone remembering to. A command with no entry in ``FIXTURES`` fails the
test that names it, rather than slipping past.

The read-only vault is a git repository with a bare remote one commit ahead, a
tracked file whose stat is stale (so a ``git status`` would refresh the index)
and an uncommitted file. Each case snapshots the folder, ``.git/`` included
(relative paths and bytes, dotfiles too), runs the command through the real CLI
in a fenced environment, and asserts the snapshot is identical whatever the
command's exit code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import make_bare_remote, make_git_vault, run_cli, write_vault_config

_PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "lore"
sys.path.insert(0, str(_PLUGIN))

from lore.argparse_util import find_subparsers_action  # noqa: E402
from lore.cli.dispatch import build_parser  # noqa: E402


def parser_commands(parser=None, prefix: tuple[str, ...] = ()) -> list[str]:
    """Every subcommand path of the lore parser, e.g. ``"record show"``.

    Reads ``_name_parser_map`` because ``lore resolve`` swaps its ``choices`` for
    an open-ended object that cannot be enumerated.
    """
    parser = parser or build_parser()
    action = find_subparsers_action(parser)
    if action is None:
        return []
    out: list[str] = []
    for name, sub in action._name_parser_map.items():
        out.append(" ".join((*prefix, name)))
        out.extend(parser_commands(sub, (*prefix, name)))
    return out


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def snapshot(folder: Path) -> dict[str, bytes | None]:
    """Every entry under *folder*, dotfiles and ``.git/`` included: files by bytes, directories ``None``."""
    out: dict[str, bytes | None] = {}
    for p in sorted(folder.rglob("*")):
        out[str(p.relative_to(folder))] = p.read_bytes() if p.is_file() and not p.is_symlink() else None
    return out


class Env:
    """Default and writable vaults plus a read-only ``ro`` one, each with a bare remote."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.config_home = tmp_path / "config"
        self.vaults = {
            n: make_git_vault(tmp_path / "vaults" / n) for n in ("default", "w1", "ro")
        }
        self.remotes = {n: make_bare_remote(tmp_path / "remotes" / f"{n}.git") for n in self.vaults}
        self.config("suite")
        self.ro_record = self.create("ro", "Ref in ro")
        self.ro_candidate_session = "ro-session"
        for name, vault in self.vaults.items():
            _git(vault, "add", "-A")
            _git(vault, "commit", "-m", "records")
            _git(vault, "remote", "add", "origin", str(self.remotes[name]))
            branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            _git(vault, "push", "-u", "origin", branch)
        self._push_one_ahead("ro")
        ro = self.vaults["ro"]
        (ro / "task" / "by-hand.md").write_text("# edited outside lore\n")
        os.utime(ro / "README.md", (1_000_000_000, 1_000_000_000))
        # Creating the record while `ro` was still writable left its write lock
        # behind; a vault changed by hand outside lore has none, and a command
        # that re-creates it must show up as a new entry.
        (ro / ".lore.lock").unlink()
        self.config("read-only")

    @property
    def ro(self) -> Path:
        return self.vaults["ro"]

    def config(self, ro_scope: str) -> None:
        write_vault_config(
            self.config_home,
            [
                ("default", "default", self.vaults["default"]),
                ("w1", "repo", self.vaults["w1"]),
                ("ro", ro_scope, self.vaults["ro"]),
            ],
        )

    def run(self, args, stdin_text=None):
        return run_cli(
            args, vault=self.vaults["default"], state_dir=self.state,
            stdin_text=stdin_text, env_extra={"XDG_CONFIG_HOME": str(self.config_home)},
        )

    def create(self, vault: str, title: str) -> str:
        r = self.run(
            ["record", "create", "--kind", "task", "--title", title, "--keyword", "k",
             "--vault", vault],
            stdin_text="body\n",
        )
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def _push_one_ahead(self, name: str) -> None:
        clone = self.tmp / f"clone-{name}"
        subprocess.run(
            ["git", "clone", "-q", str(self.remotes[name]), str(clone)],
            check=True, capture_output=True,
        )
        (clone / "task" / "ahead.md").write_text("# from another device\n")
        _git(clone, "add", "-A")
        subprocess.run(
            ["git", "-C", str(clone), "-c", "user.email=t@e.st", "-c", "user.name=T",
             "-c", "commit.gpgsign=false", "commit", "-q", "-m", "ahead"],
            check=True, capture_output=True,
        )
        _git(clone, "push", "-q", "origin", "HEAD")


# Keyed by the parser's own command path. Each returns (argv, stdin) aimed at the
# read-only vault ``ro`` or its record where the command takes a target; a command
# that writes is expected to be refused, and the snapshot is what is asserted.
FIXTURES = {
    "init": lambda e: (["init"], None),
    "status": lambda e: (["status"], None),
    "sync": lambda e: (["sync"], None),
    "publish": lambda e: (["publish", "--vault", "ro"], None),
    "flush": lambda e: (["flush"], None),
    "resolve": lambda e: (["resolve"], None),
    "resolve <vault>": lambda e: (["resolve", "ro"], None),
    "resolve take": lambda e: (["resolve", "take", e.ro_record, "--local", "--vault", "ro"], None),
    "resolve take-file": lambda e: (
        ["resolve", "take-file", "task/by-hand.md", "--local", "--vault", "ro"], None),
    "health": lambda e: (["health", "--vault", "ro"], None),
    "areas": lambda e: (["areas"], None),
    "reindex": lambda e: (["reindex"], None),
    "search": lambda e: (["search", "kind:task"], None),
    "record": lambda e: (["record"], None),
    "record create": lambda e: (
        ["record", "create", "--kind", "task", "--title", "New in ro", "--vault", "ro"], "body\n"),
    "record update": lambda e: (
        ["record", "update", e.ro_record, "--vault", "ro", "--title", "Changed"], None),
    "record supersede": lambda e: (
        ["record", "supersede", e.ro_record, "--by", e.ro_record, "--vault", "ro"], None),
    "record amend": lambda e: (
        ["record", "amend", e.ro_record, "--reason", "why", "--vault", "ro"], None),
    "record rename": lambda e: (
        ["record", "rename", e.ro_record, "--title", "Renamed", "--vault", "ro"], None),
    "record delete": lambda e: (["record", "delete", e.ro_record, "--vault", "ro"], None),
    "record history": lambda e: (["record", "history", e.ro_record, "--vault", "ro"], None),
    "record criteria": lambda e: (["record", "criteria", e.ro_record, "--vault", "ro"], None),
    "record show": lambda e: (["record", "show", e.ro_record, "--vault", "ro"], None),
    "task": lambda e: (["task"], None),
    "task graph": lambda e: (["task", "graph", e.ro_record, "--vault", "ro"], None),
    "task list": lambda e: (["task", "list", "--vault", "ro"], None),
    "pipeline": lambda e: (["pipeline"], None),
    "vault": lambda e: (["vault"], None),
    "vault add": lambda e: (
        ["vault", "add", "ro2", "--scope", "suite", "--path", str(e.ro)], None),
    "vault delete": lambda e: (
        ["vault", "delete", "ro", "--remove-from-disk", "--yes"], None),
    "vault ls": lambda e: (["vault", "ls"], None),
    "vault config": lambda e: (["vault", "config"], None),
    "vault resolve": lambda e: (["vault", "resolve", "--kind", "task"], None),
    "vault reformat": lambda e: (["vault", "reformat"], None),
    "session": lambda e: (["session"], None),
    "session candidate": lambda e: (
        ["session", "candidate", "--kind", "decision", "--phase", "build",
         "--session-id", e.ro_candidate_session], "a finding\n"),
    "session referenced": lambda e: (
        ["session", "referenced", e.ro_record, "--session-id", e.ro_candidate_session], None),
    "session show": lambda e: (["session", "show", "--session-id", e.ro_candidate_session], None),
    "signing": lambda e: (["signing"], None),
    "signing enable": lambda e: (["signing", "enable"], None),
    "signing status": lambda e: (["signing", "status"], None),
}


@pytest.mark.parametrize("command", parser_commands())
def test_no_command_touches_a_read_only_vault_folder(command, tmp_path):
    assert command in FIXTURES, (
        f"lore has a command {command!r} with no invocation fixture in FIXTURES; "
        "add one aimed at the read-only vault"
    )
    env = Env(tmp_path)
    argv, stdin = FIXTURES[command](env)
    before = snapshot(env.ro)

    r = env.run(argv, stdin_text=stdin)

    after = snapshot(env.ro)
    changed = sorted(
        p for p in before.keys() | after.keys() if before.get(p, "<absent>") != after.get(p, "<absent>")
    )
    assert after == before, (
        f"`lore {' '.join(argv)}` (exit {r.returncode}) changed the read-only vault: {changed}\n"
        f"stdout: {r.stdout}\nstderr: {r.stderr}"
    )


# Flag forms that reach different code from the bare command's fixture: the drift
# check flush runs without a sync, the pull-only sweep, a wait-for-publish flush.
VARIANTS = {
    "flush --no-sync": ["flush", "--no-sync"],
    "flush --wait": ["flush", "--wait"],
    "sync --pull-only": ["sync", "--pull-only"],
    "status --json": ["status", "--json"],
    "pipeline --json": ["pipeline", "--json"],
}


@pytest.mark.parametrize("variant", VARIANTS)
def test_no_flag_variant_touches_a_read_only_vault_folder(variant, tmp_path):
    env = Env(tmp_path)
    before = snapshot(env.ro)

    r = env.run(VARIANTS[variant])

    assert snapshot(env.ro) == before, (
        f"`lore {variant}` (exit {r.returncode}) changed the read-only vault\n"
        f"stdout: {r.stdout}\nstderr: {r.stderr}"
    )
