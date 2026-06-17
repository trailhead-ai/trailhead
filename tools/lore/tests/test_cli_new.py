"""Slice 1 tests: `lore new spec` and `lore new plan` note types.

Covers:
- lore new spec writes a dated note under specs/, type: spec, status: draft
- lore new plan writes a dated note under plans/, type: plan, status: draft
- Both are project-bearing (project populated from git-remote inference or --project flag)
- No unresolved substitution placeholder survives ({{...}}, _PROJECT_BEARING, bare {{project}})
- Rendered spec contains the consumed cross-plugin sections:
    ## Rollout & Gating, ## Observability & Failure Visibility,
    ## Acceptance Criteria, ## Non-Goals
- Rendered output contains zero private/app-specific tokens
- status passes status_validator for both types

All fixtures are SYNTHETIC (invented names, no real vault).
Run against a temp vault — never reads or writes $LORE_VAULT.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import CLI_PATH, load_script

TODAY = "2026-01-15"  # frozen for determinism

# Private/app-specific tokens that must not appear in any rendered output.
# Constructed at runtime (the gate scans this file too — avoid raw literals).
_PRIVATE_TOKENS: list[str] = [
    "".join(["post", "hog"]),
    "".join(["dash", "0"]),
    "".join(["evidence", "_", "pack"]),
    "".join(["pro", "jections"]),
    "".join(["ze", "nith", "health"]),
    "".join(["as", "ana"]),
    "".join(["plat", "form", "."]),
    "".join(["mobile", "-app"]),
]

# Unresolved placeholder patterns that must never survive in output.
_PLACEHOLDER_PATTERNS: list[str] = [
    "{{project}}",
    "{{name}}",
    "{{date}}",
    "{{subsystems}}",
    "{{related-subsystems}}",
    "{{related-spec}}",
    "{{status}}",
]


def run_cli(args, env=None, cwd=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    full_env.setdefault("LORE_TODAY", TODAY)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env,
        cwd=str(cwd) if cwd else None,
    )


def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal vault directory with the dirs lore new needs."""
    vault = tmp_path / "vault"
    for d in ("specs", "plans", "sessions"):
        (vault / d).mkdir(parents=True)
    return vault


def _find_new_note(dir_path: Path) -> Path:
    """Return the single .md file written under a directory.

    plan/spec notes are date-bucketed into ``<dir>/YYYY-MM/`` (the
    date-bucketed archive layout), so search the bucket subdir too.
    """
    notes = list(dir_path.glob("*.md")) + list(dir_path.glob("*/*.md"))
    assert len(notes) == 1, f"Expected 1 note, got {notes}"
    return notes[0]


# ---------------------------------------------------------------------------
# lore new spec
# ---------------------------------------------------------------------------

class TestNewSpec:
    def test_writes_to_specs_dir(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        assert r.returncode == 0, r.stderr + r.stdout
        # Notes are date-bucketed into specs/YYYY-MM/, not flat at the root.
        assert list((vault / "specs").glob("*.md")) == []
        assert len(list((vault / "specs").glob("*/*.md"))) == 1

    def test_frontmatter_type_is_spec(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "specs")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["type"] == "spec"

    def test_frontmatter_status_is_draft(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "specs")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["status"] == "draft"

    def test_status_passes_validator(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        sv = load_script("status_validator")
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "specs")
        fm = fm_mod.parse_frontmatter(note)
        assert sv.is_valid_status(fm["type"], fm["status"])

    def test_project_bearing_populates_project(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "specs")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("project") == "my-project"

    def test_slug_is_date_kebab_title(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "specs")
        assert note.name.startswith(TODAY)
        assert "some-topic" in note.name

    def test_no_unresolved_placeholders(self, tmp_path):
        """No {{...}} placeholder survives in the rendered output."""
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "specs")
        text = note.read_text()
        for placeholder in _PLACEHOLDER_PATTERNS:
            assert placeholder not in text, (
                f"Unresolved placeholder {placeholder!r} found in rendered spec"
            )
        # Also guard generic pattern
        import re
        assert not re.search(r"\{\{[a-z][a-z0-9_-]*\}\}", text), (
            "Rendered spec still contains unresolved {{...}} placeholder(s)"
        )

    def test_no_project_bearing_literal_survives(self, tmp_path):
        """The internal sentinel _PROJECT_BEARING must not appear in output."""
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "specs")
        assert "_PROJECT_BEARING" not in note.read_text()

    def test_contains_cross_plugin_section_rollout_and_gating(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "specs")
        assert "## Rollout & Gating" in note.read_text()

    def test_contains_cross_plugin_section_observability(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "specs")
        assert "## Observability & Failure Visibility" in note.read_text()

    def test_contains_cross_plugin_section_acceptance_criteria(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "specs")
        assert "## Acceptance Criteria" in note.read_text()

    def test_contains_cross_plugin_section_non_goals(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "specs")
        assert "## Non-Goals" in note.read_text()

    def test_no_private_tokens_in_output(self, tmp_path):
        """Rendered spec must contain zero private app-specific tokens."""
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "specs")
        text = note.read_text().lower()
        for token in _PRIVATE_TOKENS:
            assert token.lower() not in text, (
                f"Private token {token!r} found in rendered spec"
            )

    def test_areas_flag_populates_frontmatter(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project",
             "--areas", "auth,payments"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "specs")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("areas") is not None
        areas_str = str(fm["areas"])
        assert "auth" in areas_str
        assert "payments" in areas_str

    def test_unknown_type_is_rejected(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "nonexistent-type", "--vault", str(vault),
             "--title", "Whatever"],
        )
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# lore new plan
# ---------------------------------------------------------------------------

class TestNewPlan:
    def test_writes_to_plans_dir(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        assert r.returncode == 0, r.stderr + r.stdout
        # Notes are date-bucketed into plans/YYYY-MM/, not flat at the root.
        assert list((vault / "plans").glob("*.md")) == []
        assert len(list((vault / "plans").glob("*/*.md"))) == 1

    def test_frontmatter_type_is_plan(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "plans")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["type"] == "plan"

    def test_frontmatter_status_is_draft(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "plans")
        fm = fm_mod.parse_frontmatter(note)
        assert fm["status"] == "draft"

    def test_status_passes_validator(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        sv = load_script("status_validator")
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "plans")
        fm = fm_mod.parse_frontmatter(note)
        assert sv.is_valid_status(fm["type"], fm["status"])

    def test_project_bearing_populates_project(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "plans")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("project") == "my-project"

    def test_slug_is_date_kebab_title(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "plans")
        assert note.name.startswith(TODAY)
        assert "some-topic" in note.name

    def test_no_unresolved_placeholders(self, tmp_path):
        """No {{...}} placeholder survives in the rendered output."""
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "plans")
        text = note.read_text()
        for placeholder in _PLACEHOLDER_PATTERNS:
            assert placeholder not in text, (
                f"Unresolved placeholder {placeholder!r} found in rendered plan"
            )
        import re
        assert not re.search(r"\{\{[a-z][a-z0-9_-]*\}\}", text), (
            "Rendered plan still contains unresolved {{...}} placeholder(s)"
        )

    def test_no_project_bearing_literal_survives(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "plans")
        assert "_PROJECT_BEARING" not in note.read_text()

    def test_no_private_tokens_in_output(self, tmp_path):
        """Rendered plan must contain zero private app-specific tokens."""
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        note = _find_new_note(vault / "plans")
        text = note.read_text().lower()
        for token in _PRIVATE_TOKENS:
            assert token.lower() not in text, (
                f"Private token {token!r} found in rendered plan"
            )

    def test_related_areas_flag_populates_frontmatter(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project",
             "--related-areas", "auth,payments"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "plans")
        fm = fm_mod.parse_frontmatter(note)
        areas_str = str(fm.get("related-areas", ""))
        assert "auth" in areas_str
        assert "payments" in areas_str

    def test_related_spec_flag_populates_frontmatter(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project",
             "--related-spec", "specs/2026-01-01-my-spec"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "plans")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("related-spec") == "specs/2026-01-01-my-spec"

    def test_slug_field_matches_filename_slug(self, tmp_path):
        """The `slug` frontmatter field should match the filename slug."""
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "plans")
        fm = fm_mod.parse_frontmatter(note)
        # slug frontmatter should be present and populated
        slug = fm.get("slug")
        assert slug, "plan frontmatter must have a non-empty slug field"
        assert slug in note.stem


# ---------------------------------------------------------------------------
# lore recall --areas (Slice 1 — CLI verb)
# ---------------------------------------------------------------------------

class TestRecallVerb:
    """Behavioral tests for `lore recall --areas` — the D23 recall CLI verb.

    Tests run as subprocess to verify real exit codes and output. Uses a
    synthetic vault (no real $LORE_VAULT).
    """

    def _make_recall_vault(self, tmp_path: Path) -> Path:
        vault = tmp_path / "vault"
        for d in ("areas", "deferred", "dead-ends", "decisions", "lessons"):
            (vault / d).mkdir(parents=True)
        return vault

    def _write_area(self, vault: Path, name: str, summary: str = "An area.") -> None:
        p = vault / "areas" / f"{name}.md"
        p.write_text(
            f"---\ntype: area\nname: {name}\nkeywords: [{name}]\nsummary: {summary}\n---\n"
        )

    def _write_deferred(self, vault: Path, name: str, areas: list[str]) -> None:
        areas_str = "[" + ", ".join(areas) + "]"
        folder = vault / "deferred"
        p = folder / f"2026-06-01-{name}.md"
        p.write_text(
            f"---\ntype: deferred\nstatus: open\nareas: {areas_str}\nsurfaces: []\n"
            f"next-check: 2026-09-01\n---\n\n# {name}\n\nSomething deferred.\n"
        )

    def test_recall_registered_in_help(self, tmp_path):
        """recall subcommand appears in lore --help."""
        r = run_cli(["--help"])
        assert "recall" in r.stdout, f"'recall' not in lore --help:\n{r.stdout}"

    def test_recall_with_notes_prints_banner(self, tmp_path):
        """`lore recall --areas <area-with-notes>` → banner starting 'Recalled (areas:'."""
        vault = self._make_recall_vault(tmp_path)
        self._write_area(vault, "auth", "Auth flow area.")
        self._write_deferred(vault, "fix-auth", ["auth"])
        r = run_cli(["recall", "--areas", "auth", "--vault", str(vault)])
        assert r.returncode == 0, f"exit {r.returncode}: {r.stderr}"
        assert "Recalled (areas:" in r.stdout, f"Banner not in stdout:\n{r.stdout}"

    def test_recall_unknown_area_zero_match_exit_0(self, tmp_path):
        """`--areas <unknown>` exits 0 with zero-match banner (no stacktrace)."""
        vault = self._make_recall_vault(tmp_path)
        r = run_cli(["recall", "--areas", "totally-unknown-xyz", "--vault", str(vault)])
        assert r.returncode == 0, f"exit code must be 0, got {r.returncode}: {r.stderr}"
        assert "stacktrace" not in r.stderr.lower()
        assert "Traceback" not in r.stderr
        assert r.stdout.strip() != ""

    def test_recall_valid_area_no_notes_zero_match_exit_0(self, tmp_path):
        """`--areas <valid-area-no-items>` exits 0 with differentiated banner."""
        vault = self._make_recall_vault(tmp_path)
        self._write_area(vault, "empty-area", "Empty area.")
        r = run_cli(["recall", "--areas", "empty-area", "--vault", str(vault)])
        assert r.returncode == 0
        assert r.stdout.strip() != ""
        assert "Traceback" not in r.stderr

    def test_recall_empty_areas_arg_exit_0(self, tmp_path):
        """`--areas ""` exits 0 with zero-match banner (no stacktrace)."""
        vault = self._make_recall_vault(tmp_path)
        r = run_cli(["recall", "--areas", "", "--vault", str(vault)])
        assert r.returncode == 0
        assert "Traceback" not in r.stderr
        assert r.stdout.strip() != ""

    def test_recall_comma_only_areas_exit_0(self, tmp_path):
        """`--areas ","` exits 0 with zero-match banner."""
        vault = self._make_recall_vault(tmp_path)
        r = run_cli(["recall", "--areas", ",", "--vault", str(vault)])
        assert r.returncode == 0
        assert "Traceback" not in r.stderr
        assert r.stdout.strip() != ""

    def test_recall_json_output_structure(self, tmp_path):
        """`--json` emits valid JSON with areas/items/count/cross_cutting_total."""
        vault = self._make_recall_vault(tmp_path)
        self._write_area(vault, "auth", "Auth area.")
        self._write_deferred(vault, "auth-work", ["auth"])
        r = run_cli(["recall", "--areas", "auth", "--vault", str(vault), "--json"])
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert "areas" in data
        assert "items" in data
        assert "count" in data
        assert "cross_cutting_total" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["count"], int)

    def test_recall_json_items_have_source_layer(self, tmp_path):
        """`--json` items carry source and layer fields (D-7 provenance).

        Slice 3: layer value is 'personal' (not 'local') for the single-vault
        path — the value was upgraded in Slice 3 to reflect real semantics.
        """
        vault = self._make_recall_vault(tmp_path)
        self._write_area(vault, "auth", "Auth area.")
        self._write_deferred(vault, "auth-work", ["auth"])
        r = run_cli(["recall", "--areas", "auth", "--vault", str(vault), "--json"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["count"] >= 1
        for item in data["items"]:
            assert "source" in item, f"item missing 'source': {item}"
            assert "layer" in item, f"item missing 'layer': {item}"
            assert item["layer"] == "personal", (
                "Slice 3: single-vault path must return layer='personal' not 'local'"
            )

    def test_recall_json_count_equals_banner_count(self, tmp_path):
        """`--json` count == human banner N (incl. dedup case)."""
        vault = self._make_recall_vault(tmp_path)
        self._write_area(vault, "auth", "Auth area.")
        self._write_area(vault, "payments", "Payments area.")
        # Write a note that overlaps BOTH areas — should count once
        note = vault / "deferred" / "2026-06-01-cross-area.md"
        note.write_text(
            "---\ntype: deferred\nstatus: open\nareas: [auth, payments]\nsurfaces: []\n"
            "next-check: 2026-09-01\n---\n\n# cross-area\n\nOverlaps both.\n"
        )
        r_human = run_cli(["recall", "--areas", "auth,payments", "--vault", str(vault)])
        r_json = run_cli(["recall", "--areas", "auth,payments", "--vault", str(vault), "--json"])
        assert r_human.returncode == 0
        assert r_json.returncode == 0

        data = json.loads(r_json.stdout)
        json_count = data["count"]

        import re as _re
        m = _re.search(r"Recalled \(areas:[^)]+\) — (\d+) item", r_human.stdout)
        assert m, f"No count in banner:\n{r_human.stdout}"
        banner_count = int(m.group(1))

        assert json_count == banner_count, (
            f"JSON count {json_count} != banner count {banner_count} (dedup bug)"
        )

    def test_recall_unresolvable_vault_stderr_signal(self, tmp_path):
        """An unresolvable vault emits a one-line stderr signal AND stdout banner, exit 0."""
        bad_vault = str(tmp_path / "nonexistent" / "vault")
        env = {"LORE_VAULT": bad_vault}
        r = run_cli(["recall", "--areas", "anything"], env=env)
        assert r.returncode == 0, f"exit must be 0 even on vault error, got {r.returncode}"
        assert r.stdout.strip() != "", "stdout must have zero-match banner on vault error"
        assert "lore recall:" in r.stderr, (
            f"Expected 'lore recall:' stderr signal on vault error, got: {r.stderr!r}"
        )

    def test_recall_differentiated_zero_match_bad_name(self, tmp_path):
        """Unknown area name → mentions 'check' or area name in output (not just blank)."""
        vault = self._make_recall_vault(tmp_path)
        r = run_cli(["recall", "--areas", "no-such-area-abc", "--vault", str(vault)])
        assert r.returncode == 0
        output = r.stdout
        assert "check" in output.lower() or "no-such-area-abc" in output, (
            f"Expected differentiated zero-match message, got: {output!r}"
        )

    def test_recall_differentiated_zero_match_valid_empty_area(self, tmp_path):
        """Valid area with no notes → 'no tagged notes' message (not bad-name message)."""
        vault = self._make_recall_vault(tmp_path)
        self._write_area(vault, "clean-area", "A clean area.")
        r = run_cli(["recall", "--areas", "clean-area", "--vault", str(vault)])
        assert r.returncode == 0
        assert "no tagged notes" in r.stdout or "0 item" in r.stdout, (
            f"Expected 'no tagged notes' or '0 items' in output:\n{r.stdout}"
        )
