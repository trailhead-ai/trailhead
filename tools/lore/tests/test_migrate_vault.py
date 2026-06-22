"""Behavioral tests for migrate_vault.py — Slice 1 (pure reader + transcode).

Covers the bespoke stdlib legacy-YAML reader (`read_legacy`) and the in-memory
`transcode` step. NO writes, NO placement, NO orchestration (those are Slices 2/3).

Fixtures are **real-shaped** legacy records copied from the live vault at
``~/code/lore-vault`` — the actual messy forms (block scalars, multi-line block
maps, flow sequences, quoted/unquoted scalars, `type:` that disagrees with the
directory, sessions with/without `session_id`).

This module is EPHEMERAL: it is deleted together with migrate_vault.py in the
post-cutover commit (plan "Post-cutover").
"""

import subprocess
from pathlib import Path

import pytest

from conftest import load_script


def _mod():
    return load_script("migrate_vault")


# ---------------------------------------------------------------------------
# Fixture helpers — write a legacy record (dir + frontmatter + body) on disk so
# read_legacy parses real bytes, then return the path.
# ---------------------------------------------------------------------------


def write_legacy(root: Path, rel: str, frontmatter: str, body: str = "\nBody text.\n") -> Path:
    """Write a legacy `<dir>/<file>.md` with a `---` frontmatter block."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    return path


def no_wikilinks(value) -> bool:
    """Recursively assert no `[[` survives in a transcoded sidecar value."""
    if isinstance(value, str):
        return "[[" not in value
    if isinstance(value, list):
        return all(no_wikilinks(v) for v in value)
    if isinstance(value, dict):
        return all(no_wikilinks(k) and no_wikilinks(v) for k, v in value.items())
    return True


# ===========================================================================
# read_legacy — parsing primitives
# ===========================================================================


def test_read_legacy_standard_scalars(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "decisions/d.md", "type: decision\nstatus: active\nproject: zenith")
    legacy = mod.read_legacy(p)
    assert legacy.frontmatter["type"] == "decision"
    assert legacy.frontmatter["status"] == "active"
    assert legacy.frontmatter["project"] == "zenith"


def test_read_legacy_block_scalar_fold(tmp_path):
    """`key: >-` folds indented continuation lines into one space-joined string."""
    mod = _mod()
    fm = (
        "type: deferred\n"
        "closure-reason: >-\n"
        "  Dropped during the wind-down. The orchestration repo is being\n"
        "  retired with it; revive trigger preserved above.\n"
        "status: dropped"
    )
    p = write_legacy(tmp_path, "deferred/x.md", fm)
    legacy = mod.read_legacy(p)
    assert legacy.frontmatter["closure-reason"] == (
        "Dropped during the wind-down. The orchestration repo is being "
        "retired with it; revive trigger preserved above."
    )
    assert legacy.frontmatter["status"] == "dropped"


def test_read_legacy_block_scalar_literal_preserves_newlines(tmp_path):
    """`key: |` preserves newlines between continuation lines."""
    mod = _mod()
    fm = "type: lesson\nrevive-condition: |\n  line one\n  line two\nstatus: active"
    p = write_legacy(tmp_path, "lessons/x.md", fm)
    legacy = mod.read_legacy(p)
    assert legacy.frontmatter["revive-condition"] == "line one\nline two"


def test_read_legacy_flow_sequence(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "lessons/x.md", "type: lesson\nphases: [Build, Review]")
    legacy = mod.read_legacy(p)
    assert legacy.frontmatter["phases"] == ["Build", "Review"]


def test_read_legacy_flow_map(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "lessons/x.md", "type: lesson\nrelated: {decision: foo}")
    legacy = mod.read_legacy(p)
    assert legacy.frontmatter["related"] == {"decision": "foo"}


def test_read_legacy_empty_flow_map(tmp_path):
    """`related: {}` parses to an empty dict (no cross-references, not a flag)."""
    mod = _mod()
    p = write_legacy(tmp_path, "lessons/x.md", "type: lesson\nrelated: {}")
    legacy = mod.read_legacy(p)
    assert legacy.frontmatter["related"] == {}


def test_read_legacy_block_map_related(tmp_path):
    """Multi-line block map under `related:` collects indented sub-keys."""
    mod = _mod()
    fm = (
        "type: lesson\n"
        "related:\n"
        "  decision: [[decisions/2026-05/foo]]\n"
        "  dead-end: \"[[dead-ends/2026-05/bar]]\"\n"
        "project:"
    )
    p = write_legacy(tmp_path, "lessons/x.md", fm)
    legacy = mod.read_legacy(p)
    assert legacy.frontmatter["related"] == {
        "decision": "[[decisions/2026-05/foo]]",
        "dead-end": "[[dead-ends/2026-05/bar]]",
    }


def test_read_legacy_bare_empty_value_is_none(tmp_path):
    """A bare `key:` with nothing after the colon parses to None (absent)."""
    mod = _mod()
    p = write_legacy(tmp_path, "decisions/d.md", "type: decision\nsource-plan:\nstatus: active")
    legacy = mod.read_legacy(p)
    assert legacy.frontmatter["source-plan"] is None


def test_read_legacy_records_original_path(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "decisions/d.md", "type: decision\nstatus: active")
    legacy = mod.read_legacy(p)
    assert Path(legacy.path) == p


# ===========================================================================
# Wikilink conversion — the 5 variants
# ===========================================================================


def test_wikilink_variant1_pipe_alias(tmp_path):
    """Variant 1: `raised-in: [[target|alias]]` → stripped of alias."""
    mod = _mod()
    fm = "type: decision\nstatus: active\nraised-in: \"[[sessions/2026-06/foo|Foo Session]]\""
    p = write_legacy(tmp_path, "decisions/d.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["related"]["session"] == ["sessions/2026-06/foo"]
    assert no_wikilinks(t.sidecar)


def test_wikilink_variant2_flow_map(tmp_path):
    """Variant 2: `related: {decision: "[[decisions/...]]"}` → related-decision list."""
    mod = _mod()
    fm = "type: lesson\nstatus: active\nrelated: {decision: \"[[decisions/2026-05/foo]]\"}"
    p = write_legacy(tmp_path, "lessons/x.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["related"]["decision"] == ["decisions/2026-05/foo"]
    assert no_wikilinks(t.sidecar)


def test_wikilink_variant3_flow_sequence(tmp_path):
    """Variant 3: areas as flow-seq of wikilink strings → stripped targets."""
    mod = _mod()
    fm = "type: session\nstatus: complete\nsession_id: abc\nareas: [\"[[areas/workflow-worktrees]]\"]"
    p = write_legacy(tmp_path, "sessions/2026-05/s.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    # areas wikilinks are converted; no [[ survives anywhere in the sidecar.
    assert no_wikilinks(t.sidecar)


def test_wikilink_variant4_bare_scalar(tmp_path):
    """Variant 4: bare `related: target` scalar → related list (no brackets)."""
    mod = _mod()
    fm = "type: decision\nstatus: active\nrelated: decisions/2026-05/foo"
    p = write_legacy(tmp_path, "decisions/d.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["related"]["decision"] == ["decisions/2026-05/foo"]
    assert no_wikilinks(t.sidecar)


def test_wikilink_variant5_block_map(tmp_path):
    """Variant 5: multi-line block map → related-<kind> per sub-key."""
    mod = _mod()
    fm = (
        "type: lesson\nstatus: active\n"
        "related:\n"
        "  decision: [[decisions/2026-05/foo]]\n"
        "  dead-end: \"[[dead-ends/2026-05/bar]]\"\n"
    )
    p = write_legacy(tmp_path, "lessons/x.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["related"]["decision"] == ["decisions/2026-05/foo"]
    # dead-end is a legacy dir → consolidates to the lesson kind.
    assert t.sidecar["related"]["lesson"] == ["dead-ends/2026-05/bar"]
    assert no_wikilinks(t.sidecar)


def test_wikilink_empty_map_no_cross_references(tmp_path):
    """`related: {}` emits no `related` field at all and no flag."""
    mod = _mod()
    p = write_legacy(tmp_path, "lessons/x.md", "type: lesson\nstatus: active\nrelated: {}")
    t = mod.transcode(mod.read_legacy(p))
    assert "related" not in t.sidecar or t.sidecar["related"] == {}
    assert not any(f.kind == "review" for f in t.flags)


def test_wikilink_related_key_removed_from_sidecar(tmp_path):
    """The raw `related` scalar/map never survives as a top-level legacy value."""
    mod = _mod()
    fm = "type: decision\nstatus: active\nrelated: decisions/2026-05/foo"
    p = write_legacy(tmp_path, "decisions/d.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    # `related` is the typed S1 map, never the legacy raw string.
    assert isinstance(t.sidecar.get("related", {}), dict)


# ===========================================================================
# Status mapping
# ===========================================================================


def test_status_compound_strips_prose_to_breadcrumb(tmp_path):
    """`active | superseded — prose` → base status + prose in a migration note."""
    mod = _mod()
    fm = "type: decision\nstatus: \"active | superseded — replaced by the new ADR\""
    p = write_legacy(tmp_path, "decisions/d.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["status"] == "active"
    assert "replaced by the new ADR" in t.body
    assert "Migration note" in t.body


def test_status_shelved_plan_stays_shelved_or_dropped(tmp_path):
    """`shelved` on a plan maps into the plan vocab (not silently dropped to active)."""
    mod = _mod()
    p = write_legacy(tmp_path, "plans/2026-05/p.md", "type: plan\nstatus: shelved")
    t = mod.transcode(mod.read_legacy(p))
    # plan kind has no "shelved"; it must map to a valid plan status, not "active".
    assert t.sidecar["status"] in {"draft", "ready", "in-progress", "complete", "superseded", "dropped"}


def test_status_shelved_session_becomes_closed(tmp_path):
    mod = _mod()
    fm = "type: session\nstatus: shelved\nsession_id: abc"
    p = write_legacy(tmp_path, "sessions/2026-05/s.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["status"] in {"active", "complete"}


def test_status_empty_string_maps_to_active(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "decisions/d.md", "type: decision\nstatus:")
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["status"] == "active"


def test_status_blob_dir_value_normalized_to_active(tmp_path):
    """designs/ → blob; blob vocab is ('active',) so off-vocab values normalize."""
    mod = _mod()
    p = write_legacy(tmp_path, "designs/d.md", "type: design\nstatus: draft")
    t = mod.transcode(mod.read_legacy(p))
    assert t.kind == "blob"
    assert t.sidecar["status"] == "active"


def test_status_unknown_base_flags_review(tmp_path):
    """A status the table cannot classify flags review, never silent-defaults."""
    mod = _mod()
    p = write_legacy(tmp_path, "decisions/d.md", "type: decision\nstatus: totally-bogus-value")
    t = mod.transcode(mod.read_legacy(p))
    assert any(f.kind == "review" for f in t.flags)


# ===========================================================================
# Kind consolidation
# ===========================================================================


@pytest.mark.parametrize(
    "directory,expected_kind",
    [
        ("dead-ends/2026-05", "lesson"),
        ("gotchas", "lesson"),
        ("lessons/2026-05", "lesson"),
        ("deferred/2026-05", "backlog"),
        ("follow-ups", "backlog"),
        ("tracking", "backlog"),
        ("inbox", "backlog"),
        ("tools", "area"),
        ("areas", "area"),
        ("decisions/2026-05", "decision"),
        ("plans/2026-05", "plan"),
        ("sessions/2026-05", "session"),
        ("specs", "spec"),
        ("collaboration", "collaboration"),
        ("designs", "blob"),
        ("audits", "blob"),
        ("reviews", "blob"),
        ("ops", "blob"),
    ],
)
def test_kind_consolidation(tmp_path, directory, expected_kind):
    mod = _mod()
    extra = "\nsession_id: abc" if expected_kind == "session" else ""
    p = write_legacy(tmp_path, f"{directory}/r.md", f"type: whatever\nstatus: active{extra}")
    t = mod.transcode(mod.read_legacy(p))
    assert t.kind == expected_kind


def test_briefings_dropped(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "briefings/b.md", "type: briefing\nstatus: active")
    t = mod.transcode(mod.read_legacy(p))
    assert any(f.kind == "drop" for f in t.flags)


def test_post_merge_incidents_abort_gate(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "post-merge-incidents/i.md", "type: incident\nstatus: active")
    t = mod.transcode(mod.read_legacy(p))
    assert any(f.kind == "review" for f in t.flags)


# ===========================================================================
# revive-condition → lesson status
# ===========================================================================


def test_lesson_real_revive_condition_is_conditional(tmp_path):
    mod = _mod()
    fm = (
        "type: dead-end\nstatus: active\n"
        "revive-condition: the MonitorTest force-stops a coordinator test flakes again"
    )
    p = write_legacy(tmp_path, "dead-ends/2026-05/r.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.kind == "lesson"
    assert t.sidecar["status"] == "conditional"
    assert "Revisit when:" in t.body
    assert "MonitorTest force-stops a coordinator test flakes again" in t.body


def test_lesson_never_revive_condition_is_active(tmp_path):
    mod = _mod()
    fm = "type: dead-end\nstatus: active\nrevive-condition: never"
    p = write_legacy(tmp_path, "dead-ends/2026-05/r.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["status"] == "active"
    assert "Revisit when:" not in t.body


def test_lesson_absent_revive_condition_is_active(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "gotchas/r.md", "type: gotcha\nstatus: active")
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["status"] == "active"


# ===========================================================================
# Known-sidecar vs extra-keys strategy
# ===========================================================================


def test_unknown_keys_land_in_annotations_no_flag(tmp_path):
    """Unknown keys → annotations['legacy/<key>'] wholesale, never a review flag."""
    mod = _mod()
    fm = (
        "type: deferred\nstatus: dropped\n"
        "effort: S\nvalue: medium\nconsolidation-group: secure-messaging\n"
        "next-check: 2026-07-01"
    )
    p = write_legacy(tmp_path, "deferred/2026-05/d.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    ann = t.sidecar["annotations"]
    assert ann["legacy/effort"] == "S"
    assert ann["legacy/value"] == "medium"
    assert ann["legacy/consolidation-group"] == "secure-messaging"
    assert ann["legacy/next-check"] == "2026-07-01"
    assert not any(f.kind == "review" for f in t.flags)


def test_unknown_key_not_in_top_level_sidecar(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "deferred/2026-05/d.md", "type: deferred\nstatus: dropped\neffort: S")
    t = mod.transcode(mod.read_legacy(p))
    assert "effort" not in t.sidecar


# ===========================================================================
# Known-sidecar key with bad shape → Flag.review
# ===========================================================================


def test_known_key_bad_shape_flags_review(tmp_path):
    """A known key (status) with an unrecognizable value shape flags review."""
    mod = _mod()
    # A flow-map where a scalar status is expected — an unclassifiable shape.
    p = write_legacy(tmp_path, "decisions/d.md", "type: decision\nstatus: {weird: map}")
    t = mod.transcode(mod.read_legacy(p))
    assert any(f.kind == "review" for f in t.flags)


# ===========================================================================
# Lossy rehome
# ===========================================================================


def test_severity_and_closure_reason_rehomed(tmp_path):
    mod = _mod()
    fm = (
        "type: lesson\nstatus: active\n"
        "severity: medium\n"
        "closure-reason: dropped during wind-down"
    )
    p = write_legacy(tmp_path, "lessons/2026-05/l.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["annotations"]["legacy/severity"] == "medium"
    assert t.sidecar["annotations"]["legacy/closure-reason"] == "dropped during wind-down"
    assert "severity" not in t.sidecar
    assert "closure-reason" not in t.sidecar


# ===========================================================================
# Provenance
# ===========================================================================


def test_provenance_created_at_from_legacy_date(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "lessons/2026-05/l.md", "type: lesson\nstatus: active\ndate: 2026-06-04")
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["created-at"].startswith("2026-06-04")


def test_provenance_group_dropped_and_version_stamped(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "lessons/2026-05/l.md", "type: lesson\nstatus: active\ngroup: zenith")
    t = mod.transcode(mod.read_legacy(p))
    assert "group" not in t.sidecar
    assert "legacy/group" not in t.sidecar.get("annotations", {})
    assert t.sidecar["version"] == "v1"


def test_provenance_keywords_always_present(tmp_path):
    """KU-1: validate requires keywords; every sidecar carries keywords: []."""
    mod = _mod()
    p = write_legacy(tmp_path, "lessons/2026-05/l.md", "type: lesson\nstatus: active")
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["keywords"] == []


def test_provenance_created_by_from_git_log_of_original_path(tmp_path):
    """`created-by` resolves from `git log` against the ORIGINAL legacy path."""
    mod = _mod()
    # Build a tiny git repo so the record has real history under a known author.
    repo = tmp_path / "vault"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "histauthor@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Hist Author"], cwd=repo, check=True)
    p = write_legacy(repo, "lessons/2026-05/l.md", "type: lesson\nstatus: active")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add record"],
        cwd=repo,
        check=True,
    )
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["created-by"] == "histauthor@example.com"


# ===========================================================================
# No-YAML invariant
# ===========================================================================


def test_transcoded_body_never_starts_with_frontmatter(tmp_path):
    mod = _mod()
    fm = "type: lesson\nstatus: active\nrevive-condition: revisit when X happens"
    p = write_legacy(tmp_path, "dead-ends/2026-05/r.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert not t.body.lstrip().startswith("---")


# ===========================================================================
# Session naming
# ===========================================================================


def test_session_name_is_session_id(tmp_path):
    mod = _mod()
    fm = "type: session\nstatus: complete\nsession_id: 295a0017-7f96-4505-ba49-a3fc3026debb"
    p = write_legacy(tmp_path, "sessions/2026-05/s.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.name == "295a0017-7f96-4505-ba49-a3fc3026debb"


def test_session_missing_session_id_flags_review(tmp_path):
    mod = _mod()
    p = write_legacy(tmp_path, "sessions/2026-05/s.md", "type: session\nstatus: complete")
    t = mod.transcode(mod.read_legacy(p))
    assert any(f.kind == "review" for f in t.flags)


# ===========================================================================
# Round-trip sample — exact sidecar + body for one representative per old kind
# ===========================================================================


def test_round_trip_decision(tmp_path):
    mod = _mod()
    fm = "type: decision\ngroup: zenith\ndate: 2026-06-04\nstatus: active"
    p = write_legacy(tmp_path, "decisions/2026-06/d.md", fm, body="\nThe decision body.\n")
    t = mod.transcode(mod.read_legacy(p))
    assert t.kind == "decision"
    assert t.sidecar["version"] == "v1"
    assert t.sidecar["status"] == "active"
    assert t.sidecar["keywords"] == []
    assert t.sidecar["created-at"].startswith("2026-06-04")
    assert "group" not in t.sidecar
    assert t.body == "\nThe decision body.\n"


def test_round_trip_dead_end_to_lesson(tmp_path):
    mod = _mod()
    fm = "type: dead-end\ngroup: zenith\ndate: 2026-05-28\nstatus: active\nrevive-condition: the test flakes again"
    p = write_legacy(tmp_path, "dead-ends/2026-05/d.md", fm, body="\nWhat we tried.\n")
    t = mod.transcode(mod.read_legacy(p))
    assert t.kind == "lesson"
    assert t.sidecar["status"] == "conditional"
    assert "Revisit when:" in t.body
    assert "What we tried." in t.body


# ===========================================================================
# Slice 2 — write orchestrator + pre-write summary + abort gate
#
# These tests drive `run_migration(vault_root, *, dry_run=False)` end-to-end.
# A temp git-repo vault is seeded with real-shaped legacy records; the index DB
# is isolated under a per-test XDG_STATE_HOME so nothing touches the live index
# (Axiom 6). LORE_EMAIL is pinned so committer provenance resolves deterministically.
# ===========================================================================


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def make_git_vault(tmp_path) -> Path:
    """Create a committed git repo to act as the legacy vault root."""
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
    """Point the lore index DB at a throwaway state dir (Axiom 6)."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("LORE_EMAIL", "migrator@example.com")
    return state


def seed_typical_vault(vault: Path) -> dict:
    """Seed one representative of several legacy kinds + a DROP + a lossy rehome.

    Returns a dict describing the seeded counts for summary assertions.
    """
    write_legacy(vault, "decisions/2026-06/use-sqlite.md", "type: decision\ndate: 2026-06-04\nstatus: active")
    write_legacy(
        vault,
        "dead-ends/2026-05/tried-threads.md",
        "type: dead-end\nstatus: active\nseverity: medium",  # lossy rehome (severity)
        body="\nWhat we tried.\n",
    )
    write_legacy(vault, "designs/the-shape.md", "type: design\nstatus: draft")  # → blob
    write_legacy(
        vault,
        "sessions/2026-05/s.md",
        "type: session\nstatus: complete\nsession_id: 295a0017-7f96-4505-ba49-a3fc3026debb",
    )
    write_legacy(vault, "briefings/old.md", "type: briefing\nstatus: active")  # DROP
    return {
        "migrated": 4,  # decision, lesson, blob, session
        "dropped": 1,  # briefing
        "session_id": "295a0017-7f96-4505-ba49-a3fc3026debb",
    }


def md_dirs_present(vault: Path) -> set:
    return {p.name for p in vault.iterdir() if p.is_dir() and p.name != ".git"}


def all_md_files(vault: Path) -> list:
    return [p for p in vault.rglob("*.md") if ".git" not in p.parts]


# ---------------------------------------------------------------------------
# End-to-end happy path
# ---------------------------------------------------------------------------


def test_run_migration_happy_path_flat_layout(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed = seed_typical_vault(vault)
    commit_all(vault)

    rc = mod.run_migration(str(vault))
    assert rc == 0

    # Each migrated record exists as <kind>/<name>.md + .json at the flat path.
    assert (vault / "decision" / "use-sqlite.md").exists()
    assert (vault / "decision" / "use-sqlite.json").exists()
    assert (vault / "lesson" / "tried-threads.md").exists()
    assert (vault / "blob" / "the-shape.md").exists()
    # Session named by session_id.
    assert (vault / "session" / f"{seed['session_id']}.md").exists()
    assert (vault / "session" / f"{seed['session_id']}.json").exists()


def test_run_migration_removes_legacy_dirs_and_buckets(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    commit_all(vault)

    mod.run_migration(str(vault))

    legacy_dirs = {
        "dead-ends", "gotchas", "follow-ups", "deferred", "designs",
        "audits", "reviews", "ops", "briefings", "post-merge-incidents",
        "decisions", "sessions", "plans", "specs",
    }
    present = md_dirs_present(vault)
    assert not (present & legacy_dirs), f"legacy dirs survived: {present & legacy_dirs}"
    # No YYYY-MM bucket survives anywhere under the flat layout.
    import re as _re
    for p in vault.rglob("*"):
        if ".git" in p.parts:
            continue
        assert not _re.fullmatch(r"\d{4}-\d{2}", p.name), f"date bucket survived: {p}"


def test_run_migration_no_md_starts_with_frontmatter(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    commit_all(vault)

    mod.run_migration(str(vault))

    for md in all_md_files(vault):
        assert not md.read_text().lstrip().startswith("---"), f"{md} still has frontmatter"


def test_run_migration_record_count_accounts_for_all(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed = seed_typical_vault(vault)
    commit_all(vault)

    mod.run_migration(str(vault))

    # post-migration written records == migrated (dropped records are gone).
    written = [p for p in vault.rglob("*.json") if ".git" not in p.parts]
    assert len(written) == seed["migrated"]


def test_run_migration_every_sidecar_validates(tmp_path, monkeypatch):
    mod = _mod()
    record_model = load_script("record_model")
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    commit_all(vault)

    mod.run_migration(str(vault))

    import json as _json
    for sc in vault.rglob("*.json"):
        if ".git" in sc.parts:
            continue
        kind = sc.parent.name
        result = record_model.validate(_json.loads(sc.read_text()), kind=kind)
        assert result.errors == [], f"{sc}: {result.errors}"


# ---------------------------------------------------------------------------
# Byte-identical to the CLI write primitive
# ---------------------------------------------------------------------------


def test_migrated_record_byte_identical_to_validate_and_write(tmp_path, monkeypatch):
    """A migrated sidecar/body matches what record_store.validate_and_write produces."""
    mod = _mod()
    record_store = mod.record_store  # same module instance the migrator writes through
    index_store = mod.index_store
    isolate_index(monkeypatch, tmp_path)
    # Freeze updated-* time so the two independent writes stamp identical bytes.
    monkeypatch.setattr(record_store, "_now_utc_z", lambda: "2026-06-22T00:00:00Z")

    # Run the migration over a vault with a single known record.
    vault = make_git_vault(tmp_path)
    write_legacy(vault, "decisions/2026-06/use-sqlite.md", "type: decision\ndate: 2026-06-04\nstatus: active", body="\nThe body.\n")
    commit_all(vault)
    mod.run_migration(str(vault))

    migrated_md = (vault / "decision" / "use-sqlite.md").read_text()
    migrated_json = (vault / "decision" / "use-sqlite.json").read_text()

    # Reproduce the same logical input through the CLI write primitive directly,
    # from a fresh copy of the same legacy input under the same authorship.
    src = make_git_vault(tmp_path / "ref")
    p = write_legacy(src, "decisions/2026-06/use-sqlite.md", "type: decision\ndate: 2026-06-04\nstatus: active", body="\nThe body.\n")
    commit_all(src)
    t = mod.transcode(mod.read_legacy(p))

    ref_vault = tmp_path / "refvault"
    ref_vault.mkdir()
    loc = record_store.place_record(t.name, t.kind, scope=None, vault_root=str(ref_vault))
    conn = index_store.open_index()
    try:
        record_store.validate_and_write(loc, t.sidecar, t.body, conn)
        conn.commit()
    finally:
        conn.close()
    ref_md = (ref_vault / "decision" / "use-sqlite.md").read_text()
    ref_json = (ref_vault / "decision" / "use-sqlite.json").read_text()

    assert migrated_md == ref_md
    assert migrated_json == ref_json


# ---------------------------------------------------------------------------
# Summary counts + ordering
# ---------------------------------------------------------------------------


def test_summary_counts_and_ordering_on_clean_run(tmp_path, monkeypatch, capsys):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    commit_all(vault)

    mod.run_migration(str(vault))
    out = capsys.readouterr().out

    # DROP record itemized (destructive — A11).
    assert "briefings/old.md" in out
    # Lossy rehome surfaced.
    assert "lossy" in out.lower()


def test_summary_review_required_before_informational(tmp_path, monkeypatch, capsys):
    """An aborting run lists review-required items before informational counts."""
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    # Add a review-required item: a non-empty post-merge-incidents/ dir.
    write_legacy(vault, "post-merge-incidents/inc.md", "type: incident\nstatus: active")
    commit_all(vault)

    rc = mod.run_migration(str(vault))
    out = capsys.readouterr().out
    assert rc != 0

    review_pos = out.lower().find("review")
    info_pos = out.lower().find("informational")
    assert review_pos != -1 and info_pos != -1
    assert review_pos < info_pos


def test_summary_aborting_run_ends_with_what_to_do_next(tmp_path, monkeypatch, capsys):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    write_legacy(vault, "post-merge-incidents/inc.md", "type: incident\nstatus: active")
    commit_all(vault)

    rc = mod.run_migration(str(vault))
    out = capsys.readouterr().out
    assert rc != 0
    assert "What to do next" in out
    # numbered, fix-before-rerun ordering.
    assert "1." in out
    assert "re-run" in out.lower()


# ---------------------------------------------------------------------------
# Abort gate writes nothing
# ---------------------------------------------------------------------------


def test_abort_gate_writes_nothing(tmp_path, monkeypatch):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    write_legacy(vault, "post-merge-incidents/inc.md", "type: incident\nstatus: active")
    commit_all(vault)

    before = _snapshot_tree(vault)
    rc = mod.run_migration(str(vault))
    after = _snapshot_tree(vault)

    assert rc != 0
    assert before == after, "abort gate must leave the vault byte-identical"


def _snapshot_tree(vault: Path) -> dict:
    """Map every non-.git file to its bytes — for before/after equality."""
    snap = {}
    for p in sorted(vault.rglob("*")):
        if ".git" in p.parts or not p.is_file():
            continue
        snap[str(p.relative_to(vault))] = p.read_bytes()
    return snap


# ---------------------------------------------------------------------------
# Mid-pass failure surfaces split-state
# ---------------------------------------------------------------------------


def test_mid_pass_failure_surfaces_split_state(tmp_path, monkeypatch, capsys):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    commit_all(vault)

    # Stub validate_and_write to raise on the 2nd record.
    record_store = mod.record_store
    real = record_store.validate_and_write
    calls = {"n": 0}

    def flaky(location, sidecar, body, conn, shared=0):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real(location, sidecar, body, conn, shared=shared)

    monkeypatch.setattr(record_store, "validate_and_write", flaky)

    rc = mod.run_migration(str(vault))
    err = capsys.readouterr().err
    assert rc != 0
    assert "wrote 1 of" in err
    assert "SPLIT state" in err
    assert "git reset --hard" in err


# ---------------------------------------------------------------------------
# Index reproducibility
# ---------------------------------------------------------------------------


def test_index_reproducible_by_fresh_rebuild(tmp_path, monkeypatch):
    mod = _mod()
    index_store = load_script("index_store")
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    commit_all(vault)

    mod.run_migration(str(vault))

    def projected_rows(conn):
        rows = conn.execute(
            "SELECT vault, kind, name, title, status FROM records ORDER BY id"
        ).fetchall()
        facets = conn.execute(
            "SELECT id, facet, value FROM record_facet ORDER BY id, facet, value"
        ).fetchall()
        return rows, facets

    conn = index_store.open_index()
    try:
        run_rows = projected_rows(conn)
    finally:
        conn.close()

    # A fresh rebuild over the same on-disk tree must reproduce the logical rows.
    conn = index_store.open_index()
    try:
        index_store.rebuild([str(vault)], conn)
        conn.commit()
        fresh_rows = projected_rows(conn)
    finally:
        conn.close()

    assert run_rows == fresh_rows


# ---------------------------------------------------------------------------
# Provenance preflight
# ---------------------------------------------------------------------------


def test_preflight_aborts_when_committer_email_unset(tmp_path, monkeypatch, capsys):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    monkeypatch.delenv("LORE_EMAIL", raising=False)
    # Force git config user.email empty by pinning HOME to an empty dir so
    # `git config --global user.email` resolves nothing.
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)

    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    commit_all(vault)

    before = _snapshot_tree(vault)
    rc = mod.run_migration(str(vault))
    after = _snapshot_tree(vault)
    out = capsys.readouterr().out

    assert rc != 0
    assert "email" in out.lower()
    assert before == after, "preflight must abort before any write"


# ---------------------------------------------------------------------------
# Dirty-tree preflight
# ---------------------------------------------------------------------------


def test_preflight_aborts_on_dirty_working_tree(tmp_path, monkeypatch, capsys):
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    commit_all(vault)
    # Introduce an uncommitted change.
    write_legacy(vault, "decisions/2026-06/dirty.md", "type: decision\nstatus: active")

    before = _snapshot_tree(vault)
    rc = mod.run_migration(str(vault))
    after = _snapshot_tree(vault)
    out = capsys.readouterr().out

    assert rc != 0
    assert "clean" in out.lower() or "dirty" in out.lower() or "uncommitted" in out.lower()
    assert before == after, "dirty-tree preflight must abort before any write"


def test_dry_run_runs_phase_a_but_writes_nothing(tmp_path, monkeypatch):
    """dry_run=True prints the summary but skips Phase B (no writes)."""
    mod = _mod()
    isolate_index(monkeypatch, tmp_path)
    vault = make_git_vault(tmp_path)
    seed_typical_vault(vault)
    commit_all(vault)

    before = _snapshot_tree(vault)
    rc = mod.run_migration(str(vault), dry_run=True)
    after = _snapshot_tree(vault)

    assert rc == 0
    assert before == after, "dry_run must not write"


# ===========================================================================
# Slice 3 — vault relocation to canonical home + config + final reindex
#
# These tests drive `relocate_vault(source, canonical, *, config_path=None)`.
# A temp git-repo vault is moved to a canonical XDG path; the lore config.json
# is updated atomically; a final index_store.rebuild targets the canonical home.
# XDG_STATE_HOME / XDG_CONFIG_HOME are isolated per test (Axiom 6).
# ===========================================================================


def git_vault_with_history(path: Path, *, with_remote: bool = False) -> Path:
    """Create a committed git repo at *path* with one valid v1 record.

    The record is written through the real ``record_store.validate_and_write``
    seam so its sidecar satisfies the index schema (NOT NULL ``created_at`` etc.).
    """
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "histauthor@example.com")
    _git(path, "config", "user.name", "Hist Author")

    mod = _mod()
    record_store = mod.record_store
    index_store = mod.index_store
    loc = record_store.place_record("use-sqlite", "decision", scope=None, vault_root=str(path))
    sidecar = {
        "version": "v1",
        "kind": "decision",
        "title": "use-sqlite",
        "status": "active",
        "keywords": [],
        "created-at": "2026-06-04T00:00:00Z",
        "created-by": "histauthor@example.com",
    }
    conn = index_store.open_index()
    try:
        record_store.validate_and_write(loc, sidecar, "\nBody.\n", conn)
        conn.commit()
    finally:
        conn.close()

    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed canonical content")
    if with_remote:
        _git(path, "remote", "add", "origin", "https://example.com/lore-vault.git")
    return path


def write_config(config_path: Path, source: Path) -> None:
    """Write a config.json whose `default` vault points (via path) at *source*."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{"vaults": [{"name": "default", "scope": "default", "path": "%s"}]}'
        % str(source),
        encoding="utf-8",
    )


def isolate_xdg(monkeypatch, tmp_path) -> tuple[Path, Path]:
    """Isolate XDG_STATE_HOME + XDG_CONFIG_HOME; return (state, config) roots."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir(exist_ok=True)
    config.mkdir(exist_ok=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("LORE_EMAIL", "migrator@example.com")
    return state, config


def canonical_default(state: Path) -> Path:
    """The canonical default-vault home: state_dir(lore)/vaults/default."""
    return state / "lore" / "vaults" / "default"


# ---------------------------------------------------------------------------
# Idempotent — already at canonical
# ---------------------------------------------------------------------------


def test_relocate_already_at_canonical_is_noop(tmp_path, monkeypatch):
    mod = _mod()
    state, config = isolate_xdg(monkeypatch, tmp_path)
    canonical = canonical_default(state)
    git_vault_with_history(canonical)
    before = _snapshot_tree(canonical)

    rc = mod.relocate_vault(canonical, canonical, config_path=config / "lore" / "config.json")

    after = _snapshot_tree(canonical)
    assert rc == 0
    assert before == after, "no-op must not change the vault"


# ---------------------------------------------------------------------------
# Git-aware move preserves history + remote
# ---------------------------------------------------------------------------


def test_relocate_git_aware_move_preserves_history(tmp_path, monkeypatch):
    mod = _mod()
    state, config = isolate_xdg(monkeypatch, tmp_path)
    source = git_vault_with_history(tmp_path / "lore-vault", with_remote=True)
    canonical = canonical_default(state)
    config_path = config / "lore" / "config.json"
    write_config(config_path, source)

    orig_log = subprocess.run(
        ["git", "-C", str(source), "log", "--format=%H %s"],
        capture_output=True, text=True, check=True,
    ).stdout

    rc = mod.relocate_vault(source, canonical, config_path=config_path)
    assert rc == 0

    new_log = subprocess.run(
        ["git", "-C", str(canonical), "log", "--format=%H %s"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert new_log == orig_log, "history must be preserved"

    remotes = subprocess.run(
        ["git", "-C", str(canonical), "remote", "-v"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "https://example.com/lore-vault.git" in remotes

    assert not source.exists(), "old source path must be gone"
    assert not canonical.is_symlink(), "canonical must not be a symlink back to source"


# ---------------------------------------------------------------------------
# Config resolves to canonical (no explicit path override)
# ---------------------------------------------------------------------------


def test_relocate_config_resolves_to_canonical(tmp_path, monkeypatch):
    mod = _mod()
    state, config = isolate_xdg(monkeypatch, tmp_path)
    source = git_vault_with_history(tmp_path / "lore-vault")
    canonical = canonical_default(state)
    config_path = config / "lore" / "config.json"
    write_config(config_path, source)

    rc = mod.relocate_vault(source, canonical, config_path=config_path)
    assert rc == 0

    import json as _json
    data = _json.loads(config_path.read_text())
    default_entries = [v for v in data["vaults"] if v.get("name") == "default"]
    assert len(default_entries) == 1
    assert "path" not in default_entries[0], "default entry must have no explicit path"

    # Sanity: the canonical home is exactly state_dir(lore)/vaults/default.
    vault_config = load_script("vault_config")
    vaults = vault_config.load_config(str(config_path))
    default = next(v for v in vaults if v.scope == "default")
    assert default.path.resolve() == canonical.resolve()


# ---------------------------------------------------------------------------
# Config-write failure rolls back the move
# ---------------------------------------------------------------------------


def test_relocate_config_write_failure_rolls_back_move(tmp_path, monkeypatch, capsys):
    mod = _mod()
    state, config = isolate_xdg(monkeypatch, tmp_path)
    source = git_vault_with_history(tmp_path / "lore-vault", with_remote=True)
    canonical = canonical_default(state)
    config_path = config / "lore" / "config.json"
    write_config(config_path, source)

    source_before = _snapshot_tree(source)
    config_before = config_path.read_bytes()

    # Stub the atomic config write to raise AFTER the move has already happened.
    def boom(path, cfg):
        raise OSError("config write failed")

    monkeypatch.setattr(mod.vault_config, "write_config_atomic", boom)

    rc = mod.relocate_vault(source, canonical, config_path=config_path)
    err = capsys.readouterr().err

    assert rc != 0
    assert source.exists(), "repo must be moved back to source on config failure"
    assert not canonical.exists(), "canonical must be cleaned up on rollback"
    assert _snapshot_tree(source) == source_before, "source must be intact after rollback"
    assert config_path.read_bytes() == config_before, "prior config must be intact"
    assert "Recovery" in err, "recovery banner must be printed"


# ---------------------------------------------------------------------------
# Cross-filesystem copy preserves the source until verified
# ---------------------------------------------------------------------------


def test_relocate_cross_filesystem_preserves_source_until_verified(tmp_path, monkeypatch):
    mod = _mod()
    state, config = isolate_xdg(monkeypatch, tmp_path)
    source = git_vault_with_history(tmp_path / "lore-vault")
    canonical = canonical_default(state)
    config_path = config / "lore" / "config.json"
    write_config(config_path, source)

    source_before = _snapshot_tree(source)

    # Simulate cross-filesystem: Path.rename raises OSError so the copy fallback fires.
    real_rename = Path.rename

    def fake_rename(self, target):
        raise OSError("cross-device link")

    monkeypatch.setattr(Path, "rename", fake_rename)

    # The remove-source step fails after a successful copy → source must survive.
    real_rmtree = mod.shutil.rmtree

    def boom_rmtree(path, *a, **k):
        raise OSError("rmtree failed")

    monkeypatch.setattr(mod.shutil, "rmtree", boom_rmtree)

    rc = mod.relocate_vault(source, canonical, config_path=config_path)

    assert rc != 0
    assert source.exists(), "source must survive when remove-source fails"
    assert _snapshot_tree(source) == source_before, "no data loss across mounts"


# ---------------------------------------------------------------------------
# Final index targets canonical
# ---------------------------------------------------------------------------


def test_relocate_final_index_targets_canonical(tmp_path, monkeypatch):
    mod = _mod()
    index_store = load_script("index_store")
    state, config = isolate_xdg(monkeypatch, tmp_path)
    source = git_vault_with_history(tmp_path / "lore-vault")
    canonical = canonical_default(state)
    config_path = config / "lore" / "config.json"
    write_config(config_path, source)

    rc = mod.relocate_vault(source, canonical, config_path=config_path)
    assert rc == 0

    def projected_rows(conn):
        return conn.execute(
            "SELECT vault, kind, name, title, status FROM records ORDER BY id"
        ).fetchall()

    conn = index_store.open_index()
    try:
        run_rows = projected_rows(conn)
    finally:
        conn.close()

    assert run_rows, "index must have rows after relocation"
    for vault, *_ in run_rows:
        assert str(canonical) in vault, f"index vault_root must be canonical: {vault}"

    # A fresh rebuild over the canonical tree reproduces the same logical rows.
    conn = index_store.open_index()
    try:
        index_store.rebuild([str(canonical)], conn)
        conn.commit()
        fresh_rows = projected_rows(conn)
    finally:
        conn.close()
    assert run_rows == fresh_rows
