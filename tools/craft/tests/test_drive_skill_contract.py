"""`/craft:drive`'s entry point and selection phase.

`/craft:drive` is the loop's single entry point against a `ready` spec. This task ships only
its entry point and selection phase: argument validation, the `--vault` binding, the `ready`
status guard, deferral to `../slice/SKILL.md` (read inline, never invoked as a slash command),
reporting `/craft:slice`'s three outcomes, and refusing a slice that spans more than one repo.
The later phases are pinned by their own contract files.

Pinned here, using the wrap-aware `_pin` helper mirrored from
`test_execute_mode_contract.py`'s own helper (mirrored in turn from ranger's
`tests/test_sweep_contract.py`): every pinned span is asserted as a contiguous substring
**within one physical line**, so a markdown rewrap that shifts a line break fails loudly as a
wrap issue rather than reading as "phrase missing".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
DRIVE_SKILL = CRAFT / "skills" / "drive" / "SKILL.md"
SLICE_SKILL = CRAFT / "skills" / "slice" / "SKILL.md"


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


def test_drive_skill_ships():
    assert DRIVE_SKILL.exists(), f"Expected the /craft:drive skill at {DRIVE_SKILL}"


# --- contract item 1: the shape check on the spec argument ---------------------


def test_shape_checks_the_spec_argument_before_any_substitution():
    _pin(
        "the safe-value shape `^[A-Za-z0-9._/-]+$`",
        "drive/SKILL.md must validate the spec argument against the same "
        "untrusted-vault-value shape check other craft ritual text uses, before "
        "any substitution.",
    )


def test_shape_check_refuses_loudly_rather_than_omitting_the_value():
    _pin(
        "this skill refuses loudly and stops, rather than silently",
        "A value failing the shape check must produce a loud refusal, not a "
        "silent omission that would read as a zero-hit 'nothing found'.",
    )


def test_shape_check_runs_before_any_substitution():
    _pin(
        "Validate it once, **before any substitution**",
        "The shape check must run before the spec name is substituted into any "
        "command, not after the first use.",
    )


# --- contract item 2: the non-ready guard refuses with a named remedy -----------


@pytest.mark.parametrize(
    "status,remedy_fragment",
    [
        ("draft", "`/craft:gauntlet spec/<spec-name>`"),
        ("planned", "`/craft:execute`"),
        ("complete", "starts as a new spec"),
        ("superseded", "same as"),
        ("dropped", "same as"),
    ],
)
def test_non_ready_guard_names_a_remedy_per_status(status, remedy_fragment):
    text = _text()
    assert f"**`{status}`**" in text, (
        f"drive/SKILL.md must name the `{status}` status explicitly in its "
        "non-ready guard"
    )
    assert remedy_fragment in text, (
        f"drive/SKILL.md must name a remedy for the `{status}` status "
        f"(expected to find {remedy_fragment!r})"
    )


def test_non_ready_guard_writes_nothing():
    _pin(
        "none of them is written",
        "The non-ready refusal path must write nothing — a refusal is read-only.",
    )


# --- contract item 3: every `lore` call in the ritual names --vault -------------


#: `lore search` carries no `--vault` flag at all — it queries every configured vault
#: (`_shared/execute.md`'s own documented rule) — and `lore vault resolve` is the call
#: that determines the elected vault in the first place, so neither belongs here.
#: `lore record update` and `lore record create` are the other two vault-capable call
#: shapes the ritual actually makes (checkpoint/council-review appends and the
#: escalation-record write); a pin covering only `lore record show` never notices
#: either dropping `--vault`.
_VAULT_CAPABLE_LORE_CALLS = ("lore record show", "lore record update", "lore record create")


@pytest.mark.parametrize("command", _VAULT_CAPABLE_LORE_CALLS)
def test_every_vault_capable_lore_call_carries_vault(command):
    # `lore record create`'s call is a multi-line shell invocation using `\`-continued
    # lines (see the escalation-record fenced block) — join those before extracting
    # calls, the same normalization `test_drive_escalation_contract.py` applies to the
    # same command.
    text = _text().replace("\\\n", " ")
    calls = re.findall(rf"{command} [^`\n]*", text)
    assert calls, f"expected at least one literal `{command} ...` call in drive/SKILL.md"
    missing = [c for c in calls if "--vault" not in c]
    assert not missing, (
        f"drive/SKILL.md has `{command}` calls with no `--vault` flag — every "
        f"literal invocation must carry `--vault <elected-vault>` (or bind it "
        f"inline via `lore vault resolve`): {missing}"
    )


def test_states_vault_is_mandatory_on_every_lore_call():
    _pin(
        "**`--vault` is mandatory on every `lore` call this ritual makes.**",
        "The rule must be stated explicitly so an editor adding a new `lore` "
        "call has a cue to include it.",
    )


def test_binds_the_elected_vault_via_lore_vault_resolve():
    _pin(
        "lore vault resolve --kind spec --json",
        "The driver is an attended entry point handed nothing at dispatch, so "
        "it must bind `<elected-vault>` itself before any other `lore` call.",
    )


# --- contract item 4: defers to slice/SKILL.md by reading it, not invoking it ---


def test_slice_skill_ships_as_the_deferred_seam():
    assert SLICE_SKILL.exists(), (
        f"drive/SKILL.md defers to {SLICE_SKILL}, which must exist for the seam "
        "to be real"
    )


def test_names_and_reads_slice_skill_inline():
    _pin(
        "reads `../slice/SKILL.md`'s full",
        "drive/SKILL.md must name the sibling file by its relative path and "
        "state that it reads it, the seam between the two documents — not "
        "merely assert each document exists independently.",
    )


def test_states_it_never_invokes_slice_as_a_slash_command():
    _pin(
        "rather than invoking `/craft:slice` as a slash command",
        "The deferral must explicitly rule out the unreliable skill-to-skill "
        "chain, matching plan's own council-dispatch rule.",
    )


def test_states_it_never_restates_the_slice_procedure():
    _pin(
        "This skill never restates that procedure",
        "A second copy of the slice procedure here is exactly how the two "
        "would drift apart — the file must say so.",
    )


# --- contract item 5: the three outcomes are distinct, two halt --------------


def test_chosen_slice_outcome_does_not_halt_the_driver():
    _pin(
        "**This outcome does not",
        "A chosen slice parent must be reported as the one outcome that keeps "
        "the driver going once later phases exist.",
    )


def test_spec_complete_outcome_halts_the_driver():
    _pin(
        "Report the spec complete, matching `../slice/SKILL.md`'s own completion "
        "report, and **halt the",
        "The spec-complete outcome must explicitly halt the driver.",
    )


def test_early_stop_outcome_halts_the_driver():
    _pin(
        "Report what remains and why the loop stopped, matching "
        "`../slice/SKILL.md`'s own early-stop",
        "The early-stop outcome must explicitly halt the driver.",
    )


def test_the_three_outcomes_are_named_as_a_closed_set():
    text = _text()
    for label in ("A chosen slice parent.", "Spec complete.", "Early stop."):
        assert label in text, (
            f"drive/SKILL.md must name {label!r} as one of the three "
            "termination outcomes it reports verbatim from /craft:slice"
        )


def test_driver_owns_no_termination_logic_of_its_own():
    _pin(
        "The driver owns no termination logic of its own",
        "The driver must state explicitly that it reports what /craft:slice's "
        "procedure produced rather than deciding termination itself.",
    )


# --- contract item 6: multi-repo escalation ------------------------------------


def test_detection_signal_is_the_camp_group_member_count():
    _pin(
        "**camp group's member count**",
        "The multi-repo detection signal must be the camp group's member "
        "count, not repo attribution on task records.",
    )


def test_reads_manifest_json_at_the_workspace_root():
    _pin(
        "Read `manifest.json` at that derived root",
        "The manifest read must be named explicitly as the mechanism, "
        "matching the precedent `_shared/execute.md` already establishes, "
        "against the now-explicitly-derived camp workspace root.",
    )


def test_single_member_proceeds():
    _pin(
        "**One member:** this is the single-repo path this slice ships.",
        "A single-member group must proceed rather than escalate.",
    )


# --- contract item 6a: the camp workspace root is derived explicitly, and an absent -----
# --- manifest.json is a named stop, not an undefined state -----------------------------


def test_camp_workspace_root_is_derived_explicitly():
    _pin(
        "**Derive the camp workspace root as the parent directory of the repo "
        "checkout the driver session is running in**",
        "The ritual must state explicitly how the camp workspace root is "
        "derived, not merely assume `manifest.json` is reachable from an "
        "unstated location.",
    )


def test_absent_manifest_is_a_named_stop_not_vanilla_fallback():
    _pin(
        "this is not a vanilla-usage fallback the way `_shared/execute.md`'s "
        "own push mechanics support",
        "An absent or unreadable manifest.json must be named as a real stop, "
        "not silently treated as vanilla single-repo usage — the PR tail's "
        "own camp-group dependencies mean the driver cannot complete a run "
        "with no camp workspace at all.",
    )


def test_absent_manifest_escalates_under_named_trigger():
    _pin(
        "Escalate under the `no-camp-workspace` trigger",
        "An absent or unreadable manifest.json must escalate under a named "
        "trigger from the closed vocabulary, not fall through as an "
        "unhandled state.",
    )


def test_multi_member_escalates_with_the_named_trigger():
    _pin(
        "Escalate with the `multi-repo-slice`",
        "A multi-member group must escalate under the exact "
        "`multi-repo-slice` trigger name the task record specifies.",
    )


def test_multi_repo_refusal_names_the_wrong_branch_risk():
    _pin(
        "a wrong guess would build the slice on the wrong branch",
        "The refusal must state why guessing is unacceptable, not just that "
        "it refuses.",
    )


def test_multi_repo_path_defers_full_escalation_mechanics_to_the_contract():
    _pin(
        "is defined once in that contract, not restated here",
        "This phase only detects the multi-repo condition and names the "
        "trigger — the full escalation record write belongs to the "
        "escalation contract, and the file must say so rather than "
        "restating that mechanism here.",
    )


# --- contract item 6b: both step 5 escalations name the remedy that clears the -----------
# --- stranded slice parent, so a later /craft:drive re-entry does not just re-blocked -----


def test_no_camp_workspace_names_its_remedy():
    _pin(
        "Remedy: once `manifest.json` is readable at the derived root",
        "The `no-camp-workspace` escalation must name what closes it — the "
        "parent is not permanently stranded once a camp workspace exists.",
    )


def test_multi_repo_slice_names_its_remedy():
    _pin(
        "Remedy: this parent cannot be driven to completion by this ritual — "
        "the operator drops it explicitly",
        "The `multi-repo-slice` escalation must name what closes it, even "
        "though the multi-repo question has no answer inside this ritual's "
        "scope — otherwise every later `/craft:drive` re-entry finds this "
        "same parent and writes another `blocked` child against it.",
    )
