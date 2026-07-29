"""The implementation-time read path, and the pipeline handoff into distill.

Two tiers of the knowledge model (area profiles, ADRs) only earn their upkeep if the
agents building on top of them actually read them back. This slice wires that read
path into the two prose surfaces that produce or resolve a task's payload:

  - **executor** reads the vault between understanding the intent document and
    loading repo conventions — an area profile and the ADRs it cites are prior art
    on the surface the slice is about to touch, and reading them before writing code
    is cheaper than rediscovering the same constraint mid-slice. Graceful when lore
    is absent, per the vanilla-usage axiom (`docs/vision.md` #3): a sibling plugin's
    absence must never fail a slice.
  - **refine**'s existing self-serve vault-search pass gets the same area-profile-
    and-cited-ADR targets named explicitly, not left as one generic `lore search`
    among many.

It also closes the last unhandoff'd pipeline boundary: review is the stage before
distill (brainstorm -> gauntlet -> plan -> execute -> review -> distill), and every
other boundary in the pipeline already prints a fully-formed handoff command for a
fresh session to pick up (plan -> execute, distill's own closing report). Review did
not, until now. The handoff must not itself advance the spec's status — that remains
distill's licensed edge alone (`test_gauntlet_contract.py`'s `_SPEC_ADVANCE_RE`).
"""

import re
from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
EXECUTOR = CRAFT / "agents" / "executor.md"
SHARED_REFINE = CRAFT / "skills" / "_shared" / "refine.md"
REVIEW = CRAFT / "skills" / "review" / "SKILL.md"

# The same regex test_gauntlet_contract.py polices every craft prose file with —
# restated as a literal pattern (not imported from the module) so this test fails
# independently of that file's existence, and stays honest about what it checks.
_SPEC_ADVANCE_RE = re.compile(r"<spec-id>\s+--status\s+(\w+)")


def _text(path: Path) -> str:
    return path.read_text()


# --- executor: the vault-read step ---


def test_executor_vault_read_step_ships():
    text = _text(EXECUTOR)
    assert "lore search" in text, (
        "executor.md must run a `lore search` as its vault-read step."
    )
    assert "lore record show" in text, (
        "executor.md must read the ADRs an area profile cites via `lore record show` "
        "— a profile's citations are the point of reading it."
    )


def test_executor_vault_read_step_is_between_intent_and_conventions():
    text = _text(EXECUTOR)
    intent_idx = text.index("Read the intent document")
    conventions_idx = text.index("Repo conventions")
    search_idx = text.index("lore search")
    show_idx = text.index("lore record show")
    assert intent_idx < search_idx < conventions_idx, (
        "executor.md's `lore search` step must sit between reading the intent "
        "document and loading repo conventions, per the slice's binding placement."
    )
    assert intent_idx < show_idx < conventions_idx, (
        "executor.md's `lore record show` step must sit between reading the intent "
        "document and loading repo conventions, per the slice's binding placement."
    )


def test_executor_vault_read_step_names_area_profiles_and_adrs():
    text = _text(EXECUTOR)
    assert "area" in text.lower(), (
        "executor.md's vault-read step must name area profiles as the lookup target."
    )
    assert "ADR" in text, (
        "executor.md's vault-read step must name the ADRs an area profile cites."
    )


def test_executor_vault_read_step_never_globs_the_vault():
    text = _text(EXECUTOR)
    assert "never a direct file" in text or "never" in text and "glob" in text.lower(), (
        "executor.md must state vault reads go through the `lore` CLI only — never a "
        "direct file read or glob of the vault (binding decision: vault read surfaces "
        "live in lore, consumers shell out)."
    )


def test_executor_has_vanilla_fallback_for_missing_lore():
    text = _text(EXECUTOR)
    assert "if lore is not installed in this project, skip this step" in text, (
        "executor.md must state the graceful vanilla-usage fallback verbatim enough "
        "to be unambiguous: absence of lore skips the vault-read step with a note, "
        "it does not fail the slice."
    )


# --- refine: naming area profiles and cited ADRs as first-class targets ---


def test_refine_self_serve_branch_b_names_area_profiles():
    text = _text(SHARED_REFINE)
    branch_b_start = text.index("**(b) Search the vault.**")
    branch_b_end = text.index("A derived answer is")
    branch_b = text[branch_b_start:branch_b_end]
    assert "area profile" in branch_b, (
        "_shared/refine.md's branch (b) must name area profiles explicitly as a "
        "first-class lookup target, not just generic `lore search`."
    )
    assert "lore record show area/<name>" in branch_b, (
        "_shared/refine.md's branch (b) must show the concrete `lore record show "
        "area/<name>` invocation for pulling an area profile directly — "
        "`lore search 'area:<name>'` resolves to the `related-area` facet (records "
        "tagged with the area), not the profile record itself."
    )


def test_refine_self_serve_branch_b_names_cited_adrs():
    text = _text(SHARED_REFINE)
    branch_b_start = text.index("**(b) Search the vault.**")
    branch_b_end = text.index("A derived answer is")
    branch_b = text[branch_b_start:branch_b_end]
    assert "ADR" in branch_b, (
        "_shared/refine.md's branch (b) must name the ADRs an area profile cites as "
        "a first-class lookup target."
    )
    assert "lore record show" in branch_b, (
        "_shared/refine.md's branch (b) must show the concrete `lore record show` "
        "invocation for reading a cited ADR."
    )


def test_refine_unrelated_branch_a_untouched():
    """Branch (a) — reading the touched code — is out of this slice's scope."""
    text = _text(SHARED_REFINE)
    assert "**(a) Read the touched code.**" in text, (
        "_shared/refine.md's branch (a) heading must survive unchanged; this slice "
        "only touches branch (b)."
    )


# --- review: the closing handoff into distill ---


def test_review_has_fully_formed_distill_handoff():
    text = _text(REVIEW)
    assert "/craft:distill spec/" in text, (
        "review/SKILL.md must gain a closing handoff command invoking "
        "`/craft:distill spec/<...>`."
    )


def test_review_handoff_is_fully_formed_not_a_placeholder():
    text = _text(REVIEW)
    idx = text.index("/craft:distill spec/")
    tail = text[idx + len("/craft:distill spec/") : idx + len("/craft:distill spec/") + 1]
    assert tail != "<", (
        "review/SKILL.md's handoff must be printed fully formed — a real-looking "
        "spec name, never a bracketed `<placeholder>` — so it can be pasted into a "
        "fresh session as-is, matching the handoff-command convention every other "
        "pipeline boundary already follows."
    )


def test_review_handoff_does_not_trip_the_spec_advance_guard():
    """Review must never itself advance the spec — only distill is licensed to.

    Mirrors test_gauntlet_contract.py's `_SPEC_ADVANCE_RE` so a regression here is
    caught in this slice's own suite, not only when the gauntlet suite happens to
    run.
    """
    text = _text(REVIEW)
    advances = _SPEC_ADVANCE_RE.findall(text)
    assert not advances, (
        f"review/SKILL.md's new handoff text advances a spec's status ({advances}) "
        "— the `<spec-id> --status <word>` form is licensed to distill/SKILL.md "
        "alone; review's handoff must invoke distill without advancing anything "
        "itself."
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
