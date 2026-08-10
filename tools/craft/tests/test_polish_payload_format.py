"""polish's brief payload converges to templates/task.md's bold-label shape.

Before this change, polish's brief template spelled its payload with headings
(`### Delivers` / `### Test contract` / `### Expected files`) — a fourth
spelling of the same three fields refine and the child-task template already
carry as bold inline labels. A promoted standalone leaf and a polish-dispatched
followup brief must read identically to every downstream consumer (executor,
code-reviewer, drift-gate): one payload format, no divergence.
"""

from pathlib import Path

POLISH_SKILL = (
    Path(__file__).parent.parent / "plugins" / "craft" / "skills" / "polish" / "SKILL.md"
)

# The bold labels, copied from templates/task.md verbatim.
PAYLOAD_LABELS = ["**Delivers:**", "**Test contract:**", "**Files:**"]

# The old heading spellings that must not survive the convergence.
OLD_HEADINGS = ["### Delivers", "### Test contract", "### Expected files"]


def test_brief_template_carries_the_bold_payload_labels():
    text = POLISH_SKILL.read_text()
    for label in PAYLOAD_LABELS:
        assert label in text, (
            f"polish/SKILL.md must spell its brief payload with {label!r} — the "
            "same bold inline label templates/task.md and refine's promoted "
            "standalone leaf use, so execute sees one payload format everywhere"
        )


def test_brief_template_drops_the_old_headings():
    text = POLISH_SKILL.read_text()
    for heading in OLD_HEADINGS:
        assert heading not in text, (
            f"polish/SKILL.md must not carry the old heading {heading!r} — a "
            "surviving old spelling alongside the new bold labels leaves two "
            "payload formats in the same file"
        )


def test_brief_template_carries_no_yaml_frontmatter_fence():
    """Session-note records are stored verbatim, never frontmatter-parsed —
    the brief template must not model a frontmatter block for its payload."""
    text = POLISH_SKILL.read_text()
    assert "---\ntype: task" not in text, (
        "polish/SKILL.md must not carry a YAML frontmatter fence in its brief "
        "template — session-note records are stored verbatim, never "
        "frontmatter-parsed, so a frontmatter-shaped payload would be dead weight"
    )
