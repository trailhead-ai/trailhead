"""Slice 3 tests: `lore new <type>` CLI engine and supporting helpers.

Covers:
- resolve_project(): parses git remote URL to repo name; fallback to dir name.
- find_session_note(): returns newest session note / None when absent.
- `lore new <type>`: writes to correct dir, frontmatter passes status_validator,
  no literal {{user}}, backlinks in session note (deferred/dead-end/decision),
  no backlink for radar/subsystem, no-session fallback (exit 0 + notice).
- subsystem template keywords: round-trip through parse_frontmatter as list.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from conftest import CLI_PATH, SCRIPTS_DIR, load_script

TODAY = "2026-06-02"  # frozen for determinism


def run_cli(args, env=None, input_text=None, cwd=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    full_env.setdefault("LORE_TODAY", TODAY)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, input=input_text,
        cwd=str(cwd) if cwd else None,
    )


def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal vault directory structure."""
    vault = tmp_path / "vault"
    for d in ("deferred", "dead-ends", "decisions", "radar", "subsystems", "sessions"):
        (vault / d).mkdir(parents=True)
    return vault


def _make_session_note(vault: Path, name: str = "test-session") -> Path:
    """Write a minimal session note with the standard backlink sections."""
    session = vault / "sessions" / f"2026-06-02-1200-{name}.md"
    session.write_text(
        "---\ntype: session\nstatus: active\n---\n\n"
        f"# Session: {name}\n\n"
        "## What we did\n\n"
        "## Decided\n\n"
        "## Deferred\n\n"
        "## Learned\n\n"
        "## Open questions\n"
    )
    return session


# ---------------------------------------------------------------------------
# resolve_project
# ---------------------------------------------------------------------------

class TestResolveProject:
    def test_parses_https_remote(self, tmp_path):
        v = load_script("vault")
        with mock.patch.object(
            subprocess, "run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="https://github.com/acme/my-repo.git\n", stderr=""),
        ):
            assert v.resolve_project() == "my-repo"

    def test_parses_ssh_remote(self, tmp_path):
        v = load_script("vault")
        with mock.patch.object(
            subprocess, "run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="git@github.com:acme/awesome-project.git\n", stderr=""),
        ):
            assert v.resolve_project() == "awesome-project"

    def test_strips_trailing_slash(self, tmp_path):
        v = load_script("vault")
        with mock.patch.object(
            subprocess, "run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="https://github.com/acme/repo/\n", stderr=""),
        ):
            assert v.resolve_project() == "repo"

    def test_falls_back_to_cwd_name_when_no_remote(self, tmp_path):
        v = load_script("vault")
        with mock.patch.object(
            subprocess, "run",
            side_effect=OSError("no git"),
        ):
            result = v.resolve_project(cwd=tmp_path)
        assert result == tmp_path.name

    def test_falls_back_to_cwd_name_on_nonzero(self, tmp_path):
        v = load_script("vault")

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess([], 1, stdout="", stderr="no remote")

        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            result = v.resolve_project(cwd=tmp_path)
        assert result == tmp_path.name


# ---------------------------------------------------------------------------
# find_session_note
# ---------------------------------------------------------------------------

class TestFindSessionNote:
    def test_returns_newest_session_note(self, tmp_path):
        vault = _make_vault(tmp_path)
        older = vault / "sessions" / "2026-06-01-1200-main.md"
        newer = vault / "sessions" / "2026-06-02-1200-main.md"
        older.write_text("---\ntype: session\nstatus: active\n---\n")
        newer.write_text("---\ntype: session\nstatus: active\n---\n")
        v = load_script("vault")
        result = v.find_session_note(vault)
        assert result == newer

    def test_returns_none_when_sessions_dir_empty(self, tmp_path):
        vault = _make_vault(tmp_path)
        v = load_script("vault")
        assert v.find_session_note(vault) is None

    def test_returns_none_when_sessions_dir_missing(self, tmp_path):
        vault = tmp_path / "no-vault"
        vault.mkdir()
        v = load_script("vault")
        assert v.find_session_note(vault) is None

    def test_ignores_non_md_files(self, tmp_path):
        vault = _make_vault(tmp_path)
        (vault / "sessions" / "readme.txt").write_text("not a note")
        v = load_script("vault")
        assert v.find_session_note(vault) is None


# ---------------------------------------------------------------------------
# lore new — common helpers
# ---------------------------------------------------------------------------

def _find_new_note(dir_path: Path) -> Path:
    """Return the single .md file written to a directory.

    deferred/decision/radar/dead-end notes are date-bucketed into
    <dir>/YYYY-MM/ (the date-bucketed archive layout), so search the bucket
    subdir too. subsystems (name-keyed) stay flat — also matched by *.md.
    """
    notes = list(dir_path.glob("*.md")) + list(dir_path.glob("*/*.md"))
    assert len(notes) == 1, f"Expected 1 note, got {notes}"
    return notes[0]


# ---------------------------------------------------------------------------
# lore new deferred
# ---------------------------------------------------------------------------

class TestNewDeferred:
    def test_writes_to_deferred_dir(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "Put off the thing",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert (vault / "deferred").glob("*.md")

    def test_frontmatter_has_correct_type_and_status(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "Put off the thing",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["type"] == "deferred"
        assert fm["status"] == "open"

    def test_status_is_valid(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "Put off the thing",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        sv = load_script("status_validator")
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert sv.is_valid_status(fm["type"], fm["status"])

    def test_no_literal_user_placeholder(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "Put off the thing",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        note = _find_new_note(vault / "deferred")
        assert "{{user}}" not in note.read_text()

    def test_slug_is_date_kebab_title(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "Put off the thing",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        note = _find_new_note(vault / "deferred")
        assert note.name.startswith(TODAY)
        assert "put-off-the-thing" in note.name

    def test_backlinks_session_note_under_deferred_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        session = _make_session_note(vault)
        # Run from a directory named 'test-session' so find_session_note scopes correctly.
        worktree_cwd = tmp_path / "test-session"
        worktree_cwd.mkdir()
        run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "My deferral",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
            cwd=worktree_cwd,
        )
        session_text = session.read_text()
        # Backlink under ## Deferred section
        deferred_idx = session_text.index("## Deferred")
        learned_idx = session_text.index("## Learned")
        deferred_section = session_text[deferred_idx:learned_idx]
        assert "my-deferral" in deferred_section

    def test_backlink_does_not_touch_other_sections(self, tmp_path):
        vault = _make_vault(tmp_path)
        session = _make_session_note(vault)
        original = session.read_text()
        # Capture sibling sections before
        decided_before = original[original.index("## Decided"):original.index("## Deferred")]
        worktree_cwd = tmp_path / "test-session"
        worktree_cwd.mkdir()
        run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "My deferral",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
            cwd=worktree_cwd,
        )
        new_text = session.read_text()
        decided_after = new_text[new_text.index("## Decided"):new_text.index("## Deferred")]
        assert decided_before == decided_after

    def test_no_session_still_writes_note(self, tmp_path):
        vault = _make_vault(tmp_path)
        # No session note created
        r = run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "No session thing",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert len((list((vault / "deferred").glob("*.md")) + list((vault / "deferred").glob("*/*.md")))) == 1

    def test_no_session_prints_skip_notice(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "No session thing",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0
        combined = r.stdout + r.stderr
        assert "backlink" in combined.lower() or "session" in combined.lower()


# ---------------------------------------------------------------------------
# lore new dead-end
# ---------------------------------------------------------------------------

class TestNewDeadEnd:
    def test_writes_to_dead_ends_dir(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "dead-end", "--vault", str(vault),
             "--title", "Cache invalidation attempt"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert len((list((vault / "dead-ends").glob("*.md")) + list((vault / "dead-ends").glob("*/*.md")))) == 1

    def test_frontmatter_type_and_status(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "dead-end", "--vault", str(vault),
             "--title", "Cache invalidation attempt"],
            env={"LORE_USER": "ada"},
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["type"] == "dead-end"
        assert fm["status"] == "active"

    def test_status_is_valid(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "dead-end", "--vault", str(vault),
             "--title", "Cache invalidation attempt"],
            env={"LORE_USER": "ada"},
        )
        sv = load_script("status_validator")
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert sv.is_valid_status(fm["type"], fm["status"])

    def test_no_literal_user_placeholder(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "dead-end", "--vault", str(vault),
             "--title", "Cache invalidation attempt"],
            env={"LORE_USER": "ada"},
        )
        note = _find_new_note(vault / "dead-ends")
        assert "{{user}}" not in note.read_text()

    def test_no_project_field_in_dead_end(self, tmp_path):
        """Dead-ends are universal — they must NOT carry a project field."""
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "dead-end", "--vault", str(vault),
             "--title", "Cache invalidation attempt"],
            env={"LORE_USER": "ada"},
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert "project" not in fm

    def test_backlinks_session_under_learned_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        session = _make_session_note(vault)
        worktree_cwd = tmp_path / "test-session"
        worktree_cwd.mkdir()
        run_cli(
            ["new", "dead-end", "--vault", str(vault),
             "--title", "Cache invalidation attempt"],
            env={"LORE_USER": "ada"},
            cwd=worktree_cwd,
        )
        session_text = session.read_text()
        learned_idx = session_text.index("## Learned")
        # "## Open questions" follows ## Learned
        open_idx = session_text.index("## Open questions")
        learned_section = session_text[learned_idx:open_idx]
        assert "cache-invalidation-attempt" in learned_section

    def test_no_session_fallback(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "dead-end", "--vault", str(vault),
             "--title", "Cache invalidation attempt"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0
        assert len((list((vault / "dead-ends").glob("*.md")) + list((vault / "dead-ends").glob("*/*.md")))) == 1


# ---------------------------------------------------------------------------
# lore new decision
# ---------------------------------------------------------------------------

class TestNewDecision:
    def test_writes_to_decisions_dir(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "Use postgres for everything",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert len((list((vault / "decisions").glob("*.md")) + list((vault / "decisions").glob("*/*.md")))) == 1

    def test_frontmatter_type_no_status(self, tmp_path):
        """Decisions are immutable — they must NOT carry a status field."""
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "Use postgres for everything",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "decisions")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["type"] == "decision"
        assert "status" not in fm

    def test_no_literal_user_placeholder(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "Use postgres for everything",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        note = _find_new_note(vault / "decisions")
        assert "{{user}}" not in note.read_text()

    def test_body_has_required_sections(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "Use postgres for everything",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        note = _find_new_note(vault / "decisions")
        text = note.read_text()
        for section in ("## Context", "## Decision", "## Rationale", "## Consequences"):
            assert section in text, f"Missing section: {section}"

    def test_backlinks_session_under_decided_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        session = _make_session_note(vault)
        worktree_cwd = tmp_path / "test-session"
        worktree_cwd.mkdir()
        run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "Use postgres for everything",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
            cwd=worktree_cwd,
        )
        session_text = session.read_text()
        decided_idx = session_text.index("## Decided")
        deferred_idx = session_text.index("## Deferred")
        decided_section = session_text[decided_idx:deferred_idx]
        assert "use-postgres-for-everything" in decided_section

    def test_no_session_fallback(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "Use postgres for everything",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0
        assert len((list((vault / "decisions").glob("*.md")) + list((vault / "decisions").glob("*/*.md")))) == 1


# ---------------------------------------------------------------------------
# lore new radar
# ---------------------------------------------------------------------------

class TestNewRadar:
    def test_writes_to_radar_dir(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "radar", "--vault", str(vault),
             "--title", "Watch dependency X",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert len((list((vault / "radar").glob("*.md")) + list((vault / "radar").glob("*/*.md")))) == 1

    def test_frontmatter_type_and_status(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "radar", "--vault", str(vault),
             "--title", "Watch dependency X",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "radar")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["type"] == "radar"
        assert fm["status"] == "active"

    def test_status_is_valid(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "radar", "--vault", str(vault),
             "--title", "Watch dependency X",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        sv = load_script("status_validator")
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "radar")
        fm = fm_mod.parse_frontmatter(note)
        assert sv.is_valid_status(fm["type"], fm["status"])

    def test_no_literal_user_placeholder(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "radar", "--vault", str(vault),
             "--title", "Watch dependency X",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        note = _find_new_note(vault / "radar")
        assert "{{user}}" not in note.read_text()

    def test_no_backlink_attempted(self, tmp_path):
        """Radar does NOT backlink to the session note."""
        vault = _make_vault(tmp_path)
        session = _make_session_note(vault)
        original = session.read_text()
        run_cli(
            ["new", "radar", "--vault", str(vault),
             "--title", "Watch dependency X",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        # Session note should be byte-identical (no writes)
        assert session.read_text() == original


# ---------------------------------------------------------------------------
# lore new subsystem
# ---------------------------------------------------------------------------

class TestNewSubsystem:
    def test_writes_to_subsystems_dir(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "subsystem", "--vault", str(vault),
             "--title", "auth-module",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert len(list((vault / "subsystems").glob("*.md"))) == 1

    def test_frontmatter_has_type_subsystem(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "subsystem", "--vault", str(vault),
             "--title", "auth-module",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "subsystems")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["type"] == "subsystem"

    def test_no_status_field(self, tmp_path):
        """Subsystems do not carry a status field."""
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "subsystem", "--vault", str(vault),
             "--title", "auth-module",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "subsystems")
        fm = fm_mod.parse_frontmatter(note)
        assert "status" not in fm

    def test_no_literal_user_placeholder(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "subsystem", "--vault", str(vault),
             "--title", "auth-module",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        note = _find_new_note(vault / "subsystems")
        assert "{{user}}" not in note.read_text()

    def test_keywords_inline_list_roundtrip(self, tmp_path):
        """keywords: must be written as an inline list and parse back as a list
        (not a string). This is load-bearing for Slice 5 recall."""
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "subsystem", "--vault", str(vault),
             "--title", "auth-module",
             "--project", "my-project",
             "--keywords", "auth, login, oauth"],
            env={"LORE_USER": "ada"},
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "subsystems")
        fm = fm_mod.parse_frontmatter(note)
        assert isinstance(fm.get("keywords"), list), (
            f"keywords must parse as list, got {type(fm.get('keywords'))}: {fm.get('keywords')}"
        )
        assert "auth" in fm["keywords"]

    def test_no_backlink_attempted(self, tmp_path):
        """Subsystem does NOT backlink to the session note."""
        vault = _make_vault(tmp_path)
        session = _make_session_note(vault)
        original = session.read_text()
        run_cli(
            ["new", "subsystem", "--vault", str(vault),
             "--title", "auth-module",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert session.read_text() == original


# ---------------------------------------------------------------------------
# Leak gate — no absolute machine paths in generated notes
# ---------------------------------------------------------------------------
# Generated notes must never bake in an absolute home/machine path. Project- or
# stack-specific token enforcement on the *shipped* tree is delegated to the
# denylist-driven pre-commit gate (see README → forge leak gate), which reads a
# machine-local denylist so no private token lives in this tracked repo.

class TestLeakGate:
    FORBIDDEN = ["/Users/", "/home/"]

    def _check_note(self, note: Path):
        text = note.read_text().lower()
        for forbidden in self.FORBIDDEN:
            assert forbidden.lower() not in text, (
                f"Leak: {forbidden!r} found in {note}: {note.read_text()[:200]}"
            )

    def test_deferred_note_has_no_leaks(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "No leaks please",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        self._check_note(_find_new_note(vault / "deferred"))

    def test_dead_end_note_has_no_leaks(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "dead-end", "--vault", str(vault),
             "--title", "No leaks please"],
            env={"LORE_USER": "ada"},
        )
        self._check_note(_find_new_note(vault / "dead-ends"))

    def test_decision_note_has_no_leaks(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "decision", "--vault", str(vault),
             "--title", "No leaks please",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        self._check_note(_find_new_note(vault / "decisions"))

    def test_radar_note_has_no_leaks(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "radar", "--vault", str(vault),
             "--title", "No leaks please",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        self._check_note(_find_new_note(vault / "radar"))

    def test_subsystem_note_has_no_leaks(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "subsystem", "--vault", str(vault),
             "--title", "No leaks please",
             "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        self._check_note(_find_new_note(vault / "subsystems"))


# ---------------------------------------------------------------------------
# C1 — slug safety: no-overwrite and non-empty slug
# ---------------------------------------------------------------------------

class TestSlugSafety:
    def test_two_notes_same_title_same_day_are_distinct(self, tmp_path):
        """Two 'lore new deferred' calls with the same title on the same day
        must produce two distinct files — the second must NOT overwrite the first."""
        vault = _make_vault(tmp_path)
        env = {"LORE_USER": "ada", "LORE_TODAY": "2026-06-02"}
        r1 = run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "auth retry", "--project", "my-project"],
            env=env,
        )
        assert r1.returncode == 0, r1.stderr
        r2 = run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "auth retry", "--project", "my-project"],
            env=env,
        )
        assert r2.returncode == 0, r2.stderr
        notes = (list((vault / "deferred").glob("*.md")) + list((vault / "deferred").glob("*/*.md")))
        assert len(notes) == 2, (
            f"Expected 2 distinct notes, got {len(notes)}: {[n.name for n in notes]}"
        )

    def test_all_punctuation_title_produces_nonempty_slug(self, tmp_path):
        """A title of '!!!' must not produce an empty slug (which would write
        <date>-.md, a bare-hyphen filename)."""
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "!!!", "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0, r.stderr
        notes = (list((vault / "deferred").glob("*.md")) + list((vault / "deferred").glob("*/*.md")))
        assert len(notes) == 1
        name = notes[0].name
        assert not name.endswith("-.md"), (
            f"Slug is empty: filename is {name!r}"
        )

    def test_unicode_only_title_produces_nonempty_slug(self, tmp_path):
        """A unicode-only title ('日本語対応') must not produce an empty slug."""
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "日本語対応", "--project", "my-project"],
            env={"LORE_USER": "ada"},
        )
        assert r.returncode == 0, r.stderr
        notes = (list((vault / "deferred").glob("*.md")) + list((vault / "deferred").glob("*/*.md")))
        assert len(notes) == 1
        name = notes[0].name
        assert not name.endswith("-.md"), (
            f"Slug is empty: filename is {name!r}"
        )


# ---------------------------------------------------------------------------
# I2 — project inference for project-bearing types
# ---------------------------------------------------------------------------

class TestProjectInference:
    def _make_git_repo(self, tmp_path: Path, remote_url: str) -> Path:
        """Create a tmp dir that looks like a git repo with a known remote."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", remote_url],
            check=True, capture_output=True,
        )
        return repo

    def test_deferred_without_project_flag_infers_project(self, tmp_path):
        """lore new deferred --title X (no --project) inside a git repo with a
        remote must stamp project: <repo-name> — NOT an empty string."""
        vault = _make_vault(tmp_path)
        repo = self._make_git_repo(tmp_path, "https://github.com/acme/inferred-repo.git")
        env = {
            "LORE_USER": "ada",
            "LORE_VAULT": str(vault),
            "LORE_TODAY": TODAY,
        }
        r = subprocess.run(
            [sys.executable, str(CLI_PATH), "new", "deferred",
             "--vault", str(vault), "--title", "inferred project test"],
            capture_output=True, text=True, env={**os.environ, **env},
            cwd=str(repo),
        )
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("project") == "inferred-repo", (
            f"Expected project='inferred-repo', got {fm.get('project')!r}"
        )

    def test_dead_end_still_has_no_project_field(self, tmp_path):
        """dead-end notes must NOT carry a project: field regardless of whether
        --project is passed or the cwd is a git repo."""
        vault = _make_vault(tmp_path)
        repo = self._make_git_repo(tmp_path, "https://github.com/acme/some-repo.git")
        env = {
            "LORE_USER": "ada",
            "LORE_VAULT": str(vault),
            "LORE_TODAY": TODAY,
        }
        r = subprocess.run(
            [sys.executable, str(CLI_PATH), "new", "dead-end",
             "--vault", str(vault), "--title", "no project dead end"],
            capture_output=True, text=True, env={**os.environ, **env},
            cwd=str(repo),
        )
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert "project" not in fm, (
            f"dead-end must have no project field, but got project={fm.get('project')!r}"
        )


# ---------------------------------------------------------------------------
# M1 — find_session_note ignores non-date-prefixed filenames
# ---------------------------------------------------------------------------

class TestFindSessionNoteM1:
    def test_ignores_stray_undated_file(self, tmp_path):
        """A stray 'notes.md' in sessions/ must not win the sort over a real
        date-prefixed session note."""
        vault = _make_vault(tmp_path)
        stray = vault / "sessions" / "notes.md"
        stray.write_text("---\ntype: session\nstatus: active\n---\n")
        dated = vault / "sessions" / "2026-06-02-1200-main.md"
        dated.write_text("---\ntype: session\nstatus: active\n---\n")
        v = load_script("vault")
        result = v.find_session_note(vault)
        assert result == dated, (
            f"Expected dated note {dated.name}, got {result and result.name!r}"
        )

    def test_returns_none_when_only_stray_files(self, tmp_path):
        """If sessions/ contains only undated files, find_session_note returns None."""
        vault = _make_vault(tmp_path)
        (vault / "sessions" / "notes.md").write_text("some content")
        v = load_script("vault")
        result = v.find_session_note(vault)
        assert result is None


# ---------------------------------------------------------------------------
# I1 — lore new backlinks into the CURRENT worktree's session note, not the
# newest note across all worktrees.
# ---------------------------------------------------------------------------

def _make_worktree_session_note(vault: Path, worktree: str, stamp: str) -> Path:
    """Write a minimal session note for a specific worktree."""
    session = vault / "sessions" / f"{stamp}-{worktree}.md"
    session.write_text(
        f"---\ntype: session\nworktree: {worktree}\nstatus: active\n---\n\n"
        f"# Session: {worktree}\n\n"
        "## What we did\n\n"
        "## Decided\n\n"
        "## Deferred\n\n"
        "## Learned\n\n"
        "## Open questions\n"
    )
    return session


class TestBacklinkWorktreeScoped:
    def test_backlink_goes_into_alpha_note_not_newer_beta(self, tmp_path):
        """When run from worktree 'alpha', lore new must backlink into alpha's
        session note even if beta's note has a newer timestamp."""
        vault = _make_vault(tmp_path)
        # alpha: older stamp; beta: newer stamp
        alpha_session = _make_worktree_session_note(vault, "alpha", "2026-06-01-1000")
        beta_session = _make_worktree_session_note(vault, "beta", "2026-06-02-1000")

        # Run lore new from a directory named 'alpha'
        alpha_cwd = tmp_path / "alpha"
        alpha_cwd.mkdir()

        r = subprocess.run(
            [sys.executable, str(CLI_PATH), "new", "deferred",
             "--vault", str(vault), "--title", "my deferral",
             "--project", "my-project"],
            capture_output=True, text=True,
            env={**os.environ, "LORE_USER": "ada", "LORE_TODAY": TODAY},
            cwd=str(alpha_cwd),
        )
        assert r.returncode == 0, r.stderr + r.stdout

        # Backlink must be in alpha's session note
        alpha_text = alpha_session.read_text()
        deferred_idx = alpha_text.index("## Deferred")
        learned_idx = alpha_text.index("## Learned")
        deferred_section = alpha_text[deferred_idx:learned_idx]
        assert "my-deferral" in deferred_section, (
            f"backlink missing from alpha session note. Section:\n{deferred_section}"
        )

        # Beta's session note must NOT be touched
        beta_text = beta_session.read_text()
        assert "my-deferral" not in beta_text, (
            "backlink incorrectly appeared in beta's session note"
        )

    def test_no_session_for_current_worktree_skips_backlink_with_notice(self, tmp_path):
        """When no session note exists for the current worktree (even if another
        worktree has a note), backlink is skipped with a notice, exit 0."""
        vault = _make_vault(tmp_path)
        # Only beta has a session note; we're running from 'alpha'
        _make_worktree_session_note(vault, "beta", "2026-06-02-1000")

        alpha_cwd = tmp_path / "alpha"
        alpha_cwd.mkdir()

        r = subprocess.run(
            [sys.executable, str(CLI_PATH), "new", "deferred",
             "--vault", str(vault), "--title", "orphan deferral",
             "--project", "my-project"],
            capture_output=True, text=True,
            env={**os.environ, "LORE_USER": "ada", "LORE_TODAY": TODAY},
            cwd=str(alpha_cwd),
        )
        assert r.returncode == 0, r.stderr + r.stdout
        # The note itself must be written
        assert len((list((vault / "deferred").glob("*.md")) + list((vault / "deferred").glob("*/*.md")))) == 1
        # Some notice about missing/skipped session
        combined = r.stdout + r.stderr
        assert "session" in combined.lower() or "backlink" in combined.lower()
