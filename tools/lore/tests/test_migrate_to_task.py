"""Fixture-driven tests for the throwaway backlog+plan → task migration.

The migration script (``scripts/migrate_to_task.py``) is a one-shot,
dry-run-by-default cutover: it folds the retired ``backlog`` and ``plan`` kinds
into the unified ``task`` kind, remaps their statuses, sweeps every vault-wide
``related.backlog``/``related.plan`` reference into ``related.task`` (merging,
never clobbering), and reindexes. These tests exercise the pure transforms,
every precondition/post-check, and the git-as-rollback recovery against
throwaway fixture vaults — never the live vault or the live index (Axiom 6).

The script (and this test file) are deleted once the cutover is verified.
"""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from conftest import PLUGIN_ROOT

SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "migrate_to_task.py"


def _mod():
    """Load the throwaway migration script fresh (isolated from sys.modules)."""
    spec = importlib.util.spec_from_file_location("migrate_to_task", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# git + index isolation helpers (mirror the S7 migration test precedent)
# ---------------------------------------------------------------------------


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def make_git_vault(tmp_path) -> Path:
    vault = tmp_path / "lore-vault"
    vault.mkdir(parents=True)
    _git(vault, "init", "-q")
    _git(vault, "config", "user.email", "histauthor@example.com")
    _git(vault, "config", "user.name", "Hist Author")
    return vault


def commit_all(vault: Path, message: str = "seed") -> None:
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", message)


def isolate_index(monkeypatch, tmp_path) -> Path:
    """Point the lore index DB + committer email at throwaway values (Axiom 6)."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("LORE_EMAIL", "migrator@example.com")
    return state


def write_record(vault: Path, kind: str, name: str, *, status: str, related=None, body="\nBody.\n"):
    """Write a legacy-shaped ``<kind>/<name>.json`` + ``.md`` pair to the fixture vault."""
    sidecar = {
        "version": "v1",
        "kind": kind,
        "title": name,
        "status": status,
        "created-at": "2026-06-01T00:00:00Z",
        "created-by": "seed@example.com",
        "updated-at": "2026-06-01T00:00:00Z",
        "updated-by": "seed@example.com",
    }
    if related is not None:
        sidecar["related"] = related
    kind_dir = vault / kind
    kind_dir.mkdir(parents=True, exist_ok=True)
    (kind_dir / f"{name}.json").write_text(
        json.dumps(sidecar, sort_keys=True, separators=(",", ":"))
    )
    (kind_dir / f"{name}.md").write_text(body)
    return sidecar


def clean_composed(tmp_path) -> Path:
    """A composed-skill-tree fixture with NO legacy-kind references."""
    root = tmp_path / "composed"
    skill = root / "claude_code" / "plugins" / "lore" / "skills" / "record"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "Create a task with `lore record create --kind task NAME`.\n"
        "The eight kinds are area, blob, collaboration, decision, lesson,\n"
        "session, spec, task.\n"
    )
    return root


def dirty_composed(tmp_path) -> Path:
    """A composed tree that still emits a retired-kind command."""
    root = tmp_path / "composed-dirty"
    skill = root / "claude_code" / "plugins" / "craft" / "skills" / "brainstorm"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "Defer with `lore record create --kind backlog NAME`.\n"
    )
    return root


def seed_full_vault(vault: Path) -> None:
    """Seed one record for every legacy status plus link-merge fixtures."""
    write_record(vault, "backlog", "b-open", status="open")
    write_record(vault, "backlog", "b-track", status="tracking")
    write_record(vault, "backlog", "b-drop", status="dropped")
    write_record(vault, "plan", "p-draft", status="draft")
    write_record(vault, "plan", "p-ready", status="ready")
    write_record(vault, "plan", "p-prog", status="in-progress")
    write_record(vault, "plan", "p-done", status="complete")
    write_record(vault, "plan", "p-super", status="superseded")
    write_record(vault, "plan", "p-drop", status="dropped")
    # A decision that references both retired kinds AND already has a task list:
    # the sweep must MERGE, not clobber.
    write_record(
        vault,
        "decision",
        "d-links",
        status="active",
        related={"task": ["keep-me"], "backlog": ["from-backlog"], "plan": ["from-plan"]},
    )
    # A migrating backlog record that itself carries a related.plan key.
    write_record(vault, "backlog", "b-rel", status="open", related={"plan": ["x-plan"]})


def read_sidecar(vault: Path, record_id: str) -> dict:
    return json.loads((vault / f"{record_id}.json").read_text())


def tree_digest(vault: Path) -> dict:
    """Content map of every non-.git file under the vault (for no-write asserts)."""
    out = {}
    for p in sorted(vault.rglob("*")):
        if ".git" in p.parts or not p.is_file():
            continue
        out[str(p.relative_to(vault))] = p.read_bytes()
    return out


# ---------------------------------------------------------------------------
# Pure transforms
# ---------------------------------------------------------------------------


def test_status_map_backlog():
    mod = _mod()
    assert mod.map_status("backlog", "open") == "open"
    assert mod.map_status("backlog", "tracking") == "blocked"
    assert mod.map_status("backlog", "dropped") == "dropped"


def test_status_map_plan():
    mod = _mod()
    assert mod.map_status("plan", "draft") == "open"
    assert mod.map_status("plan", "ready") == "ready"
    assert mod.map_status("plan", "in-progress") == "in-progress"
    assert mod.map_status("plan", "complete") == "done"
    assert mod.map_status("plan", "superseded") == "superseded"
    assert mod.map_status("plan", "dropped") == "dropped"


def test_status_map_rejects_unmapped():
    mod = _mod()
    with pytest.raises(ValueError):
        mod.map_status("plan", "banana")


def test_sweep_related_merges_not_clobbers():
    mod = _mod()
    swept = mod.sweep_related(
        {"task": ["keep"], "backlog": ["fromb"], "plan": ["fromp"], "spec": ["s"]}
    )
    assert "backlog" not in swept and "plan" not in swept
    assert set(swept["task"]) == {"keep", "fromb", "fromp"}
    # An untouched key survives verbatim.
    assert swept["spec"] == ["s"]


def test_sweep_related_dedups_across_kinds():
    mod = _mod()
    swept = mod.sweep_related({"task": ["a"], "backlog": ["a"], "plan": ["a"]})
    assert swept["task"] == ["a"]


def test_sweep_related_noop_when_no_legacy_keys():
    mod = _mod()
    original = {"task": ["a"], "session": ["s"]}
    assert mod.sweep_related(original) == original


def test_related_union_covers_backlog_plan_task_only():
    mod = _mod()
    union = mod.related_union({"backlog": ["a"], "plan": ["b"], "task": ["c"], "spec": ["d"]})
    assert union == {"a", "b", "c"}


def test_check_subset_catches_dropped_link_even_with_equal_count():
    """The gate is a real subset check, not a count check (the S7 lesson)."""
    mod = _mod()
    # pre union {a, b}; post {b, z} — same COUNT (2), but link 'a' was dropped.
    missing = mod.check_subset({"a", "b"}, {"b", "z"})
    assert missing == {"a"}


def test_check_subset_passes_when_post_is_superset():
    mod = _mod()
    assert mod.check_subset({"a", "b"}, {"a", "b", "c"}) == set()


# ---------------------------------------------------------------------------
# Composed-tree precondition (injectable path; provable without bin/trailhead)
# ---------------------------------------------------------------------------


def test_check_composed_tree_flags_legacy_kind_command(tmp_path):
    mod = _mod()
    violations = mod.check_composed_tree(dirty_composed(tmp_path))
    assert violations, "a --kind backlog command must be flagged"
    assert any("backlog" in v for v in violations)


def test_check_composed_tree_clean_passes(tmp_path):
    mod = _mod()
    assert mod.check_composed_tree(clean_composed(tmp_path)) == []


# ---------------------------------------------------------------------------
# Dry-run writes nothing
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_full_vault(vault)
    commit_all(vault)

    before = tree_digest(vault)
    rc = mod.run_migration(str(vault), apply=False)
    after = tree_digest(vault)

    assert rc == 0
    assert before == after
    assert not (vault / "task").exists()
    assert (vault / "backlog").exists() and (vault / "plan").exists()


# ---------------------------------------------------------------------------
# --apply happy path
# ---------------------------------------------------------------------------


def test_apply_migrates_records_with_correct_status_and_merged_links(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_full_vault(vault)
    commit_all(vault)

    rc = mod.run_migration(str(vault), apply=True, composed_root=clean_composed(tmp_path))
    assert rc == 0

    # Every legacy record now lives under task/ with the mapped status.
    expected_status = {
        "b-open": "open",
        "b-track": "blocked",
        "b-drop": "dropped",
        "p-draft": "open",
        "p-ready": "ready",
        "p-prog": "in-progress",
        "p-done": "done",
        "p-super": "superseded",
        "p-drop": "dropped",
        "b-rel": "open",
    }
    for name, status in expected_status.items():
        sc = read_sidecar(vault, f"task/{name}")
        assert sc["kind"] == "task"
        assert sc["status"] == status

    # Link merge: the decision keeps its own task link AND both retired links.
    d = read_sidecar(vault, "decision/d-links")
    assert set(d["related"]["task"]) == {"keep-me", "from-backlog", "from-plan"}
    assert "backlog" not in d["related"] and "plan" not in d["related"]
    # The migrating backlog record's own related.plan swept into task.
    assert read_sidecar(vault, "task/b-rel")["related"]["task"] == ["x-plan"]


def test_apply_leaves_no_legacy_kinds_or_keys(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_full_vault(vault)
    commit_all(vault)

    mod.run_migration(str(vault), apply=True, composed_root=clean_composed(tmp_path))

    assert not (vault / "backlog").exists()
    assert not (vault / "plan").exists()
    for jp in vault.rglob("*.json"):
        if ".git" in jp.parts:
            continue
        related = json.loads(jp.read_text()).get("related", {})
        assert "backlog" not in related and "plan" not in related


def test_apply_every_sidecar_validates_clean(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_full_vault(vault)
    commit_all(vault)

    mod.run_migration(str(vault), apply=True, composed_root=clean_composed(tmp_path))

    for jp in vault.rglob("*.json"):
        if ".git" in jp.parts:
            continue
        kind = jp.parent.name
        result = mod.record_model.validate(json.loads(jp.read_text()), kind=kind)
        assert result.errors == [], f"{jp}: {result.errors}"


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


def test_apply_refuses_dirty_tree(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_full_vault(vault)
    commit_all(vault)
    # Introduce an uncommitted change.
    write_record(vault, "backlog", "uncommitted", status="open")

    before = tree_digest(vault)
    rc = mod.run_migration(str(vault), apply=True, composed_root=clean_composed(tmp_path))

    assert rc == 1
    assert tree_digest(vault) == before  # nothing migrated
    assert not (vault / "task").exists()


def test_apply_refuses_uncomposed_tree(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_full_vault(vault)
    commit_all(vault)

    before = tree_digest(vault)
    rc = mod.run_migration(str(vault), apply=True, composed_root=dirty_composed(tmp_path))

    assert rc == 1
    assert tree_digest(vault) == before
    assert not (vault / "task").exists()


# ---------------------------------------------------------------------------
# Subset gate blocks a dropped link (negative case)
# ---------------------------------------------------------------------------


def test_apply_subset_gate_blocks_dropped_link(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_full_vault(vault)
    commit_all(vault)

    # A buggy sweep that DROPS a link while preserving element count — the exact
    # failure a count-based check would miss. The subset gate must catch it.
    def buggy_sweep(related):
        if not isinstance(related, dict):
            return related
        result = {k: list(v) for k, v in related.items() if k not in ("backlog", "plan")}
        union = mod.related_union(related)
        if union:
            # Same count as the correct merge, but one real link replaced by junk.
            result["task"] = ["__junk__"] + sorted(union)[1:]
        return result

    monkeypatch.setattr(mod, "sweep_related", buggy_sweep)

    before = tree_digest(vault)
    rc = mod.run_migration(str(vault), apply=True, composed_root=clean_composed(tmp_path))

    assert rc == 1
    # Blocked before any write — vault untouched.
    assert tree_digest(vault) == before
    assert not (vault / "task").exists()


# ---------------------------------------------------------------------------
# Interrupted run → restore → re-run converges
# ---------------------------------------------------------------------------


def test_interrupted_apply_rolls_back_then_reruns_to_same_state(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    # Freeze the write clock so an uninterrupted run and a converged re-run
    # produce byte-identical sidecars.
    monkeypatch.setattr(mod.record_store, "_now_utc_z", lambda: "2026-07-08T00:00:00Z")

    # Reference: a clean uninterrupted run on an identical vault.
    ref_vault = make_git_vault(tmp_path / "ref")
    seed_full_vault(ref_vault)
    commit_all(ref_vault)
    assert mod.run_migration(str(ref_vault), apply=True, composed_root=clean_composed(tmp_path)) == 0
    reference = tree_digest(ref_vault)

    # Subject: interrupt mid-write, then let the script's rollback restore it.
    vault = make_git_vault(tmp_path / "subj")
    seed_full_vault(vault)
    commit_all(vault)
    original = tree_digest(vault)

    real_write = mod.record_store.validate_and_write
    calls = {"n": 0}

    def flaky_write(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated crash mid-apply")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(mod.record_store, "validate_and_write", flaky_write)
    rc = mod.run_migration(str(vault), apply=True, composed_root=clean_composed(tmp_path))
    assert rc == 1
    # git-as-rollback restored the pre-apply state exactly.
    assert tree_digest(vault) == original

    # Re-run cleanly — converges to the same end state as the uninterrupted run.
    monkeypatch.setattr(mod.record_store, "validate_and_write", real_write)
    rc = mod.run_migration(str(vault), apply=True, composed_root=clean_composed(tmp_path))
    assert rc == 0
    assert tree_digest(vault) == reference


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_second_apply_is_noop(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_full_vault(vault)
    commit_all(vault)

    assert mod.run_migration(str(vault), apply=True, composed_root=clean_composed(tmp_path)) == 0
    commit_all(vault, "post-migration")
    after_first = tree_digest(vault)

    # Second run: nothing left to migrate.
    rc = mod.run_migration(str(vault), apply=True, composed_root=clean_composed(tmp_path))
    assert rc == 0
    assert tree_digest(vault) == after_first
