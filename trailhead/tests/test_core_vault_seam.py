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

from trailhead.core_vault import MARKER_NAME, core_copy_dir, install_core_vault

pytestmark = pytest.mark.seam_real

REPO_ROOT = Path(__file__).resolve().parents[2]
LORE_CLI = REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "cli" / "lore"
REAL_LORE_DIRS = [Path.home() / ".config" / "lore", Path.home() / ".local" / "state" / "lore"]


def _mtimes(roots):
    out = {}
    for r in roots:
        if r.exists():
            for p in [r, *r.rglob("*")]:
                out[str(p)] = p.stat().st_mtime_ns
    return out


@pytest.fixture(autouse=True)
def _real_lore_untouched():
    before = _mtimes(REAL_LORE_DIRS)
    yield
    assert _mtimes(REAL_LORE_DIRS) == before, "the real lore config/state was modified"


@pytest.fixture
def env(tmp_path):
    e = {k: v for k, v in os.environ.items() if not k.startswith("LORE_")}
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
    return subprocess.run(
        [sys.executable, str(LORE_CLI), *args[1:]], cwd=kw["env"]["HOME"], **{k: v for k, v in kw.items()}
    )


def lore(env, *args):
    r = real_lore(["lore", *args], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def write_record(root: Path, name: str, title: str, body: str) -> None:
    (root / "blob").mkdir(parents=True, exist_ok=True)
    (root / "blob" / f"{name}.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "kind": "blob",
                "title": title,
                "status": "active",
                "created-at": "2026-06-17T10:00:00Z",
                "updated-at": "2026-06-17T10:00:00Z",
            }
        )
    )
    (root / "blob" / f"{name}.md").write_text(body + "\n")


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


def test_registered_core_is_searchable_and_listed(source, env, lore_ready):
    assert step(source, env).outcome == "registered"
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
