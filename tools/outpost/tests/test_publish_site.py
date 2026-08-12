"""Behavioral tests for the ``outpost:publish-site`` skill's bundled script.

``publish_site.py`` is the deterministic half of the skill: validate a source
directory, stage it atomically into ``<vault>/sites/<slug>/``, and — unless
told otherwise — run ``lore sync`` before ever printing a success URL. These
tests never touch a real lore vault or config; every run is fenced to a
``tmp_path``-scoped ``XDG_STATE_HOME``, and the ``lore`` binary the sync step
shells out to is a stubbed executable placed on ``PATH``.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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


def _env(tmp_path: Path, *, path: str = "/usr/bin:/bin") -> dict[str, str]:
    return {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
        "PATH": path,
    }


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


def test_rejects_nested_git_directory_in_payload(tmp_path):
    """A payload containing a ``.git`` directory would publish as a gitlink (or
    a fatal ``git add -A``) once `lore sync`'s bare ``git add -A`` reaches it —
    reject it at publish, before anything is written into the vault."""
    vault = _make_vault(tmp_path)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    git_dir = source / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n")

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert ".git" in result.stderr
    assert not (vault / "sites" / "mysite").exists()


def test_rejects_git_plain_file_in_payload(tmp_path):
    """A gitlink-style ``.git`` file (as a submodule checkout leaves behind) is
    just as hazardous to `lore sync`'s bare ``git add -A`` as a full nested
    repo — reject it too."""
    vault = _make_vault(tmp_path)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    (source / ".git").write_text("gitdir: ../.git/modules/sub\n")

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert ".git" in result.stderr
    assert not (vault / "sites" / "mysite").exists()


def test_rejects_nested_git_directory_names_the_offending_path(tmp_path):
    """The error must name the exact offending path, not just the string
    ``.git`` — a nested ``.git`` several directories deep should be pinpointed."""
    vault = _make_vault(tmp_path)
    source = _write_site(tmp_path / "src", {"index.html": "<html></html>"})
    git_dir = source / "sub" / ".git"
    git_dir.mkdir(parents=True)

    result = _run(
        [str(source), "mysite", "--vault-path", str(vault), "--no-sync"],
        _env(tmp_path),
    )

    assert result.returncode != 0
    assert str(Path("sub") / ".git") in result.stderr
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
