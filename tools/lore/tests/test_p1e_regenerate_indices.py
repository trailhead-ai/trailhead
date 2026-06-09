"""P1-E tests: regenerate_indices.py — folder _index.md regeneration.

All fixtures use SYNTHETIC vocabulary (synth-*, invented slugs/content) per the
public-repo fixture discipline axiom. No real brain content, no real subsystem names.

Test contract (all must fail before the implementation, pass after):
- Fixture vault → _index.md regenerated for each present folder with expected
  ordering/grouping.
- Idempotent: second run byte-identical (no-op).
- A note missing grouping fields → still indexed (fallback), no crash.
- Pre-commit hook STAGES the regenerated indices so they land in the commit.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def load_regen():
    """Load regenerate_indices freshly (env-independent)."""
    mod_path = SCRIPTS_DIR / "regenerate_indices.py"
    for cached in ("regenerate_indices",):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location("regenerate_indices", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixture vault builders
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path, *dirs: str) -> Path:
    vault = tmp_path / "vault"
    for d in dirs:
        (vault / d).mkdir(parents=True)
    return vault


def _write_deferred(vault: Path, slug: str, *, value: str = "high",
                    effort: str = "S", status: str = "open",
                    revisit: str = "2099-06-01") -> Path:
    p = vault / "deferred" / f"{slug}.md"
    p.write_text(
        f"---\n"
        f"type: deferred\n"
        f"status: {status}\n"
        f"value: {value}\n"
        f"effort: {effort}\n"
        f"revisit-after: {revisit}\n"
        f"---\n\n"
        f"# {slug}\n\nSynthetic deferred item.\n"
    )
    return p


def _write_radar(vault: Path, slug: str, *, revisit: str = "2099-06-01",
                 status: str = "open") -> Path:
    p = vault / "radar" / f"{slug}.md"
    p.write_text(
        f"---\n"
        f"type: radar\n"
        f"status: {status}\n"
        f"revisit-after: {revisit}\n"
        f"added: 2099-01-01\n"
        f"---\n\n"
        f"# {slug}\n\nSynthetic radar item.\n"
    )
    return p


def _write_lesson(vault: Path, slug: str, *, date: str = "2099-01-01",
                  severity: str = "medium", subsystems: str = "synth-sub") -> Path:
    p = vault / "lessons" / f"{slug}.md"
    p.write_text(
        f"---\n"
        f"type: lesson\n"
        f"date: {date}\n"
        f"severity: {severity}\n"
        f"subsystems: [{subsystems}]\n"
        f"status: active\n"
        f"---\n\n"
        f"# {slug}\n\nSynthetic lesson.\n"
    )
    return p


def _write_plan(vault: Path, slug: str, *, status: str = "in-progress",
                updated: str = "2099-01-01") -> Path:
    p = vault / "plans" / f"{slug}.md"
    p.write_text(
        f"---\n"
        f"type: plan\n"
        f"status: {status}\n"
        f"updated: '{updated}'\n"
        f"created: 2099-01-01T00:00:00.000Z\n"
        f"---\n\n"
        f"# {slug}\n\nSynthetic plan.\n"
    )
    return p


def _write_spec(vault: Path, slug: str, *, status: str = "draft",
                updated: str = "2099-01-01") -> Path:
    p = vault / "specs" / f"{slug}.md"
    p.write_text(
        f"---\n"
        f"type: spec\n"
        f"status: {status}\n"
        f"updated: '{updated}'\n"
        f"created: 2099-01-01T00:00:00.000Z\n"
        f"---\n\n"
        f"# {slug}\n\nSynthetic spec.\n"
    )
    return p


def _write_design(vault: Path, slug: str, *, status: str = "draft",
                  updated: str = "2099-01-01") -> Path:
    p = vault / "designs" / f"{slug}.md"
    p.write_text(
        f"---\n"
        f"type: design\n"
        f"status: {status}\n"
        f"updated: '{updated}'\n"
        f"created: 2099-01-01T00:00:00.000Z\n"
        f"---\n\n"
        f"# {slug}\n\nSynthetic design.\n"
    )
    return p


def _git_vault(tmp_path: Path, *dirs: str) -> Path:
    vault = _make_vault(tmp_path, *dirs)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@e.st"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Tester"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
                   check=True, capture_output=True)
    return vault


def _run_main(vault: Path) -> int:
    """Run regenerate_indices.main() with LORE_VAULT pointing at vault."""
    import os
    env_backup = os.environ.get("LORE_VAULT")
    os.environ["LORE_VAULT"] = str(vault)
    try:
        mod = load_regen()
        return mod.main()
    finally:
        if env_backup is None:
            os.environ.pop("LORE_VAULT", None)
        else:
            os.environ["LORE_VAULT"] = env_backup


# ---------------------------------------------------------------------------
# Basic generation: each folder produces a _index.md
# ---------------------------------------------------------------------------

class TestBasicGeneration:
    def test_deferred_index_created(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        _write_deferred(vault, "synth-deferred-alpha")
        rc = _run_main(vault)
        assert rc == 0
        assert (vault / "deferred" / "_index.md").exists()

    def test_radar_index_created(self, tmp_path):
        vault = _make_vault(tmp_path, "radar")
        _write_radar(vault, "synth-radar-alpha")
        rc = _run_main(vault)
        assert rc == 0
        assert (vault / "radar" / "_index.md").exists()

    def test_lessons_index_created(self, tmp_path):
        vault = _make_vault(tmp_path, "lessons")
        _write_lesson(vault, "synth-lesson-alpha")
        rc = _run_main(vault)
        assert rc == 0
        assert (vault / "lessons" / "_index.md").exists()

    def test_plans_index_created(self, tmp_path):
        vault = _make_vault(tmp_path, "plans")
        _write_plan(vault, "synth-plan-alpha")
        rc = _run_main(vault)
        assert rc == 0
        assert (vault / "plans" / "_index.md").exists()

    def test_specs_index_created(self, tmp_path):
        vault = _make_vault(tmp_path, "specs")
        _write_spec(vault, "synth-spec-alpha")
        rc = _run_main(vault)
        assert rc == 0
        assert (vault / "specs" / "_index.md").exists()

    def test_designs_index_created(self, tmp_path):
        vault = _make_vault(tmp_path, "designs")
        _write_design(vault, "synth-design-alpha")
        rc = _run_main(vault)
        assert rc == 0
        assert (vault / "designs" / "_index.md").exists()

    def test_missing_folder_skipped_gracefully(self, tmp_path):
        """Folders not present → no index attempted, no crash."""
        vault = _make_vault(tmp_path, "plans")
        _write_plan(vault, "synth-plan-only")
        rc = _run_main(vault)
        assert rc == 0
        # only plans/ was created → only plans/_index.md exists
        assert (vault / "plans" / "_index.md").exists()
        assert not (vault / "deferred" / "_index.md").exists()

    def test_vault_not_found_returns_nonzero(self, tmp_path):
        import os
        nonexistent = tmp_path / "no-such-vault"
        os.environ["LORE_VAULT"] = str(nonexistent)
        try:
            mod = load_regen()
            rc = mod.main()
        finally:
            os.environ.pop("LORE_VAULT", None)
        assert rc != 0


# ---------------------------------------------------------------------------
# Content: deferred ordering (value/effort)
# ---------------------------------------------------------------------------

class TestDeferredOrdering:
    def test_high_value_before_low_value(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        _write_deferred(vault, "synth-low", value="low", effort="S")
        _write_deferred(vault, "synth-high", value="high", effort="S")
        _run_main(vault)
        content = (vault / "deferred" / "_index.md").read_text()
        high_pos = content.find("synth-high")
        low_pos = content.find("synth-low")
        assert high_pos < low_pos, "high-value item should appear before low-value item"

    def test_open_section_present(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        _write_deferred(vault, "synth-open-item")
        _run_main(vault)
        content = (vault / "deferred" / "_index.md").read_text()
        assert "## Open" in content

    def test_closed_items_in_closed_section(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        _write_deferred(vault, "synth-resolved", status="resolved")
        _run_main(vault)
        content = (vault / "deferred" / "_index.md").read_text()
        assert "## Closed" in content

    def test_total_count_in_header(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        _write_deferred(vault, "synth-d1")
        _write_deferred(vault, "synth-d2", value="low")
        _run_main(vault)
        content = (vault / "deferred" / "_index.md").read_text()
        assert "**Total:**" in content
        assert "2" in content


# ---------------------------------------------------------------------------
# Content: plans/specs/designs — status grouping
# ---------------------------------------------------------------------------

class TestStatusGrouping:
    def test_in_progress_bucket_present(self, tmp_path):
        vault = _make_vault(tmp_path, "plans")
        _write_plan(vault, "synth-active-plan", status="in-progress")
        _run_main(vault)
        content = (vault / "plans" / "_index.md").read_text()
        assert "## In progress" in content

    def test_completed_bucket_present(self, tmp_path):
        vault = _make_vault(tmp_path, "plans")
        _write_plan(vault, "synth-done-plan", status="complete")
        _run_main(vault)
        content = (vault / "plans" / "_index.md").read_text()
        assert "## Completed" in content

    def test_uncategorized_bucket_for_unknown_status(self, tmp_path):
        vault = _make_vault(tmp_path, "plans")
        _write_plan(vault, "synth-weird-plan", status="synth-unknown-status")
        _run_main(vault)
        content = (vault / "plans" / "_index.md").read_text()
        assert "## Uncategorized" in content

    def test_item_link_appears_in_index(self, tmp_path):
        vault = _make_vault(tmp_path, "plans")
        _write_plan(vault, "synth-link-plan")
        _run_main(vault)
        content = (vault / "plans" / "_index.md").read_text()
        assert "synth-link-plan" in content


# ---------------------------------------------------------------------------
# Idempotency: second run is byte-identical
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_deferred_idempotent(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        _write_deferred(vault, "synth-idem-d1")
        _write_deferred(vault, "synth-idem-d2", value="medium")
        _run_main(vault)
        first = (vault / "deferred" / "_index.md").read_text()
        _run_main(vault)
        second = (vault / "deferred" / "_index.md").read_text()
        assert first == second, "deferred _index.md not byte-identical on second run"

    def test_plans_idempotent(self, tmp_path):
        vault = _make_vault(tmp_path, "plans")
        _write_plan(vault, "synth-idem-plan")
        _run_main(vault)
        first = (vault / "plans" / "_index.md").read_text()
        _run_main(vault)
        second = (vault / "plans" / "_index.md").read_text()
        assert first == second, "plans _index.md not byte-identical on second run"

    def test_radar_idempotent(self, tmp_path):
        vault = _make_vault(tmp_path, "radar")
        _write_radar(vault, "synth-idem-radar")
        _run_main(vault)
        first = (vault / "radar" / "_index.md").read_text()
        _run_main(vault)
        second = (vault / "radar" / "_index.md").read_text()
        assert first == second, "radar _index.md not byte-identical on second run"

    def test_lessons_idempotent(self, tmp_path):
        vault = _make_vault(tmp_path, "lessons")
        _write_lesson(vault, "synth-idem-lesson")
        _run_main(vault)
        first = (vault / "lessons" / "_index.md").read_text()
        _run_main(vault)
        second = (vault / "lessons" / "_index.md").read_text()
        assert first == second, "lessons _index.md not byte-identical on second run"


# ---------------------------------------------------------------------------
# Graceful degradation: missing grouping fields → fallback, no crash
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_deferred_missing_value_and_effort_still_indexed(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        p = vault / "deferred" / "synth-no-fields.md"
        p.write_text(
            "---\n"
            "type: deferred\n"
            "status: open\n"
            "---\n\n"
            "# synth-no-fields\n\nItem with no grouping fields.\n"
        )
        rc = _run_main(vault)
        assert rc == 0
        content = (vault / "deferred" / "_index.md").read_text()
        assert "synth-no-fields" in content

    def test_plan_missing_status_lands_in_uncategorized(self, tmp_path):
        vault = _make_vault(tmp_path, "plans")
        p = vault / "plans" / "synth-no-status.md"
        p.write_text(
            "---\n"
            "type: plan\n"
            "---\n\n"
            "# synth-no-status\n\nPlan with no status.\n"
        )
        rc = _run_main(vault)
        assert rc == 0
        content = (vault / "plans" / "_index.md").read_text()
        assert "synth-no-status" in content
        assert "## Uncategorized" in content

    def test_note_missing_date_fields_still_indexed(self, tmp_path):
        vault = _make_vault(tmp_path, "radar")
        p = vault / "radar" / "synth-no-date.md"
        p.write_text(
            "---\n"
            "type: radar\n"
            "status: open\n"
            "---\n\n"
            "# synth-no-date\n\nRadar item with no date.\n"
        )
        rc = _run_main(vault)
        assert rc == 0
        content = (vault / "radar" / "_index.md").read_text()
        assert "synth-no-date" in content

    def test_lesson_missing_subsystems_still_indexed(self, tmp_path):
        vault = _make_vault(tmp_path, "lessons")
        p = vault / "lessons" / "synth-no-sub.md"
        p.write_text(
            "---\n"
            "type: lesson\n"
            "date: 2099-01-01\n"
            "severity: low\n"
            "status: active\n"
            "---\n\n"
            "# synth-no-sub\n\nLesson with no subsystem.\n"
        )
        rc = _run_main(vault)
        assert rc == 0
        content = (vault / "lessons" / "_index.md").read_text()
        assert "synth-no-sub" in content

    def test_empty_folder_produces_empty_index(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        rc = _run_main(vault)
        assert rc == 0
        content = (vault / "deferred" / "_index.md").read_text()
        assert "_(empty)_" in content

    def test_all_folders_populated_no_crash(self, tmp_path):
        """End-to-end: all six folder types present → all indices generated."""
        vault = _make_vault(
            tmp_path, "deferred", "radar", "lessons", "plans", "specs", "designs"
        )
        _write_deferred(vault, "synth-all-d")
        _write_radar(vault, "synth-all-r")
        _write_lesson(vault, "synth-all-l")
        _write_plan(vault, "synth-all-p")
        _write_spec(vault, "synth-all-s")
        _write_design(vault, "synth-all-g")
        rc = _run_main(vault)
        assert rc == 0
        for folder in ("deferred", "radar", "lessons", "plans", "specs", "designs"):
            assert (vault / folder / "_index.md").exists(), f"missing index for {folder}"


# ---------------------------------------------------------------------------
# Pre-commit hook staging: regenerated indices are staged for the commit
# ---------------------------------------------------------------------------

def _install_regen_hook(vault: Path, plugin_root: Path) -> None:
    """Run install-vault-hooks.sh with LORE_PLUGIN_ROOT set."""
    result = subprocess.run(
        [str(plugin_root / "hooks" / "install-vault-hooks.sh"), str(vault)],
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "LORE_PLUGIN_ROOT": str(plugin_root),
            "HOME": str(vault.parent),
        },
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"install-vault-hooks.sh failed: {result.stderr}"


def _initial_commit(vault: Path, msg: str = "init") -> None:
    """Create an initial commit so the repo has a HEAD."""
    readme = vault / "README.md"
    readme.write_text("# vault\n")
    subprocess.run(["git", "-C", str(vault), "add", "README.md"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-m", msg],
                   check=True, capture_output=True)


def _commit_file(vault: Path, path: Path, msg: str = "add note") -> subprocess.CompletedProcess:
    """Stage a file and commit; returns the completed process (may fail — caller checks)."""
    subprocess.run(["git", "-C", str(vault), "add", "--", str(path)],
                   check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", msg],
        capture_output=True, text=True,
    )


def _git_status_clean(vault: Path) -> bool:
    r = subprocess.run(["git", "-C", str(vault), "status", "--porcelain"],
                       capture_output=True, text=True)
    return r.returncode == 0 and not r.stdout.strip()


def _get_tree_files(vault: Path) -> set[str]:
    r = subprocess.run(
        ["git", "-C", str(vault), "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return set(r.stdout.strip().splitlines())


class TestBucketedScan:
    """Slice 4: plans/specs/designs index regeneration recurses one level into
    YYYY-MM/ buckets and emits resolvable vault-relative wikilinks. Out-of-scope
    folders (deferred/radar/lessons) stay flat.

    Lore cannot import the brain auditor, so link correctness is asserted on the
    emitted string: it must be the resolvable `[[plans/2026-06/foo]]` form and
    must NOT be the broken `[[2026-06/foo]]` form.
    """

    def test_plans_index_lists_notes_across_two_buckets(self, tmp_path):
        vault = _make_vault(tmp_path, "plans/2026-05", "plans/2026-06")
        (vault / "plans" / "2026-05" / "synth-alpha-plan.md").write_text(
            "---\ntype: plan\nstatus: in-progress\nupdated: '2026-05-10'\n"
            "created: 2026-05-01T00:00:00.000Z\n---\n\n# synth-alpha-plan\n\nSynthetic.\n"
        )
        (vault / "plans" / "2026-06" / "synth-beta-plan.md").write_text(
            "---\ntype: plan\nstatus: in-progress\nupdated: '2026-06-20'\n"
            "created: 2026-06-01T00:00:00.000Z\n---\n\n# synth-beta-plan\n\nSynthetic.\n"
        )
        rc = _run_main(vault)
        assert rc == 0
        content = (vault / "plans" / "_index.md").read_text()
        assert "synth-alpha-plan" in content
        assert "synth-beta-plan" in content

    def test_bucketed_link_is_resolvable_not_broken_form(self, tmp_path):
        vault = _make_vault(tmp_path, "plans/2026-06")
        (vault / "plans" / "2026-06" / "synth-widget-plan.md").write_text(
            "---\ntype: plan\nstatus: in-progress\nupdated: '2026-06-01'\n"
            "created: 2026-06-01T00:00:00.000Z\n---\n\n# synth-widget-plan\n\nSynthetic.\n"
        )
        _run_main(vault)
        content = (vault / "plans" / "_index.md").read_text()
        assert "[[2026-06/synth-widget-plan]]" not in content, "broken parent-only link emitted"
        assert "[[plans/2026-06/synth-widget-plan]]" in content, "resolvable vault-relative link missing"

    def test_specs_index_across_two_buckets(self, tmp_path):
        vault = _make_vault(tmp_path, "specs/2026-05", "specs/2026-06")
        (vault / "specs" / "2026-05" / "synth-alpha-spec.md").write_text(
            "---\ntype: spec\nstatus: draft\nupdated: '2026-05-10'\n"
            "created: 2026-05-01T00:00:00.000Z\n---\n\n# synth-alpha-spec\n\nSynthetic.\n"
        )
        (vault / "specs" / "2026-06" / "synth-beta-spec.md").write_text(
            "---\ntype: spec\nstatus: draft\nupdated: '2026-06-20'\n"
            "created: 2026-06-01T00:00:00.000Z\n---\n\n# synth-beta-spec\n\nSynthetic.\n"
        )
        _run_main(vault)
        content = (vault / "specs" / "_index.md").read_text()
        assert "synth-alpha-spec" in content
        assert "synth-beta-spec" in content
        assert "[[specs/2026-06/synth-beta-spec]]" in content

    def test_underscore_files_and_dirs_skipped_in_buckets(self, tmp_path):
        vault = _make_vault(tmp_path, "plans/_archive", "plans/_test")
        _write_plan(vault, "synth-real-plan")
        (vault / "plans" / "_archive" / "synth-archived-plan.md").write_text(
            "---\ntype: plan\nstatus: in-progress\nupdated: '2026-06-01'\n"
            "created: 2026-06-01T00:00:00.000Z\n---\n\n# synth-archived-plan\n\nSynthetic.\n"
        )
        (vault / "plans" / "_test" / "synth-test-plan.md").write_text(
            "---\ntype: plan\nstatus: in-progress\nupdated: '2026-06-01'\n"
            "created: 2026-06-01T00:00:00.000Z\n---\n\n# synth-test-plan\n\nSynthetic.\n"
        )
        _run_main(vault)
        content = (vault / "plans" / "_index.md").read_text()
        assert "synth-real-plan" in content
        assert "synth-archived-plan" not in content
        assert "synth-test-plan" not in content

    def test_flat_and_bucketed_both_listed_transition_safe(self, tmp_path):
        vault = _make_vault(tmp_path, "plans/2026-06")
        _write_plan(vault, "synth-still-flat-plan")
        (vault / "plans" / "2026-06" / "synth-bucketed-plan.md").write_text(
            "---\ntype: plan\nstatus: in-progress\nupdated: '2026-06-01'\n"
            "created: 2026-06-01T00:00:00.000Z\n---\n\n# synth-bucketed-plan\n\nSynthetic.\n"
        )
        _run_main(vault)
        content = (vault / "plans" / "_index.md").read_text()
        assert "synth-still-flat-plan" in content
        assert "synth-bucketed-plan" in content
        assert "[[plans/synth-still-flat-plan]]" in content
        assert "[[plans/2026-06/synth-bucketed-plan]]" in content

    def test_deferred_index_recurses_into_buckets(self, tmp_path):
        """Slice 6 inverts the Slice 4 guard: deferred is now bucketed, so its
        index lists both flat AND YYYY-MM-bucketed notes with resolvable links."""
        vault = _make_vault(tmp_path, "deferred/2026-06")
        _write_deferred(vault, "synth-flat-deferred")
        (vault / "deferred" / "2026-06" / "synth-nested-deferred.md").write_text(
            "---\ntype: deferred\nstatus: open\nvalue: high\neffort: S\n"
            "revisit-after: 2099-06-01\n---\n\n# synth-nested-deferred\n\nSynthetic.\n"
        )
        _run_main(vault)
        content = (vault / "deferred" / "_index.md").read_text()
        assert "synth-flat-deferred" in content
        assert "synth-nested-deferred" in content
        assert "[[deferred/2026-06/synth-nested-deferred]]" in content

    def test_radar_index_recurses_into_buckets(self, tmp_path):
        vault = _make_vault(tmp_path, "radar/2026-06")
        _write_radar(vault, "synth-flat-radar")
        (vault / "radar" / "2026-06" / "synth-nested-radar.md").write_text(
            "---\ntype: radar\nstatus: open\nrevisit-after: 2099-06-01\n"
            "added: 2099-01-01\n---\n\n# synth-nested-radar\n\nSynthetic.\n"
        )
        _run_main(vault)
        content = (vault / "radar" / "_index.md").read_text()
        assert "synth-flat-radar" in content
        assert "synth-nested-radar" in content

    def test_lessons_index_recurses_into_buckets(self, tmp_path):
        vault = _make_vault(tmp_path, "lessons/2026-06")
        _write_lesson(vault, "synth-flat-lesson")
        (vault / "lessons" / "2026-06" / "synth-nested-lesson.md").write_text(
            "---\ntype: lesson\ndate: 2099-01-01\nseverity: medium\n"
            "subsystems: [synth-sub]\nstatus: active\n---\n\n# synth-nested-lesson\n\nSynthetic.\n"
        )
        _run_main(vault)
        content = (vault / "lessons" / "_index.md").read_text()
        assert "synth-flat-lesson" in content
        assert "synth-nested-lesson" in content

    def test_living_index_does_not_descend_two_levels(self, tmp_path):
        """Over-recursion guard: only the month bucket level is scanned."""
        vault = _make_vault(tmp_path, "deferred/2026-06/deeper")
        (vault / "deferred" / "2026-06" / "deeper" / "synth-too-deep.md").write_text(
            "---\ntype: deferred\nstatus: open\nvalue: high\neffort: S\n"
            "revisit-after: 2099-06-01\n---\n\n# synth-too-deep\n\nSynthetic.\n"
        )
        _run_main(vault)
        content = (vault / "deferred" / "_index.md").read_text()
        assert "synth-too-deep" not in content


class TestPreCommitHookStagesIndices:
    """The pre-commit hook must stage regenerated _index.md files so they
    are included in the commit being made (P1-D's lesson: unstaged changes
    leave the vault dirty).
    """

    def test_index_included_in_commit_tree(self, tmp_path):
        """After committing a vault note, the _index.md is in the git tree."""
        vault = _git_vault(tmp_path, "plans")
        _initial_commit(vault)
        _install_regen_hook(vault, PLUGIN_ROOT)

        plan = _write_plan(vault, "synth-hook-plan")
        result = _commit_file(vault, plan, "add synth plan")
        assert result.returncode == 0, f"commit failed: {result.stderr}"

        tree = _get_tree_files(vault)
        assert "plans/_index.md" in tree, (
            "plans/_index.md should be in the commit tree — hook must stage it"
        )

    def test_vault_clean_after_commit(self, tmp_path):
        """After the commit triggered by the hook, git status is clean.

        Pre-condition: the index WAS generated (so there's something to be clean about).
        """
        vault = _git_vault(tmp_path, "plans")
        _initial_commit(vault)
        _install_regen_hook(vault, PLUGIN_ROOT)

        plan = _write_plan(vault, "synth-clean-plan")
        result = _commit_file(vault, plan, "add synth plan")
        assert result.returncode == 0, f"commit failed: {result.stderr}"

        # The index must exist (regen ran) AND the vault must be clean.
        assert (vault / "plans" / "_index.md").exists(), (
            "plans/_index.md must be generated by the pre-commit hook"
        )
        assert _git_status_clean(vault), (
            "vault should be clean after commit — _index.md must be staged by hook"
        )

    def test_regen_idempotent_in_hook_context(self, tmp_path):
        """Second commit triggers regen again; index byte-identical → no extra commit."""
        vault = _git_vault(tmp_path, "plans")
        _initial_commit(vault)
        _install_regen_hook(vault, PLUGIN_ROOT)

        plan = _write_plan(vault, "synth-idem-hook-plan")
        _commit_file(vault, plan, "first commit")

        # Verify index was created by the first commit
        assert (vault / "plans" / "_index.md").exists(), (
            "_index.md must be created by the first commit's pre-commit hook"
        )

        # Second commit: add another note
        plan2 = _write_plan(vault, "synth-idem-hook-plan-two", status="complete")
        result = _commit_file(vault, plan2, "second commit")
        assert result.returncode == 0, f"second commit failed: {result.stderr}"

        assert _git_status_clean(vault), (
            "vault should be clean after second commit"
        )

    def test_hook_idempotent_on_reinvocation(self, tmp_path):
        """install-vault-hooks.sh is idempotent: second install does not corrupt the hook."""
        vault = _git_vault(tmp_path, "plans")
        _initial_commit(vault)
        _install_regen_hook(vault, PLUGIN_ROOT)
        _install_regen_hook(vault, PLUGIN_ROOT)  # second install

        plan = _write_plan(vault, "synth-reinvoke-plan")
        result = _commit_file(vault, plan, "after reinvoke")
        assert result.returncode == 0, f"commit after reinvoke failed: {result.stderr}"

        tree = _get_tree_files(vault)
        assert "plans/_index.md" in tree
