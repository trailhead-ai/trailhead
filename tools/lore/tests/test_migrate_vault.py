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


def test_status_shelved_session_becomes_clean(tmp_path):
    """`shelved` resolves to the legacy `complete`, which the Slice 6 session remap
    translates to `clean` (the second path covered by `_SESSION_STATUS_REMAP`)."""
    mod = _mod()
    fm = "type: session\nstatus: shelved\nsession_id: abc"
    p = write_legacy(tmp_path, "sessions/2026-05/s.md", fm)
    t = mod.transcode(mod.read_legacy(p))
    assert t.sidecar["status"] == "clean"


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


def test_session_missing_session_id_repaired_to_title_slug(tmp_path):
    """Slice 6 (KU5): a session with no session_id is repaired to its sanitized title
    slug rather than flagged for review, so no session lands unnamed."""
    mod = _mod()
    p = write_legacy(tmp_path, "sessions/2026-05/s.md", "type: session\nstatus: complete")
    t = mod.transcode(mod.read_legacy(p))
    assert t.name == "s"
    assert not any(f.kind == "review" and "session_id" in f.detail for f in t.flags)


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
