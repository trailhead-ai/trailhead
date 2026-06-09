"""Slice 2 tests: the `lore` CLI (init / patch / set-status / stats).

The CLI is exercised as a subprocess so we test the real executable + its
sibling-module import path, exit codes, and stdout/stderr.
"""

import os
import subprocess
import sys
from pathlib import Path

from conftest import CLI_PATH, SCRIPTS_DIR, load_script

TAXONOMY = [
    "sessions", "deferred", "subsystems", "decisions", "dead-ends", "lessons",
    "radar", "collaboration", "specs", "plans", "designs", "inbox",
    "briefings", "reviews", "gotchas", "audits", "tools", "templates",
]
STARTER_DOCS = ["README.md", "glossary.md", "phases.md", "harvest-protocol.md"]


def run_cli(args, env=None, input_text=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, input=input_text,
    )


def home(tmp_path):
    """Env that puts $HOME above tmp_path so a tmp target counts as in-home.

    pytest's tmp_path is outside the real $HOME (e.g. /private/tmp on macOS),
    which the init in-home guard would otherwise reject. Tests exercising the
    happy path scaffold under a HOME pointed at tmp_path's parent.
    """
    return {"HOME": str(tmp_path)}


# ---- lore init --------------------------------------------------------------

def test_init_scaffolds_full_taxonomy(tmp_path):
    target = tmp_path / "vault"
    r = run_cli(["init", str(target), "--yes"], env=home(tmp_path))
    assert r.returncode == 0, r.stderr
    for d in TAXONOMY:
        assert (target / d).is_dir(), f"missing taxonomy dir {d}"
    for doc in STARTER_DOCS:
        assert (target / doc).is_file(), f"missing starter doc {doc}"
    assert (target / "harvest-pending.md").is_file()


def test_init_starter_docs_carry_no_status(tmp_path):
    """Starter docs are reference material, not tracked note types — they carry
    no `status:` frontmatter, so the status guard never constrains them. (If a
    future change adds frontmatter with a status, that must be a deliberate edit
    that also makes the value canonical — this test forces that conversation.)"""
    target = tmp_path / "vault"
    r = run_cli(["init", str(target), "--yes"], env=home(tmp_path))
    assert r.returncode == 0, r.stderr
    fm = load_script("frontmatter")
    for doc in STARTER_DOCS:
        meta = fm.parse_frontmatter(target / doc)
        assert meta.get("status") is None, f"{doc} unexpectedly carries a status field"


def test_init_prints_export_line(tmp_path):
    target = tmp_path / "vault"
    r = run_cli(["init", str(target), "--yes"], env=home(tmp_path))
    assert r.returncode == 0
    assert f"export LORE_VAULT={target.resolve()}" in r.stdout
    assert "~/lore" in r.stdout  # mentions the default fallback footgun


def test_init_copies_templates_dir_contents(tmp_path):
    # A template present in the shipped templates dir lands in the vault's .templates/
    target = tmp_path / "vault"
    r = run_cli(["init", str(target), "--yes"], env=home(tmp_path))
    assert r.returncode == 0
    assert (target / ".templates").is_dir()


def test_init_refuses_nonempty_without_force(tmp_path):
    target = tmp_path / "vault"
    target.mkdir()
    (target / "existing.txt").write_text("keep me")
    before = sorted(p.name for p in target.iterdir())
    r = run_cli(["init", str(target), "--yes"], env=home(tmp_path))
    assert r.returncode != 0
    assert "non-empty" in (r.stderr + r.stdout).lower()
    # zero writes — directory unchanged
    after = sorted(p.name for p in target.iterdir())
    assert before == after == ["existing.txt"]


def test_init_force_allows_nonempty(tmp_path):
    target = tmp_path / "vault"
    target.mkdir()
    (target / "existing.txt").write_text("keep me")
    r = run_cli(["init", str(target), "--yes", "--force"], env=home(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (target / "sessions").is_dir()
    assert (target / "existing.txt").read_text() == "keep me"


def test_init_refuses_outside_home_without_flag(tmp_path, monkeypatch):
    # Point HOME at a sandbox so tmp_path is "outside home".
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    outside = tmp_path / "outside" / "vault"
    r = run_cli(["init", str(outside), "--yes"], env={"HOME": str(fake_home)})
    assert r.returncode != 0
    assert "home" in (r.stderr + r.stdout).lower()
    assert not outside.exists()


def test_init_allow_outside_home_flag(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    outside = tmp_path / "outside" / "vault"
    r = run_cli(
        ["init", str(outside), "--yes", "--allow-outside-home"],
        env={"HOME": str(fake_home)},
    )
    assert r.returncode == 0, r.stderr
    assert (outside / "sessions").is_dir()


def test_init_requires_confirmation_without_yes(tmp_path):
    target = tmp_path / "vault"
    # Decline at the prompt.
    r = run_cli(["init", str(target)], env=home(tmp_path), input_text="n\n")
    assert r.returncode != 0
    assert not (target / "sessions").exists()
    # Tree was printed for review
    assert "sessions" in r.stdout


# ---- lore patch -------------------------------------------------------------

def _session_doc():
    return (
        "---\ntype: session\nstatus: active\n---\n\n"
        "# Session\n\n"
        "## What we did\n"
        "did stuff\n\n"
        "## Decided\n"
        "decided stuff\n"
    )


def test_patch_appends_under_section_via_arg(tmp_path):
    p = tmp_path / "s.md"
    p.write_text(_session_doc())
    r = run_cli(["patch", str(p), "What we did", "--text", "- more work"])
    assert r.returncode == 0, r.stderr
    text = p.read_text()
    what_idx = text.index("## What we did")
    decided_idx = text.index("## Decided")
    assert "- more work" in text[what_idx:decided_idx]


def test_patch_leaves_sibling_byte_identical(tmp_path):
    p = tmp_path / "s.md"
    p.write_text(_session_doc())
    r = run_cli(["patch", str(p), "What we did", "--text", "- more work"])
    assert r.returncode == 0
    decided_block = p.read_text()
    decided_block = decided_block[decided_block.index("## Decided"):]
    assert decided_block == "## Decided\ndecided stuff\n"


def test_patch_reads_stdin(tmp_path):
    p = tmp_path / "s.md"
    p.write_text(_session_doc())
    r = run_cli(["patch", str(p), "Decided"], input_text="- from stdin\n")
    assert r.returncode == 0, r.stderr
    assert "- from stdin" in p.read_text()


# ---- lore set-status --------------------------------------------------------

def test_set_status_rejects_noncanonical(tmp_path):
    p = tmp_path / "d.md"
    original = "---\ntype: deferred\nstatus: open\n---\n\nbody\n"
    p.write_text(original)
    r = run_cli(["set-status", str(p), "bogus"])
    assert r.returncode != 0
    assert p.read_text() == original  # no write


def test_set_status_accepts_canonical(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("---\ntype: deferred\nstatus: open\n---\n\nbody\n")
    r = run_cli(["set-status", str(p), "resolved"])
    assert r.returncode == 0, r.stderr
    fm = load_script("frontmatter")
    assert fm.parse_frontmatter(p)["status"] == "resolved"


# ---- lore stats -------------------------------------------------------------

def test_stats_counts_resolved_vault(tmp_path):
    target = tmp_path / "vault"
    assert run_cli(["init", str(target), "--yes"], env=home(tmp_path)).returncode == 0
    # Seed one open deferred note.
    (target / "deferred" / "x.md").write_text(
        "---\ntype: deferred\nstatus: open\n---\n\n# X\n"
    )
    r = run_cli(["stats"], env={"LORE_VAULT": str(target)})
    assert r.returncode == 0, r.stderr
    assert "deferred" in r.stdout.lower()
    assert "1" in r.stdout
