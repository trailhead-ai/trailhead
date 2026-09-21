"""Behavioral tests for the ``outpost:publish-site`` skill's bundled script.

``publish_site.py`` is the deterministic half of the skill: validate a source
directory, stage it atomically into ``<vault>/sites/<slug>/``, and — unless
told otherwise — run ``lore sync`` before ever printing a success URL. These
tests never touch a real lore vault or config; every run is fenced to a
``tmp_path``-scoped ``XDG_STATE_HOME``, and the ``lore`` binary the sync step
shells out to is a stubbed executable placed on ``PATH``.
"""

from __future__ import annotations

import fcntl
import importlib.util
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import trailhead.paths as trailhead_paths

SCRIPT_PATH = (
    Path(__file__).parent.parent
    / "plugins"
    / "outpost"
    / "skills"
    / "publish-site"
    / "publish_site.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module():
    """Import publish_site.py fresh, for tests that need to patch its internals."""
    spec = importlib.util.spec_from_file_location("publish_site", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_vault(tmp_path: Path, name: str = "acme") -> Path:
    vault = tmp_path / "state" / "lore" / "vaults" / name
    vault.mkdir(parents=True)
    return vault


def _make_workspace(camp_state_dir: Path, group: str = "acme", slug: str = "feat-x") -> Path:
    ws = camp_state_dir / group / "worktrees" / slug
    ws.mkdir(parents=True)
    return ws


def _write_site(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def _write_lore_stub(
    bin_dir: Path,
    *,
    exit_code: int,
    stderr: str = "",
    stdout: str = "",
    record_path: Path | None = None,
) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "lore"
    lines = ["#!/bin/sh"]
    if record_path is not None:
        lines.append(f'echo "$@" > "{record_path}"')
    if stdout:
        lines.append(f'echo "{stdout}"')
    if stderr:
        lines.append(f'echo "{stderr}" >&2')
    lines.append(f"exit {exit_code}")
    stub.write_text("\n".join(lines) + "\n")
    stub.chmod(0o755)


def _url_lines(stdout: str) -> list[str]:
    """The URL lines in *stdout* — sync's own output streams through it too."""
    return [line.strip() for line in stdout.splitlines() if line.strip().startswith("http://")]


def _env(
    tmp_path: Path, *, path: str = "/usr/bin:/bin", camp_state_dir: Path | None = None
) -> dict[str, str]:
    env = {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
        "PATH": path,
    }
    if camp_state_dir is not None:
        env["CAMP_STATE_DIR"] = str(camp_state_dir)
    return env


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_rejects_missing_index_html(tmp_path):
    vault = _make_vault(tmp_path)
    source = _write_site(tmp_path / "src", {"style.css": "body{}"})

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "index.html" in result.stderr
    assert not (vault / "sites").exists() or not any((vault / "sites").iterdir())


def test_rejects_symlink_in_payload(tmp_path):
    vault = _make_vault(tmp_path)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    (source / "evil").symlink_to(source / "index.html")

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "symlink" in result.stderr


@pytest.mark.parametrize(
    ("relpath", "kind"),
    [
        (".git", "dir"),          # a full nested repo publishes as a gitlink
        (".git", "file"),         # the gitlink file a submodule checkout leaves
        ("sub/.git", "dir"),      # the fold applies at depth, and is pinpointed
        (".GIT", "dir"),          # case-insensitive fs: git's own guard skips it
        ("sub/.Git", "file"),     # mixed case, nested — both folds at once
    ],
    ids=["dir", "gitlink-file", "nested-dir", "uppercase", "mixedcase-nested"],
)
def test_rejects_a_git_entry_in_payload(tmp_path, relpath, kind):
    """Any `.git` entry, at any depth and in any case, is refused before a write.

    `lore sync`'s bare `git add -A` treats a nested `.git` as a gitlink or fails
    outright, and on a case-insensitive filesystem git's own `.git`-name
    protection skips a `.GIT`, so the site would publish and sync clean while the
    directory never reaches a teammate. The refusal must name the offending path
    — not just the string `.git` — and must leave the vault untouched.
    """
    vault = _make_vault(tmp_path)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    target = source / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == "dir":
        target.mkdir()
        (target / "config").write_text("[core]\n")
    else:
        target.write_text("gitdir: ../.git/modules/sub\n")

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert str(Path(relpath)) in result.stderr
    assert not (vault / "sites" / "mysite").exists()


def test_rejects_bad_slug(tmp_path):
    vault = _make_vault(tmp_path)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "Bad_Slug!", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "slug" in result.stderr


def test_warns_above_5mb_but_still_publishes(tmp_path):
    vault = _make_vault(tmp_path)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    big = source / "big.bin"
    with open(big, "wb") as f:
        f.seek(5 * 1024 * 1024)
        f.write(b"\0")

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    assert "5 MB" in result.stderr
    assert (vault / "sites" / "mysite" / "big.bin").exists()


# ---------------------------------------------------------------------------
# Publish / republish / overwrite
# ---------------------------------------------------------------------------


def test_publish_into_empty_vault_mirrors_source(tmp_path):
    vault = _make_vault(tmp_path)
    source = _write_site(
        tmp_path / "src",
        {"index.html": "<html>v1</html>", "style.css": "body{}", "sub/page.html": "<p>sub</p>"},
    )

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    target = vault / "sites" / "mysite"
    assert (target / "index.html").read_text() == "<html>v1</html>"
    assert (target / "style.css").read_text() == "body{}"
    assert (target / "sub" / "page.html").read_text() == "<p>sub</p>"


def test_republish_without_overwrite_refuses_and_prints_summary(tmp_path):
    vault = _make_vault(tmp_path)
    source_dir = tmp_path / "src"
    _write_site(source_dir, {"index.html": "<html>v1</html>", "style.css": "body{}"})
    first = _run(
        [str(source_dir), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )
    assert first.returncode == 0, first.stderr

    # New source tree: index.html changed, style.css removed, extra.html added.
    shutil.rmtree(source_dir)
    _write_site(source_dir, {"index.html": "<html>v2</html>", "extra.html": "<p>new</p>"})

    result = _run(
        [str(source_dir), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "--overwrite" in result.stderr
    assert "add: extra.html" in result.stderr
    assert "change: index.html" in result.stderr
    assert "remove: style.css" in result.stderr

    # Target untouched by the refused republish.
    target = vault / "sites" / "mysite"
    assert (target / "index.html").read_text() == "<html>v1</html>"
    assert (target / "style.css").exists()
    assert not (target / "extra.html").exists()


def test_overwrite_replaces_wholesale_including_deletions(tmp_path):
    vault = _make_vault(tmp_path)
    source_dir = tmp_path / "src"
    _write_site(source_dir, {"index.html": "<html>v1</html>", "style.css": "body{}"})
    first = _run(
        [str(source_dir), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )
    assert first.returncode == 0, first.stderr

    shutil.rmtree(source_dir)
    _write_site(source_dir, {"index.html": "<html>v2</html>", "extra.html": "<p>new</p>"})

    result = _run(
        [str(source_dir), "mysite", "--vault-path", str(vault), "--no-sync", "--overwrite"],
        _env(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    target = vault / "sites" / "mysite"
    assert (target / "index.html").read_text() == "<html>v2</html>"
    assert (target / "extra.html").read_text() == "<p>new</p>"
    assert not (target / "style.css").exists()


def test_mid_stage_failure_leaves_no_partial_site_dir(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    source = _write_site(
        tmp_path / "src", {"index.html": "<html></html>", "extra.html": "<p>x</p>"}
    )

    mod = _load_module()
    real_copy2 = shutil.copy2
    calls = {"n": 0}

    def _flaky_copy2(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated disk failure")
        return real_copy2(src, dst, *a, **kw)

    monkeypatch.setattr(mod.shutil, "copy2", _flaky_copy2)

    rc = mod.main(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        env=_env(tmp_path),
    )

    assert rc != 0
    assert not (vault / "sites" / "mysite").exists()
    sites_dir = vault / "sites"
    assert not sites_dir.exists() or list(sites_dir.iterdir()) == []


def test_keyboard_interrupt_mid_stage_leaves_no_partial_site_dir(tmp_path, monkeypatch):
    """A Ctrl-C mid-copy must not leave a ``.slug.stage-XXXX`` staging tree
    behind for the next `lore sync` to commit — the cleanup path must run on
    any interrupt, not just ordinary exceptions."""
    vault = _make_vault(tmp_path)
    source = _write_site(
        tmp_path / "src", {"index.html": "<html></html>", "extra.html": "<p>x</p>"}
    )

    mod = _load_module()
    real_copy2 = shutil.copy2
    calls = {"n": 0}

    def _interrupting_copy2(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt()
        return real_copy2(src, dst, *a, **kw)

    monkeypatch.setattr(mod.shutil, "copy2", _interrupting_copy2)

    with pytest.raises(KeyboardInterrupt):
        mod.main(
            [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
            env=_env(tmp_path),
        )

    assert not (vault / "sites" / "mysite").exists()
    sites_dir = vault / "sites"
    assert not sites_dir.exists() or list(sites_dir.iterdir()) == []


def test_keyboard_interrupt_during_swap_restores_the_previous_site(tmp_path, monkeypatch):
    """A Ctrl-C between the two renames must still restore the previous site
    rather than leaving the target missing."""
    vault = _make_vault(tmp_path)
    source_dir = tmp_path / "src"
    _write_site(source_dir, {"index.html": "<html>v1</html>", "style.css": "body{}"})
    first = _run(
        [str(source_dir), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )
    assert first.returncode == 0, first.stderr

    shutil.rmtree(source_dir)
    _write_site(source_dir, {"index.html": "<html>v2</html>"})

    mod = _load_module()
    real_rename = mod.os.rename
    calls = {"n": 0}

    def _interrupting_rename(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # the swap-in of the freshly staged tree
            raise KeyboardInterrupt()
        return real_rename(src, dst, *a, **kw)

    monkeypatch.setattr(mod.os, "rename", _interrupting_rename)

    with pytest.raises(KeyboardInterrupt):
        mod.main(
            [str(source_dir), "mysite", "--vault-path", str(vault), "--no-sync", "--overwrite"],
            env=_env(tmp_path),
        )

    target = vault / "sites" / "mysite"
    assert (target / "index.html").read_text() == "<html>v1</html>"
    assert (target / "style.css").read_text() == "body{}"
    assert [p.name for p in (vault / "sites").iterdir()] == ["mysite"]


def test_overwrite_leaves_exactly_the_new_tree_and_no_staging_leftovers(tmp_path):
    """A successful overwrite leaves the sites dir holding only the site — no
    staging directory and no set-aside copy of the replaced tree."""
    vault = _make_vault(tmp_path)
    source_dir = tmp_path / "src"
    _write_site(source_dir, {"index.html": "<html>v1</html>", "style.css": "body{}"})
    first = _run(
        [str(source_dir), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )
    assert first.returncode == 0, first.stderr

    shutil.rmtree(source_dir)
    _write_site(source_dir, {"index.html": "<html>v2</html>"})

    result = _run(
        [str(source_dir), "mysite", "--vault-path", str(vault), "--no-sync", "--overwrite"],
        _env(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    sites_dir = vault / "sites"
    assert [p.name for p in sites_dir.iterdir()] == ["mysite"]
    assert (sites_dir / "mysite" / "index.html").read_text() == "<html>v2</html>"


def test_failed_overwrite_leaves_the_previous_site_intact(tmp_path, monkeypatch):
    """The replace must never expose a partial site: if the swap fails after the
    live tree has been moved aside, the previous site is put back and still
    serves."""
    vault = _make_vault(tmp_path)
    source_dir = tmp_path / "src"
    _write_site(source_dir, {"index.html": "<html>v1</html>", "style.css": "body{}"})
    first = _run(
        [str(source_dir), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )
    assert first.returncode == 0, first.stderr

    shutil.rmtree(source_dir)
    _write_site(source_dir, {"index.html": "<html>v2</html>"})

    mod = _load_module()
    real_rename = mod.os.rename
    calls = {"n": 0}

    def _flaky_rename(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # the swap-in of the freshly staged tree
            raise OSError("simulated rename failure")
        return real_rename(src, dst, *a, **kw)

    monkeypatch.setattr(mod.os, "rename", _flaky_rename)

    rc = mod.main(
        [str(source_dir), "mysite", "--vault-path", str(vault), "--no-sync", "--overwrite"],
        env=_env(tmp_path),
    )

    assert rc != 0
    target = vault / "sites" / "mysite"
    assert (target / "index.html").read_text() == "<html>v1</html>"
    assert (target / "style.css").read_text() == "body{}"
    assert [p.name for p in (vault / "sites").iterdir()] == ["mysite"]


# ---------------------------------------------------------------------------
# Vault-path and vault-name preconditions
# ---------------------------------------------------------------------------


def test_missing_vault_path_errors_without_creating_it(tmp_path):
    """A typo'd --vault-path must not be created as a new tree beside the real
    vaults."""
    _make_vault(tmp_path)  # establishes the real vaults root under tmp_path
    typo = tmp_path / "state" / "lore" / "vaults" / "acmee"
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "mysite", "--vault-path", str(typo), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "not a directory" in result.stderr or "does not exist" in result.stderr
    assert not typo.exists()


def test_vault_path_that_is_a_file_errors(tmp_path):
    _make_vault(tmp_path)
    not_a_dir = tmp_path / "state" / "lore" / "vaults" / "afile"
    not_a_dir.write_text("x")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "mysite", "--vault-path", str(not_a_dir), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "not a directory" in result.stderr
    assert "Traceback" not in result.stderr
    assert not_a_dir.is_file()


def test_vault_name_must_match_the_vault_path_basename(tmp_path):
    """--vault names the vault sync targets and --vault-path names the tree that
    is written; a mismatch would sync a vault the site was never written into."""
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--vault", "other", "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "other" in result.stderr and "acme" in result.stderr
    assert not (vault / "sites" / "mysite").exists()


def test_vault_directory_name_must_be_url_safe(tmp_path):
    """The vault path's basename becomes the first URL segment, which the daemon
    gates on ^[a-z0-9][a-z0-9._-]*$ — a name that fails it would 404."""
    vault = _make_vault(tmp_path, name="Acme Vault")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "Acme Vault" in result.stderr
    assert not (vault / "sites").exists()


# ---------------------------------------------------------------------------
# Payload path segments (mirrors the daemon's serve-time segment rule)
# ---------------------------------------------------------------------------


def test_rejects_file_name_containing_double_dot(tmp_path):
    """A serve-time segment rule rejects any segment CONTAINING '..', so a file
    like notes..v2.html would publish and then 404 — reject it at publish."""
    vault = _make_vault(tmp_path)
    source = _write_site(
        tmp_path / "src", {"index.html": "<html></html>", "notes..v2.html": "<p>x</p>"}
    )

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "notes..v2.html" in result.stderr
    assert not (vault / "sites" / "mysite").exists()


def test_rejects_directory_name_containing_double_dot(tmp_path):
    vault = _make_vault(tmp_path)
    source = _write_site(
        tmp_path / "src", {"index.html": "<html></html>", "a..b/page.html": "<p>x</p>"}
    )

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "a..b" in result.stderr
    assert not (vault / "sites" / "mysite").exists()


def test_rejects_file_name_containing_a_backslash(tmp_path):
    vault = _make_vault(tmp_path)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    (source / "back\\slash.html").write_text("<p>x</p>")

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "back\\slash.html" in result.stderr
    assert not (vault / "sites" / "mysite").exists()


def test_non_direct_child_vault_path_errors(tmp_path):
    _make_vault(tmp_path)  # establishes the real vaults root under tmp_path
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "mysite", "--vault-path", str(foreign), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert "vaults root" in result.stderr
    assert not (foreign / "sites").exists()


# ---------------------------------------------------------------------------
# Sync gate
# ---------------------------------------------------------------------------


def test_sync_success_prints_url_only_on_exit_0(tmp_path):
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    bin_dir = tmp_path / "bin"
    _write_lore_stub(bin_dir, exit_code=0)

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--vault", "acme"],
        _env(tmp_path, path=f"{bin_dir}:/usr/bin:/bin"),
    )

    assert result.returncode == 0, result.stderr
    assert _url_lines(result.stdout) == ["http://127.0.0.1:7314/acme/mysite/"]


def test_sync_output_streams_through_to_the_console(tmp_path):
    """`lore sync`'s own stdout/stderr reach the operator rather than being
    captured and dropped — sync can degrade (offline) and still exit 0, so its
    output is the only signal that the remote was actually reached."""
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    bin_dir = tmp_path / "bin"
    _write_lore_stub(
        bin_dir, exit_code=0, stdout="offline: committed locally", stderr="warning: no remote"
    )

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--vault", "acme"],
        _env(tmp_path, path=f"{bin_dir}:/usr/bin:/bin"),
    )

    assert result.returncode == 0, result.stderr
    assert "offline: committed locally" in result.stdout
    assert "warning: no remote" in result.stderr
    assert _url_lines(result.stdout) == ["http://127.0.0.1:7314/acme/mysite/"]


def test_lore_missing_from_path_fails_the_sync_gate(tmp_path):
    """No `lore` on PATH: the publish landed locally, but the sync gate fails
    cleanly — nonzero exit, NOT-synced messaging, and no success URL."""
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--vault", "acme"],
        _env(tmp_path, path=str(tmp_path / "empty-bin")),
    )

    assert result.returncode != 0
    assert _url_lines(result.stdout) == []
    assert "NOT synced" in result.stderr
    assert "lore" in result.stderr
    assert (vault / "sites" / "mysite" / "index.html").exists()


def test_sync_failure_no_success_url_nonzero_exit(tmp_path):
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    bin_dir = tmp_path / "bin"
    _write_lore_stub(bin_dir, exit_code=1, stderr="sync boom")

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--vault", "acme"],
        _env(tmp_path, path=f"{bin_dir}:/usr/bin:/bin"),
    )

    assert result.returncode != 0
    assert "http://" not in result.stdout
    assert "NOT synced" in result.stderr
    assert "sync boom" in result.stderr
    # Publish itself already happened locally.
    assert (vault / "sites" / "mysite" / "index.html").exists()


def test_no_sync_flag_publishes_but_prints_warning_not_url(tmp_path):
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),  # no `lore` stub on PATH — must never be invoked
    )

    assert result.returncode == 0, result.stderr
    assert "http://" not in result.stdout
    assert "NOT synced" in result.stdout
    assert (vault / "sites" / "mysite" / "index.html").exists()


def test_sites_port_override_and_trailing_slash_form(tmp_path):
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    bin_dir = tmp_path / "bin"
    _write_lore_stub(bin_dir, exit_code=0)

    result = _run(
        [
            str(source),
            "mysite",
            "--vault-path",
            str(vault),
            "--vault",
            "acme",
            "--sites-port",
            "9999",
        ],
        _env(tmp_path, path=f"{bin_dir}:/usr/bin:/bin"),
    )

    assert result.returncode == 0, result.stderr
    assert _url_lines(result.stdout) == ["http://127.0.0.1:9999/acme/mysite/"]


def test_vault_name_omitted_syncs_bare(tmp_path):
    """No --vault given (the default-floor case): sync runs with no --vault flag."""
    vault = _make_vault(tmp_path, name="default")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    bin_dir = tmp_path / "bin"
    record = tmp_path / "lore-argv.txt"
    _write_lore_stub(bin_dir, exit_code=0, record_path=record)

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault)],
        _env(tmp_path, path=f"{bin_dir}:/usr/bin:/bin"),
    )

    assert result.returncode == 0, result.stderr
    assert record.read_text().strip() == "sync"


def test_vault_name_given_syncs_scoped(tmp_path):
    """A real resolved vault name: sync runs scoped with --vault <name>."""
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    bin_dir = tmp_path / "bin"
    record = tmp_path / "lore-argv.txt"
    _write_lore_stub(bin_dir, exit_code=0, record_path=record)

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--vault", "acme"],
        _env(tmp_path, path=f"{bin_dir}:/usr/bin:/bin"),
    )

    assert result.returncode == 0, result.stderr
    assert record.read_text().strip() == "sync --vault acme"


# ---------------------------------------------------------------------------
# End-to-end payload (spec's End-to-end proof acceptance criterion)
# ---------------------------------------------------------------------------

_MULTI_PAGE_SITE = {
    "index.html": (
        "<html><head><title>Docs Home</title>"
        '<link rel="stylesheet" href="style.css"></head>'
        '<body><h1>Docs Home</h1><a href="about.html">About</a></body></html>'
    ),
    "about.html": (
        "<html><head><title>About</title>"
        '<link rel="stylesheet" href="style.css"></head>'
        '<body><h1>About</h1><a href="index.html">Home</a></body></html>'
    ),
    "style.css": "body { font-family: sans-serif; }",
}


def test_publish_multi_page_site_mirrors_source_and_syncs(tmp_path):
    """Two HTML pages linking each other plus one CSS asset, the exact shape
    the spec's end-to-end acceptance criterion names, publishes cleanly into a
    real-shaped tmp vault and only prints the success URL once sync succeeds.
    """
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", _MULTI_PAGE_SITE)
    bin_dir = tmp_path / "bin"
    _write_lore_stub(bin_dir, exit_code=0)

    result = _run(
        [str(source), "docs", "--vault-path", str(vault), "--vault", "acme"],
        _env(tmp_path, path=f"{bin_dir}:/usr/bin:/bin"),
    )

    assert result.returncode == 0, result.stderr
    assert _url_lines(result.stdout) == ["http://127.0.0.1:7314/acme/docs/"]

    target = vault / "sites" / "docs"
    assert target.is_dir()
    assert (target / "index.html").read_text() == _MULTI_PAGE_SITE["index.html"]
    assert (target / "about.html").read_text() == _MULTI_PAGE_SITE["about.html"]
    assert (target / "style.css").read_text() == _MULTI_PAGE_SITE["style.css"]
    # The relative links between the two pages and the shared stylesheet
    # survive the copy byte-for-byte — nothing rewrites them.
    assert 'href="style.css"' in (target / "index.html").read_text()
    assert 'href="about.html"' in (target / "index.html").read_text()
    assert 'href="index.html"' in (target / "about.html").read_text()


def test_rejects_symlink_crafted_into_multi_page_site_payload(tmp_path):
    """The same multi-page payload, with a symlink manually placed inside it,
    is refused at publish time before anything is written into the vault.
    """
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", _MULTI_PAGE_SITE)
    (source / "shortcut.html").symlink_to(source / "index.html")

    result = _run(
        [str(source), "docs", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),  # no `lore` stub on PATH — publish must fail before sync
    )

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert not (vault / "sites" / "docs").exists()


# ---------------------------------------------------------------------------
# Workspace publish target
# ---------------------------------------------------------------------------


def test_workspace_publish_writes_tree_and_prints_group_slug_key(tmp_path):
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    source = _write_site(tmp_path / "src", {"index.html": "<html>v1</html>"})

    result = _run(
        [str(source), "mysite", "--workspace-path", str(ws)],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode == 0, result.stderr
    assert (ws / "sites" / "mysite" / "index.html").read_text() == "<html>v1</html>"
    assert _url_lines(result.stdout) == ["http://127.0.0.1:7314/acme-feat-x/mysite/"]


def test_workspace_success_output_distinguishable_from_vault(tmp_path):
    """Sharing happens by copying printed text out of a terminal, with no
    cockpit badge in the picture — the workspace output must name the
    workspace it dies with and say the link is machine-local; the vault
    output must do neither."""
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    vault = _make_vault(tmp_path, name="acme")
    source_ws = _write_site(tmp_path / "src-ws", {"index.html": "<html></html>"})
    source_vault = _write_site(tmp_path / "src-vault", {"index.html": "<html></html>"})
    env = _env(tmp_path, camp_state_dir=camp_state)

    ws_result = _run([str(source_ws), "mysite", "--workspace-path", str(ws)], env)
    vault_result = _run(
        [str(source_vault), "mysite", "--vault-path", str(vault), "--no-sync"], env
    )

    assert ws_result.returncode == 0, ws_result.stderr
    assert vault_result.returncode == 0, vault_result.stderr
    assert "acme-feat-x" in ws_result.stdout
    assert "acme-feat-x" not in vault_result.stdout
    assert "this machine" in ws_result.stdout.lower()
    assert "this machine" not in vault_result.stdout.lower()


def test_workspace_target_skips_sync_vault_target_runs_it(tmp_path):
    """One flag varied (--workspace-path vs --vault-path), two behaviours: the
    workspace target never invokes `lore sync`; the vault target does."""
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    vault = _make_vault(tmp_path, name="acme")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    bin_dir = tmp_path / "bin"
    record = tmp_path / "lore-argv.txt"
    _write_lore_stub(bin_dir, exit_code=0, record_path=record)
    env = _env(tmp_path, path=f"{bin_dir}:/usr/bin:/bin", camp_state_dir=camp_state)

    ws_result = _run([str(source), "mysite", "--workspace-path", str(ws)], env)
    assert ws_result.returncode == 0, ws_result.stderr
    assert not record.exists()

    vault_result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--vault", "acme"], env
    )
    assert vault_result.returncode == 0, vault_result.stderr
    assert record.exists()


def test_workspace_publish_touches_no_vault_root(tmp_path):
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    vaults_root = tmp_path / "state" / "lore" / "vaults"
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "mysite", "--workspace-path", str(ws)],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode == 0, result.stderr
    assert not vaults_root.exists()


def test_workspace_path_outside_worktrees_root_refused(tmp_path):
    camp_state = tmp_path / "camp-state"
    camp_state.mkdir(parents=True)
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "mysite", "--workspace-path", str(foreign)],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode != 0
    assert not (foreign / "sites").exists()


def test_workspace_path_symlink_escape_refused(tmp_path):
    """A --workspace-path that structurally LOOKS like a legal workspace path
    but resolves outside the worktrees root via a symlink must be refused —
    judged on whether the caller can COMPUTE the escaping path, not on
    whether the raw string sits outside the tree."""
    camp_state = tmp_path / "camp-state"
    _make_workspace(camp_state, group="acme", slug="feat-x")
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    evil_link = camp_state / "acme" / "worktrees" / "escaped"
    evil_link.symlink_to(foreign)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [str(source), "mysite", "--workspace-path", str(evil_link)],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode != 0
    assert not (foreign / "sites").exists()


def test_both_targets_given_refuses(tmp_path):
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    vault = _make_vault(tmp_path)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run(
        [
            str(source),
            "mysite",
            "--vault-path",
            str(vault),
            "--workspace-path",
            str(ws),
            "--no-sync",
        ],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode != 0
    assert not (vault / "sites").exists()
    assert not (ws / "sites").exists()


def test_neither_target_given_refuses(tmp_path):
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})

    result = _run([str(source), "mysite"], _env(tmp_path))

    assert result.returncode != 0
    # A clean refusal, not an unhandled crash falling through to the vault
    # branch with a None --vault-path.
    assert "Traceback" not in result.stderr
    assert "required" in result.stderr


def test_workspace_target_rejects_symlink_in_payload(tmp_path):
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    (source / "evil").symlink_to(source / "index.html")

    result = _run(
        [str(source), "mysite", "--workspace-path", str(ws)],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert not (ws / "sites" / "mysite").exists()


def test_workspace_target_rejects_missing_index(tmp_path):
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state)
    source = _write_site(tmp_path / "src", {"style.css": "body{}"})

    result = _run(
        [str(source), "mysite", "--workspace-path", str(ws)],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode != 0
    assert "index.html" in result.stderr


def test_workspace_target_rejects_nested_git(tmp_path):
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("[core]\n")

    result = _run(
        [str(source), "mysite", "--workspace-path", str(ws)],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode != 0
    assert ".git" in result.stderr
    assert not (ws / "sites" / "mysite").exists()


def test_workspace_target_republish_without_overwrite_prints_preview(tmp_path):
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state)
    source_dir = tmp_path / "src"
    _write_site(source_dir, {"index.html": "<html>v1</html>", "style.css": "body{}"})
    env = _env(tmp_path, camp_state_dir=camp_state)

    first = _run([str(source_dir), "mysite", "--workspace-path", str(ws)], env)
    assert first.returncode == 0, first.stderr

    shutil.rmtree(source_dir)
    _write_site(source_dir, {"index.html": "<html>v2</html>", "extra.html": "<p>new</p>"})

    result = _run([str(source_dir), "mysite", "--workspace-path", str(ws)], env)

    assert result.returncode != 0
    assert "--overwrite" in result.stderr
    assert "add: extra.html" in result.stderr
    assert "change: index.html" in result.stderr
    assert "remove: style.css" in result.stderr


def test_camp_state_root_agrees_with_trailhead_paths_state_dir(tmp_path):
    """The hand-rolled camp state-root mirror must never drift from
    ``trailhead.paths.state_dir("camp")`` — a divergence would compute a
    workspace root that disagrees with the directory camp's own observer
    walks, and a workspace publish would 'succeed' into a directory nothing
    scans."""
    mod = _load_module()

    matrix = [
        {"CAMP_STATE_DIR": str(tmp_path / "custom-camp"), "HOME": str(tmp_path / "home")},
        {"XDG_STATE_HOME": str(tmp_path / "xdg-state"), "HOME": str(tmp_path / "home")},
        {"HOME": str(tmp_path / "home")},
    ]

    for env in matrix:
        expected = trailhead_paths.state_dir("camp", env=env, platform=sys.platform)
        actual = mod._camp_state_root(env)
        assert actual == expected, env


def test_workspace_directory_disappearing_before_rename_refuses_cleanly(tmp_path, monkeypatch):
    """A publish whose workspace directory vanishes after validation but
    before the swap-in rename — the teardown race — must refuse and leave no
    tree behind, driven deterministically by removing the directory while
    the lock is held."""
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    source = _write_site(
        tmp_path / "src", {"index.html": "<html></html>", "extra.html": "<p>x</p>"}
    )

    mod = _load_module()
    real_copy2 = shutil.copy2
    calls = {"n": 0}

    def _vanish_copy2(src, dst, *a, **kw):
        calls["n"] += 1
        result = real_copy2(src, dst, *a, **kw)
        if calls["n"] == 1:
            shutil.rmtree(ws)
        return result

    monkeypatch.setattr(mod.shutil, "copy2", _vanish_copy2)

    rc = mod.main(
        [str(source), "mysite", "--workspace-path", str(ws)],
        env=_env(tmp_path, camp_state_dir=camp_state),
    )

    assert rc != 0
    assert not ws.exists()


def test_publish_blocks_while_lock_held_and_proceeds_after_release(tmp_path):
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    lock_path = ws.parent / f"{ws.name}.lock"

    holder_fd = open(lock_path, "w")
    fcntl.flock(holder_fd.fileno(), fcntl.LOCK_EX)

    result_holder: dict[str, subprocess.CompletedProcess] = {}

    def _run_publish():
        result_holder["cp"] = _run(
            [str(source), "mysite", "--workspace-path", str(ws)],
            _env(tmp_path, camp_state_dir=camp_state),
        )

    t = threading.Thread(target=_run_publish)
    t.start()
    time.sleep(0.5)
    try:
        assert not (ws / "sites" / "mysite").exists()  # still blocked on the lock
    finally:
        fcntl.flock(holder_fd.fileno(), fcntl.LOCK_UN)
        holder_fd.close()
    t.join(timeout=10)

    assert result_holder["cp"].returncode == 0, result_holder["cp"].stderr
    assert (ws / "sites" / "mysite" / "index.html").exists()


def test_lock_reaped_and_recreated_while_waiting_does_not_orphan(tmp_path):
    """A lockfile unlinked and re-created (camp's own reap-then-recreate
    shape) while a publish blocks on it must not hand the publish an
    orphaned inode — it must retry on the fresh file and proceed."""
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    lock_path = ws.parent / f"{ws.name}.lock"

    holder_fd = open(lock_path, "w")
    fcntl.flock(holder_fd.fileno(), fcntl.LOCK_EX)

    result_holder: dict[str, subprocess.CompletedProcess] = {}

    def _run_publish():
        result_holder["cp"] = _run(
            [str(source), "mysite", "--workspace-path", str(ws)],
            _env(tmp_path, camp_state_dir=camp_state),
        )

    t = threading.Thread(target=_run_publish)
    t.start()
    time.sleep(0.5)
    # The publish must actually still be blocked at this point — otherwise
    # the reap/recreate below exercises nothing, and success at the end would
    # be trivially true whether or not the lock (or its retry loop) does
    # anything at all.
    assert not (ws / "sites" / "mysite").exists()

    lock_path.unlink()
    lock_path.write_text("")  # a fresh inode at the same path
    fcntl.flock(holder_fd.fileno(), fcntl.LOCK_UN)
    holder_fd.close()

    t.join(timeout=10)

    assert result_holder["cp"].returncode == 0, result_holder["cp"].stderr
    assert (ws / "sites" / "mysite" / "index.html").exists()


def test_two_concurrent_publishes_leave_exactly_one_whole_site(tmp_path):
    """Two concurrent publishes to the same workspace slug must leave the
    served directory as exactly one whole site — never a merged or partial
    tree. The atomic stage-and-replace is expected to give this for free on
    a workspace target as it does on a vault one; untested is not the same
    as safe."""
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    src_a = _write_site(tmp_path / "src-a", {"index.html": "<html>A</html>", "a.txt": "A"})
    src_b = _write_site(tmp_path / "src-b", {"index.html": "<html>B</html>", "b.txt": "B"})
    env = _env(tmp_path, camp_state_dir=camp_state)

    results: dict[str, subprocess.CompletedProcess] = {}

    def _publish(name: str, src: Path):
        results[name] = _run(
            [str(src), "mysite", "--workspace-path", str(ws), "--overwrite"], env
        )

    t1 = threading.Thread(target=_publish, args=("a", src_a))
    t2 = threading.Thread(target=_publish, args=("b", src_b))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert results["a"].returncode == 0, results["a"].stderr
    assert results["b"].returncode == 0, results["b"].stderr

    target = ws / "sites" / "mysite"
    assert [p.name for p in (ws / "sites").iterdir()] == ["mysite"]
    content = (target / "index.html").read_text()
    assert content in ("<html>A</html>", "<html>B</html>")
    if content == "<html>A</html>":
        assert (target / "a.txt").exists() and not (target / "b.txt").exists()
    else:
        assert (target / "b.txt").exists() and not (target / "a.txt").exists()


# ---------------------------------------------------------------------------
# Write-side containment: a symlinked sites/ directory must never be followed
# ---------------------------------------------------------------------------


def test_workspace_sites_symlink_escape_refused(tmp_path):
    """A workspace's own agent can create `sites` as a symlink pointing anywhere.
    Following it would publish outside the workspace entirely while reporting
    success — the write-side twin of the escape the serving layer already
    refuses."""
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ws / "sites").symlink_to(elsewhere)
    source = _write_site(tmp_path / "src", {"index.html": "<html>payload</html>"})

    result = _run(
        [str(source), "mysite", "--workspace-path", str(ws)],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode == 1
    assert "is a symlink" in result.stderr
    assert list(elsewhere.iterdir()) == []
    assert _url_lines(result.stdout) == []


def test_workspace_sites_symlink_into_sibling_workspace_refused(tmp_path):
    """The sibling-workspace case specifically: one workstream's agent must not
    be able to inject files into another's live worktree."""
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    victim = _make_workspace(camp_state, group="acme", slug="feat-y")
    (ws / "sites").symlink_to(victim)
    source = _write_site(tmp_path / "src", {"index.html": "<html>payload</html>"})

    result = _run(
        [str(source), "mysite", "--workspace-path", str(ws)],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode == 1
    assert list(victim.iterdir()) == []


def test_vault_sites_symlink_escape_refused(tmp_path):
    """The vault target carries the identical unguarded pattern."""
    vault = _make_vault(tmp_path, name="acme")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (vault / "sites").symlink_to(elsewhere)
    source = _write_site(tmp_path / "src", {"index.html": "<html>payload</html>"})

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode == 1
    assert "is a symlink" in result.stderr
    assert list(elsewhere.iterdir()) == []


def test_workspace_slug_symlink_escape_refused(tmp_path):
    """One level down: `sites/<slug>` itself symlinked out of the tree."""
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ws / "sites").mkdir()
    (ws / "sites" / "mysite").symlink_to(elsewhere)
    source = _write_site(tmp_path / "src", {"index.html": "<html>payload</html>"})

    result = _run(
        [str(source), "mysite", "--workspace-path", str(ws), "--overwrite"],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode == 1
    assert list(elsewhere.iterdir()) == []


def test_workspace_publish_still_works_with_a_real_sites_directory(tmp_path):
    """The containment check must not refuse the ordinary case: a real
    pre-existing `sites/` directory publishes exactly as before."""
    camp_state = tmp_path / "camp-state"
    ws = _make_workspace(camp_state, group="acme", slug="feat-x")
    (ws / "sites").mkdir()
    source = _write_site(tmp_path / "src", {"index.html": "<html>v1</html>"})

    result = _run(
        [str(source), "mysite", "--workspace-path", str(ws)],
        _env(tmp_path, camp_state_dir=camp_state),
    )

    assert result.returncode == 0, result.stderr
    assert (ws / "sites" / "mysite" / "index.html").read_text() == "<html>v1</html>"
