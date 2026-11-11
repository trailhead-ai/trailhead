"""Reads against a ``read-only`` vault that is a plain folder (not a git repo).

Each test registers a temp folder through the real ``lore vault add --scope
read-only`` and drives the real CLI, under injected ``XDG_STATE_HOME`` /
``XDG_CONFIG_HOME`` — never the live install.
"""

import json
import os
import subprocess
import sys

import pytest

from conftest import CLI_PATH


def _run(args, *, tmp_path):
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["LORE_EMAIL"] = "tester@example.com"
    env.pop("LORE_VAULT", None)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args], capture_output=True, text=True, env=env
    )


def _folder(tmp_path):
    """A plain folder (no .git) holding one blob record and one area."""
    d = tmp_path / "ro"
    (d / "blob").mkdir(parents=True)
    (d / "area").mkdir()
    (d / "blob" / "zebra-notes.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "kind": "blob",
                "title": "Zebra notes",
                "status": "active",
                "created-at": "2026-06-17T10:00:00Z",
                "updated-at": "2026-06-17T10:00:00Z",
            }
        )
    )
    (d / "blob" / "zebra-notes.md").write_text("striped quadruped\n")
    (d / "area" / "zebra-area.md").write_text(
        "---\ntype: area\nname: zebra-area\nkeywords: [zebra]\nsummary: stripes\n---\n\n"
        "## Overview\n\nThis is the zebra-area area.\n"
    )
    return d


def _register(tmp_path, trust):
    cfg = tmp_path / "config" / "lore" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"vaults": [{"name": "default", "scope": "default"}]}))
    folder = _folder(tmp_path)
    assert not (folder / ".git").exists()
    r = _run(
        ["vault", "add", "ro", "--scope", "read-only", "--path", str(folder), trust],
        tmp_path=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    return folder


@pytest.mark.parametrize("trust", ["--trusted", "--shared"])
def test_search_returns_the_record_and_an_absent_term_returns_none(tmp_path, trust):
    _register(tmp_path, trust)

    hit = _run(["search", "zebra"], tmp_path=tmp_path)
    assert hit.returncode == 0, hit.stderr
    assert "1 result" in hit.stdout
    assert "Zebra notes" in hit.stdout

    miss = _run(["search", "platypus"], tmp_path=tmp_path)
    assert miss.returncode == 0, miss.stderr
    assert "0 results" in miss.stdout
    assert "Zebra notes" not in miss.stdout


def test_record_show_returns_the_body(tmp_path):
    _register(tmp_path, "--trusted")
    r = _run(["record", "show", "blob/zebra-notes"], tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "striped quadruped" in r.stdout


def test_trusted_area_is_listed_and_shared_area_drops_out(tmp_path):
    _register(tmp_path, "--trusted")
    listed = _run(["areas"], tmp_path=tmp_path)
    assert listed.returncode == 0, listed.stderr
    assert "zebra-area" in listed.stdout

    # same folder, re-registered as shared: trust, not read-only, decides
    assert _run(["vault", "delete", "ro"], tmp_path=tmp_path).returncode == 0
    r = _run(
        ["vault", "add", "ro", "--scope", "read-only", "--path", str(tmp_path / "ro"), "--shared"],
        tmp_path=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    dropped = _run(["areas"], tmp_path=tmp_path)
    assert dropped.returncode == 0, dropped.stderr
    assert "zebra-area" not in dropped.stdout


def test_trusted_hit_is_listed_trusted_without_a_fence(tmp_path):
    _register(tmp_path, "--trusted")
    r = _run(["search", "zebra"], tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Trusted:" in r.stdout
    assert "Zebra notes" in r.stdout
    assert "<external-memory" not in r.stdout


def test_shared_hit_is_inside_the_shared_fence(tmp_path):
    _register(tmp_path, "--shared")
    r = _run(["search", "zebra"], tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert '<external-memory layer="shared"' in out
    assert out.index("<external-memory") < out.index("Zebra notes") < out.index("</external-memory>")
