"""P1-F tests: lore review — vault migration-discipline report.

All fixtures use SYNTHETIC vocabulary (synth-*, invented slugs/content) per the
public-repo fixture discipline axiom. No real brain content, no real subsystem names.

Test contract (all must fail before the implementation, pass after):
- Fixture vault → report contains the expected sections (open deferred count,
  dead-ends, radar, lessons, stale-profile list).
- No hardcoded brain path in the shipped helper (grep assertion).
- The forge leak gate passes on the new files.
- Graceful: empty vault / missing folders → report still generates (zeros), no crash.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


def load_review():
    """Load review module freshly (env-independent)."""
    mod_path = SCRIPTS_DIR / "review.py"
    for cached in ("review",):
        sys.modules.pop(cached, None)
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("review", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cli(args, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env,
    )


# ---------------------------------------------------------------------------
# Fixture vault builders (synthetic vocabulary only)
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path, *dirs: str) -> Path:
    vault = tmp_path / "synth-vault"
    for d in dirs:
        (vault / d).mkdir(parents=True)
    return vault


def _write_deferred(vault: Path, slug: str, *, status: str = "open",
                    surfaces: str = "synth-alpha", next_check: str = "when synth-condition fires") -> Path:
    p = vault / "deferred" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: deferred\nstatus: {status}\n"
        f"surfaces: [{surfaces}]\nnext-check: {next_check}\n---\n\n# {slug}\n\nSynthetic.\n"
    )
    return p


def _write_dead_end(vault: Path, slug: str, *, revive: str = "when synth-lib ships") -> Path:
    p = vault / "dead-ends" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: dead-end\nstatus: open\nrevive-condition: {revive}\n---\n\n# {slug}\n\nSynthetic.\n"
    )
    return p


def _write_radar(vault: Path, slug: str, *, status: str = "open") -> Path:
    p = vault / "radar" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: radar\nstatus: {status}\nrevisit-after: 2099-01-01\n---\n\n# {slug}\n\nSynthetic.\n"
    )
    return p


def _write_lesson(vault: Path, slug: str, *, status: str = "active") -> Path:
    p = vault / "lessons" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: lesson\nstatus: {status}\n---\n\n# {slug}\n\nSynthetic.\n"
    )
    return p


def _write_subsystem(vault: Path, slug: str, *, last_touched: str = "2020-01-01") -> Path:
    p = vault / "subsystems" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: subsystem\nlast-touched: {last_touched}\n---\n\n# {slug}\n\nSynthetic.\n"
    )
    return p


def _write_collaboration(vault: Path, slug: str, *, status: str = "active",
                          date: str = "2020-01-01") -> Path:
    p = vault / "collaboration" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: collaboration\nstatus: {status}\ndate: {date}\n---\n\n# {slug}\n\nSynthetic.\n"
    )
    return p


def _init_git(vault: Path) -> None:
    subprocess.run(["git", "init", str(vault)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "test@synth.example"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Synth Tester"],
                   capture_output=True, check=True)
    # Create a placeholder file so the initial commit is non-empty
    placeholder = vault / ".vault"
    placeholder.write_text("synth vault\n")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "synth: initial fixture", "--no-gpg-sign"],
        capture_output=True, check=True,
    )


# ---------------------------------------------------------------------------
# Section-content tests (report contains expected headings / content)
# ---------------------------------------------------------------------------

class TestReportSections:
    """The report generator emits the expected sections."""

    def test_report_has_activity_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Activity since last review" in report

    def test_report_has_drift_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Action taxonomy drift" in report

    def test_report_has_graduation_candidates_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Graduation candidates" in report

    def test_report_has_stale_subsystems_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Stale subsystem profiles" in report

    def test_report_has_open_deferred_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Open deferred items" in report

    def test_report_has_dead_ends_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Dead-ends" in report

    def test_report_has_radar_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Open radar items" in report

    def test_report_has_lessons_section(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Active lessons" in report


# ---------------------------------------------------------------------------
# Counts / content accuracy
# ---------------------------------------------------------------------------

class TestReportContent:
    """Report accurately counts / lists vault contents."""

    def test_open_deferred_item_appears(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_deferred(vault, "synth-defer-alpha", status="open")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-defer-alpha" in report

    def test_resolved_deferred_item_not_listed(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_deferred(vault, "synth-defer-resolved", status="resolved")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-defer-resolved" not in report

    def test_dead_end_appears(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_dead_end(vault, "synth-dead-beta", revive="when synth-lib-v2 ships")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-dead-beta" in report

    def test_open_radar_item_appears(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_radar(vault, "synth-radar-gamma", status="open")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-radar-gamma" in report

    def test_dropped_radar_item_not_listed(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_radar(vault, "synth-radar-dropped", status="dropped")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-radar-dropped" not in report

    def test_active_lesson_appears(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_lesson(vault, "synth-lesson-delta", status="active")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-lesson-delta" in report

    def test_superseded_lesson_not_listed(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_lesson(vault, "synth-lesson-superseded", status="superseded")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-lesson-superseded" not in report

    def test_stale_subsystem_appears(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "synth-sub-epsilon", last_touched="2020-01-01")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-sub-epsilon" in report

    def test_fresh_subsystem_not_in_stale_list(self, tmp_path):
        import datetime as dt
        vault = _make_vault(tmp_path)
        today = dt.date.today().isoformat()
        _write_subsystem(vault, "synth-sub-fresh", last_touched=today)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        # Should not appear in the "stale" list (might appear elsewhere)
        assert "last-touched" not in report or "synth-sub-fresh" not in report.split("## Stale subsystem profiles")[1].split("##")[0]

    def test_old_collaboration_note_appears_as_graduation_candidate(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_collaboration(vault, "synth-collab-zeta", status="active", date="2020-01-01")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        graduation_section = report.split("## Graduation candidates")[1].split("##")[0]
        assert "synth-collab-zeta" in graduation_section

    def test_resurfaced_deferred_appears(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_deferred(vault, "synth-defer-resurfaced", status="resurfaced")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-defer-resurfaced" in report


# ---------------------------------------------------------------------------
# Graceful empty / missing-folder cases
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    """Empty vault or missing folders → report still generates (zeros), no crash."""

    def test_empty_vault_no_crash(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert isinstance(report, str)
        assert len(report) > 0

    def test_missing_deferred_dir_no_crash(self, tmp_path):
        vault = _make_vault(tmp_path, "dead-ends", "radar", "lessons")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Open deferred items" in report
        deferred_section = report.split("## Open deferred items")[1].split("##")[0]
        assert "No open deferred items" in deferred_section

    def test_missing_dead_ends_dir_no_crash(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Dead-ends" in report

    def test_missing_radar_dir_no_crash(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Open radar items" in report

    def test_missing_lessons_dir_no_crash(self, tmp_path):
        vault = _make_vault(tmp_path, "deferred")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Active lessons" in report

    def test_missing_subsystems_dir_no_crash(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Stale subsystem profiles" in report

    def test_missing_collaboration_dir_no_crash(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "## Graduation candidates" in report

    def test_non_git_vault_no_crash(self, tmp_path):
        """Vault without git init → activity section gracefully reports no commits."""
        vault = _make_vault(tmp_path)
        _write_deferred(vault, "synth-defer-nogit", status="open")
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert isinstance(report, str)
        assert "## Open deferred items" in report
        assert "synth-defer-nogit" in report


# ---------------------------------------------------------------------------
# Last-review-date resolution
# ---------------------------------------------------------------------------

class TestLastReviewDate:
    """last_review_date() correctly finds and parses dates from reviews/ dir."""

    def test_no_reviews_dir_returns_none(self, tmp_path):
        vault = _make_vault(tmp_path)
        mod = load_review()
        assert mod.last_review_date(vault) is None

    def test_empty_reviews_dir_returns_none(self, tmp_path):
        vault = _make_vault(tmp_path, "reviews")
        mod = load_review()
        assert mod.last_review_date(vault) is None

    def test_finds_most_recent_review(self, tmp_path):
        import datetime as dt
        vault = _make_vault(tmp_path, "reviews")
        (vault / "reviews" / "2026-05-01-1200.md").write_text("# old\n")
        (vault / "reviews" / "2026-05-20-0900.md").write_text("# newer\n")
        mod = load_review()
        result = mod.last_review_date(vault)
        assert result == dt.date(2026, 5, 20)

    def test_non_date_prefixed_files_ignored(self, tmp_path):
        vault = _make_vault(tmp_path, "reviews")
        (vault / "reviews" / "README.md").write_text("# index\n")
        mod = load_review()
        assert mod.last_review_date(vault) is None


# ---------------------------------------------------------------------------
# resolve_since
# ---------------------------------------------------------------------------

class TestResolveSince:
    """resolve_since returns the right git-compatible window string."""

    def test_explicit_date_passthrough(self, tmp_path):
        vault = _make_vault(tmp_path)
        mod = load_review()
        assert mod.resolve_since(vault, "2026-04-01") == "2026-04-01"

    def test_explicit_duration_passthrough(self, tmp_path):
        vault = _make_vault(tmp_path)
        mod = load_review()
        assert mod.resolve_since(vault, "14d") == "14d"

    def test_defaults_to_7_days_when_no_reviews(self, tmp_path):
        vault = _make_vault(tmp_path)
        mod = load_review()
        assert mod.resolve_since(vault, None) == "7 days ago"

    def test_defaults_to_last_review_date(self, tmp_path):
        vault = _make_vault(tmp_path, "reviews")
        (vault / "reviews" / "2026-05-15-1200.md").write_text("# review\n")
        mod = load_review()
        assert mod.resolve_since(vault, None) == "2026-05-15"


# ---------------------------------------------------------------------------
# CLI integration: `lore review` subcommand
# ---------------------------------------------------------------------------

class TestCliReview:
    """The `lore review` subcommand runs and produces a report."""

    def test_cli_review_exits_zero_with_vault(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        r = run_cli(["review"], env={"LORE_VAULT": str(vault)})
        assert r.returncode == 0, r.stderr

    def test_cli_review_output_contains_sections(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        r = run_cli(["review"], env={"LORE_VAULT": str(vault)})
        assert "## Activity since last review" in r.stdout
        assert "## Open deferred items" in r.stdout
        assert "## Dead-ends" in r.stdout

    def test_cli_review_with_since_flag(self, tmp_path):
        vault = _make_vault(tmp_path)
        _init_git(vault)
        r = run_cli(["review", "--since", "14d"], env={"LORE_VAULT": str(vault)})
        assert r.returncode == 0, r.stderr

    def test_cli_review_nonexistent_vault_exits_nonzero(self, tmp_path):
        r = run_cli(["review"], env={"LORE_VAULT": str(tmp_path / "does-not-exist")})
        assert r.returncode != 0

    def test_cli_review_populates_items_from_vault(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_deferred(vault, "synth-cli-defer-test", status="open")
        _write_dead_end(vault, "synth-cli-dead-test")
        _init_git(vault)
        r = run_cli(["review"], env={"LORE_VAULT": str(vault)})
        assert r.returncode == 0, r.stderr
        assert "synth-cli-defer-test" in r.stdout
        assert "synth-cli-dead-test" in r.stdout


# ---------------------------------------------------------------------------
# Structural / no-hardcoded-paths checks
# ---------------------------------------------------------------------------

class TestNoHardcodedPaths:
    """Shipped files use $LORE_VAULT (env-driven), not hardcoded brain paths."""

    def test_review_py_has_no_brain_vault_literal(self):
        review_path = SCRIPTS_DIR / "review.py"
        content = review_path.read_text()
        assert "BRAIN_VAULT" not in content

    def test_review_py_has_no_home_path_literal(self):
        review_path = SCRIPTS_DIR / "review.py"
        content = review_path.read_text()
        assert "/Users/" not in content

    def test_skill_md_has_no_brain_vault_literal(self):
        skill_path = PLUGIN_ROOT / "skills" / "review" / "SKILL.md"
        content = skill_path.read_text()
        assert "BRAIN_VAULT" not in content

    def test_skill_md_has_no_home_path_literal(self):
        skill_path = PLUGIN_ROOT / "skills" / "review" / "SKILL.md"
        content = skill_path.read_text()
        assert "/Users/" not in content

    def test_leak_gate_passes_on_new_files(self):
        """The forge leak gate certifies the new files as clean.

        This is the authoritative check — it uses the machine-local denylist
        so the test file itself never embeds the private tokens.

        Skipped when the leak gate or its denylist is absent (e.g. CI).
        """
        gate_path = Path.home() / "code" / "forge" / "plugins" / "forge" / "scripts" / "leak_gate.py"
        denylist_path = Path.home() / ".claude" / "leak-gate.denylist"
        if not gate_path.exists() or not denylist_path.exists():
            import pytest
            pytest.skip("leak gate not installed on this machine")

        result = subprocess.run(
            [
                sys.executable, str(gate_path),
                str(SCRIPTS_DIR),
                str(PLUGIN_ROOT / "skills"),
                "--denylist", str(denylist_path),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"leak gate found forbidden tokens:\n{result.stdout}{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Slice 6: living folders (deferred/dead-ends/radar/lessons) are date-bucketed.
# The /brain-review re-justification sweep must see notes living in YYYY-MM/
# buckets, not just flat at the folder root.
# ---------------------------------------------------------------------------

def _bucket(vault: Path, folder: str, slug: str, body: str) -> Path:
    p = vault / folder / "2026-06" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


class TestReviewSeesBucketedLivingNotes:
    def test_bucketed_deferred_appears(self, tmp_path):
        vault = _make_vault(tmp_path)
        _bucket(vault, "deferred", "synth-defer-bucketed",
                "---\ntype: deferred\nstatus: open\nsurfaces: [synth-alpha]\n"
                "next-check: when synth fires\n---\n\n# synth-defer-bucketed\n")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-defer-bucketed" in report

    def test_bucketed_dead_end_appears(self, tmp_path):
        vault = _make_vault(tmp_path)
        _bucket(vault, "dead-ends", "synth-dead-bucketed",
                "---\ntype: dead-end\nstatus: open\nrevive-condition: when x\n"
                "---\n\n# synth-dead-bucketed\n")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-dead-bucketed" in report

    def test_bucketed_radar_appears(self, tmp_path):
        vault = _make_vault(tmp_path)
        _bucket(vault, "radar", "synth-radar-bucketed",
                "---\ntype: radar\nstatus: open\nrevisit-after: 2099-01-01\n"
                "---\n\n# synth-radar-bucketed\n")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-radar-bucketed" in report

    def test_bucketed_lesson_appears(self, tmp_path):
        vault = _make_vault(tmp_path)
        _bucket(vault, "lessons", "synth-lesson-bucketed",
                "---\ntype: lesson\nstatus: active\n---\n\n# synth-lesson-bucketed\n")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-lesson-bucketed" in report

    def test_action_drift_sees_bucketed_dead_end(self, tmp_path):
        vault = _make_vault(tmp_path)
        _bucket(vault, "dead-ends", "synth-dead-action",
                "---\ntype: dead-end\nstatus: open\nactions: [synth-action]\n"
                "---\n\n# synth-dead-action\n")
        _init_git(vault)
        mod = load_review()
        actions = mod._collect_actions(vault)
        assert "synth-action" in actions

    def test_subsystems_stay_flat_in_review(self, tmp_path):
        """Over-recursion guard: a subsystem profile in a YYYY-MM subdir must
        NOT be picked up by the stale-subsystems section."""
        vault = _make_vault(tmp_path)
        _bucket(vault, "subsystems", "synth-sub-bucketed",
                "---\ntype: subsystem\nlast-touched: 2020-01-01\n"
                "---\n\n# synth-sub-bucketed\n")
        _init_git(vault)
        mod = load_review()
        report = mod.build_report(vault, since="7 days ago")
        assert "synth-sub-bucketed" not in report
