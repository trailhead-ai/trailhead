"""Brainstorm's framing step (step 1, "Frame") resolves a maturity level for
every repository the work touches, before grilling (step 2) begins — per
`spec/project-maturity-levels-calibrate-craft-s-standard-of-care` AC3/AC3b.

These tests bind the skill's prose to something executable — the real
`maturity_resolve.py` resolver run as a subprocess against real fixture
agent-instruction bodies — never to a copy of the skill's own wording, mirroring
`test_slice_candidate_set_contract.py`'s established pattern for this repo.

The second half of this module (below the "Write path" banner) covers
brainstorm's spec-write step (step "6a. Write the Spec"): it fills the spec
template's `## Maturity` section from step 1's resolution and certifies the
drafted body through `maturity_stamp.py` before `lore record create` runs —
per `task/brainstorm-stamps-the-spec-and-the-template-carries-the-section`.
Those tests bind to the real `maturity_stamp.py`, `candidate_set.py`, and
`covers_gate.py` scripts run as subprocesses against real fixture spec
bodies, never to a copy of the skill's own wording.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# The resolver runner and fixture bodies are the same ones the resolver's own
# suite drives, so a change to either the invocation or the token block reaches
# both suites from one place.
from test_maturity_resolve import (
    INVALID_VALUE_DECLARED,
    NO_SECTION_AT_ALL,
    _lines,
    _run,
)
# The reader's own suite owns its script path, so the two suites can never
# drift onto different files.
from test_maturity_stamp import STAMP

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
BRAINSTORM_SKILL = CRAFT / "skills" / "brainstorm" / "SKILL.md"
SCRIPTS_DIR = CRAFT / "scripts"
CANDIDATE_SET = SCRIPTS_DIR / "candidate_set.py"
COVERS_GATE = SCRIPTS_DIR / "covers_gate.py"
SPEC_TEMPLATE = CRAFT / "templates" / "spec.md"

# The waiver-discoverability tests below bind the guidance to the real
# renderer, never to a retyped copy of `_CONCERNS` or the guidance's own
# wording — mirroring this module's established pattern.
BARS = SCRIPTS_DIR / "maturity_bars.py"
sys.path.insert(0, str(SCRIPTS_DIR))
import maturity_bars  # noqa: E402


def _skill_text() -> str:
    return BRAINSTORM_SKILL.read_text(encoding="utf-8")


def _step(name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading."""
    text = _skill_text()
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d+\.", rest)
    return rest[: end.start()] if end else rest


# ---- 1. the framing step instructs resolution, before grilling begins ----


# ---- 2. the closed vocabulary is named, and only that vocabulary ----


# ---- 3. an absent declaration resolves to production ----


def test_absent_declaration_resolves_to_production_via_the_real_resolver():
    result = _run(NO_SECTION_AT_ALL.encode("utf-8"))
    assert result.returncode == 0, result.stderr
    assert "level: production" in _lines(result)
    assert "reason: section-absent" in _lines(result)

    frame_step = _step("### 1. Frame")
    # Scoped to the absent-declaration clause alone (up to its terminating
    # `;`), so a `production` mention belonging to the neighbouring
    # invalid-value clause can never satisfy this — a decoy the unscoped
    # `absent...production` search across the whole step would miss.
    absent_clause_match = re.search(r"section-absent[^;]*;", frame_step, re.IGNORECASE)
    assert absent_clause_match, (
        "step 1 must have a section-absent clause terminated by ';'"
    )
    assert "production" in absent_clause_match.group(0), (
        "step 1 must state, within the absent-declaration clause itself, that "
        f"it resolves to production: {absent_clause_match.group(0)!r}"
    )


# ---- 4. an out-of-vocabulary declaration resolves to production and is reported ----


def test_out_of_vocabulary_declaration_resolves_to_production_via_the_real_resolver():
    result = _run(INVALID_VALUE_DECLARED.encode("utf-8"))
    assert result.returncode == 0, result.stderr
    assert "level: production" in _lines(result)
    assert "reason: invalid-value" in _lines(result)
    assert any(line.startswith("offending-value:") for line in _lines(result)), (
        "the resolver must emit the offending-value line the report reads"
    )


# ---- 5. the resolved level is stated per repository, in the session ----


# ---- 6. the resolver is invoked by the same absolute-path convention ----


# ---- 7. behaviour is named for a non-zero resolver exit on an existing file ----


# ---- 8. the unreachable-repository case is named, not silently omitted ----


# ===========================================================================
# Write path — step "6a. Write the Spec" fills `## Maturity` and certifies it
# through `maturity_stamp.py` before `lore record create` runs.
# ===========================================================================

sys.path.insert(0, str(SCRIPTS_DIR))
import maturity_stamp  # noqa: E402

# Scanned from the reader's own module namespace rather than hand-listed here:
# a hardcoded copy of this list is exactly the staleness this reader's tenth
# reason-code (`member-name-too-long`) already exposed once — a new eleventh
# code must fail this test until documented, not silently pass a frozen list.
_STAMP_REASON_CODES = sorted(
    value
    for name, value in vars(maturity_stamp).items()
    if name.endswith("_REASON_CODE")
)


def _write_step() -> str:
    return _step("### 6a. Write the Spec")


def _run_script(
    script: Path, body: str, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *(extra_args or [])],
        input=body.encode("utf-8"),
        capture_output=True,
    )


# ---- 9. the spec template's `## Maturity` section, filled in, round-trips
#         through the real stamp reader at exit 0 ----------------------------


def _render_template_with_maturity_entries(entries: dict[str, str]) -> str:
    """Render `templates/spec.md` with the given `{member: level}` entries filled
    into its `## Maturity` section — the same shape brainstorm's write step
    produces from step 1's resolution."""
    text = SPEC_TEMPLATE.read_text(encoding="utf-8")
    marker = "## Acceptance Criteria"
    idx = text.index(marker)
    bullet_lines = "\n".join(f"- {name}: {level}" for name, level in entries.items())
    return text[:idx] + bullet_lines + "\n\n" + text[idx:]


def test_template_rendered_with_two_repository_maturity_section_accepted_at_exit_0():
    body = _render_template_with_maturity_entries(
        {"trailhead": "production", "lookout": "prototype"}
    )
    result = _run_script(STAMP, body)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert result.stdout.decode("utf-8") == "maturity: lookout=prototype, trailhead=production\n"


# ---- 5. the write step instructs writing the section, by heading, keyed by
#         camp member name ---------------------------------------------------


# ---- 6. the write step instructs certifying via the real reader before
#         `lore record create`, and refusing on a non-zero exit -------------


# ---- 7. every reason-code the reader can emit gets its own named remedy ----


# ---- 8. the unresolved-enumeration case is named, not silently completed ---


# ---- Slices-sibling hard constraint: the template's placement is inert to
#      both spec-reading gates, for a spec that carries a real `## Slices`
#      ledger and one that carries none — a permanent, narrower successor to
#      the assumption-prover's own four-placement probe. -------------------

_HEAD = "# Fixture Spec\n\n## Problem\nSome problem text.\n\n## Objectives\n- Objective one.\n\n"
_AC_SECTION = (
    "## Acceptance Criteria\n\n"
    "- **AC1.** A fixture criterion.\n"
    "- **AC2.** A fixture criterion.\n"
    "- **AC3.** A fixture criterion.\n\n"
)
_SLICES_SECTION = (
    "## Slices\n\n"
    "- **First slice** — a fixture value claim. "
    "(`task/first`, closed 2026-01-01, covers AC1, AC2)\n"
    "- **Second slice** — a fixture value claim. "
    "(`task/second`, closed 2026-01-02, covers AC3)\n\n"
)
_TAIL = "## Non-Goals\nNothing else in scope.\n\n## Related\nn/a\n"
_MATURITY_SECTION = "## Maturity\n\n- trailhead: production\n- lookout: prototype\n\n"

_BASE_WITH_LEDGER = _HEAD + _AC_SECTION + _SLICES_SECTION + _TAIL
_TEMPLATE_PLACEMENT_WITH_LEDGER = _HEAD + _MATURITY_SECTION + _AC_SECTION + _SLICES_SECTION + _TAIL
_BASE_NO_LEDGER = _HEAD + _AC_SECTION + _TAIL
_TEMPLATE_PLACEMENT_NO_LEDGER = _HEAD + _MATURITY_SECTION + _AC_SECTION + _TAIL
# The hazard the prover found: a heading wedged between the `## Slices` heading
# and its first ledger bullet — never the template's own placement, but the
# fixture that proves the inertness assertions below are actually sensitive
# to placement rather than vacuously true.
_HAZARD_MATURITY_NESTED_IN_SLICES = (
    _HEAD
    + _AC_SECTION
    + "## Slices\n\n"
    + _MATURITY_SECTION
    + "- **First slice** — a fixture value claim. "
    "(`task/first`, closed 2026-01-01, covers AC1, AC2)\n"
    "- **Second slice** — a fixture value claim. "
    "(`task/second`, closed 2026-01-02, covers AC3)\n\n"
    + _TAIL
)


def test_template_placement_is_inert_to_candidate_set_with_ledger():
    base = _run_script(CANDIDATE_SET, _BASE_WITH_LEDGER)
    variant = _run_script(CANDIDATE_SET, _TEMPLATE_PLACEMENT_WITH_LEDGER)
    assert variant.returncode == base.returncode
    assert variant.stdout == base.stdout, (base.stdout, variant.stdout)


def test_template_placement_is_inert_to_candidate_set_without_ledger():
    base = _run_script(CANDIDATE_SET, _BASE_NO_LEDGER)
    variant = _run_script(CANDIDATE_SET, _TEMPLATE_PLACEMENT_NO_LEDGER)
    assert variant.returncode == base.returncode
    assert variant.stdout == base.stdout, (base.stdout, variant.stdout)


def test_template_placement_is_inert_to_covers_gate_with_ledger():
    args = ["--covers", "AC1, AC2, AC3"]
    base = _run_script(COVERS_GATE, _BASE_WITH_LEDGER, args)
    variant = _run_script(COVERS_GATE, _TEMPLATE_PLACEMENT_WITH_LEDGER, args)
    assert variant.returncode == base.returncode
    assert variant.stdout == base.stdout, (base.stdout, variant.stdout)


def test_template_placement_is_inert_to_covers_gate_without_ledger():
    args = ["--covers", "AC1, AC2, AC3"]
    base = _run_script(COVERS_GATE, _BASE_NO_LEDGER, args)
    variant = _run_script(COVERS_GATE, _TEMPLATE_PLACEMENT_NO_LEDGER, args)
    assert variant.returncode == base.returncode
    assert variant.stdout == base.stdout, (base.stdout, variant.stdout)


def test_maturity_nested_inside_slices_diverges_from_the_ledger_positive_control():
    """Positive control for the four tests above: proves they are actually
    sensitive to placement rather than passing vacuously. The hazard placement
    (heading nested between `## Slices` and its first bullet) must NOT be
    inert — `candidate_set.py` regresses `candidates` to the full criteria set
    and reports `complete-eligible: yes` at exit 0, silently losing the
    ledger's two entries."""
    base = _run_script(CANDIDATE_SET, _BASE_WITH_LEDGER)
    hazard = _run_script(CANDIDATE_SET, _HAZARD_MATURITY_NESTED_IN_SLICES)
    assert hazard.returncode == 0, hazard.stderr.decode("utf-8")
    assert hazard.stdout != base.stdout, (
        "the nested-in-Slices hazard placement must diverge from the base body "
        "(silent ledger truncation) — if it doesn't, the inertness tests above "
        "are not actually sensitive to placement"
    )


# ---- the unresolved-enumeration case has its own reason-code, and it still
#      refuses the write like every other non-zero exit -----------------------
#
# Step 6a instructs writing an explicit note when step 1's enumeration could not
# reach the repositories at all, and separately gates `lore record create` on the
# reader exiting 0. `empty-section` used to double as this case's outcome too —
# byte-identical to a stamp the author simply forgot to fill — which is why the
# reader now emits a ninth, distinct reason-code for it. It still refuses: the
# bright line ("a non-zero exit always refuses the write") holds with no
# exception. These bind to the real reader, not to the skill's wording.

_UNRESOLVED_ENUMERATION_BODY = """# X

## Maturity
<!-- unresolved-enumeration: step 1 could not enumerate the repositories this work touches -->

## Acceptance Criteria

- **AC1.** thing
"""


# ---- the near-miss heading (trailing/doubled whitespace) is named in the
#      section-absent remedy, not just "add it" -----------------------------


def test_section_absent_fixture_heading_with_trailing_space_reproduces_the_near_miss():
    """Fixture proof the near-miss case is real: a heading with a trailing space
    exits `section-absent`, not some other code — grounding the remedy-text
    assertion above in actual reader behaviour."""
    body = "# X\n\n## Maturity \n\n- lookout: prototype\n\n## Acceptance Criteria\n"
    result = _run_script(STAMP, body)
    assert result.returncode == 2
    assert "reason-code: section-absent" in result.stderr.decode("utf-8")


# ---- the vanilla single-repo path has a defined stamp key -------------------


# ---- the keep-marked reminder survives the transformation the skill actually
#      prescribes: strip the pre-fill comment, keep the keep-marked one --------


def _apply_skill_prescribed_fill_transformation(section_text: str) -> str:
    """Strip the longer pre-fill comment, keep the keep-marked reminder
    comment — the exact transformation step 6a's own prose prescribes
    ("Keep the template's keep-marked reminder comment in the filled
    section; strip only the longer pre-fill comment above it.")."""
    comments = re.findall(r"<!--.*?-->", section_text, re.DOTALL)
    assert len(comments) == 2, (
        "fixture assumption: the template's Maturity section carries exactly "
        f"two HTML comments (pre-fill + keep-marked): {comments!r}"
    )
    prefill, keep_marked = comments
    assert "keep this line" in keep_marked, (
        "fixture assumption: the second comment is the keep-marked reminder: "
        f"{keep_marked!r}"
    )
    return section_text.replace(prefill, "")


def test_keep_marked_reminder_survives_pre_fill_strip_with_vocabulary_intact():
    """Task 2's contract: the grammar reminder must survive into a rendered
    spec body — a body with the pre-fill comment stripped still tells a later
    hand-editor what it may contain. This observes an actual rendered body,
    not just that a marker string and the word 'keep' each exist somewhere."""
    template_text = SPEC_TEMPLATE.read_text(encoding="utf-8")
    start = template_text.index("## Maturity")
    end = template_text.index("## Acceptance Criteria")
    section = template_text[start:end]

    rendered = _apply_skill_prescribed_fill_transformation(section)

    assert "prototype" in rendered
    assert "early" in rendered
    assert "production" in rendered
    # The pre-fill comment's own distinguishing prose is gone — proof the
    # vocabulary above survived via the keep-marked comment, not a fluke of
    # the pre-fill comment being left in place.
    assert "never invented here" not in rendered


# ---- the malformed-entry remedy is actionable even when the offending text
#      is the member name itself, un-representable in the safe grammar -----


def _malformed_entry_bullet() -> str:
    text = BRAINSTORM_SKILL.read_text()
    bullet = re.search(r"- `malformed-entry`[^\n]*(?:\n  [^\n]*)*", text)
    assert bullet, "the `malformed-entry` remedy bullet must exist"
    return bullet.group(0)


# ---- Waiver discoverability — step 6a names the `Waives:` marker where an
#      author actually writes Non-Goals (task/waiver-is-discoverable-where-
#      non-goals-are-authored) ---------------------------------------------


def _waiver_guidance_section() -> str:
    text = BRAINSTORM_SKILL.read_text()
    start = text.index("**Waive a maturity-sensitive concern")
    end = text.index("**Certify the drafted body")
    return text[start:end]


def _guidance_example_bullet() -> str:
    # Deliberately not anchored on the literal `Waives:` marker itself — the
    # marker's presence is exactly what the round-trip below must prove by
    # running the extracted bullet through the real renderer, not by a regex
    # here standing in for that check.
    section = _waiver_guidance_section()
    match = re.search(r"```\n(- [^\n]+)\n```", section)
    assert match, f"waiver guidance must show a fenced example bullet: {section!r}"
    return match.group(1)


def test_guidance_example_bullet_round_trips_through_the_real_renderer():
    """The exact bullet the guidance shows an author, extracted from the
    guidance text and run through the real renderer, must actually waive
    its concern and produce a stand-down — proving documentation and
    mechanism cannot drift apart."""
    bullet = _guidance_example_bullet()
    matched_concerns = [concern for concern in maturity_bars._CONCERNS if concern in bullet]
    assert len(matched_concerns) == 1, (
        f"guidance example bullet must name exactly one canonical concern: {bullet!r}"
    )
    concern = matched_concerns[0]

    spec_text = f"""\
# Some Spec

## Maturity

- lookout: production

## Non-Goals

{bullet}
"""
    result = subprocess.run(
        [sys.executable, str(BARS)],
        input=spec_text.encode("utf-8"),
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8")
    out = result.stdout.decode("utf-8")
    assert f"stand-down: {concern} — waived by Non-Goal:" in out, (
        f"guidance example bullet did not waive {concern!r} through the real "
        f"renderer: {out!r}"
    )
    assert f"- {concern}: Critical" not in out, (
        f"waived concern {concern!r} must not still be rated: {out!r}"
    )
