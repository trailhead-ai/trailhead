"""docket's shared test fixtures (env, hermetic_git_env), registered by each test module through
pytest_plugins: a second bare conftest.py would collide with the other tools' under pytest's prepend
import mode."""
from __future__ import annotations

import json
import subprocess

import pytest

from _docket_cli import completed, install, run_cli, vault_ls
from trailhead.tests._git_support import hermetic_git_env  # noqa: F401  (fixture)

from docket import changes

DEFAULT = "proj"


class FakeLore:
    """Scripted lore and git: logs each call; a sync can be failed or hooked to change the vault."""

    def __init__(self, vault, vault_dir):
        self.vault = vault
        self.vault_dir = vault_dir
        self.calls: list[list[str]] = []
        self.records: dict[str, list[str]] = {}
        self.failing: set[str] = set()
        self.sync_plan: dict[int, str] = {}
        self.sync_hooks: dict[int, object] = {}
        self.syncs = 0
        self.ahead = "0"
        self.dirty = ""
        self.has_upstream = True
        self.holding_outcome = "holding"
        self.holding_stderr = "lore: the push did not go through; needs a person\n"
        self.vault_ls_result = None

    def know(self, ref, keywords=()):
        self.records[ref] = list(keywords)

    def tags(self):
        out = []
        for argv in self.calls:
            if argv[:3] == ["lore", "vault", "ls"]:
                out.append("vault-ls")
            elif argv[:3] == ["lore", "record", "show"]:
                out.append("show")
            elif argv[:2] == ["lore", "sync"]:
                out.append("sync")
            elif argv[0] == "git" and "rev-list" in argv:
                out.append("rev-list")
            elif argv[0] == "git" and "status" in argv:
                out.append("status")
            else:
                out.append(" ".join(argv))
        return out

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:4] == ["lore", "vault", "ls", "--json"]:
            if self.vault_ls_result is not None:
                return completed(argv, *self.vault_ls_result)
            return vault_ls(argv, self.vault_dir)
        if argv[:3] == ["lore", "record", "show"]:
            assert argv[3:5] == ["--json", "--vault"], argv
            vault, record = argv[5], argv[6]
            ref = f"{vault}/{record}"
            if ref in self.failing:
                body = {"record_id": record, "sidecar": {"title": "T", "status": "active", "keywords": ["note"]}}
                return completed(argv, 1, json.dumps(body), "lore: lookup failed\n")
            if ref not in self.records:
                return completed(argv, 1, "", f"lore: no such record {record}\n")
            body = {"record_id": record, "sidecar": {"title": "T", "status": "active", "keywords": self.records[ref]}}
            return completed(argv, 0, json.dumps(body), "revision: abc123\n")
        if argv[:2] == ["lore", "sync"]:
            assert argv[2:] == ["--vault", self.vault, "--json"], argv
            n = self.syncs
            self.syncs += 1
            hook = self.sync_hooks.get(n)
            if hook:
                hook(self)
            if self.sync_plan.get(n) == "holding":
                doc = {"vaults": [{"vault": self.vault, "outcome": self.holding_outcome}]}
                return completed(argv, 1, "  main: Committed: x\n" + json.dumps(doc, indent=2) + "\n",
                                  self.holding_stderr)
            doc = {"vaults": [{"vault": self.vault, "outcome": "published"}]}
            return completed(argv, 0, f"  {self.vault}: Committed: x\n  {self.vault}: Pushed to origin.\n"
                              + json.dumps(doc, indent=2) + "\n")
        if argv[:3] == ["git", "-C", str(self.vault_dir)] and "rev-list" in argv:
            assert argv[3:] == ["rev-list", "--count", "@{u}..HEAD"], argv
            if not self.has_upstream:
                return completed(argv, 128, "", "fatal: no upstream configured\n")
            return completed(argv, 0, self.ahead + "\n")
        if argv[:3] == ["git", "-C", str(self.vault_dir)] and "status" in argv:
            assert argv[3:] == ["status", "--porcelain", "--", "sites/docket"], argv
            return completed(argv, 0, self.dirty)
        raise AssertionError(f"unexpected call {argv}")


class Env:
    def __init__(self, vault_dir, fake, monkeypatch, capsys):
        self.vault_dir = vault_dir
        self.fake = fake
        self._capsys = capsys
        self.install(monkeypatch)

    def install(self, monkeypatch, **store_kwargs):
        install(monkeypatch, changes.make_commands(self.fake, lock_deadline=0.2, **store_kwargs))

    def run(self, *argv):
        return run_cli(self._capsys, argv)

    def list_path(self, vault=DEFAULT):
        return self.vault_dir / "sites" / "docket" / "lists" / f"{vault}.json"

    def put(self, vault, *entries):
        path = self.list_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for e in entries:
            e = (e,) if isinstance(e, str) else e
            kind, slug = e[0].split("/")
            row = {"kind": kind, "slug": slug}
            if len(e) > 1:
                row["workspace"] = {"group": e[1], "slug": e[2]}
            rows.append(row)
        path.write_text(json.dumps({"version": 1, "entries": rows}))

    def entries(self, vault=DEFAULT):
        path = self.list_path(vault)
        if not path.exists():
            return None
        out = []
        for row in json.loads(path.read_text())["entries"]:
            text = f"{row['kind']}/{row['slug']}"
            if "workspace" in row:
                text += "@" + row["workspace"]["group"] + "/" + row["workspace"]["slug"]
            out.append(text)
        return out

    def raw(self, vault=DEFAULT):
        path = self.list_path(vault)
        return path.read_bytes() if path.exists() else None


@pytest.fixture
def env(tmp_path, hermetic_git_env, monkeypatch, capsys):  # noqa: F811  (hermetic_git_env is the imported fixture)
    vault_dir = tmp_path / "vault"
    subprocess.run(["git", "init", "-q", str(vault_dir)], check=True, env=hermetic_git_env)
    fake = FakeLore(DEFAULT, vault_dir)
    for ref in ("task/n1", "task/n2", "task/n3", "task/a", "task/b", "task/c", "task/d"):
        fake.know(f"{DEFAULT}/{ref}")
    return Env(vault_dir, fake, monkeypatch, capsys)


def ref(kind_slug, vault=DEFAULT):
    return f"{vault}/{kind_slug}"
