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
    documents, in the order they appear."""
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    blocks = re.findall(r"```\n(.*?)\n```", step4, re.DOTALL)
    return [b.strip() for b in blocks if b.strip().startswith("- **<slice title>**")]


def _render(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace(f"<{key}>", value)
    return out


def test_step_4_documents_a_legacy_and_a_covers_carrying_ledger_line_shape():
    templates = _ledger_line_templates()
    assert len(templates) == 2, (
        "slice/SKILL.md step 4 must document exactly two ledger-line shapes — "
        f"one with no coverage field and one carrying it; found {templates}"
    )


def test_legacy_parent_with_no_covers_field_yields_a_ledger_line_with_no_coverage_field():
    templates = _ledger_line_templates()
    legacy = min(templates, key=len)  # the shape with no coverage token is shorter
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


def test_covers_carrying_parent_yields_a_ledger_line_naming_the_coverage():
    templates = _ledger_line_templates()
    covers_shape = max(templates, key=len)
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
    assert "AC2, AC5" in rendered, (
        "slice/SKILL.md step 4's covers-carrying ledger-line shape must let "
        f"the covered identifiers be substituted in: {rendered!r}"
    )
