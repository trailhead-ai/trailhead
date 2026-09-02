"""`/craft:drive`'s escalation contract.

This task ships the driver's escalation contract: no retries, an escalation record shaped as a
`blocked` child of the slice parent naming a typed trigger from a declared vocabulary and the
decision needed, a credential-scrubbed body, a draft-PR push routed through the shared execute
procedure's pre-push secret scan, and an in-session terminal report carrying a fully formed
resume command. It does not build the plan, build, or PR-tail phases themselves (each is a later
task against this same file); it defines the contract every escalation site — present and
future — must follow.

Pinned here, using the wrap-aware `_pin` helper mirrored from `test_drive_resume_contract.py`
(itself mirrored from `test_drive_skill_contract.py`'s own helper, mirrored in turn from
`test_execute_mode_contract.py`, mirrored from ranger's `tests/test_sweep_contract.py`): every
pinned span is asserted as a contiguous substring **within one physical line**, so a markdown
rewrap that shifts a line break fails loudly as a wrap issue rather than reading as "phrase
missing".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
DRIVE_SKILL = CRAFT / "skills" / "drive" / "SKILL.md"


def _text() -> str:
    return DRIVE_SKILL.read_text()


def _pin(phrase: str, why: str, path: Path = DRIVE_SKILL) -> None:
    """Assert *phrase* appears inside a single physical line of *path*."""
    text = path.read_text()
    if any(phrase in line for line in text.splitlines()):
        return
    if phrase in " ".join(text.split()):
        pytest.fail(
            f"{path.name}: the pinned span {phrase!r} is present but straddles a line "
            f"wrap — keep it on one physical line. {why}"
        )
    pytest.fail(f"{path.name}: missing the pinned span {phrase!r}. {why}")


# --- contract item 1: the escalation record is a child of the slice parent, never standalone ---


def test_escalation_record_create_names_parent_flag():
    _pin(
        "--parent <slice-parent-name>",
        "The escalation write must name `--parent` pointing at the slice parent "
        "on the same `lore record create` invocation.",
    )


def test_escalation_record_is_never_standalone():
    _pin(
        "This parent edge is the entire mechanism keeping the escalation out of "
        "the automation it exists to interrupt.",
        "The ritual must state why the parent edge is load-bearing, not just "
        "include the flag incidentally.",
    )


def test_escalation_record_write_names_the_status():
    _pin(
        "Write a `task` record at `blocked`, as a **child of the slice parent, "
        "never standalone**",
        "The escalation record's shape — `blocked`, child, never standalone — "
        "must be stated as the write's own heading claim.",
    )


def test_never_drop_the_parent_flag_to_simplify():
    _pin(
        "Never drop the `--parent` flag to \"simplify\" this write",
        "A later editor must be told explicitly not to simplify away the "
        "parent edge — that is exactly the mechanism this record depends on.",
    )


# --- contract item 2: the write names --vault explicitly ------------------------------


def test_escalation_record_create_names_vault_flag():
    _pin(
        "--vault <elected-vault>",
        "The escalation `lore record create` call must name `--vault` "
        "explicitly, matching the ritual's own mandatory-vault rule.",
    )


def test_escalation_create_command_is_literal_and_carries_both_flags():
    text = _text()
    calls = re.findall(r"lore record create [^\n`]*", text.replace("\\\n", " "))
    # The escalation create command is a multi-line shell invocation in a fenced block;
    # search the fenced block directly instead of a single-line regex.
    block_match = re.search(
        r"```sh\n(printf '%s' \"\$BODY\" \| lore record create.*?)\n```",
        text,
        re.DOTALL,
    )
    assert block_match, "expected a fenced `lore record create` escalation command"
    command = block_match.group(1)
    assert "--parent" in command, f"escalation create command missing --parent: {command}"
    assert "--vault" in command, f"escalation create command missing --vault: {command}"
    assert "--status blocked" in command, f"escalation create command missing --status blocked: {command}"
    assert not calls or True  # calls unused beyond sanity; block_match is the real assertion


# --- contract item 3: the escalation body runs through the credential-pattern scrub ----


def test_escalation_body_runs_through_the_credential_scrub():
    _pin(
        "Run `$BODY` through the credential-pattern scrub",
        "The escalation body must run through the shared credential-pattern "
        "scrub before the write, exactly like the checkpoint block.",
    )


def test_escalation_body_is_evidence_from_a_failed_build():
    _pin(
        "this body is evidence gathered from a failed build (worker output, "
        "CI text, error detail)",
        "The scrub rationale must name what kind of text the body carries — "
        "evidence a failed build produced — not merely gesture at 'the body'.",
    )


def test_escalation_scrub_references_shared_execute_phase_5():
    _pin(
        "(`_shared/execute.md`, [Phase 5](../_shared/execute.md#phase-5-flow-out))",
        "The scrub reference must point at `_shared/execute.md`'s Phase 5 "
        "definition rather than restating the regex list here.",
    )


# --- contract item 4: the draft-PR push routes through the executor's pre-push scan ----


def test_draft_pr_push_routes_through_the_shared_pre_push_scan():
    _pin(
        "This push routes through `_shared/execute.md`'s existing pre-push secret scan",
        "The draft-PR push must be stated to route through the shared execute "
        "procedure's own pre-push secret scan.",
    )


def test_draft_pr_push_names_phase_6_reference():
    _pin(
        "([Phase 6](../_shared/execute.md#phase-6-close-and-completion-report))",
        "The pre-push scan reference must point at Phase 6's definition in "
        "`_shared/execute.md`.",
    )


def test_draft_pr_push_rules_out_a_bespoke_driver_side_push():
    _pin(
        "rather than a bespoke driver-side `git push`",
        "The ritual must explicitly rule out a driver-authored `git push` "
        "that would bypass the shared scan.",
    )


def test_work_in_flight_is_pushed_as_a_draft_pr():
    _pin(
        "push whatever is on the branch as a **draft PR**",
        "The push must be explicitly a draft PR, not a ready-for-review one.",
    )


# --- contract item 5: the trigger vocabulary is declared and every path names one ------


_DECLARED_TRIGGERS = ["multi-repo-slice", "build-resume-dirty-branch", "plan-critical", "worker-stalled"]


@pytest.mark.parametrize("trigger", _DECLARED_TRIGGERS)
def test_trigger_is_declared_in_the_vocabulary(trigger):
    _pin(
        f"**`{trigger}`**",
        f"the `{trigger}` trigger must be named in the declared vocabulary list.",
    )


def test_vocabulary_is_declared_as_closed():
    _pin(
        "The trigger is typed from a **declared, closed vocabulary**",
        "The vocabulary must be stated as closed, not open-ended free text.",
    )


def test_vocabulary_additions_are_not_speculative():
    _pin(
        "Add a member to this list only when a phase genuinely needs one",
        "The ritual must warn against naming triggers speculatively ahead of "
        "the phase that would raise them.",
    )


#: One test node per existing escalation *site* in the ritual text — not one test
#: covering the vocabulary as a whole — so that removing the trigger name from a
#: single site fails only that site's node, proving each pin is independently load-
#: bearing rather than one assertion vacuously satisfied by any other site.
_ESCALATION_SITES = {
    "multi-repo-slice": "Escalate with the `multi-repo-slice`",
    "build-resume-dirty-branch": "Escalate instead, under the `build-resume-dirty-branch` trigger",
}


@pytest.mark.parametrize("trigger,phrase", list(_ESCALATION_SITES.items()))
def test_escalation_site_names_its_trigger(trigger, phrase):
    _pin(
        phrase,
        f"the escalation site for `{trigger}` must name its trigger inline, "
        "not merely rely on the vocabulary list elsewhere in the file.",
    )


# --- contract item 6: no path retries a failed phase -----------------------------------


def test_no_retries_stated_as_the_rule():
    _pin(
        "**No retries: the first escalation from any phase ends the run.**",
        "The no-retry rule must be stated as the governing rule for every "
        "escalation, not implied.",
    )


def test_escalation_stops_rather_than_continuing():
    _pin(
        "it writes the escalation record, pushes work in flight, reports "
        "in-session, and stops",
        "The escalation contract must state the driver's full response is "
        "write, push, report, stop — with no retry or continuation step.",
    )


# --- contract item 7: no affordance for resolving, merging, or reverting ---------------


def test_driver_cannot_end_its_own_escalation():
    _pin(
        "The driver cannot end its own escalation",
        "The ritual must state plainly that the driver cannot resolve its "
        "own escalation.",
    )


def test_resolving_is_named_as_the_operators_act():
    _pin(
        "resolving a `blocked` escalation record is the operator's act",
        "The ritual must name resolution as the operator's act, not "
        "something the driver can do.",
    )


def test_merging_is_named_as_portages_job():
    _pin(
        "merging is portage's job once the operator's answer produces a "
        "green PR",
        "The ritual must state merging is delegated to portage's existing "
        "automation, not owned by the driver.",
    )


def test_reverting_is_named_as_outside_the_ritual():
    _pin(
        "reverting is a git operation the operator or portage's own tooling "
        "performs, never a step this ritual names",
        "The ritual must state reverting is never a step it names itself, "
        "delegating it instead.",
    )


# --- contract item 8: the escalation emits a terminal report with a resume command -----


def test_escalation_reports_it_escalated_and_names_the_record():
    _pin(
        "that the run escalated, naming the trigger and the escalation "
        "record's task id",
        "The terminal report must name that the run escalated and the "
        "escalation record's task id.",
    )


def test_resume_command_is_a_concrete_example_not_a_bare_placeholder():
    _pin(
        "e.g. `/craft:drive spec/streaming-export`",
        "The resume command must be shown as a concrete, runnable example "
        "with a real spec name substituted in, matching the slice ritual's "
        "own early-stop precedent, rather than left as bare template text.",
    )


def test_resume_command_forbids_leaving_template_text_in_the_report():
    _pin(
        "never the literal `<spec-name>` template text",
        "The ritual must explicitly forbid emitting the literal placeholder "
        "text in the actual report — the run's own resolved spec name must "
        "be substituted in.",
    )


def test_escalation_report_mirrors_slice_close_precedent():
    _pin(
        "This mirrors what the slice close already commits to at its own "
        "early-stop report",
        "The escalation report's resume-command commitment must be stated "
        "as mirroring the slice ritual's own early-stop report precedent.",
    )
