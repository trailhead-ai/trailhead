"""Slice 9b tests: subsystem notes must use bare-name (no date prefix) filenames.

Covers:
- lore new subsystem --title "Payments Service" → subsystems/payments-service.md
  (NO date prefix; stem matches the logical name)
- Re-running the same command → non-zero exit, no second file, "already exists" message
- deferred/dead-end still get YYYY-MM-DD- prefixed names (change is scoped to subsystem)
- INTEGRATION (must FAIL before fix, PASS after): create subsystem via real CLI
  (`lore new subsystem --title payments --keywords pay`), then create a deferred note
  with `--surfaces payments`, then assert infer_subsystems returns ['payments'] (bare name,
  no date prefix) AND render_subsystem_block surfaces the deferred note.
"""
from __future__ import annotations

import os
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


def _find_notes(dir_path: Path) -> list[Path]:
    # Date-bucketed types (deferred/decision/radar/dead-end) live in
    # <dir>/YYYY-MM/; subsystems (name-keyed) stay flat. Search both so this
    # helper works for either layout.
    return sorted(list(dir_path.glob("*.md")) + list(dir_path.glob("*/*.md")))


# ---------------------------------------------------------------------------
# Subsystem notes must use bare-name (no date prefix) filenames
# ---------------------------------------------------------------------------

class TestSubsystemNoDatePrefix:
    def test_subsystem_note_has_no_date_prefix(self, tmp_path):
        """lore new subsystem --title 'Payments Service' must produce
        subsystems/payments-service.md — NOT subsystems/2026-06-02-payments-service.md."""
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "Payments Service",
            "--keywords", "pay",
        ])
        assert r.returncode == 0, r.stderr
        notes = _find_notes(vault / "subsystems")
        assert len(notes) == 1, f"Expected 1 note, got {[n.name for n in notes]}"
        note = notes[0]
        assert note.name == "payments-service.md", (
            f"Expected payments-service.md, got {note.name!r}"
        )

    def test_subsystem_stem_matches_logical_name(self, tmp_path):
        """The file stem of a subsystem note must be the bare kebab title
        (no date, no numeric suffix), so recall can match it by stem."""
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "auth module",
            "--keywords", "auth",
        ])
        assert r.returncode == 0, r.stderr
        notes = _find_notes(vault / "subsystems")
        assert len(notes) == 1
        assert notes[0].stem == "auth-module", (
            f"Expected stem 'auth-module', got {notes[0].stem!r}"
        )

    def test_subsystem_frontmatter_name_agrees_with_stem(self, tmp_path):
        """The note's name: frontmatter field must match the filename stem."""
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r.returncode == 0, r.stderr
        fm_mod = load_script("frontmatter")
        notes = _find_notes(vault / "subsystems")
        assert len(notes) == 1
        fm = fm_mod.parse_frontmatter(notes[0])
        assert fm.get("name") == notes[0].stem, (
            f"name frontmatter {fm.get('name')!r} does not match stem {notes[0].stem!r}"
        )


# ---------------------------------------------------------------------------
# Re-creating an existing subsystem profile must be refused
# ---------------------------------------------------------------------------

class TestSubsystemRefuseOnDuplicate:
    def test_second_creation_exits_nonzero(self, tmp_path):
        """Running lore new subsystem twice with the same title must fail
        on the second run (non-zero exit)."""
        vault = _make_vault(tmp_path)
        r1 = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r1.returncode == 0, r1.stderr

        r2 = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r2.returncode != 0, (
            "Expected non-zero exit when recreating existing subsystem profile"
        )

    def test_second_creation_writes_no_second_file(self, tmp_path):
        """The second lore new subsystem must not create a second file
        (no date-suffix dup like payments-2.md or 2026-06-02-payments.md)."""
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        notes = _find_notes(vault / "subsystems")
        assert len(notes) == 1, (
            f"Expected exactly 1 note after duplicate create, got {[n.name for n in notes]}"
        )

    def test_second_creation_prints_refuse_message(self, tmp_path):
        """The refusal message must mention the profile name and the path,
        and guide the user to edit it directly."""
        vault = _make_vault(tmp_path)
        run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        r2 = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        combined = r2.stdout + r2.stderr
        assert "already exists" in combined.lower(), (
            f"Expected 'already exists' in refusal message, got: {combined!r}"
        )
        assert "payments" in combined.lower(), (
            f"Expected profile name 'payments' in refusal message, got: {combined!r}"
        )


# ---------------------------------------------------------------------------
# Other note types still get YYYY-MM-DD- prefixes (change is scoped to subsystem)
# ---------------------------------------------------------------------------

class TestDatedNamingUnchangedForOtherTypes:
    def test_deferred_note_still_has_date_prefix(self, tmp_path):
        """Deferred notes must still use YYYY-MM-DD-<slug>.md naming."""
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "a deferred thing",
        ])
        assert r.returncode == 0, r.stderr
        notes = _find_notes(vault / "deferred")
        assert len(notes) == 1
        assert notes[0].name.startswith(TODAY), (
            f"Deferred note should start with date {TODAY!r}, got {notes[0].name!r}"
        )

    def test_dead_end_note_still_has_date_prefix(self, tmp_path):
        """Dead-end notes must still use YYYY-MM-DD-<slug>.md naming."""
        vault = _make_vault(tmp_path)
        r = run_cli([
            "new", "dead-end", "--vault", str(vault),
            "--title", "a dead end",
        ])
        assert r.returncode == 0, r.stderr
        notes = _find_notes(vault / "dead-ends")
        assert len(notes) == 1
        assert notes[0].name.startswith(TODAY), (
            f"Dead-end note should start with date {TODAY!r}, got {notes[0].name!r}"
        )


# ---------------------------------------------------------------------------
# INTEGRATION TEST: capture→recall with subsystem created via real CLI
# (This must FAIL before the fix and PASS after.)
# ---------------------------------------------------------------------------

class TestCaptureRecallIntegrationViaCLI:
    """Proves that the capture→recall loop closes end-to-end when the subsystem
    profile is created via the real `lore new subsystem` CLI (not a hand-authored
    fixture). The bug: lore new subsystem wrote a DATE-prefixed filename, but recall
    derives identity from the bare stem, so the match never landed."""

    def test_infer_subsystems_returns_bare_name_not_dated(self, tmp_path):
        """After `lore new subsystem --title payments --keywords pay`,
        infer_subsystems('feature/pay-flow') must return ['payments'] (bare name),
        not ['2026-06-02-payments'] (dated stem)."""
        vault = _make_vault(tmp_path)

        r = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r.returncode == 0, r.stderr

        recall = load_script("recall")
        result = recall.infer_subsystems(vault, "feature/pay-flow")
        assert result == ["payments"], (
            f"Expected ['payments'] (bare name), got {result!r}. "
            "This indicates the subsystem file has a date prefix in its stem."
        )

    def test_full_capture_recall_loop_via_cli_subsystem(self, tmp_path):
        """End-to-end integration:
        1. Create subsystem via `lore new subsystem --title payments --keywords pay`
        2. Capture `lore new deferred --surfaces payments`
        3. infer_subsystems('feature/pay-flow') → ['payments'] (bare name)
        4. render_subsystem_block(['payments']) surfaces the deferred note.
        """
        vault = _make_vault(tmp_path)

        # Step 1: create subsystem via real CLI
        r_sub = run_cli([
            "new", "subsystem", "--vault", str(vault),
            "--title", "payments",
            "--keywords", "pay",
        ])
        assert r_sub.returncode == 0, r_sub.stderr

        # Step 2: capture a deferred item with --surfaces payments
        r_def = run_cli([
            "new", "deferred", "--vault", str(vault),
            "--title", "Fix payment retry logic",
            "--surfaces", "payments",
        ])
        assert r_def.returncode == 0, r_def.stderr

        # Step 3: infer subsystems from a branch containing 'pay'
        recall = load_script("recall")
        matched = recall.infer_subsystems(vault, "feature/pay-flow")
        assert "payments" in matched, (
            f"infer_subsystems('feature/pay-flow') should include 'payments', got {matched!r}. "
            "This proves the date-prefix bug: the stem was dated so it never matched 'pay'."
        )

        # Verify bare name (not dated name) is in matched
        assert not any(m.startswith(TODAY) for m in matched), (
            f"infer_subsystems returned a dated name: {matched!r}. "
            "Subsystem profiles must use bare names so recall can match 'surfaces: [payments]'."
        )

        # Step 4: render_subsystem_block must surface the deferred note
        block = recall.render_subsystem_block(vault, matched)
        assert block is not None, "render_subsystem_block returned None for matched subsystems"

        deferred_notes = _find_notes(vault / "deferred")
        assert len(deferred_notes) == 1
        note_stem = deferred_notes[0].stem
        assert note_stem in block, (
            f"Capture→recall BROKEN: deferred note {note_stem!r} not in recall block.\n"
            f"This means 'surfaces: [payments]' did not match subsystem 'payments'.\n"
            f"Block:\n{block}"
        )
        assert "Open deferred items" in block
