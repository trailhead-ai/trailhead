"""Consuming a routed task must close the loop on the source record.

Refine writes the `route=plan|brainstorm` sidecar label (see
test_refine_contract.py); outpost renders it into paste-ready
`/craft:plan` / `/craft:brainstorm` commands. The only actors that know the
routing was *acted on* are the consumer skills those commands invoke — so plan
and brainstorm must supersede the consumed source and clear its label, or the
routed chip / next-step affordance (and a dead `open` task) persist forever.

Each pinned phrase lives on ONE physical line in the skill prose — line wraps
break these substring pins (see
lesson/phrase-pinned-prose-contracts-break-on-line-wraps).
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_SKILL = REPO_ROOT / "craft" / "plugins" / "craft" / "skills" / "plan" / "SKILL.md"
BRAINSTORM_SKILL = (
    REPO_ROOT / "craft" / "plugins" / "craft" / "skills" / "brainstorm" / "SKILL.md"
)


def test_plan_supersedes_a_consumed_routed_source():
    text = PLAN_SKILL.read_text()
    assert (
        "`lore record update task/<source-name> --status superseded --related task=<parent-name> --unset-label route`"
        in text
    ), (
        "plan/SKILL.md must instruct superseding a consumed routed source task, "
        "linking it to the new parent, and clearing the route label in one write"
    )
    assert "If this plan consumed a routed task" in text


def test_brainstorm_supersedes_a_consumed_routed_source():
    text = BRAINSTORM_SKILL.read_text()
    assert (
        "`lore record update task/<source-name> --status superseded --related spec=<spec-name> --unset-label route`"
        in text
    ), (
        "brainstorm/SKILL.md must instruct superseding a consumed routed source "
        "task, linking it to the new spec, and clearing the route label in one write"
    )
    assert "If this brainstorm consumed a routed task" in text


def test_consumption_clear_is_a_single_write():
    """Both skills bind supersede + related + unset-label into one invocation —
    a follow-up write could land on a record whose state has since moved."""
    for skill in (PLAN_SKILL, BRAINSTORM_SKILL):
        text = skill.read_text()
        assert "--unset-label route` — one write" in text, (
            f"{skill.name} must bind the route-label clear to the same "
            "supersede write, not a follow-up invocation"
        )
