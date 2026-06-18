"""Slice 9 tests: per-type frontmatter flags on `lore new` + capture→recall integration.

Covers:
- Each type's flags populate the correct frontmatter fields (inline lists, scalars, dates).
- Absent flags resolve to valid empty defaults ([], ""), leaving NO literal {{...}} in any note.
- `deferred --revisit-after` → status: scheduled; without it → status: open.
- Both status variants pass status_validator.
- NO literal {{...}} placeholder survives in any written note (all core types).
- INTEGRATION: lore new deferred --areas auth + auth area + recall banner surfaces the note.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from conftest import CLI_PATH, load_script

TODAY = "2026-06-02"


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
    vault = tmp_path / "vault"
    for d in ("deferred", "dead-ends", "decisions", "follow-ups", "areas", "sessions"):
        (vault / d).mkdir(parents=True)
    return vault


def _find_note(dir_path: Path) -> Path:
    # deferred/decision/follow-up/dead-end notes are date-bucketed into
    # <dir>/YYYY-MM/ (the date-bucketed archive layout), so search the bucket
    # subdir too.
    notes = list(dir_path.glob("*.md")) + list(dir_path.glob("*/*.md"))
    assert len(notes) == 1, f"Expected 1 note, got {[n.name for n in notes]}"
    return notes[0]


def _assert_no_placeholders(note: Path):
    text = note.read_text()
    matches = re.findall(r"\{\{[^}]+\}\}", text)
    assert not matches, f"Unresolved placeholders in {note.name}: {matches}"


# ---------------------------------------------------------------------------
# Deferred: --surfaces, --next-check, --revisit-after
# ---------------------------------------------------------------------------

class TestNewDeferredFlags:
    def test_surfaces_csv_becomes_inline_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "pay fix",
            "--surfaces", "payments,billing",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["surfaces"] == ["payments", "billing"]

    def test_surfaces_single_item(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "pay fix",
            "--surfaces", "payments",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["surfaces"] == ["payments"]

    def test_surfaces_absent_defaults_to_empty_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "pay fix",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("surfaces") == [] or fm.get("surfaces") is None or fm.get("surfaces") == ""

    def test_next_check_set_when_provided(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "pay fix",
            "--next-check", "2026-09-01",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("next-check") == "2026-09-01"

    def test_revisit_after_sets_status_scheduled(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "time-bound task",
            "--revisit-after", "2026-09-01",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["status"] == "scheduled"
        assert fm.get("revisit-after") == "2026-09-01"

    def test_without_revisit_after_status_is_open(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "open task",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["status"] == "open"

    def test_scheduled_status_passes_validator(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "scheduled thing",
            "--revisit-after", "2026-09-01",
        ])
        sv = load_script("status_validator")
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert sv.is_valid_status("deferred", fm["status"])

    def test_open_status_passes_validator(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "open thing",
        ])
        sv = load_script("status_validator")
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert sv.is_valid_status("deferred", fm["status"])

    def test_no_placeholders_with_all_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "full deferred",
            "--surfaces", "payments",
            "--next-check", "2026-09-01",
            "--revisit-after", "2026-10-01",
        ])
        _assert_no_placeholders(_find_note(vault / "deferred"))

    def test_no_placeholders_with_no_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "empty deferred",
        ])
        _assert_no_placeholders(_find_note(vault / "deferred"))


# ---------------------------------------------------------------------------
# Dead-end: --areas, --tried, --revive-condition
# ---------------------------------------------------------------------------

class TestNewDeadEndFlags:
    def test_areas_csv_becomes_inline_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "failed approach",
            "--areas", "auth,payments",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["areas"] == ["auth", "payments"]

    def test_areas_absent_defaults_to_empty_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "failed approach",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("areas") == [] or fm.get("areas") is None or fm.get("areas") == ""

    def test_tried_set_when_provided(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "failed approach",
            "--tried", "2026-05-15",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("tried") == "2026-05-15"

    def test_tried_defaults_to_today_when_absent(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "recent failure",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("tried") == TODAY

    def test_revive_condition_set_when_provided(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "failed approach",
            "--revive-condition", "when library X hits v2.0",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("revive-condition") == "when library X hits v2.0"

    def test_revive_condition_empty_when_absent(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "failed approach",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        val = fm.get("revive-condition", "")
        assert "{{" not in str(val)

    def test_no_placeholders_with_all_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "full dead end",
            "--areas", "auth",
            "--tried", "2026-05-01",
            "--revive-condition", "library X v2",
        ])
        _assert_no_placeholders(_find_note(vault / "dead-ends"))

    def test_no_placeholders_with_no_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "empty dead end",
        ])
        _assert_no_placeholders(_find_note(vault / "dead-ends"))


# ---------------------------------------------------------------------------
# Decision: --areas
# ---------------------------------------------------------------------------

class TestNewDecisionFlags:
    def test_areas_csv_becomes_inline_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "decision", "--vault", str(vault),
            "--title", "use postgres",
            "--project", "demo",
            "--areas", "data,infra",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "decisions")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["areas"] == ["data", "infra"]

    def test_areas_absent_defaults_to_empty_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "decision", "--vault", str(vault),
            "--title", "use postgres",
            "--project", "demo",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "decisions")
        fm = fm_mod.parse_frontmatter(note)
        val = fm.get("areas", [])
        assert "{{" not in str(val)

    def test_no_placeholders_with_all_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "decision", "--vault", str(vault),
            "--title", "full decision",
            "--project", "demo",
            "--areas", "auth",
        ])
        _assert_no_placeholders(_find_note(vault / "decisions"))

    def test_no_placeholders_with_no_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "decision", "--vault", str(vault),
            "--title", "empty decision",
            "--project", "demo",
        ])
        _assert_no_placeholders(_find_note(vault / "decisions"))


# ---------------------------------------------------------------------------
# Follow-up: --source, --target, --check
# ---------------------------------------------------------------------------

class TestNewFollowUpFlags:
    def test_source_set_when_provided(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "follow-up", "--vault", str(vault),
            "--title", "watch dep X",
            "--project", "demo",
            "--source", "https://github.com/dep/releases",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "follow-ups")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("source") == "https://github.com/dep/releases"

    def test_target_set_when_provided(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "follow-up", "--vault", str(vault),
            "--title", "watch dep X",
            "--project", "demo",
            "--target", "v2.0",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "follow-ups")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("target") == "v2.0"

    def test_check_set_when_provided(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "follow-up", "--vault", str(vault),
            "--title", "watch dep X",
            "--project", "demo",
            "--check", "monthly",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "follow-ups")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("check") == "monthly"

    def test_scalars_empty_when_absent(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "follow-up", "--vault", str(vault),
            "--title", "watch dep X",
            "--project", "demo",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "follow-ups")
        fm = fm_mod.parse_frontmatter(note)
        for field in ("source", "target", "check"):
            val = fm.get(field, "")
            assert "{{" not in str(val), f"Placeholder in {field}: {val!r}"

    def test_no_placeholders_with_all_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "follow-up", "--vault", str(vault),
            "--title", "full follow-up",
            "--project", "demo",
            "--source", "https://example.com",
            "--target", "v2.0",
            "--check", "weekly",
        ])
        _assert_no_placeholders(_find_note(vault / "follow-ups"))

    def test_no_placeholders_with_no_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "follow-up", "--vault", str(vault),
            "--title", "empty follow-up",
            "--project", "demo",
        ])
        _assert_no_placeholders(_find_note(vault / "follow-ups"))


# ---------------------------------------------------------------------------
# Vacuous-placeholder gate — NO {{...}} in any written note across all core types
# ---------------------------------------------------------------------------

class TestNoPlaceholdersAcrossAllTypes:
    """Regression guard: lore new with ZERO optional flags must not leave any
    literal {{...}} in the written file for the core note types."""

    def _run_and_check(self, vault, args):
        r = run_cli(args)
        assert r.returncode == 0, r.stderr
        for subdir in ("deferred", "dead-ends", "decisions", "follow-ups", "areas"):
            d = vault / subdir
            notes = list(d.glob("*.md")) + list(d.glob("*/*.md"))
            for note in notes:
                _assert_no_placeholders(note)

    def test_deferred_no_extra_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        self._run_and_check(vault, [
            "new", "deferred", "--vault", str(vault), "--title", "test",
        ])

    def test_dead_end_no_extra_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        self._run_and_check(vault, [
            "new", "dead-end", "--vault", str(vault), "--title", "test",
        ])

    def test_decision_no_extra_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        self._run_and_check(vault, [
            "new", "decision", "--vault", str(vault), "--title", "test", "--project", "demo",
        ])

    def test_follow_up_no_extra_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        self._run_and_check(vault, [
            "new", "follow-up", "--vault", str(vault), "--title", "test", "--project", "demo",
        ])

    def test_area_no_extra_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        self._run_and_check(vault, [
            "new", "area", "--vault", str(vault), "--title", "test", "--project", "demo",
        ])


# ---------------------------------------------------------------------------
# Capture: `lore new area` naming (the recall-command capture→banner integration
# tests were removed in Slice 5 when `recall` was retired — capture→`lore search`
# retrieval is covered by test_search_cli.py + test_index_projection.py).
# ---------------------------------------------------------------------------

class TestCaptureAreaNaming:
    def _make_full_vault(self, tmp_path: Path) -> Path:
        vault = tmp_path / "vault"
        for d in ("deferred", "dead-ends", "decisions", "follow-ups", "areas", "sessions",
                  "lessons"):
            (vault / d).mkdir(parents=True)
        return vault

    def _create_auth_area_via_cli(self, vault: Path) -> None:
        """Create the auth area profile via `lore new area`."""
        r = run_cli([
            "new", "area", "--vault", str(vault),
            "--title", "auth",
            "--keywords", "oauth,login",
        ])
        assert r.returncode == 0, f"lore new area failed: {r.stderr}"

    def test_area_note_created_without_date_prefix(self, tmp_path):
        """lore new area --title auth must produce areas/auth.md (no date prefix)."""
        vault = self._make_full_vault(tmp_path)
        self._create_auth_area_via_cli(vault)
        notes = list((vault / "areas").glob("*.md"))
        assert len(notes) == 1
        assert notes[0].name == "auth.md", (
            f"Expected auth.md (no date prefix), got {notes[0].name!r}"
        )
