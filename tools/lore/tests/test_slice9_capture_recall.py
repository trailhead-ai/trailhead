"""Slice 9 tests: per-type frontmatter flags on `lore new` + capture→recall integration.

Covers:
- Each type's flags populate the correct frontmatter fields (inline lists, scalars, dates).
- Absent flags resolve to valid empty defaults ([], ""), leaving NO literal {{...}} in any note.
- `deferred --revisit-after` → status: scheduled; without it → status: open.
- Both status variants pass status_validator.
- NO literal {{...}} placeholder survives in any written note (all 5 types).
- INTEGRATION: lore new deferred --surfaces payments + payments subsystem with keywords
  → infer_subsystems detects payments AND render_subsystem_block surfaces the deferred note.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from conftest import CLI_PATH, SCRIPTS_DIR, load_script

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
    for d in ("deferred", "dead-ends", "decisions", "radar", "subsystems", "sessions"):
        (vault / d).mkdir(parents=True)
    return vault


def _find_note(dir_path: Path) -> Path:
    # deferred/decision/radar/dead-end notes are date-bucketed into
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
# Dead-end: --subsystems, --tried, --revive-condition
# ---------------------------------------------------------------------------

class TestNewDeadEndFlags:
    def test_subsystems_csv_becomes_inline_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "failed approach",
            "--subsystems", "auth,payments",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["subsystems"] == ["auth", "payments"]

    def test_subsystems_absent_defaults_to_empty_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "failed approach",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "dead-ends")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("subsystems") == [] or fm.get("subsystems") is None or fm.get("subsystems") == ""

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
        # Absent or empty string is fine — must not be a literal placeholder
        val = fm.get("revive-condition", "")
        assert "{{" not in str(val)

    def test_no_placeholders_with_all_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "full dead end",
            "--subsystems", "auth",
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
# Decision: --subsystems
# ---------------------------------------------------------------------------

class TestNewDecisionFlags:
    def test_subsystems_csv_becomes_inline_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "decision", "--vault", str(vault),
            "--title", "use postgres",
            "--project", "demo",
            "--subsystems", "data,infra",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "decisions")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["subsystems"] == ["data", "infra"]

    def test_subsystems_absent_defaults_to_empty_list(self, tmp_path):
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
        # Empty list, None, or empty string — but not a placeholder
        val = fm.get("subsystems", [])
        assert "{{" not in str(val)

    def test_no_placeholders_with_all_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "decision", "--vault", str(vault),
            "--title", "full decision",
            "--project", "demo",
            "--subsystems", "auth",
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
# Radar: --source, --target, --check
# ---------------------------------------------------------------------------

class TestNewRadarFlags:
    def test_source_set_when_provided(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "radar", "--vault", str(vault),
            "--title", "watch dep X",
            "--project", "demo",
            "--source", "https://github.com/dep/releases",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "radar")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("source") == "https://github.com/dep/releases"

    def test_target_set_when_provided(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "radar", "--vault", str(vault),
            "--title", "watch dep X",
            "--project", "demo",
            "--target", "v2.0",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "radar")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("target") == "v2.0"

    def test_check_set_when_provided(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "radar", "--vault", str(vault),
            "--title", "watch dep X",
            "--project", "demo",
            "--check", "monthly",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "radar")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("check") == "monthly"

    def test_scalars_empty_when_absent(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "radar", "--vault", str(vault),
            "--title", "watch dep X",
            "--project", "demo",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "radar")
        fm = fm_mod.parse_frontmatter(note)
        for field in ("source", "target", "check"):
            val = fm.get(field, "")
            assert "{{" not in str(val), f"Placeholder in {field}: {val!r}"

    def test_no_placeholders_with_all_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "radar", "--vault", str(vault),
            "--title", "full radar",
            "--project", "demo",
            "--source", "https://example.com",
            "--target", "v2.0",
            "--check", "weekly",
        ])
        _assert_no_placeholders(_find_note(vault / "radar"))

    def test_no_placeholders_with_no_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "radar", "--vault", str(vault),
            "--title", "empty radar",
            "--project", "demo",
        ])
        _assert_no_placeholders(_find_note(vault / "radar"))


# ---------------------------------------------------------------------------
# Subsystem: --key-files
# ---------------------------------------------------------------------------

class TestNewSubsystemFlags:
    def test_key_files_csv_becomes_inline_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "auth-module",
            "--project", "demo",
            "--key-files", "lib/auth.ex,lib/token.ex",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "subsystems")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["key-files"] == ["lib/auth.ex", "lib/token.ex"]

    def test_key_files_absent_defaults_to_empty_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "auth-module",
            "--project", "demo",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "subsystems")
        fm = fm_mod.parse_frontmatter(note)
        val = fm.get("key-files", [])
        assert "{{" not in str(val)

    def test_key_files_and_keywords_together(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "auth-module",
            "--project", "demo",
            "--keywords", "auth,oauth",
            "--key-files", "lib/auth.ex",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "subsystems")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["keywords"] == ["auth", "oauth"]
        assert fm["key-files"] == ["lib/auth.ex"]

    def test_no_placeholders_with_all_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "full subsystem",
            "--project", "demo",
            "--keywords", "auth",
            "--key-files", "lib/auth.ex",
        ])
        _assert_no_placeholders(_find_note(vault / "subsystems"))

    def test_no_placeholders_with_no_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "empty subsystem",
            "--project", "demo",
        ])
        _assert_no_placeholders(_find_note(vault / "subsystems"))


# ---------------------------------------------------------------------------
# Vacuous-placeholder gate — NO {{...}} in any written note across all 5 types
# ---------------------------------------------------------------------------

class TestNoPlaceholdersAcrossAllTypes:
    """Regression guard: lore new with ZERO optional flags must not leave any
    literal {{...}} in the written file for any of the 5 note types."""

    def _run_and_check(self, vault, args):
        r = run_cli(args)
        assert r.returncode == 0, r.stderr
        # Find the single note in all subdirs. Date-bucketed types live in
        # <subdir>/YYYY-MM/; subsystems stays flat — search both layouts.
        for subdir in ("deferred", "dead-ends", "decisions", "radar", "subsystems"):
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

    def test_radar_no_extra_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        self._run_and_check(vault, [
            "new", "radar", "--vault", str(vault), "--title", "test", "--project", "demo",
        ])

    def test_subsystem_no_extra_flags(self, tmp_path):
        vault = _make_vault(tmp_path)
        self._run_and_check(vault, [
            "new", "subsystem", "--vault", str(vault), "--title", "test", "--project", "demo",
        ])


# ---------------------------------------------------------------------------
# INTEGRATION TEST: capture→recall end-to-end
# ---------------------------------------------------------------------------

class TestCaptureRecallIntegration:
    """The headline integration test: proves that a deferred item captured via
    `lore new deferred --surfaces payments` resurfaces in recall when the branch
    name matches the payments subsystem's keywords.

    The subsystem profile is created via the REAL CLI (`lore new subsystem`)
    so this test would have caught the date-prefix bug (Slice 9b): the CLI
    previously wrote a DATE-prefixed file (2026-06-02-payments.md) whose stem
    never matched the bare 'payments' name referenced in `surfaces: [payments]`.
    """

    def _make_full_vault(self, tmp_path: Path) -> Path:
        vault = tmp_path / "vault"
        for d in ("deferred", "dead-ends", "decisions", "radar", "subsystems", "sessions"):
            (vault / d).mkdir(parents=True)
        return vault

    def _create_payments_subsystem_via_cli(self, vault: Path) -> None:
        """Create the payments subsystem profile via the real `lore new subsystem` CLI."""
        r = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r.returncode == 0, f"lore new subsystem failed: {r.stderr}"

    def test_infer_subsystems_includes_payments_on_matching_branch(self, tmp_path):
        """After `lore new subsystem --title payments --keywords pay`,
        infer_subsystems on 'feature/pay-flow' must include 'payments' (bare name)."""
        vault = self._make_full_vault(tmp_path)
        self._create_payments_subsystem_via_cli(vault)
        recall = load_script("recall")
        result = recall.infer_subsystems(vault, "feature/pay-flow")
        assert "payments" in result, (
            f"Expected 'payments' in infer_subsystems result, got {result}"
        )

    def test_deferred_with_surfaces_appears_in_render_block(self, tmp_path):
        """After `lore new deferred --surfaces payments`, the written note's
        wikilink must appear in render_subsystem_block for ['payments']."""
        vault = self._make_full_vault(tmp_path)
        self._create_payments_subsystem_via_cli(vault)

        # Capture via lore new with --surfaces payments
        r = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "Fix payment retry logic",
            "--surfaces", "payments",
            "--project", "demo",
        ])
        assert r.returncode == 0, r.stderr + r.stdout

        # The written note should have surfaces: [payments] in frontmatter
        fm_mod = load_script("frontmatter")
        note = _find_note(vault / "deferred")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("surfaces") == ["payments"], (
            f"Expected surfaces=['payments'], got {fm.get('surfaces')!r}"
        )

        # render_subsystem_block must include the deferred note's wikilink
        recall = load_script("recall")
        block = recall.render_subsystem_block(vault, ["payments"], project="demo")
        assert block is not None, "render_subsystem_block returned None"
        note_stem = note.stem
        assert note_stem in block, (
            f"Deferred note {note_stem!r} not found in recall block:\n{block}"
        )

    def test_full_capture_recall_loop(self, tmp_path):
        """End-to-end: branch with 'pay' → infer_subsystems finds 'payments' →
        render_subsystem_block includes the deferred note captured with --surfaces payments.
        This proves the capture→recall loop closes."""
        vault = self._make_full_vault(tmp_path)
        self._create_payments_subsystem_via_cli(vault)

        # Step 1: capture a deferred item targeting payments
        r = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "Fix payment retry logic",
            "--surfaces", "payments",
            "--project", "demo",
        ])
        assert r.returncode == 0, r.stderr

        # Step 2: infer subsystems from branch containing keyword 'pay'
        recall = load_script("recall")
        matched = recall.infer_subsystems(vault, "feature/pay-flow")
        assert "payments" in matched, (
            f"infer_subsystems('feature/pay-flow') should include 'payments', got {matched}"
        )

        # Step 3: render the recall block for those subsystems
        block = recall.render_subsystem_block(vault, matched, project="demo")
        assert block is not None, "render_subsystem_block returned None for matched subsystems"

        # Step 4: the deferred note's wikilink must be in the block
        note = _find_note(vault / "deferred")
        note_stem = note.stem
        assert note_stem in block, (
            f"Capture→recall BROKEN: deferred note {note_stem!r} not found in recall block.\n"
            f"Block:\n{block}"
        )
        # Also confirm the "Open deferred items" section exists
        assert "Open deferred items" in block, (
            "Expected 'Open deferred items' section in recall block"
        )
