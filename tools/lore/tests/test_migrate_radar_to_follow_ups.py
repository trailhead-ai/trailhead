"""Behavioral tests for the radar→follow-ups vault migration (Slice 6).

Fixture-based: every test operates on a `tmp_path` vault with synthetic notes —
NEVER the live vault. Covers the council-raised concerns:

- dir move radar/ → follow-ups/ preserving YYYY-MM/ substructure
- `type: radar` → `type: follow-up` rewrite in frontmatter ONLY (body prose
  carrying the word "radar" is left byte-identical)
- the single `status: closed` off-vocab outlier is fixed to `status: dropped`
- a pre-migration manifest {old_path → new_path} is written for trivial reverse
- the old radar/_index.md is deleted (regenerated separately)
- idempotency: a second run is a no-op
- `--dry-run` touches NOTHING on disk
- path-traversal refusal (no `../` escape of the vault root)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
MIGRATE_SCRIPT = SCRIPTS_DIR / "migrate_radar_to_follow_ups.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ── fixtures ────────────────────────────────────────────────────────────────


def _radar_note(
    *, slug: str, status: str = "active", type_: str = "radar", body_extra: str = ""
) -> str:
    return (
        "---\n"
        f"type: {type_}\n"
        "project: demo\n"
        f"status: {status}\n"
        "source: npm\n"
        "target: x\n"
        "check: daily\n"
        "added: 2026-01-01\n"
        "last-checked:\n"
        "last-state:\n"
        "---\n\n"
        "## What we're watching\n"
        f"the {slug} thing — note the word radar appears in this body prose.\n"
        f"{body_extra}"
    )


def _make_vault(tmp_path: Path) -> Path:
    """A vault with radar/ notes incl. a YYYY-MM bucket + a status:closed outlier."""
    vault = tmp_path / "vault"
    radar = vault / "radar"
    bucket = radar / "2026-01"
    bucket.mkdir(parents=True)
    # flat note
    (radar / "watch-alpha.md").write_text(_radar_note(slug="alpha"))
    # bucketed note
    (bucket / "2026-01-05-watch-beta.md").write_text(_radar_note(slug="beta"))
    # the single off-vocab outlier
    (bucket / "2026-01-09-watch-closed.md").write_text(_radar_note(slug="closed", status="closed"))
    # an auto-generated index (must be deleted, not moved)
    (radar / "_index.md").write_text("# radar\n\n[[radar/watch-alpha]]\n")
    return vault


def _run_migrate(vault: Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT), "--vault", str(vault), *extra],
        capture_output=True,
        text=True,
    )


# ── real-mode migration ─────────────────────────────────────────────────────


class TestRealMigration:
    def test_dir_moved_and_old_radar_gone(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = _run_migrate(vault)
        assert r.returncode == 0, r.stderr
        assert not (vault / "radar").exists(), "old radar/ dir must be gone"
        assert (vault / "follow-ups").is_dir(), "follow-ups/ dir must exist"

    def test_substructure_preserved(self, tmp_path):
        vault = _make_vault(tmp_path)
        _run_migrate(vault)
        assert (vault / "follow-ups" / "watch-alpha.md").exists()
        assert (vault / "follow-ups" / "2026-01" / "2026-01-05-watch-beta.md").exists()
        assert (vault / "follow-ups" / "2026-01" / "2026-01-09-watch-closed.md").exists()

    def test_type_rewritten_in_frontmatter(self, tmp_path):
        vault = _make_vault(tmp_path)
        _run_migrate(vault)
        moved = (vault / "follow-ups" / "watch-alpha.md").read_text()
        assert "type: follow-up" in moved
        assert "type: radar" not in moved

    def test_body_prose_untouched(self, tmp_path):
        vault = _make_vault(tmp_path)
        _run_migrate(vault)
        moved = (vault / "follow-ups" / "watch-alpha.md").read_text()
        # the body word "radar" (prose) must survive — only frontmatter type changed
        assert "note the word radar appears in this body prose." in moved

    def test_status_closed_outlier_fixed_to_dropped(self, tmp_path):
        vault = _make_vault(tmp_path)
        _run_migrate(vault)
        outlier = (vault / "follow-ups" / "2026-01" / "2026-01-09-watch-closed.md").read_text()
        assert "status: dropped" in outlier
        assert "status: closed" not in outlier

    def test_other_statuses_untouched(self, tmp_path):
        vault = _make_vault(tmp_path)
        _run_migrate(vault)
        moved = (vault / "follow-ups" / "watch-alpha.md").read_text()
        assert "status: active" in moved

    def test_index_deleted_not_moved(self, tmp_path):
        vault = _make_vault(tmp_path)
        _run_migrate(vault)
        assert not (vault / "follow-ups" / "_index.md").exists(), (
            "the stale radar/_index.md must be deleted, not carried over"
        )

    def test_body_horizontal_rule_and_body_keys_untouched(self, tmp_path):
        """A note whose BODY contains a markdown `---` rule plus lines that mimic
        frontmatter keys (`type: radar`, `status: closed`) must have ONLY the real
        frontmatter rewritten — the body region after the closing fence is verbatim.
        """
        vault = tmp_path / "vault"
        radar = vault / "radar"
        radar.mkdir(parents=True)
        body = (
            "## Notes\n"
            "Earlier we had a separate radar entry.\n"
            "\n"
            "---\n"
            "\n"
            "An example frontmatter line in prose: `type: radar` and `status: closed`.\n"
        )
        (radar / "tricky.md").write_text(_radar_note(slug="tricky", body_extra=body))
        _run_migrate(vault)
        moved = (vault / "follow-ups" / "tricky.md").read_text()
        # real frontmatter rewritten
        assert "type: follow-up" in moved
        # body region preserved verbatim — the prose mention survives
        assert "`type: radar` and `status: closed`." in moved
        # the body markdown rule survived
        assert moved.count("\n---\n") >= 1

    def test_manifest_written_with_all_moves(self, tmp_path):
        vault = _make_vault(tmp_path)
        _run_migrate(vault)
        manifest_path = vault / "follow-ups-migration-manifest.json"
        assert manifest_path.exists(), "a reverse manifest must be written"
        data = json.loads(manifest_path.read_text())
        moves = {entry["old"]: entry["new"] for entry in data["moves"]}
        # the three notes (index excluded) appear as old→new pairs
        assert any(old.endswith("radar/watch-alpha.md") for old in moves)
        assert all(new.startswith("follow-ups/") for new in moves.values())
        assert len(data["moves"]) == 3, f"expected 3 note moves, got {data['moves']}"


# ── idempotency ──────────────────────────────────────────────────────────────


class TestIdempotency:
    def test_second_run_is_noop(self, tmp_path):
        vault = _make_vault(tmp_path)
        first = _run_migrate(vault)
        assert first.returncode == 0, first.stderr
        # snapshot follow-ups state after first run
        before = {
            p.relative_to(vault).as_posix(): p.read_text()
            for p in (vault / "follow-ups").rglob("*.md")
        }
        second = _run_migrate(vault)
        assert second.returncode == 0, second.stderr
        after = {
            p.relative_to(vault).as_posix(): p.read_text()
            for p in (vault / "follow-ups").rglob("*.md")
        }
        assert before == after, "second run must not change any follow-ups note"
        assert not (vault / "radar").exists()

    def test_refuses_to_clobber_existing_manifest(self, tmp_path):
        """A pre-existing manifest (interrupted prior run) must not be overwritten —
        re-planning would shrink the reverse record to the not-yet-moved subset."""
        vault = _make_vault(tmp_path)
        # simulate a prior run's manifest already on disk
        (vault / "follow-ups-migration-manifest.json").write_text(
            '{"moves": [{"old": "radar/already-moved.md", "new": "follow-ups/already-moved.md"}]}'
        )
        r = _run_migrate(vault)
        assert r.returncode != 0, "must refuse when a manifest already exists"
        assert "manifest" in (r.stdout + r.stderr).lower()
        # the original manifest is intact (not clobbered) and radar/ untouched
        data = json.loads((vault / "follow-ups-migration-manifest.json").read_text())
        assert data["moves"][0]["old"] == "radar/already-moved.md"
        assert (vault / "radar").is_dir()

    def test_second_run_reports_nothing_to_migrate(self, tmp_path):
        vault = _make_vault(tmp_path)
        _run_migrate(vault)
        second = _run_migrate(vault)
        out = (second.stdout + second.stderr).lower()
        assert "nothing to migrate" in out or "no radar" in out or "already" in out


# ── dry-run ──────────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_touches_nothing(self, tmp_path):
        vault = _make_vault(tmp_path)
        before = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*"))
        r = _run_migrate(vault, "--dry-run")
        assert r.returncode == 0, r.stderr
        after = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*"))
        assert before == after, "dry-run must not move/create/delete anything"
        assert (vault / "radar").is_dir(), "radar/ must be untouched after dry-run"
        assert not (vault / "follow-ups").exists()
        assert not (vault / "follow-ups-migration-manifest.json").exists()

    def test_dry_run_prints_planned_moves_and_rewrites(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = _run_migrate(vault, "--dry-run")
        out = r.stdout + r.stderr
        # planned moves shown
        assert "radar/watch-alpha.md" in out and "follow-ups/watch-alpha.md" in out
        # planned type rewrite + the status outlier fix shown
        assert "type: radar" in out and "type: follow-up" in out
        assert "status: closed" in out and "status: dropped" in out


# ── path safety ──────────────────────────────────────────────────────────────


class TestPathSafety:
    def test_refuses_nonexistent_vault(self, tmp_path):
        missing = tmp_path / "no-such-vault"
        r = _run_migrate(missing)
        # graceful: nothing-to-migrate (no radar dir) is fine; a crash is not.
        assert r.returncode == 0, r.stderr

    def test_refuses_path_escaping_vault_root(self, tmp_path):
        """A radar note whose resolved destination escapes the vault root is refused.

        Simulated via a symlinked radar dir pointing outside the vault: the
        migration must refuse rather than write outside the vault boundary.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        # radar/ is a symlink to a dir outside the vault root
        (vault / "radar").symlink_to(outside, target_is_directory=True)
        (outside / "evil.md").write_text(_radar_note(slug="evil"))
        r = _run_migrate(vault)
        assert r.returncode != 0, (
            "migration must refuse a radar/ that resolves outside the vault root"
        )
        assert "vault" in (r.stdout + r.stderr).lower()
        # nothing was written outside
        assert not (outside / "evil.md").read_text().count("follow-up"), (
            "the outside note must not have been rewritten"
        )
