"""Tests for the generic leak gate.

The gate is denylist-driven: it ships ZERO private strings. The denylist lives
machine-local at ~/.claude/leak-gate.denylist (untracked). So these tests prove
the *mechanism* with synthetic tokens — never the real private tokens (putting
those here would itself leak them into the tracked forge repo, the exact thing
the gate exists to prevent).

Exit-code contract:
  0 → clean (no denylist token in the scanned tree)
  1 → leak found (prints relpath:lineno:token per hit)
  2 → error / fail-closed (denylist missing, unreadable, or pattern-empty)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "forge" / "scripts" / "leak_gate.py"


def _run(tree, denylist: Path | None) -> subprocess.CompletedProcess:
    trees = tree if isinstance(tree, (list, tuple)) else [tree]
    cmd = [sys.executable, str(GATE), *[str(t) for t in trees]]
    if denylist is not None:
        cmd += ["--denylist", str(denylist)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _write(p: Path, name: str, body: str) -> Path:
    f = p / name
    f.write_text(body, encoding="utf-8")
    return f


@pytest.fixture
def denylist(tmp_path: Path) -> Path:
    """A synthetic denylist exercising plain tokens + a word-boundary anchor."""
    dl = tmp_path / "denylist"
    dl.write_text(
        "# synthetic test denylist — no real private strings\n"
        "sekritcorp\n"
        "\\bwidget\\b\n"
        "metric\\.[a-z_]+\n",
        encoding="utf-8",
    )
    return dl


# ---- clean / leak basics ----------------------------------------------------

def test_clean_tree_exits_0(tmp_path: Path, denylist: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    _write(tree, "ok.md", "nothing forbidden here\njust prose\n")
    r = _run(tree, denylist)
    assert r.returncode == 0, r.stderr + r.stdout


def test_seeded_plain_token_exits_1_and_names_file(tmp_path: Path, denylist: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    _write(tree, "bad.md", "we use sekritcorp internally\n")
    r = _run(tree, denylist)
    assert r.returncode == 1, r.stderr
    assert "bad.md" in r.stdout
    assert "sekritcorp" in r.stdout
    assert ":1:" in r.stdout  # line number reported


def test_word_boundary_anchor_matches_word_not_substring(tmp_path: Path, denylist: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    _write(tree, "hit.md", "the widget is here\n")
    _write(tree, "miss.md", "widgets and widgetry are fine\n")
    r = _run(tree, denylist)
    assert r.returncode == 1
    assert "hit.md" in r.stdout
    assert "miss.md" not in r.stdout


def test_dotted_metric_pattern(tmp_path: Path, denylist: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    _write(tree, "hit.md", "emit metric.action_count today\n")
    _write(tree, "miss.md", "the metric was good\n")  # 'metric' alone, no dot
    r = _run(tree, denylist)
    assert r.returncode == 1
    assert "hit.md" in r.stdout
    assert "miss.md" not in r.stdout


def test_case_insensitive(tmp_path: Path, denylist: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    _write(tree, "bad.md", "SEKRITCORP shouting\n")
    r = _run(tree, denylist)
    assert r.returncode == 1
    assert "bad.md" in r.stdout


def test_binary_and_pycache_skipped(tmp_path: Path, denylist: Path):
    tree = tmp_path / "tree"
    cache = tree / "__pycache__"
    cache.mkdir(parents=True)
    # token inside __pycache__ must be ignored
    _write(cache, "x.txt", "sekritcorp\n")
    (tree / "blob.bin").write_bytes(b"\x00\x01sekritcorp\x02")
    r = _run(tree, denylist)
    assert r.returncode == 0, r.stdout


# ---- multiple trees ---------------------------------------------------------

def test_multiple_trees_all_scanned(tmp_path: Path, denylist: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    _write(a, "ok.md", "clean prose\n")
    _write(b, "bad.md", "uses sekritcorp here\n")
    r = _run([a, b], denylist)
    assert r.returncode == 1, r.stderr
    assert "bad.md" in r.stdout


def test_multiple_trees_all_clean_exits_0(tmp_path: Path, denylist: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    _write(a, "ok.md", "clean\n")
    _write(b, "fine.md", "also clean\n")
    assert _run([a, b], denylist).returncode == 0


def test_multiple_trees_one_missing_fails_closed(tmp_path: Path, denylist: Path):
    a = tmp_path / "a"; a.mkdir()
    _write(a, "ok.md", "clean\n")
    r = _run([a, tmp_path / "missing"], denylist)
    assert r.returncode == 2


# ---- fail-closed ------------------------------------------------------------

def test_missing_denylist_exits_2(tmp_path: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    _write(tree, "ok.md", "harmless\n")
    missing = tmp_path / "does-not-exist.denylist"
    r = _run(tree, missing)
    assert r.returncode == 2, f"expected fail-closed, got {r.returncode}: {r.stdout}"
    assert "denylist" in (r.stderr + r.stdout).lower()


def test_empty_denylist_exits_2(tmp_path: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    _write(tree, "ok.md", "harmless\n")
    empty = tmp_path / "empty.denylist"
    empty.write_text("# only comments, no patterns\n\n", encoding="utf-8")
    r = _run(tree, empty)
    assert r.returncode == 2, f"empty denylist must fail closed, got {r.returncode}"


def test_missing_tree_exits_2(tmp_path: Path, denylist: Path):
    r = _run(tmp_path / "nope", denylist)
    assert r.returncode == 2


# ---- KU2: real shippable surfaces are clean (skip if denylist absent) -------

REAL_DENYLIST = Path.home() / ".claude" / "leak-gate.denylist"


@pytest.mark.skipif(not REAL_DENYLIST.exists(), reason="machine-local denylist not present")
@pytest.mark.parametrize(
    "surface",
    [Path.home() / "code" / "forge" / "plugins" / "forge",
     Path.home() / "code" / "lore" / "plugins" / "lore"],
    ids=["forge", "lore"],
)
def test_real_shippable_surface_is_clean(surface: Path):
    if not surface.exists():
        pytest.skip(f"{surface} not checked out")
    r = _run(surface, REAL_DENYLIST)
    assert r.returncode == 0, f"leak in {surface}:\n{r.stdout}"
