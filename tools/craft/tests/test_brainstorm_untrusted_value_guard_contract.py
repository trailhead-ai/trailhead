"""`brainstorm/SKILL.md` shape-checks vault-sourced values before substitution.

Brainstorm substitutes vault-sourced identifiers into executed `lore` command
lines — most visibly the routed-task close-out in step 6a, which interpolates both
`<source-name>` and `<spec-name>`. Every other craft skill that does this
(`slice`, `plan`, `distill`) names the safe-value shape and states that a failing
value causes a loud refusal rather than a silent omission. These pins hold
brainstorm to the same contract, and to the same *once, governing every site*
wording — a per-site restatement is what lets a later site ship unguarded.
"""

import re
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
BRAINSTORM = CRAFT / "skills" / "brainstorm" / "SKILL.md"

SAFE_VALUE_SHAPE = "^[A-Za-z0-9._/-]+$"


def _text() -> str:
    return BRAINSTORM.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Whitespace-collapsed, so a rewrap that shifts a line break can't disarm a pin."""
    return re.sub(r"\s+", " ", text)


def _pin(phrase: str, why: str) -> None:
    assert _flat(phrase) in _flat(_text()), why


def test_brainstorm_names_the_safe_value_shape():
    _pin(
        f"safe-value shape `{SAFE_VALUE_SHAPE}`",
        "brainstorm/SKILL.md must name the safe-value shape literally — the same "
        "regex slice/, plan/, and distill/SKILL.md already name — so a reader "
        "applying the guard knows exactly what passes it",
    )


def test_the_guard_fires_before_any_substitution():
    _pin(
        "**before ANY substitution**",
        "brainstorm/SKILL.md must state that validation happens before ANY "
        "substitution — a check applied after a value has already reached a "
        "command line is not a guard",
    )


def test_a_failing_value_is_a_loud_refusal_not_a_silent_omission():
    # Lowercased: these phrases sit at sentence boundaries, so their
    # capitalization is a wrapping artifact, not part of the contract.
    text = _flat(_text()).lower()
    assert "never substituted, quoted, or escaped in" in text, (
        "brainstorm/SKILL.md must state that a failing value is never substituted, "
        "quoted, or escaped in — escaping it is the tempting wrong answer"
    )
    assert "refuse loudly" in text or "refuses loudly" in text, (
        "brainstorm/SKILL.md must state that a failing value causes a loud refusal"
    )
    assert "silently omitting" in text or "silent omission" in text, (
        "brainstorm/SKILL.md must name silent omission as the failure mode the "
        "refusal exists to prevent — an omitted value turns a refusal into a "
        "command that reads as an ordinary empty result"
    )


def test_the_guard_governs_every_substitution_site_rather_than_a_fixed_count():
    _pin(
        "governs every substitution site",
        "brainstorm/SKILL.md must state the guard once as governing every "
        "substitution site in the file — matching slice/ and distill/SKILL.md. "
        "Enumerating sites instead is what lets a site added later ship unguarded",
    )


def test_the_guard_is_stated_once_not_repeated_per_site():
    flat = _flat(_text())
    count = flat.count(f"safe-value shape `{SAFE_VALUE_SHAPE}`")
    assert count == 1, (
        f"brainstorm/SKILL.md must state the safe-value shape exactly once "
        f"(found {count}) — the whole point of 'governs every substitution site' "
        "is that the rule is not restated per site, where copies drift apart"
    )


def test_the_guard_precedes_the_routed_task_close_out_it_governs():
    """The close-out in step 6a is brainstorm's most exposed interpolation: it
    substitutes two vault-sourced names into one executed command. A guard stated
    *after* it reads as not covering it."""
    text = _text()
    guard = text.index(SAFE_VALUE_SHAPE)
    close_out = text.index("lore record update task/<source-name> --status superseded")
    assert guard < close_out, (
        "brainstorm/SKILL.md's safe-value guard must be stated before the routed-task "
        "close-out write it governs, not after it"
    )
