"""Commands that act on every vault skip a ``read-only`` one; a named one is refused.

A read-only vault's folder is changed by hand outside lore. The write lock already
refuses lore's own writers, but a sweep that merely *reaches* such a vault still
runs git against it — and git writes ``.git/`` (``FETCH_HEAD``, remote-ref logs,
objects, the index) without taking any lore lock. So every sweep drops the vault
from its target set before the first git call, a named target is refused before
it, and the read-only vault stays byte-identical.

Every read-only vault here is a git repository with a bare remote and an
uncommitted change, so a sweep that touched it would visibly stage, commit, pull
or push. Each test snapshots the folder INCLUDING ``.git/`` (relative paths and
bytes) and the bare remote, and a writable vault in the same config is processed
normally, so each test discriminates. Config is real JSON under ``tmp_path`` with
injected XDG dirs — never the live install.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import make_bare_remote, make_git_vault, run_cli, write_vault_config
from test_read_only_write_guard import MESSAGE, _refused, _snapshot

SKIP_LINE = f"{MESSAGE} — skipped"
LINK_BODY = "see [[task/alpha]] for the origin\n"


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def _branch(vault: Path) -> str:
    return _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _remote_head(remote: Path, branch: str) -> str:
    return _git(remote, "rev-parse", branch).stdout.strip()


class _Install:
    """Four git vaults — ``default``, ``w1``, ``w2`` writable and ``ro`` read-only —
    each with its own bare remote. ``ro`` and ``w1`` end with an uncommitted change."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.config_home = tmp_path / "config"
        self.env = {"XDG_CONFIG_HOME": str(self.config_home)}
        self.vaults = {
            name: make_git_vault(tmp_path / "vaults" / name)
            for name in ("default", "w1", "w2", "ro")
        }
        self.remotes = {
            name: make_bare_remote(tmp_path / "remotes" / f"{name}.git") for name in self.vaults
        }
        self.config(ro_scope="suite")
        self.alpha = self.create("w1", "Alpha", "the record to rename\n")
        self.ref_ro = self.create("ro", "Ref in ro", LINK_BODY)
        self.ref_w2 = self.create("w2", "Ref in w2", LINK_BODY)
        for name, vault in self.vaults.items():
            _git(vault, "add", "-A")
            _git(vault, "commit", "-m", "records")
            _git(vault, "remote", "add", "origin", str(self.remotes[name]))
            _git(vault, "push", "-u", "origin", _branch(vault))
        self.config(ro_scope="read-only")
        for name in ("ro", "w1"):
            (self.vaults[name] / "task" / "by-hand.md").write_text("# edited outside lore\n")
            (self.vaults[name] / "task" / "README.md").write_text("vault, edited\n")

    @property
    def ro(self) -> Path:
        return self.vaults["ro"]

    def config(self, *, ro_scope: str) -> None:
        write_vault_config(
            self.config_home,
            [
                ("default", "default", self.vaults["default"]),
                ("w1", "repo", self.vaults["w1"]),
                ("w2", "product", self.vaults["w2"]),
                ("ro", ro_scope, self.vaults["ro"]),
            ],
        )

    def run(self, args, stdin_text=None):
        return run_cli(
            args, vault=self.vaults["default"], state_dir=self.state,
            stdin_text=stdin_text, env_extra=dict(self.env),
        )

    def create(self, vault: str, title: str, body: str) -> str:
        r = self.run(
            ["record", "create", "--kind", "task", "--title", title, "--keyword", "k",
             "--vault", vault],
            stdin_text=body,
        )
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def push_ahead(self, name: str) -> str:
        """Land one commit on *name*'s bare remote from a throwaway clone; return its sha."""
        clone = self.tmp / f"clone-{name}"
        subprocess.run(
            ["git", "clone", "-q", str(self.remotes[name]), str(clone)],
            check=True, capture_output=True,
        )
        ident = ["-c", "user.email=t@e.st", "-c", "user.name=T", "-c", "commit.gpgsign=false"]
        (clone / "task" / f"ahead-{name}.md").write_text("# from another device\n")
        _git(clone, "add", "-A")
        subprocess.run(
            ["git", "-C", str(clone), *ident, "commit", "-q", "-m", "ahead"],
            check=True, capture_output=True,
        )
        _git(clone, "push", "-q", "origin", "HEAD")
        return _git(clone, "rev-parse", "HEAD").stdout.strip()

    def frozen(self) -> dict:
        """What must not move: the read-only folder incl. ``.git/``, and its remote."""
        return {"ro": _snapshot(self.ro), "remote": _snapshot(self.remotes["ro"])}


@pytest.fixture
def install(tmp_path):
    return _Install(tmp_path)


def _skip_lines(r) -> list[str]:
    return [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip() == SKIP_LINE]


# ---------------------------------------------------------------------------
# AC7 — `lore sync` with no vault named
# ---------------------------------------------------------------------------


def test_sync_with_no_vault_named_skips_the_read_only_vault_and_syncs_the_writable_one(install):
    frozen = install.frozen()
    w1_head_before = _git(install.vaults["w1"], "rev-parse", "HEAD").stdout.strip()
    r = install.run(["sync"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert _skip_lines(r) == [SKIP_LINE], r.stdout + r.stderr
    assert install.frozen() == frozen
    w1 = install.vaults["w1"]
    assert _git(w1, "rev-parse", "HEAD").stdout.strip() != w1_head_before
    assert _remote_head(install.remotes["w1"], _branch(w1)) == _git(w1, "rev-parse", "HEAD").stdout.strip()


def test_sync_skips_a_read_only_vault_whose_remote_is_ahead_without_pulling_it(install):
    install.push_ahead("ro")
    frozen = install.frozen()
    r = install.run(["sync"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert install.frozen() == frozen


# ---------------------------------------------------------------------------
# AC8 — `lore flush` publishes every writable vault
# ---------------------------------------------------------------------------


def test_flush_requests_a_publish_for_the_writable_vaults_and_not_the_read_only_one(install):
    frozen = install.frozen()
    r = install.run(["flush"])
    assert r.returncode == 0, r.stdout + r.stderr
    requested = [ln for ln in r.stderr.splitlines() if ln.startswith("Publish requested for:")]
    assert requested == ["Publish requested for: default, w1, w2"], r.stderr
    assert _skip_lines(r) == [SKIP_LINE], r.stdout + r.stderr
    assert install.frozen() == frozen


def test_flush_wait_syncs_the_writable_vaults_and_leaves_the_read_only_one_alone(install):
    frozen = install.frozen()
    w1 = install.vaults["w1"]
    w1_head_before = _git(w1, "rev-parse", "HEAD").stdout.strip()
    r = install.run(["flush", "--wait"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert install.frozen() == frozen
    assert _skip_lines(r) == [SKIP_LINE], r.stdout + r.stderr
    assert _git(w1, "rev-parse", "HEAD").stdout.strip() != w1_head_before
    assert _remote_head(install.remotes["w1"], _branch(w1)) == _git(w1, "rev-parse", "HEAD").stdout.strip()


def _ro_state_files(install) -> dict[str, bytes | None]:
    """State-dir entries naming the ``ro`` vault (request, lock, log, marker files)."""
    return {
        rel: data for rel, data in _snapshot(install.state).items()
        if any(part == "ro" or part.startswith(("ro-", "ro.")) for part in rel.split("/"))
    }


def test_flush_skips_a_read_only_vault_whose_folder_is_absent_and_writes_nothing_for_it(install):
    shutil.rmtree(install.ro)
    ro_state = _ro_state_files(install)
    r = install.run(["flush"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ro_state_files(install) == ro_state
    requested = [ln for ln in r.stderr.splitlines() if ln.startswith("Publish requested for:")]
    assert requested == ["Publish requested for: default, w1, w2"], r.stderr
    assert _skip_lines(r) == [SKIP_LINE], r.stdout + r.stderr
    assert not install.ro.exists()


def test_sync_naming_a_read_only_vault_whose_folder_is_absent_is_refused_as_read_only(install):
    shutil.rmtree(install.ro)
    r = install.run(["sync", "--vault", "ro"])
    _refused(r)
    assert "not found" not in r.stdout + r.stderr
    assert not install.ro.exists()

    w1 = install.vaults["w1"]
    ok = install.run(["sync", "--vault", "w1"])
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert _remote_head(install.remotes["w1"], _branch(w1)) == _git(w1, "rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------------------
# AC9 / AC10 — a named read-only vault is refused
# ---------------------------------------------------------------------------


def test_sync_naming_the_read_only_vault_is_refused_and_nothing_changes(install):
    install.push_ahead("ro")
    frozen = install.frozen()
    _refused(install.run(["sync", "--vault", "ro"]))
    assert install.frozen() == frozen


def test_sync_naming_a_writable_vault_still_syncs_it(install):
    w1 = install.vaults["w1"]
    r = install.run(["sync", "--vault", "w1"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert _remote_head(install.remotes["w1"], _branch(w1)) == _git(w1, "rev-parse", "HEAD").stdout.strip()


def test_publish_naming_the_read_only_vault_is_refused_and_nothing_changes(install):
    install.push_ahead("ro")
    frozen = install.frozen()
    state_before = _snapshot(install.state)
    _refused(install.run(["publish", "--vault", "ro", "--quiet-for", "0"]))
    assert install.frozen() == frozen
    # Refused before a publish marker, lock or log exists for it.
    assert _snapshot(install.state) == state_before


def test_publish_naming_a_writable_vault_publishes_it(install):
    w1 = install.vaults["w1"]
    r = install.run(["publish", "--vault", "w1", "--quiet-for", "0"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert _remote_head(install.remotes["w1"], _branch(w1)) == _git(w1, "rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------------------
# Added scope — no unlocked git operation runs against a read-only vault
# ---------------------------------------------------------------------------


def test_pull_only_with_no_vault_named_skips_the_read_only_vault_and_fetches_the_writable_one(
    install,
):
    ro_sha = install.push_ahead("ro")
    w2_sha = install.push_ahead("w2")
    w1 = install.vaults["w1"]
    _git(w1, "add", "-A")
    _git(w1, "commit", "-m", "by hand")
    _git(w1, "push", "origin", _branch(w1))
    frozen = install.frozen()
    r = install.run(["sync", "--pull-only"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert _skip_lines(r) == [SKIP_LINE], r.stdout + r.stderr
    assert install.frozen() == frozen
    assert _git(install.ro, "cat-file", "-e", ro_sha).returncode != 0, "fetched into ro"
    w2 = install.vaults["w2"]
    assert _git(w2, "rev-parse", f"origin/{_branch(w2)}").stdout.strip() == w2_sha


def test_pull_only_naming_the_read_only_vault_is_refused_and_git_is_untouched(install):
    install.push_ahead("ro")
    frozen = install.frozen()
    _refused(install.run(["sync", "--pull-only", "--vault", "ro"]))
    assert install.frozen() == frozen


def test_resolve_naming_the_read_only_vault_is_refused_before_its_fetch(install):
    install.push_ahead("ro")
    frozen = install.frozen()
    _refused(install.run(["resolve", "ro"]))
    assert install.frozen() == frozen


def test_a_record_update_naming_the_read_only_vault_runs_no_implicit_pull_in_it(install):
    install.push_ahead("ro")
    # Setup's own writes stamped the vault as freshly fetched, which would let the
    # throttle skip the fetch whatever guards it.
    shutil.rmtree(install.state / "lore" / "fetch")
    frozen = install.frozen()
    r = install.run(["record", "update", install.ref_ro, "--vault", "ro"], stdin_text="new body\n")
    assert r.returncode != 0
    assert MESSAGE in r.stderr
    assert install.frozen() == frozen


# ---------------------------------------------------------------------------
# AC11 — `lore vault reformat`
# ---------------------------------------------------------------------------


def _decanonicalise(vault: Path, record_id: str) -> tuple[Path, str]:
    kind, name = record_id.split("/", 1)
    sidecar = vault / kind / f"{name}.json"
    text = json.dumps(json.loads(sidecar.read_text(encoding="utf-8")), separators=(",", ":"))
    sidecar.write_text(text, encoding="utf-8")
    return sidecar, text


def test_reformat_leaves_the_read_only_vault_byte_identical_and_reformats_a_writable_one(install):
    ro_sidecar, ro_text = _decanonicalise(install.ro, install.ref_ro)
    w2_sidecar, w2_text = _decanonicalise(install.vaults["w2"], install.ref_w2)
    frozen = install.frozen()
    r = install.run(["vault", "reformat"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert _skip_lines(r) == [SKIP_LINE], r.stdout + r.stderr
    assert install.frozen() == frozen
    assert ro_sidecar.read_text(encoding="utf-8") == ro_text
    assert w2_sidecar.read_text(encoding="utf-8") != w2_text
    assert w2_sidecar.read_text(encoding="utf-8").endswith("}\n")


# ---------------------------------------------------------------------------
# AC12 — the link rewrite after a rename
# ---------------------------------------------------------------------------


def test_rename_leaves_a_read_only_referrer_untouched_and_rewrites_the_writable_one(install):
    ro_body = install.ro / "task" / f"{install.ref_ro.split('/', 1)[1]}.md"
    w2_body = install.vaults["w2"] / "task" / f"{install.ref_w2.split('/', 1)[1]}.md"
    assert "[[task/alpha]]" in ro_body.read_text(encoding="utf-8")
    frozen = install.frozen()
    r = install.run(["record", "rename", install.alpha, "--title", "Omega"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert _skip_lines(r) == [SKIP_LINE], r.stdout + r.stderr
    assert install.frozen() == frozen
    assert "[[task/omega]]" in w2_body.read_text(encoding="utf-8")
    assert "[[task/alpha]]" not in w2_body.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# `lore reindex` — still reads a read-only vault, without taking its lock
# ---------------------------------------------------------------------------


def test_reindex_indexes_a_read_only_vault_without_touching_its_folder(install):
    frozen = install.frozen()
    r = install.run(["reindex"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    # alpha + ref_ro + ref_w2 across the vaults: the read-only vault's record is counted.
    assert int(r.stdout.strip()) >= 3
    found = install.run(["search", "Ref in ro"])
    assert install.ref_ro in found.stdout, found.stdout + found.stderr
    assert install.frozen() == frozen
