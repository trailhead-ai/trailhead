"""`camp site-export` — the remote half of fetching a workspace site.

The export function is exercised against a real fixture workspace on disk; the
CLI verb is exercised through the real `main()` entry point.
"""

from __future__ import annotations

import importlib
import io
import os
import sys
import tarfile
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[3] / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

GROUP = "grp"
SLUG = "ws"
SITE = "docs"


def _export_module():
    return importlib.import_module("camp.sites.export")


def _env(tmp_path: Path) -> dict[str, str]:
    return {"CAMP_STATE_DIR": str(tmp_path / "state"), "HOME": str(tmp_path)}


def _site_dir(tmp_path: Path, site: str = SITE) -> Path:
    d = tmp_path / "state" / GROUP / "worktrees" / SLUG / "sites" / site
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def site(tmp_path: Path) -> Path:
    d = _site_dir(tmp_path)
    (d / "index.html").write_bytes(b"<h1>hi</h1>\n")
    return d


def _export(tmp_path: Path, **over) -> tarfile.TarFile:
    mod = _export_module()
    buf = io.BytesIO()
    args = dict(group=GROUP, slug=SLUG, site=SITE)
    args.update(over)
    hook = args.pop("before_read", None)
    mod.export_site(args["group"], args["slug"], args["site"], buf, env=_env(tmp_path), before_read=hook)
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r:")


def test_regular_files_round_trip_byte_for_byte_across_nested_dirs(tmp_path, site):
    payload = bytes(range(256)) * 40
    (site / "a" / "b").mkdir(parents=True)
    (site / "a" / "b" / "blob.bin").write_bytes(payload)
    (site / "a" / "x.css").write_bytes(b"body{}\r\n")
    (site / "empty").mkdir()

    tar = _export(tmp_path)

    members = {m.name: m for m in tar.getmembers()}
    assert set(members) == {"index.html", "a", "a/b", "a/b/blob.bin", "a/x.css", "empty"}
    assert tar.extractfile("a/b/blob.bin").read() == payload
    assert tar.extractfile("a/x.css").read() == b"body{}\r\n"
    assert tar.extractfile("index.html").read() == b"<h1>hi</h1>\n"
    assert {m.type for m in tar.getmembers()} <= {tarfile.REGTYPE, tarfile.DIRTYPE}
    assert members["a"].type == tarfile.DIRTYPE
    assert members["a/x.css"].type == tarfile.REGTYPE


def test_symlinks_are_never_archived_or_read_through(tmp_path, site):
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"TOPSECRET")
    (site / "real.txt").write_bytes(b"real")
    os.symlink(outside, site / "to-outside.txt")
    os.symlink(site / "real.txt", site / "to-inside.txt")
    os.symlink(tmp_path, site / "dirlink")

    tar = _export(tmp_path)

    assert {m.name for m in tar.getmembers()} == {"index.html", "real.txt"}
    for m in tar.getmembers():
        if m.isfile():
            assert b"TOPSECRET" not in tar.extractfile(m).read()


def test_file_swapped_for_symlink_after_lstat_is_not_read_through(tmp_path, site):
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"TOPSECRET")
    victim = site / "victim.txt"
    victim.write_bytes(b"innocent")

    def swap(path: str) -> None:
        if os.path.basename(path) == "victim.txt":
            os.unlink(path)
            os.symlink(outside, path)

    tar = _export(tmp_path, before_read=swap)

    assert "victim.txt" not in {m.name for m in tar.getmembers()}
    assert "index.html" in {m.name for m in tar.getmembers()}


def test_file_with_a_second_hard_link_is_skipped(tmp_path, site):
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_bytes(b"shared")
    os.link(elsewhere, site / "linked.txt")
    (site / "solo.txt").write_bytes(b"solo")

    tar = _export(tmp_path)

    assert {m.name for m in tar.getmembers()} == {"index.html", "solo.txt"}


def test_fifo_is_skipped_and_the_export_completes(tmp_path, site):
    os.mkfifo(site / "pipe")
    (site / "after.txt").write_bytes(b"after")

    tar = _export(tmp_path)

    assert {m.name for m in tar.getmembers()} == {"index.html", "after.txt"}


def test_fifo_swapped_in_after_lstat_does_not_hang_or_appear(tmp_path, site):
    victim = site / "victim.txt"
    victim.write_bytes(b"innocent")

    def swap(path: str) -> None:
        if os.path.basename(path) == "victim.txt":
            os.unlink(path)
            os.mkfifo(path)

    tar = _export(tmp_path, before_read=swap)

    assert "victim.txt" not in {m.name for m in tar.getmembers()}


@pytest.mark.parametrize(
    "field,value",
    [
        ("group", ".."),
        ("group", "a/b"),
        ("group", "Grp"),
        ("group", "grp\n"),
        ("slug", ".."),
        ("slug", "a/b"),
        ("slug", "WS"),
        ("slug", "ws\n"),
        ("site", ".."),
        ("site", "a/b"),
        ("site", "Docs"),
        ("site", "-docs"),
        ("site", "docs\n"),
        ("site", ".hidden"),
        ("site", ""),
    ],
)
def test_out_of_grammar_names_are_refused_before_any_path_is_built(tmp_path, site, monkeypatch, field, value):
    mod = _export_module()
    built = []
    real_stat = os.lstat
    monkeypatch.setattr(os, "lstat", lambda *a, **k: built.append(a) or real_stat(*a, **k))
    monkeypatch.setattr(os, "open", lambda *a, **k: built.append(a) or pytest.fail("opened"))
    import camp.group.manifest as manifest

    monkeypatch.setattr(manifest, "workspace_dir", lambda *a, **k: built.append(a) or pytest.fail("path built"))
    args = dict(group=GROUP, slug=SLUG, site=SITE)
    args[field] = value
    buf = io.BytesIO()

    with pytest.raises(mod.SiteExportUsageError):
        mod.export_site(args["group"], args["slug"], args["site"], buf, env=_env(tmp_path))

    assert built == []
    assert buf.getvalue() == b""


def test_valid_neighbouring_names_are_accepted(tmp_path):
    d = _site_dir(tmp_path, "a.b_c-1")
    (d / "index.html").write_bytes(b"x")

    tar = _export(tmp_path, site="a.b_c-1")

    assert [m.name for m in tar.getmembers()] == ["index.html"]


def test_missing_workspace_is_not_found(tmp_path, site):
    with pytest.raises(_export_module().SiteNotFoundError) as e:
        _export(tmp_path, slug="nows")
    assert "\n" not in str(e.value)


def test_missing_site_is_not_found(tmp_path, site):
    with pytest.raises(_export_module().SiteNotFoundError):
        _export(tmp_path, site="nosite")


def test_symlinked_site_dir_is_not_found(tmp_path, site):
    real = tmp_path / "realsite"
    real.mkdir()
    (real / "index.html").write_bytes(b"x")
    os.symlink(real, site.parent / "linked")

    with pytest.raises(_export_module().SiteNotFoundError):
        _export(tmp_path, site="linked")


def test_symlinked_sites_dir_is_not_found(tmp_path):
    ws = tmp_path / "state" / GROUP / "worktrees" / SLUG
    real = tmp_path / "realsites"
    (real / SITE).mkdir(parents=True)
    (real / SITE / "index.html").write_bytes(b"x")
    ws.mkdir(parents=True)
    os.symlink(real, ws / "sites")

    with pytest.raises(_export_module().SiteNotFoundError):
        _export(tmp_path)


def test_site_without_index_html_is_not_found(tmp_path):
    d = _site_dir(tmp_path)
    (d / "other.html").write_bytes(b"x")

    with pytest.raises(_export_module().SiteNotFoundError):
        _export(tmp_path)


def test_site_whose_index_html_is_a_symlink_is_not_found(tmp_path):
    d = _site_dir(tmp_path)
    (tmp_path / "t.html").write_bytes(b"x")
    os.symlink(tmp_path / "t.html", d / "index.html")

    with pytest.raises(_export_module().SiteNotFoundError):
        _export(tmp_path)


# ---- the CLI verb through the real entry point ----------------------------


def _run_main(monkeypatch, tmp_path, argv):
    dispatch = importlib.import_module("camp.cli.dispatch")
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    with pytest.raises(SystemExit) as e:
        dispatch.main()
    return e.value.code


def test_cli_streams_the_archive_to_binary_stdout(tmp_path, site, monkeypatch, capfdbinary):
    (site / "n.bin").write_bytes(b"\x00\r\n\xff")
    dispatch = importlib.import_module("camp.cli.dispatch")
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(sys, "argv", ["camp", "site-export", f"--group={GROUP}", f"--slug={SLUG}", f"--site={SITE}"])
    dispatch.main()
    out = capfdbinary.readouterr().out
    tar = tarfile.open(fileobj=io.BytesIO(out), mode="r:")
    assert tar.extractfile("n.bin").read() == b"\x00\r\n\xff"


def test_cli_exits_with_the_not_found_code_and_one_stderr_line(tmp_path, site, monkeypatch, capfdbinary):
    code = _run_main(monkeypatch, tmp_path, ["site-export", f"--group={GROUP}", f"--slug={SLUG}", "--site=nosite"])
    cap = capfdbinary.readouterr()
    assert code == _export_module().EXIT_NOT_FOUND
    assert cap.out == b""
    lines = cap.err.decode().splitlines()
    assert len(lines) == 1 and lines[0].startswith("camp site-export: ")


def test_cli_grammar_refusal_is_not_the_not_found_code(tmp_path, site, monkeypatch, capfdbinary):
    code = _run_main(monkeypatch, tmp_path, ["site-export", f"--group={GROUP}", f"--slug={SLUG}", "--site=Docs"])
    cap = capfdbinary.readouterr()
    assert code not in (0, _export_module().EXIT_NOT_FOUND)
    assert cap.out == b""
    assert cap.err.decode().startswith("camp site-export: ")


def test_cli_refuses_the_host_flag(tmp_path, site, monkeypatch, capfdbinary):
    code = _run_main(
        monkeypatch, tmp_path, ["site-export", "--host=other", f"--group={GROUP}", f"--slug={SLUG}", f"--site={SITE}"]
    )
    cap = capfdbinary.readouterr()
    assert code not in (0, None)
    assert cap.out == b""
    assert b"--host has no meaning here" in cap.err


@pytest.mark.parametrize(
    ("token", "is_verb"),
    [("site-export", True), ("site-exports", False)],
)
def test_only_the_exact_site_export_token_is_dispatched_as_the_verb(tmp_path, monkeypatch, capfdbinary, token, is_verb):
    code = _run_main(monkeypatch, tmp_path, [token])
    err = capfdbinary.readouterr().err.decode()
    assert code == 1
    assert ("bare slug dispatch" not in err) is is_verb
    assert ("camp site-export:" in err) is is_verb
