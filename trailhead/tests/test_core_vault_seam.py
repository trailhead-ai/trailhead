"""Real-lore seam tests for the core-vault install step.

Every test drives the real lore CLI end to end under a fully fenced HOME / XDG /
LORE_* environment and asserts the real ``~/.config/lore`` and
``~/.local/state/lore`` were not touched.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from trailhead.core_vault import MARKER_NAME, core_copy_dir, core_show, install_core_vault
from trailhead.tests.test_cli_shims import _minimal_path

pytestmark = pytest.mark.seam_real

REPO_ROOT = Path(__file__).resolve().parents[2]
LORE_CLI = REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "cli" / "lore"
REAL_LORE_CONFIG_DIR = Path.home() / ".config" / "lore"
REAL_TRAILHEAD_STATE = [core_copy_dir(), core_copy_dir().parent / MARKER_NAME]


def _content_snapshot(roots, read=Path.read_bytes):
    """Existence and content of every entry under each root, symlinks not followed.

    A file that vanishes between listing and reading is left out of the snapshot.
    """
    out = {}
    for root in roots:
        if not (root.exists() or root.is_symlink()):
            continue
        for p in [root, *sorted(root.rglob("*"))] if root.is_dir() and not root.is_symlink() else [root]:
            try:
                if p.is_symlink():
                    out[str(p)] = ("link", os.readlink(p))
                elif p.is_dir():
                    out[str(p)] = "dir"
                else:
                    out[str(p)] = read(p)
            except FileNotFoundError:
                continue
    return out


def test_content_snapshot_tolerates_a_file_vanishing_mid_run(tmp_path):
    transient = tmp_path / "state" / "transient.db-wal"
    transient.parent.mkdir()
    transient.write_bytes(b"x")
    (tmp_path / "state" / "a.txt").write_bytes(b"a")

    def reader(p):
        transient.unlink(missing_ok=True)
        return p.read_bytes()

    snap = _content_snapshot([tmp_path / "state"], read=reader)

    assert snap[str(tmp_path / "state" / "a.txt")] == b"a"
    assert str(transient) not in snap


@pytest.fixture(autouse=True)
def _real_lore_untouched():
    watched = [REAL_LORE_CONFIG_DIR, *REAL_TRAILHEAD_STATE]
    before = _content_snapshot(watched)
    yield
    assert _content_snapshot(watched) == before, "the real lore config or trailhead core-vault copy was modified"


@pytest.fixture
def env(tmp_path):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("LORE_", "TRAILHEAD_"))}
    fence = {
        "HOME": tmp_path / "home",
        "XDG_CONFIG_HOME": tmp_path / "cfg",
        "XDG_STATE_HOME": tmp_path / "state",
        "XDG_CACHE_HOME": tmp_path / "cache",
        "XDG_DATA_HOME": tmp_path / "data",
    }
    for k, v in fence.items():
        v.mkdir()
        e[k] = str(v)
    e["LORE_GROUPS_DIR"] = str(tmp_path / "groups")
    e["LORE_EMAIL"] = "tester@example.com"
    e["LORE_USER"] = "tester"
    e["LORE_PUBLISH_DISABLE"] = "1"
    return e


def real_lore(args, **kw):
    return subprocess.run([sys.executable, str(LORE_CLI), *args[1:]], cwd=kw["env"]["HOME"], **kw)


def lore(env, *args):
    r = real_lore(["lore", *args], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def write_record(root: Path, name: str, title: str, body: str, kind: str = "blob") -> None:
    (root / kind).mkdir(parents=True, exist_ok=True)
    (root / kind / f"{name}.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "kind": kind,
                "title": title,
                "status": "active",
                "created-at": "2026-06-17T10:00:00Z",
                "updated-at": "2026-06-17T10:00:00Z",
            }
        )
    )
    (root / kind / f"{name}.md").write_text(body + "\n")


def snapshot(root: Path) -> dict[str, bytes | None]:
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


def step(source, env):
    return install_core_vault(source=source, env=env, lore_bin=LORE_CLI, runner=real_lore)


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "src"
    write_record(src, "zebra-notes", "Zebra notes", "striped quadruped savanna")
    return src


@pytest.fixture
def lore_ready(env):
    lore(env, "init")


def vault_ls(env):
    return {v["name"]: v for v in json.loads(lore(env, "vault", "ls", "--json"))}


def test_registered_core_is_searchable_and_listed(source, env, lore_ready, capsys):
    assert step(source, env).outcome == "registered"
    assert "trailhead: core vault: Note: older lore releases refuse" in capsys.readouterr().out
    copy = core_copy_dir(env)
    assert "zebra-notes" in lore(env, "search", "zebra")
    core = vault_ls(env)["core"]
    assert core["scope"] == "read-only" and core["shared"] is False
    assert Path(core["path"]) == copy.resolve()


def test_source_edit_is_invisible_until_the_step_runs_again(source, env, lore_ready):
    step(source, env)
    (source / "blob" / "zebra-notes.json").unlink()
    (source / "blob" / "zebra-notes.md").unlink()
    write_record(source, "okapi-notes", "Okapi notes", "forest giraffe relative")
    assert "okapi" not in lore(env, "search", "okapi")
    assert "zebra-notes" in lore(env, "search", "zebra")
    assert step(source, env).outcome == "refreshed"
    assert "okapi-notes" in lore(env, "search", "okapi")
    assert "zebra-notes" not in lore(env, "search", "zebra")
    assert not (core_copy_dir(env).parent / MARKER_NAME).exists()


def test_second_run_leaves_the_copy_byte_identical_and_lock_free(source, env, lore_ready):
    step(source, env)
    copy = core_copy_dir(env)
    lore(env, "search", "zebra")
    before = snapshot(copy)
    stats = {p: p.stat().st_mtime_ns for p in copy.rglob("*")}
    assert step(source, env).outcome == "up_to_date"
    assert snapshot(copy) == before
    assert {p: p.stat().st_mtime_ns for p in copy.rglob("*")} == stats
    assert not list(copy.rglob(".lore.lock"))


def test_preexisting_core_elsewhere_is_left_registered_and_untouched(source, env, lore_ready, tmp_path):
    other = tmp_path / "other-core"
    write_record(other, "kept-notes", "Kept notes", "unrelated hippo")
    lore(env, "vault", "add", "core", "--scope", "read-only", "--trusted", "--path", str(other))
    before = snapshot(other)
    out = step(source, env)
    assert out.outcome == "declined"
    assert Path(vault_ls(env)["core"]["path"]) == other.resolve()
    assert snapshot(other) == before
    assert not core_copy_dir(env).exists()


# -- trailhead core show, against real lore ---------------------------------


def _show(env, record_id="adr/x"):
    return core_show(record_id, env=env, runner=real_lore, lore_bin=LORE_CLI)


def _adr(root: Path, body: str) -> None:
    write_record(root, "x", "X", body, kind="adr")


def test_core_show_reads_core_never_another_vault(source, env, lore_ready, tmp_path):
    _adr(source, "CORE-BODY-MARK")
    other = tmp_path / "mine"
    _adr(other, "OTHER-BODY-MARK")
    lore(env, "vault", "add", "mine", "--scope", "team", "--path", str(other))
    assert step(source, env).outcome == "registered"
    assert "OTHER-BODY-MARK" in lore(env, "record", "show", "adr/x")
    r = _show(env)
    assert r.code == 0 and "CORE-BODY-MARK" in r.stdout
    assert "OTHER-BODY-MARK" not in r.stdout + r.stderr


def test_core_show_without_a_core_vault_is_unavailable(env, lore_ready, tmp_path):
    other = tmp_path / "mine"
    _adr(other, "OTHER-BODY-MARK")
    lore(env, "vault", "add", "mine", "--scope", "team", "--path", str(other))
    r = _show(env)
    assert r.code == 3 and r.stdout == ""
    assert "trailhead install" in r.stderr and "OTHER-BODY-MARK" not in r.stderr


def test_core_show_with_core_at_another_path_is_unavailable(env, lore_ready, tmp_path):
    other = tmp_path / "other-core"
    _adr(other, "OTHER-BODY-MARK")
    lore(env, "vault", "add", "core", "--scope", "read-only", "--trusted", "--path", str(other))
    r = _show(env)
    assert r.code == 3 and r.stdout == ""
    assert str(other.resolve()) in r.stderr and "OTHER-BODY-MARK" not in r.stderr


def test_core_show_of_a_record_absent_from_core_exits_1(source, env, lore_ready):
    step(source, env)
    r = _show(env, "adr/nope")
    assert r.code == 1 and r.stdout == "" and r.stderr


# -- trailhead core show, through the installed `trailhead` shim ------------


def test_trailhead_shim_shows_a_core_record_from_an_unrelated_cwd(source, env, lore_ready, tmp_path):
    from trailhead import install

    _adr(source, "CORE-BODY-MARK")
    assert step(source, env).outcome == "registered"
    shim_dir = install.build_cli_shims({"camp": LORE_CLI}, env=env)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shim_env = {**env, "PATH": _minimal_path(tmp_path, shim_dir)}

    shown = subprocess.run(
        ["trailhead", "core", "show", "adr/x"], env=shim_env, cwd=elsewhere, capture_output=True, text=True, timeout=60
    )
    refused = subprocess.run(
        ["trailhead", "core", "show", "../x"], env=shim_env, cwd=elsewhere, capture_output=True, text=True, timeout=60
    )

    assert shown.returncode == 0, shown.stderr
    assert "CORE-BODY-MARK" in shown.stdout
    assert refused.returncode == 2
