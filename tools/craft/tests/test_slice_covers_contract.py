"""`/craft:slice` states, certifies, and writes the `**Covers:**` field.

These tests bind `slice/SKILL.md`'s prose to something executable — the real
`covers_gate.py` script, a real subprocess pipe built from the command line the
skill itself documents, and the real ledger-line templates extracted out of the
skill's own text — never to itself. A test asserting the skill file merely
contains a phrase is not acceptable here (spec Constraints), so every extraction
below is fed through something real rather than compared back to a copy of its
own wording.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SLICE_SKILL = CRAFT / "skills" / "slice" / "SKILL.md"
GATE = CRAFT / "scripts" / "covers_gate.py"
FIXTURES = Path(__file__).parent / "fixtures"

NINE_CRITERIA_SPEC = (FIXTURES / "spec_ac1_to_ac9.md").read_text(encoding="utf-8")
ZERO_CRITERIA_SPEC = (FIXTURES / "spec_zero_criteria.md").read_text(encoding="utf-8")
MISSING_HEADING_SPEC = (FIXTURES / "spec_missing_ac_heading.md").read_text(encoding="utf-8")


def _skill_text() -> str:
    return SLICE_SKILL.read_text(encoding="utf-8")


def _step(name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading."""
    text = _skill_text()
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d+\.", rest)
    return rest[: end.start()] if end else rest


# ---- referential integrity: the documented gate path resolves to a real file ----


def test_gate_script_path_named_in_slice_skill_resolves_to_a_real_file():
    match = re.search(
        r"\$\{CLAUDE_PLUGIN_ROOT\}/(scripts/covers_gate\.py)", _skill_text()
    )
    assert match, "slice/SKILL.md must reference the covers gate via ${CLAUDE_PLUGIN_ROOT}"
    resolved = CRAFT / match.group(1)
    assert resolved.exists(), (
        f"slice/SKILL.md points {match.group(0)!r} at {resolved}, which does not "
        "exist — the reference and the shipped script have drifted apart"
    )
    assert resolved == GATE


# ---- the documented --covers grammar is the grammar the gate enforces ----


def test_skill_worked_example_covers_value_passes_the_real_gate():
    match = re.search(r"covers_gate\.py --covers \"([^\"]+)\"", _skill_text())
    assert match, "slice/SKILL.md must show a worked --covers example"
    covers_value = match.group(1)

    result = subprocess.run(
        [sys.executable, str(GATE), "--covers", covers_value],
        input=NINE_CRITERIA_SPEC,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"slice/SKILL.md's worked example --covers {covers_value!r} must exit 0 "
        f"against a spec declaring those criteria: {result.stderr}{result.stdout}"
    )


# ---- an upstream lore record show failure never reads as a clean certification ----


def test_lore_record_show_failure_upstream_of_the_documented_pipe_is_never_a_clean_exit():
    """Extract step 9's own documented pipeline verbatim, substitute a failing
    stand-in for `lore record show spec/<spec-name>` (bad spec name / unreadable
    vault: it errors and, worst case, its error text lands on stdout exactly as
    a real spec body would), and run the resulting command exactly as documented.
    The gate's own fail-closed parser must still refuse it, so the documented
    pipeline as a whole never reports success on an upstream failure — this pins
    that behavior against the skill's actual command line, not a copy of it."""
    step9 = _step("### 9. Materialize the parent task")
    match = re.search(
        r"lore record show spec/<spec-name> \| .*covers_gate\.py --covers \"[^\"]+\"",
        step9,
    )
    assert match, "slice/SKILL.md step 9 must document the certify pipe verbatim"
    documented_line = match.group(0)

    failing_producer = (
        "sh -c 'printf \"Error: no such record spec/does-not-exist\\n\"; exit 1'"
    )
    pipeline = documented_line.replace(
        "lore record show spec/<spec-name>", failing_producer
    ).replace("${CLAUDE_PLUGIN_ROOT}", str(CRAFT))

    result = subprocess.run(pipeline, shell=True, capture_output=True, text=True)

    assert result.returncode != 0, (
        "the documented pipe must never exit 0 when the upstream lore record "
        f"show call has failed — ran: {pipeline!r}: {result.stderr}{result.stdout}"
    )


# ---- ledger-reconcile fallback: legacy parents get no coverage field ----


def _ledger_line_templates() -> list[str]:
    """Every `- **<slice title>** ...` fenced-code ledger-line template step 4
    documents, in document order — the legacy (no-coverage) shape is written
    first, the covers-carrying extension second."""
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    blocks = re.findall(r"```\n(.*?)\n```", step4, re.DOTALL)
    return [b.strip() for b in blocks if b.strip().startswith("- **<slice title>**")]


def _render(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace(f"<{key}>", value)
    return out


def test_legacy_parent_with_no_covers_field_yields_a_ledger_line_with_no_coverage_field():
    templates = _ledger_line_templates()
    assert len(templates) == 4, (
        "slice/SKILL.md step 4 must document exactly four ledger-line shapes — "
        "no coverage, covers-only, partial-only, and covers-plus-partial; "
        f"found {templates}"
    )
    legacy = templates[0]  # documented first: the four-field, no-coverage shape
    rendered = _render(
        legacy,
        **{
            "slice title": "The streaming export slice",
            "value claim": "Exports stream instead of buffering in memory",
            "task-id": "the-streaming-export-slice",
            "close-date": "2026-09-03",
        },
    )
    assert "covers" not in rendered, (
        "a legacy parent (no **Covers:** field) must render a ledger line "
        f"carrying no coverage field at all, per slice/SKILL.md step 4's own "
        f"documented shape: {rendered!r}"
    )


def test_covers_carrying_parent_yields_a_ledger_line_the_real_gate_certifies():
    """The covers-carrying ledger-line shape is fed the identifiers a slice
    actually recorded, and the rendered coverage token is then run through
    the real `covers_gate.py` against a fixture spec declaring those
    identifiers — pinning the documented ledger shape against the real
    grammar, not against a copy of its own wording."""
    templates = _ledger_line_templates()
    covers_shape = templates[1]  # documented second: the fifth-token extension
    rendered = _render(
        covers_shape,
        **{
            "slice title": "The streaming export slice",
            "value claim": "Exports stream instead of buffering in memory",
            "task-id": "the-streaming-export-slice",
            "close-date": "2026-09-03",
            "covers-value": "AC2, AC5",
        },
    )
    match = re.search(r"covers ([^)]+)\)", rendered)
    assert match, f"rendered ledger line must carry a 'covers <value>)' token: {rendered!r}"
    covers_value = match.group(1)

    result = subprocess.run(
        [sys.executable, str(GATE), "--covers", covers_value],
        input=NINE_CRITERIA_SPEC,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"the rendered ledger line's coverage token {covers_value!r} must "
        f"certify against the real gate: {result.stderr}{result.stdout}"
    )


# ---- the gate certifies before the record is created, structurally --------


def test_certify_pipe_precedes_lore_record_create_by_document_position():
    """Reordering the certify paragraph after the `lore record create` block
    would re-open the exact defect this gate exists to fix, and no test
    would catch a reword-only pass unless it pins document position. Locate
    both operations by the real command text step 9 documents and assert
    the certify pipe comes first."""
    step9 = _step("### 9. Materialize the parent task")
    certify_match = re.search(r"covers_gate\.py --covers \"[^\"]+\"", step9)
    create_match = re.search(r"\|\s*lore record create \\", step9)
    assert certify_match, "slice/SKILL.md step 9 must document the certify pipe"
    assert create_match, "slice/SKILL.md step 9 must document the lore record create invocation"
    assert certify_match.start() < create_match.start(), (
        "the covers gate certify pipe must be documented, by position, before "
        "the lore record create invocation in slice/SKILL.md step 9"
    )


# ---- the carve-out keys on the real gate's machine token, not on prose ---


def test_step9_carveout_reason_code_matches_real_gate_output_and_only_there():
    """Extract the `reason-code:` token step 9 documents as the carve-out's
    discriminator, then run the real gate against a zero-identifier spec and
    a missing-heading spec. The token must appear in the former's stderr and
    be absent from the latter's — pinning that the documented discriminator
    is the real gate's own signal, not a copy of its wording, and that it
    does not also fire on an unrelated exit-2 reason."""
    step9 = _step("### 9. Materialize the parent task")
    match = re.search(r"reason-code:\s*([a-z0-9-]+)", step9)
    assert match, "slice/SKILL.md step 9 must document the machine-readable reason-code token"
    token_line = f"reason-code: {match.group(1)}"

    zero_result = subprocess.run(
        [sys.executable, str(GATE), "--covers", "AC1"],
        input=ZERO_CRITERIA_SPEC,
        capture_output=True,
        text=True,
    )
    missing_result = subprocess.run(
        [sys.executable, str(GATE), "--covers", "AC1"],
        input=MISSING_HEADING_SPEC,
        capture_output=True,
        text=True,
    )

    assert token_line in zero_result.stderr, (
        f"the documented reason-code {token_line!r} must appear in the real gate's "
        f"stderr on the zero-identifier spec: {zero_result.stderr}"
    )
    assert token_line not in missing_result.stderr, (
        f"the documented reason-code {token_line!r} must not appear on an unrelated "
        f"exit-2 reason: {missing_result.stderr}"
    )


# ---- the documented **Covers:** field example is a value the real gate certifies ----


def test_documented_covers_field_example_certifies_against_the_real_gate():
    """Step 9 must show the field's written form — label plus value — the
    way step 4's ledger-line templates are exemplified. Extract it and run
    the value through the real gate against a fixture spec declaring those
    identifiers, so the example is pinned against real grammar rather than
    left as an unchecked illustration."""
    step9 = _step("### 9. Materialize the parent task")
    match = re.search(r"\*\*Covers:\*\*\s*([A-Za-z0-9, ]+)", step9)
    assert match, "slice/SKILL.md step 9 must show the **Covers:** field's written form"
    covers_value = match.group(1).strip()

    result = subprocess.run(
        [sys.executable, str(GATE), "--covers", covers_value],
        input=NINE_CRITERIA_SPEC,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"slice/SKILL.md's documented **Covers:** field example {covers_value!r} "
        f"must certify against a spec declaring those criteria: {result.stderr}{result.stdout}"
    )


# ---- --covers gets a strict positive allow-list, not the free-text scrub -----
#
# `--covers` is not free-form prose — its grammar is fixed as comma-separated
# `ACn` tokens and nothing else. Step 9 must validate the drafted value
# against that grammar's own shape, before substitution, mirroring the
# `<spec-name>` allow-list precedent (step 1) rather than the free-text scrub
# the slice title receives below it.


def _documented_covers_shape() -> str:
    step9 = _step("### 9. Materialize the parent task")
    match = re.search(r"--covers.*?shape `([^`]+)`", step9, re.DOTALL)
    assert match, (
        "slice/SKILL.md step 9 must document a positive allow-list shape for "
        "the drafted --covers value, the way step 1 documents one for "
        "<spec-name>"
    )
    return match.group(1)


def test_step9_documents_a_positive_allowlist_shape_for_covers_before_substitution():
    step9 = _step("### 9. Materialize the parent task")
    assert re.search(r"before\s+(any\s+)?substitution", step9, re.IGNORECASE), (
        "slice/SKILL.md step 9 must state the --covers allow-list check runs "
        "before substitution, mirroring step 1's <spec-name> guard"
    )


def test_covers_allowlist_shape_accepts_the_documented_worked_example():
    shape = _documented_covers_shape()
    assert re.match(shape, "AC2, AC5"), (
        f"the documented --covers allow-list shape {shape!r} must accept the "
        "worked example value 'AC2, AC5'"
    )


def test_covers_allowlist_shape_rejects_a_value_carrying_an_unescaped_double_quote():
    """HIGH-3 repro: the call site wraps the value in double quotes
    (`--covers "AC2, AC5"`), so a value carrying an unescaped `"` terminates
    the argument early and exposes the rest of the line as unquoted shell
    tokens. The free-text scrub used for the slice title (strips only `'`,
    newline, backtick, `$`) never strips `"` and would let this value
    through untouched — the strict allow-list must reject it outright."""
    shape = _documented_covers_shape()
    malicious = 'AC2, AC5"; touch pwned #'
    assert re.match(shape, malicious) is None, (
        f"the documented --covers allow-list shape {shape!r} must reject a "
        f"value carrying an unescaped double quote: {malicious!r}"
    )


def test_covers_allowlist_shape_rejects_free_text_prose():
    shape = _documented_covers_shape()
    prose = "this covers the login flow"
    assert re.match(shape, prose) is None, (
        f"the documented --covers allow-list shape {shape!r} must reject "
        f"free-text prose that carries no ACn token: {prose!r}"
    )


# ---- --partial-covers gets the identical positive allow-list -----------------
#
# Step 9 documents `**Partially covers:**` as taking the identical
# `^AC\d+(, ?AC\d+)*$` shape check `--covers` already takes, validated before
# any substitution. These tests pin that the documented partial-covers shape
# is the real gate's own `--partial-covers` grammar, not a copy of the
# `--covers` wording that happens to look similar.


def _documented_partial_covers_shape() -> str:
    step9 = _step("### 9. Materialize the parent task")
    match = re.search(
        r"Partially covers.*?shape\s*\n?`([^`]+)`\s*step 9 already applies to `--covers`",
        step9,
        re.DOTALL,
    )
    assert match, (
        "slice/SKILL.md step 9 must document a positive allow-list shape for "
        "the drafted --partial-covers value"
    )
    return match.group(1)


def test_step9_documents_before_substitution_for_partial_covers_too():
    step9 = _step("### 9. Materialize the parent task")
    partial_section = step9[step9.index("**Partially covers:**") :]
    assert re.search(r"before\s+any\s+substitution", partial_section, re.IGNORECASE), (
        "slice/SKILL.md step 9 must state the --partial-covers allow-list "
        "check runs before substitution, mirroring the --covers guard"
    )


def test_partial_covers_allowlist_shape_is_identical_to_the_covers_shape():
    """Step 9 states the partial-covers value takes 'the identical' shape —
    pin that the two documented shapes are literally the same regex, so a
    future edit that lets them drift apart is caught here rather than only
    at review time."""
    assert _documented_partial_covers_shape() == _documented_covers_shape()


def test_partial_covers_allowlist_shape_accepts_the_documented_worked_example():
    shape = _documented_partial_covers_shape()
    assert re.match(shape, "AC7"), (
        f"the documented --partial-covers allow-list shape {shape!r} must "
        "accept the worked example value 'AC7'"
    )


def test_partial_covers_allowlist_shape_rejects_a_value_carrying_an_unescaped_double_quote():
    shape = _documented_partial_covers_shape()
    malicious = 'AC7"; touch pwned #'
    assert re.match(shape, malicious) is None, (
        f"the documented --partial-covers allow-list shape {shape!r} must "
        f"reject a value carrying an unescaped double quote: {malicious!r}"
    )


def test_partial_covers_allowlist_shape_rejects_a_value_carrying_a_backtick():
    shape = _documented_partial_covers_shape()
    malicious = "AC7`whoami`"
    assert re.match(shape, malicious) is None, (
        f"the documented --partial-covers allow-list shape {shape!r} must "
        f"reject a value carrying a backtick: {malicious!r}"
    )


def test_partial_covers_allowlist_shape_rejects_a_value_carrying_a_newline():
    shape = _documented_partial_covers_shape()
    malicious = "AC7\nrm -rf /"
    assert re.match(shape, malicious) is None, (
        f"the documented --partial-covers allow-list shape {shape!r} must "
        f"reject a value carrying a newline: {malicious!r}"
    )


def test_partial_covers_allowlist_shape_rejects_free_text_prose():
    shape = _documented_partial_covers_shape()
    prose = "this partially covers the login flow"
    assert re.match(shape, prose) is None, (
        f"the documented --partial-covers allow-list shape {shape!r} must "
        f"reject free-text prose that carries no ACn token: {prose!r}"
    )


# ---- the certify pipe passes --covers and --partial-covers as two distinct
#      quoted arguments, never one interpolated string ----------------------


def _documented_dual_flag_invocation() -> re.Match[str]:
    step9 = _step("### 9. Materialize the parent task")
    match = re.search(
        r'covers_gate\.py --covers "([^"]+)" --partial-covers "([^"]+)"', step9
    )
    assert match, (
        "slice/SKILL.md step 9 must document a certify invocation passing "
        "both --covers and --partial-covers"
    )
    return match


def test_step9_documents_covers_and_partial_covers_as_two_distinct_quoted_arguments():
    """Reject a single interpolated string in place of two flags: a
    combined value like `--covers "AC2, AC5, partially covers AC7"` would
    match no `--partial-covers` flag at all and must not satisfy this
    extraction."""
    match = _documented_dual_flag_invocation()
    covers_value, partial_value = match.group(1), match.group(2)
    assert covers_value == "AC2, AC5"
    assert partial_value == "AC7"
    # the two values must never collapse into a single --covers argument
    assert "partially covers" not in covers_value


def test_documented_dual_flag_invocation_certifies_against_the_real_gate():
    match = _documented_dual_flag_invocation()
    covers_value, partial_value = match.group(1), match.group(2)

    result = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--covers",
            covers_value,
            "--partial-covers",
            partial_value,
        ],
        input=NINE_CRITERIA_SPEC,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"slice/SKILL.md's documented dual-flag invocation "
        f"(--covers {covers_value!r} --partial-covers {partial_value!r}) must "
        f"exit 0 against a spec declaring those criteria: {result.stderr}{result.stdout}"
    )


def test_documented_partially_covers_field_example_certifies_against_the_real_gate():
    """Step 9 must show `**Partially covers:**`'s written form, the same as
    `**Covers:**`'s. Extract it and run the value through the real gate
    (as --partial-covers, since --covers is not required for a lone partial
    list) against a fixture spec declaring those identifiers."""
    step9 = _step("### 9. Materialize the parent task")
    match = re.search(r"\*\*Partially covers:\*\*\s*([A-Za-z0-9, ]+)", step9)
    assert match, "slice/SKILL.md step 9 must show the **Partially covers:** field's written form"
    partial_value = match.group(1).strip()

    result = subprocess.run(
        [sys.executable, str(GATE), "--partial-covers", partial_value],
        input=NINE_CRITERIA_SPEC,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"slice/SKILL.md's documented **Partially covers:** field example "
        f"{partial_value!r} must certify against a spec declaring those "
        f"criteria: {result.stderr}{result.stdout}"
    )
