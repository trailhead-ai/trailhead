"""Tests for the ledger gate.

The gate reads a spec body on stdin, derives its `## Slices` ledger's
per-entry structure via the sibling `candidate_set.py`'s
`parse_ledger_entries`, and certifies the ledger's append-only integrity —
optionally cross-checked against each entry's parent record, supplied as a
JSON file via `--parent-coverage`.

Fixtures are synthetic spec bodies under `tests/fixtures/`, prefixed
`ledger_` — never real vault records.

Exit-code contract:
  0 → certified — token block on stdout, `parent-cross-check: checked` or
      `parent-cross-check: skipped ...`
  1 → integrity violation: invisible-ledger-task-id, duplicate-ledger-task-id,
      coverage-claimed-twice, orphaned-ledger-entry (parent-coverage only),
      coverage-contradicts-parent (parent-coverage only) — every reason
      carries a stable `reason-code:` line
  2 → could not certify: empty-stdin, non-utf8-stdin, stdin-too-large,
      duplicate-slices-heading, unterminated-masked-region,
      malformed-coverage-token, malformed-parent-coverage — every reason
      carries a stable `reason-code:` line
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "craft" / "scripts"
GATE = SCRIPTS_DIR / "ledger_gate.py"
FIXTURES = Path(__file__).parent / "fixtures"

sys.path.insert(0, str(SCRIPTS_DIR))
import candidate_set  # noqa: E402

CLEAN_MULTI_ENTRY = (FIXTURES / "ledger_clean_multi_entry.md").read_text(encoding="utf-8")
DUPLICATE_TASK_ID = (FIXTURES / "ledger_duplicate_task_id.md").read_text(encoding="utf-8")
COVERAGE_CLAIMED_TWICE = (FIXTURES / "ledger_coverage_claimed_twice.md").read_text(
    encoding="utf-8"
)
WIDENED_CONTRADICTS_PARENT = (
    FIXTURES / "ledger_widened_covers_contradicts_parent.md"
).read_text(encoding="utf-8")
BACKFILL_MONOTONIC = (FIXTURES / "ledger_backfill_monotonic.md").read_text(encoding="utf-8")
ORPHANED_ENTRY = (FIXTURES / "ledger_orphaned_entry.md").read_text(encoding="utf-8")
FORGED_IN_FENCE = (FIXTURES / "ledger_forged_duplicate_in_fence.md").read_text(
    encoding="utf-8"
)
FORGED_IN_HTML_COMMENT = (
    FIXTURES / "ledger_forged_duplicate_in_html_comment.md"
).read_text(encoding="utf-8")
NESTED_LIST_FAKE_ENTRY = (FIXTURES / "ledger_nested_list_fake_entry.md").read_text(
    encoding="utf-8"
)
UNTERMINATED_FENCE = (FIXTURES / "ledger_unterminated_fence.md").read_text(encoding="utf-8")
INVISIBLE_TASK_ID = (FIXTURES / "ledger_invisible_task_id.md").read_text(encoding="utf-8")
CREDENTIAL_SHAPED_DUPLICATE_TASK_ID = (
    FIXTURES / "ledger_credential_shaped_duplicate_task_id.md"
).read_text(encoding="utf-8")
CONTROL_BYTE_DUPLICATE_TASK_ID = (
    FIXTURES / "ledger_control_byte_duplicate_task_id.md"
).read_text(encoding="utf-8")


def _run(
    spec_body: str, extra_args: list[str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *(extra_args or [])],
        input=spec_body,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
    )


def _tokens(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def _reason_code(stderr: str) -> str:
    for line in stderr.splitlines():
        if line.startswith("ledger-gate: reason-code:"):
            return line.split(":", 2)[2].strip()
    raise AssertionError(f"no reason-code line in stderr: {stderr!r}")


def _write_parent_coverage(tmp_path: Path, data: dict) -> str:
    path = tmp_path / "parents.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Contract item 5 — clean multi-entry ledger, with and without --parent-coverage
# ---------------------------------------------------------------------------


def test_clean_multi_entry_ledger_exits_0_without_parent_coverage():
    r = _run(CLEAN_MULTI_ENTRY)
    assert r.returncode == 0, r.stderr
    tokens = _tokens(r.stdout)
    assert tokens["entries"] == "3"
    assert "skipped" in tokens["parent-cross-check"]


def test_clean_multi_entry_ledger_exits_0_with_matching_parent_coverage(tmp_path):
    parent_coverage = _write_parent_coverage(
        tmp_path,
        {
            "alpha": {"covers": "AC1, AC2"},
            "beta": {"partially covers": "AC3"},
            "gamma": {"covers": "AC4", "partially covers": "AC5"},
        },
    )
    r = _run(CLEAN_MULTI_ENTRY, ["--parent-coverage", parent_coverage])
    assert r.returncode == 0, r.stderr
    tokens = _tokens(r.stdout)
    assert tokens["parent-cross-check"] == "checked"


# ---------------------------------------------------------------------------
# Contract item 8 — reported identifiers/task ids match parse_ledger_entries
# ---------------------------------------------------------------------------


def test_reported_task_ids_and_identifiers_match_parse_ledger_entries():
    r = _run(CLEAN_MULTI_ENTRY)
    assert r.returncode == 0, r.stderr
    expected_entries = candidate_set.parse_ledger_entries(CLEAN_MULTI_ENTRY)
    for entry in expected_entries:
        covers_str = ", ".join(entry.covers) if entry.covers else "none"
        partial_str = ", ".join(entry.partial) if entry.partial else "none"
        expected_line = f"{entry.task_id}: covers={covers_str}, partial={partial_str}"
        assert expected_line in r.stdout.splitlines()


# ---------------------------------------------------------------------------
# Contract item 1 — each exit-1 reason fires on its own dedicated fixture
# ---------------------------------------------------------------------------


def test_duplicate_ledger_task_id_exits_1():
    r = _run(DUPLICATE_TASK_ID)
    assert r.returncode == 1
    assert _reason_code(r.stderr) == "duplicate-ledger-task-id"
    assert "dup" in r.stderr


def test_coverage_claimed_twice_exits_1():
    r = _run(COVERAGE_CLAIMED_TWICE)
    assert r.returncode == 1
    assert _reason_code(r.stderr) == "coverage-claimed-twice"
    assert "AC2" in r.stderr


def test_orphaned_ledger_entry_exits_1_only_when_parent_coverage_supplied(tmp_path):
    parent_coverage = _write_parent_coverage(tmp_path, {"someone-else": {"covers": "AC1"}})
    r = _run(ORPHANED_ENTRY, ["--parent-coverage", parent_coverage])
    assert r.returncode == 1
    assert _reason_code(r.stderr) == "orphaned-ledger-entry"
    assert "mystery" in r.stderr


def test_orphaned_entry_fixture_alone_certifies_structure_only():
    r = _run(ORPHANED_ENTRY)
    assert r.returncode == 0, r.stderr


def test_invisible_task_id_is_refused_not_certified():
    r = _run(INVISIBLE_TASK_ID)
    assert r.returncode == 1
    assert _reason_code(r.stderr) == "invisible-ledger-task-id"


# ---------------------------------------------------------------------------
# Contract item 2 — the deliberate-edit case: covers widened in place beyond
# what the parent still declares. This is the test that must fail before the
# cross-check exists — it distinguishes this gate from one that only detects
# concurrent corruption.
# ---------------------------------------------------------------------------


def test_covers_widened_in_place_contradicts_parent_exits_1(tmp_path):
    parent_coverage = _write_parent_coverage(tmp_path, {"alpha": {"covers": "AC2"}})
    r = _run(WIDENED_CONTRADICTS_PARENT, ["--parent-coverage", parent_coverage])
    assert r.returncode == 1, (
        f"expected exit 1 (coverage-contradicts-parent), got {r.returncode}; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert _reason_code(r.stderr) == "coverage-contradicts-parent"
    assert "alpha" in r.stderr


# ---------------------------------------------------------------------------
# Contract item 3 — the monotonic rule holds in both directions
# ---------------------------------------------------------------------------


def test_no_token_line_whose_parent_carries_one_exits_0_backfill_precondition(tmp_path):
    parent_coverage = _write_parent_coverage(tmp_path, {"legacy": {"covers": "AC1, AC2"}})
    r = _run(BACKFILL_MONOTONIC, ["--parent-coverage", parent_coverage])
    assert r.returncode == 0, r.stderr


def test_token_differing_from_parent_exits_1(tmp_path):
    parent_coverage = _write_parent_coverage(tmp_path, {"alpha": {"covers": "AC9"}})
    r = _run(WIDENED_CONTRADICTS_PARENT, ["--parent-coverage", parent_coverage])
    assert r.returncode == 1
    assert _reason_code(r.stderr) == "coverage-contradicts-parent"


def test_lenient_entry_missing_closed_date_is_treated_as_no_token_not_a_violation(tmp_path):
    """`_parse_entry_fields` is lenient: an entry whose parenthetical head does
    not fully match the field grammar (here, missing ', closed <date>') still
    yields an entry with empty covers/partial rather than being skipped. This
    gate consumes `parse_ledger_entries` as-is and re-derives nothing, so
    such an entry is indistinguishable from a genuine legacy no-token entry —
    it does not falsely contradict a parent that already carries coverage."""
    lenient_body = (
        "## Acceptance Criteria\n\n"
        "- **AC1.** A fixture criterion.\n\n"
        "## Slices\n\n"
        "- **First slice** — a fixture value claim with a malformed "
        "parenthetical. (`task/lenient`, covers AC1)\n"
    )
    entries = candidate_set.parse_ledger_entries(lenient_body)
    assert len(entries) == 1
    assert entries[0].covers == []
    assert entries[0].partial == []

    parent_coverage = _write_parent_coverage(tmp_path, {"lenient": {"covers": "AC1"}})
    r = _run(lenient_body, ["--parent-coverage", parent_coverage])
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------
# Contract item 4 — each exit-2 reason fires on its own fixture, uniformly
# carrying both reason and reason-code
# ---------------------------------------------------------------------------


def test_empty_stdin_exits_2():
    r = _run("")
    assert r.returncode == 2
    assert _reason_code(r.stderr) == "empty-stdin"


def test_non_utf8_stdin_exits_2():
    r = subprocess.run(
        [sys.executable, str(GATE)],
        input=b"\xff\xfe not utf-8",
        capture_output=True,
    )
    assert r.returncode == 2
    assert b"non-utf8-stdin" in r.stderr


def test_stdin_too_large_exits_2():
    huge = CLEAN_MULTI_ENTRY + ("x" * (256 * 1024 + 1))
    r = _run(huge)
    assert r.returncode == 2
    assert _reason_code(r.stderr) == "stdin-too-large"


def test_duplicate_slices_heading_exits_2():
    body = CLEAN_MULTI_ENTRY + "\n## Slices\n\nmore stuff\n"
    r = _run(body)
    assert r.returncode == 2
    assert _reason_code(r.stderr) == "duplicate-slices-heading"


def test_unterminated_masked_region_exits_2():
    r = _run(UNTERMINATED_FENCE)
    assert r.returncode == 2
    assert _reason_code(r.stderr) == "unterminated-masked-region"


def test_malformed_coverage_token_exits_2():
    body = (
        "## Acceptance Criteria\n\n- **AC1.** A fixture criterion.\n\n"
        "## Slices\n\n"
        "- **First slice** — a fixture value claim. "
        "(`task/alpha`, closed 2026-01-01, covers AC1, AC1)\n"
    )
    r = _run(body)
    assert r.returncode == 2
    assert _reason_code(r.stderr) == "malformed-coverage-token"


def test_malformed_parent_coverage_missing_file_exits_2():
    r = _run(CLEAN_MULTI_ENTRY, ["--parent-coverage", "/no/such/file.json"])
    assert r.returncode == 2
    assert _reason_code(r.stderr) == "malformed-parent-coverage"


def test_malformed_parent_coverage_not_json_object_exits_2(tmp_path):
    path = tmp_path / "parents.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    r = _run(CLEAN_MULTI_ENTRY, ["--parent-coverage", str(path)])
    assert r.returncode == 2
    assert _reason_code(r.stderr) == "malformed-parent-coverage"


def test_malformed_parent_coverage_bad_field_shape_exits_2(tmp_path):
    parent_coverage = _write_parent_coverage(tmp_path, {"alpha": {"covers": "not-a-token"}})
    r = _run(CLEAN_MULTI_ENTRY, ["--parent-coverage", parent_coverage])
    assert r.returncode == 2
    assert _reason_code(r.stderr) == "malformed-parent-coverage"


# ---------------------------------------------------------------------------
# Contract item 6 — forged structure never falsely violates; an unterminated
# masked region fails closed (covered above)
# ---------------------------------------------------------------------------


def test_forged_duplicate_in_fenced_block_is_invisible():
    r = _run(FORGED_IN_FENCE)
    assert r.returncode == 0, r.stderr
    tokens = _tokens(r.stdout)
    assert tokens["entries"] == "1"


def test_forged_duplicate_in_html_comment_is_invisible():
    r = _run(FORGED_IN_HTML_COMMENT)
    assert r.returncode == 0, r.stderr
    tokens = _tokens(r.stdout)
    assert tokens["entries"] == "1"


def test_forged_duplicate_in_nested_list_is_invisible():
    r = _run(NESTED_LIST_FAKE_ENTRY)
    assert r.returncode == 0, r.stderr
    tokens = _tokens(r.stdout)
    assert tokens["entries"] == "1"


# ---------------------------------------------------------------------------
# Security obligation — the `reason:` line echoes vault-sourced ledger text
# (a task id, a coverage identifier), so it must never reproduce a credential-
# shaped secret or a raw control byte. Matches the established convention in
# `test_criterion_gate.py`'s `test_credential_shaped_span_refuses_without_
# reproducing_the_secret` and `test_control_bytes_in_a_refused_call_syntax_
# span_are_neutralized` — this is a separate obligation from item 4's
# reason-code-only persistence contract: that one is about what the shipped
# document tells a session to persist, this one is about the gate itself
# never reproducing a secret in its own output in the first place.
# ---------------------------------------------------------------------------


def test_credential_shaped_task_id_refuses_without_reproducing_the_secret():
    r = _run(CREDENTIAL_SHAPED_DUPLICATE_TASK_ID)
    assert r.returncode == 1, r.stderr + r.stdout
    assert _reason_code(r.stderr) == "duplicate-ledger-task-id"
    assert "sk_live_Zq7Kd2" not in r.stderr
    assert "deploy-" in r.stderr


def test_control_byte_in_a_duplicated_task_id_is_neutralized():
    r = subprocess.run(
        [sys.executable, str(GATE)],
        input=CONTROL_BYTE_DUPLICATE_TASK_ID.encode("utf-8"),
        capture_output=True,
    )
    assert r.returncode == 1
    assert b"\x1b" not in r.stderr
    assert b"duplicate-ledger-task-id" in r.stderr
    # the neutralized escape must still be legible as evidence, not dropped
    assert b"\\x1b" in r.stderr


def test_safe_output_boundary_neutralizes_control_bytes_and_scrubs_credentials():
    """Direct pin on `ledger_gate._safe`, the gate's single output-join point.

    Every reason-message call site in this module wraps its one echoed
    untrusted field (the task id) in `!r`, and Python's own `repr()` already
    escapes a raw control byte — so the CLI-level fixture above stays GREEN
    even with `_safe`'s neutralization pass fully bypassed (verified: with
    `_safe` short-circuited to `return text`, the CLI-level control-byte test
    above still passes, `repr()` alone carries it). That is a real,
    independently-confirmed finding, not a reason to skip pinning `_safe`
    itself — a future call site echoing raw (non-`!r`) untrusted text would
    have no such protection, and this is the one test that would catch a
    regression in `_safe`'s own contract."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ledger_gate_direct", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._safe("secret sk_live_Zq7Kd2 here") == "secret [redacted] here"
    assert module._safe("raw \x1b escape") == "raw \\x1b escape"


# ---------------------------------------------------------------------------
# Sibling-import robustness — matches the established test across the family
# ---------------------------------------------------------------------------


def test_sibling_import_resolves_from_an_unrelated_cwd(tmp_path):
    r = _run(CLEAN_MULTI_ENTRY, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
