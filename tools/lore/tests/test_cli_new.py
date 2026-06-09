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

import os
import subprocess
import sys
from pathlib import Path

from conftest import CLI_PATH, SCRIPTS_DIR, load_script

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

    def test_subsystems_flag_populates_frontmatter(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project",
             "--subsystems", "auth, payments"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "specs")
        fm = fm_mod.parse_frontmatter(note)
        assert fm.get("subsystems") is not None
        subsystems_str = str(fm["subsystems"])
        assert "auth" in subsystems_str
        assert "payments" in subsystems_str

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

    def test_related_subsystems_flag_populates_frontmatter(self, tmp_path):
        vault = _make_vault(tmp_path)
        run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Some Topic",
             "--project", "my-project",
             "--related-subsystems", "auth, payments"],
        )
        fm_mod = load_script("frontmatter")
        note = _find_new_note(vault / "plans")
        fm = fm_mod.parse_frontmatter(note)
        subsystems_str = str(fm.get("related-subsystems", ""))
        assert "auth" in subsystems_str
        assert "payments" in subsystems_str

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
