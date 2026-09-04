"""`/craft:slice` step 4 must certify the `## Slices` ledger through the real
`ledger_gate.py` before it derives a candidate set from it — strictly before
`candidate_set.py`'s own pipe, per the plan this task builds:
`task/the-ledger-s-coverage-attestation-is-trustworthy-by-construction`.

These tests bind the skill's documented ledger-gate invocation to something
executable — the real gate script, run either as a subprocess or (for the
shell-injection-shaped item 1) through `bash -c` exactly as the document
shows it — never to a copy of the document's own wording. A test asserting
the document merely contains a phrase is not acceptable here.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from test_ledger_gate import (
    CLEAN_MULTI_ENTRY,
    COVERAGE_CLAIMED_TWICE,
    DUPLICATE_TASK_ID,
    INVISIBLE_TASK_ID,
    ORPHANED_ENTRY,
    UNTERMINATED_FENCE,
    WIDENED_CONTRADICTS_PARENT,
    BACKFILL_MONOTONIC,
    GATE as LEDGER_GATE,
    _run as _run_gate,
    _write_parent_coverage,
)

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SLICE_SKILL = CRAFT / "skills" / "slice" / "SKILL.md"
SCRIPTS_DIR = CRAFT / "scripts"
FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATE_SET = SCRIPTS_DIR / "candidate_set.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import candidate_set  # noqa: E402

BACKFILL_END_TO_END = (FIXTURES / "ledger_backfill_end_to_end.md").read_text(encoding="utf-8")


def _skill_text() -> str:
    return SLICE_SKILL.read_text(encoding="utf-8")


def _step(name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading."""
    text = _skill_text()
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d+\.", rest)
    return rest[: end.start()] if end else rest


def _step4() -> str:
    return _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")


# ---------------------------------------------------------------------------
# Item 1 — the documented ledger-gate invocation, executed exactly as written
# ---------------------------------------------------------------------------


def _documented_ledger_gate_command() -> str:
    step4 = _step4()
    blocks = re.findall(r"```sh\n(.*?)\n```", step4, re.DOTALL)
    matches = [b.strip() for b in blocks if "ledger_gate.py" in b]
    assert len(matches) == 1, (
        "slice/SKILL.md step 4 must document exactly one ledger_gate.py "
        f"invocation in a ```sh fence; found {len(matches)}"
    )
    return matches[0]


def _run_documented_ledger_gate_command(spec_body: str, parent_coverage_path: Path) -> subprocess.CompletedProcess:
    command = _documented_ledger_gate_command()
    command = command.replace("<spec-name>", "fixture-spec")
    command = re.sub(r"<path-to-temp-parent-coverage\.json>", str(parent_coverage_path), command)
    spec_file = parent_coverage_path.parent / "spec_body.md"
    spec_file.write_text(spec_body, encoding="utf-8")
    script = (
        f"lore() {{ cat {spec_file}; }}\n"
        f"export CLAUDE_PLUGIN_ROOT={CRAFT}\n"
        f"{command}\n"
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_documented_invocation_exits_0_on_a_clean_ledger(tmp_path):
    parent_coverage = tmp_path / "parents.json"
    parent_coverage.write_text(
        json.dumps(
            {
                "alpha": {"covers": "AC1, AC2"},
                "beta": {"partially covers": "AC3"},
                "gamma": {"covers": "AC4", "partially covers": "AC5"},
            }
        ),
        encoding="utf-8",
    )
    result = _run_documented_ledger_gate_command(CLEAN_MULTI_ENTRY, parent_coverage)
    assert result.returncode == 0, result.stderr + result.stdout


def test_documented_invocation_exits_nonzero_on_a_line_contradicting_its_parent(tmp_path):
    parent_coverage = tmp_path / "parents.json"
    parent_coverage.write_text(json.dumps({"alpha": {"covers": "AC9"}}), encoding="utf-8")
    result = _run_documented_ledger_gate_command(WIDENED_CONTRADICTS_PARENT, parent_coverage)
    assert result.returncode != 0, (
        "the documented invocation must refuse a ledger line that contradicts "
        f"its parent's coverage: {result.stdout!r} {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Item 2 — the documented exit-code contract agrees with the real exits
# ---------------------------------------------------------------------------


def _documented_reason_codes(step4: str, exit_label: str) -> set[str]:
    match = re.search(rf"On exit {exit_label} — (.+?) —", step4, re.DOTALL)
    assert match, f"slice/SKILL.md step 4 must document exit {exit_label}'s reason codes"
    return set(re.findall(r"`([a-z0-9-]+)`", match.group(1)))


REAL_EXIT_1_CODES = {
    "invisible-ledger-task-id",
    "duplicate-ledger-task-id",
    "coverage-claimed-twice",
    "orphaned-ledger-entry",
    "coverage-contradicts-parent",
}

REAL_EXIT_2_CODES = {
    "empty-stdin",
    "non-utf8-stdin",
    "stdin-too-large",
    "duplicate-slices-heading",
    "unterminated-masked-region",
    "malformed-coverage-token",
    "malformed-parent-coverage",
}


def test_documented_exit_1_reason_codes_match_the_gate_s_real_exit_1_codes():
    documented = _documented_reason_codes(_step4(), "1")
    assert documented == REAL_EXIT_1_CODES, (
        f"documented exit-1 codes {documented} diverge from the gate's real "
        f"exit-1 codes {REAL_EXIT_1_CODES}"
    )


def test_documented_exit_2_reason_codes_match_the_gate_s_real_exit_2_codes():
    documented = _documented_reason_codes(_step4(), "2")
    assert documented == REAL_EXIT_2_CODES, (
        f"documented exit-2 codes {documented} diverge from the gate's real "
        f"exit-2 codes {REAL_EXIT_2_CODES}"
    )


def test_each_documented_exit_1_code_actually_produces_exit_1(tmp_path):
    cases = [
        (DUPLICATE_TASK_ID, None, "duplicate-ledger-task-id"),
        (COVERAGE_CLAIMED_TWICE, None, "coverage-claimed-twice"),
        (INVISIBLE_TASK_ID, None, "invisible-ledger-task-id"),
    ]
    for body, extra, code in cases:
        r = _run_gate(body, extra)
        assert r.returncode == 1, f"{code}: {r.stderr}"
        assert code in r.stderr, r.stderr

    orphaned_parents = _write_parent_coverage(tmp_path, {"someone-else": {"covers": "AC1"}})
    r = _run_gate(ORPHANED_ENTRY, ["--parent-coverage", orphaned_parents])
    assert r.returncode == 1
    assert "orphaned-ledger-entry" in r.stderr

    widened_parents = _write_parent_coverage(tmp_path, {"alpha": {"covers": "AC2"}})
    r = _run_gate(WIDENED_CONTRADICTS_PARENT, ["--parent-coverage", widened_parents])
    assert r.returncode == 1
    assert "coverage-contradicts-parent" in r.stderr


def test_each_documented_exit_2_code_actually_produces_exit_2(tmp_path):
    r = _run_gate("")
    assert r.returncode == 2 and "empty-stdin" in r.stderr

    r = subprocess.run([sys.executable, str(LEDGER_GATE)], input=b"\xff\xfe", capture_output=True)
    assert r.returncode == 2 and b"non-utf8-stdin" in r.stderr

    huge = CLEAN_MULTI_ENTRY + ("x" * (256 * 1024 + 1))
    r = _run_gate(huge)
    assert r.returncode == 2 and "stdin-too-large" in r.stderr

    dup_heading = CLEAN_MULTI_ENTRY + "\n## Slices\n\nmore\n"
    r = _run_gate(dup_heading)
    assert r.returncode == 2 and "duplicate-slices-heading" in r.stderr

    r = _run_gate(UNTERMINATED_FENCE)
    assert r.returncode == 2 and "unterminated-masked-region" in r.stderr

    malformed_token_body = (
        "## Acceptance Criteria\n\n- **AC1.** A fixture criterion.\n\n"
        "## Slices\n\n- **First slice** — a fixture value claim. "
        "(`task/alpha`, closed 2026-01-01, covers AC1, AC1)\n"
    )
    r = _run_gate(malformed_token_body)
    assert r.returncode == 2 and "malformed-coverage-token" in r.stderr

    r = _run_gate(CLEAN_MULTI_ENTRY, ["--parent-coverage", "/no/such/file.json"])
    assert r.returncode == 2 and "malformed-parent-coverage" in r.stderr


# ---------------------------------------------------------------------------
# Item 3 — document order: ledger gate invocation precedes candidate-set gate
# ---------------------------------------------------------------------------


def test_ledger_gate_invocation_documented_before_candidate_set_gate():
    step4 = _step4()
    ledger_match = re.search(r"ledger_gate\.py", step4)
    candidate_match = re.search(r"candidate_set\.py", step4)
    assert ledger_match, "slice/SKILL.md step 4 must document the ledger_gate.py invocation"
    assert candidate_match, "slice/SKILL.md step 4 must document the candidate_set.py invocation"
    assert ledger_match.start() < candidate_match.start(), (
        "the ledger_gate.py invocation must be documented, by position, before "
        "the candidate_set.py invocation — an unverifiable ledger must refuse "
        "the pass before a candidate set is derived from it"
    )


# ---------------------------------------------------------------------------
# Item 4 — the documented failure-handling step persists only reason-code
# ---------------------------------------------------------------------------


def _harvested_gate_tokens() -> set[str]:
    """The set of `<name>:` output-token names the real gate actually emits,
    read off a live exit-2 run rather than hardcoded — if the gate ever stops
    emitting one of `reason:` / `reason-code:` this harvest silently loses it
    and the assertions below that require both catch that drift."""
    result = _run_gate("")
    assert result.returncode == 2, f"expected empty-stdin exit 2: {result.stderr!r}"
    return {f"{name}:" for name in re.findall(r"^ledger-gate: ([a-z-]+):", result.stderr, re.MULTILINE)}


_PERSIST_KEYWORDS = re.compile(r"persist|copied? into|durable artifact", re.I)
_NEGATION_KEYWORDS = re.compile(r"\bnever\b|\bnot\b|\bno\b", re.I)


def _persist_directives(step4: str, tokens: set[str]) -> dict[str, bool]:
    """For each harvested token mentioned, in the same clause, alongside a
    persistence-directive keyword, derive whether that clause directs a
    session to persist it (True) or forbids persisting it (False) — a clause
    is split on em-dashes and sentence boundaries so this reads the
    document's own clause structure rather than a fixed phrase's wording."""
    clauses = re.split(r"\s—\s|(?<=[.:])\s+(?=[A-Z*])", step4)
    directives: dict[str, bool] = {}
    for clause in clauses:
        if not _PERSIST_KEYWORDS.search(clause):
            continue
        for tok in tokens:
            if f"`{tok}`" in clause:
                directives[tok] = not _NEGATION_KEYWORDS.search(clause)
    return directives


def test_step4_never_documents_persisting_the_free_text_reason_line():
    """Following the document for a refusal must produce a report/body
    carrying only the `reason-code:` token, never the `reason:` free text —
    the shape `lesson/an-agent-retelling-untrusted-content-launders-it-out-of-its-marker`
    names.

    This is derived relationally, not by phrase-matching the document's
    wording: the set of tokens the *real* gate emits is harvested from a live
    run, then cross-referenced against which of those tokens step 4's own
    clause structure directs a session to persist. A test asserting the
    document merely contains a phrase is not acceptable here."""
    tokens = _harvested_gate_tokens()
    assert "reason:" in tokens and "reason-code:" in tokens, (
        "the ledger gate must emit both a `reason:` and a `reason-code:` "
        f"token for this pin to mean anything; harvested {tokens}"
    )

    directives = _persist_directives(_step4(), tokens)
    persist_set = {tok for tok, must_persist in directives.items() if must_persist}

    assert "reason-code:" in persist_set, (
        "slice/SKILL.md step 4 must direct a session to persist the "
        "reason-code: token into a durable artifact on the gate's refusal"
    )
    assert "reason:" not in persist_set, (
        "slice/SKILL.md step 4 must never direct a session to persist the "
        "free-text reason: line into a durable artifact"
    )


# ---------------------------------------------------------------------------
# Item 5 — exactly one documented ledger coverage-write site in the corpus
# ---------------------------------------------------------------------------


def test_exactly_one_documented_ledger_coverage_write_site_in_the_corpus():
    marker = re.compile(r"Reconcile the `## Slices` ledger")
    skills_dir = CRAFT / "skills"
    hits = []
    for f in sorted(skills_dir.rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        for _ in marker.finditer(text):
            hits.append(f)
    assert len(hits) == 1, (
        f"expected exactly one documented ledger coverage-write site across the "
        f"craft skill corpus, found {len(hits)}: {hits}"
    )


# ---------------------------------------------------------------------------
# Task 4 — the legacy backfill: an existing no-token line gains a token,
# exactly once, under the monotonic rule step 4's new clause documents.
# ---------------------------------------------------------------------------


def _backfill(spec_body: str, parent_coverage: dict) -> str:
    """Test-local embodiment of slice/SKILL.md step 4's documented backfill
    clause ("Backfilling an existing line under the same rule"): every
    `## Slices` line carrying no `covers`/`partially covers` token is
    extended in place with those field(s) from its own named parent
    (`parent_coverage`, keyed on the bare task id), in the same
    trailing-parenthetical shape the append-new-line logic already writes.
    A line that already carries a token is never touched, regardless of
    what its parent says. This calls the real, frozen `parse_ledger_entries`
    to find each entry's task id and span rather than re-deriving either."""
    lines = candidate_set._COMMONMARK_LINE_RE.split(spec_body)
    for entry in candidate_set.parse_ledger_entries(spec_body):
        if entry.covers or entry.partial:
            continue
        parent = parent_coverage.get(entry.task_id)
        if not parent:
            continue
        covers_val = parent.get("covers")
        partial_val = parent.get("partially covers")
        if not covers_val and not partial_val:
            continue
        idx = entry.end_line - 1
        stripped = lines[idx].rstrip()
        trailing_ws = lines[idx][len(stripped):]
        assert stripped.endswith(")"), f"entry line must end in its parenthetical: {stripped!r}"
        insert = ""
        if covers_val:
            insert += f", covers {covers_val}"
        if partial_val:
            insert += f", partially covers {partial_val}"
        lines[idx] = stripped[:-1] + insert + ")" + trailing_ws
    return "\n".join(lines)


def _run_candidate_set(spec_body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CANDIDATE_SET)], input=spec_body, capture_output=True, text=True
    )


def _entries_by_id(spec_body: str) -> dict:
    return {e.task_id: e for e in candidate_set.parse_ledger_entries(spec_body)}


def test_backfill_writes_a_no_token_line_s_token_exactly_once_and_is_idempotent():
    """Contract item 1: a no-token line whose parent carries one is
    backfilled exactly once — running the backfill twice over the same
    body is idempotent, producing no second token and no duplicate entry."""
    parent_coverage = {"legacy": {"covers": "AC1, AC2"}}
    once = _backfill(BACKFILL_MONOTONIC, parent_coverage)
    assert "covers AC1, AC2" in once

    before = _entries_by_id(BACKFILL_MONOTONIC)
    assert before["legacy"].covers == [] and before["legacy"].partial == []

    after_once = _entries_by_id(once)
    assert after_once["legacy"].covers == ["AC1", "AC2"]

    twice = _backfill(once, parent_coverage)
    assert twice == once, "a second backfill pass must not change an already-backfilled body"

    after_twice = _entries_by_id(twice)
    assert len(after_twice) == 1, "backfilling twice must not duplicate the ledger entry"
    assert after_twice["legacy"].covers == ["AC1", "AC2"]


def test_backfill_never_rewrites_a_line_that_already_carries_a_token(tmp_path):
    """Contract item 2: a line that already carries a token is never
    rewritten by the backfill, even when its parent disagrees — that case
    is `coverage-contradicts-parent`, a refusal, not a backfill."""
    parent_coverage = {"alpha": {"covers": "AC9"}}
    result = _backfill(WIDENED_CONTRADICTS_PARENT, parent_coverage)
    assert result == WIDENED_CONTRADICTS_PARENT, (
        "the backfill must leave a line carrying a token untouched, even "
        "when its parent's coverage now disagrees with it"
    )

    # The untouched line still correctly refuses at the ledger gate — this
    # is the gate's job (coverage-contradicts-parent), never the backfill's.
    parent_coverage_path = _write_parent_coverage(tmp_path, parent_coverage)
    r = _run_gate(result, ["--parent-coverage", parent_coverage_path])
    assert r.returncode == 1
    assert "coverage-contradicts-parent" in r.stderr


def test_backfill_leaves_a_line_untouched_when_its_parent_also_carries_no_token():
    """Contract item 3: a line with no token whose parent also carries none
    stays legacy and untouched, and the ledger gate still exits 0 on it."""
    result = _backfill(BACKFILL_MONOTONIC, {})
    assert result == BACKFILL_MONOTONIC, (
        "the backfill must leave a no-token line untouched when its parent "
        "declares no coverage either"
    )
    r = _run_gate(result)
    assert r.returncode == 0, r.stderr


def test_ledger_gate_exits_0_after_a_backfill(tmp_path):
    """Contract item 4: after a backfill, ledger_gate.py exits 0 on the
    resulting body — the backfill cannot produce a state its own gate
    refuses."""
    parent_coverage = {"legacy": {"covers": "AC1, AC2"}}
    backfilled = _backfill(BACKFILL_MONOTONIC, parent_coverage)
    assert _entries_by_id(backfilled)["legacy"].covers == ["AC1", "AC2"], (
        "the backfill must have actually written the token before this checks the gate"
    )
    parent_coverage_path = _write_parent_coverage(tmp_path, parent_coverage)
    r = _run_gate(backfilled, ["--parent-coverage", parent_coverage_path])
    assert r.returncode == 0, r.stderr


def test_end_to_end_backfill_moves_candidate_set_from_no_to_yes():
    """Contract item 6: end to end on a fixture reproducing this spec's
    shape — two legacy entries, parents since given coverage fields —
    candidate_set.py moves from `complete-eligible: no` to `yes` and the
    previously-stranded identifiers leave the candidate set."""
    before = _run_candidate_set(BACKFILL_END_TO_END)
    assert before.returncode == 0, before.stderr
    assert "complete-eligible: no" in before.stdout
    assert "candidates: AC1, AC2" in before.stdout

    parent_coverage = {"legacy-one": {"covers": "AC1"}, "legacy-two": {"covers": "AC2"}}
    backfilled = _backfill(BACKFILL_END_TO_END, parent_coverage)
    assert backfilled != BACKFILL_END_TO_END

    after = _run_candidate_set(backfilled)
    assert after.returncode == 0, after.stderr
    assert "complete-eligible: yes" in after.stdout
    assert "candidates: none" in after.stdout
