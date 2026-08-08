"""The execute mode contract — attended/unattended over one shared procedure.

Execute's ritual is extracted into `skills/_shared/execute.md` (refine.md's shape):
a single source of truth carrying a mode table so an attended caller
(`/craft:execute`, a human in the loop) and a future unattended caller (a ranger
drain loop, no human channel) read the same document rather than two that can drift.
`execute/SKILL.md` becomes a thin attended wrapper — it does not re-inline the
procedure it wraps.

Pinned here, using the wrap-aware `_pin` helper mirrored from ranger's
`tests/test_sweep_contract.py` (`_pin` helper at line 74 there):

  - The shared procedure ships at `skills/_shared/execute.md`.
  - The mode table is present with "Mode follows the caller." semantics — refine.md's
    exact framing, reused so the two shared procedures read as one family.
  - `execute/SKILL.md` references the shared procedure and re-inlines none of its
    steps (the thinness guard `test_refine_contract.py` already applies to
    `refine/SKILL.md`).
  - Unattended mode names the outcome-file write and the `## Refine — unresolved`
    park heading verbatim — the answered-predicate's literal heading per the ranger
    drain spec, so a later answered-blocked sweep can find a parked run by grep.
  - Every `--vault`-capable `lore` invocation in the shared procedure carries
    `--vault` — a dispatched agent's cwd is not the operator's, so an unqualified
    write lands in the wrong vault and an unqualified read answers from a
    different vault's same-named record. The commands that offer no `--vault`
    flag (`lore task graph`, `lore record create`, `lore session candidate`) are
    carved out in prose rather than mandated into a rejected flag.
  - `status-ownership.md` carries both pre-authorized carve-outs: the loop session
    as sole task-status writer (the dispatched executor never writes status), and
    the PR decision pre-authorized only into the portage tail.

Every pinned span is asserted as a contiguous substring **within one physical
line** — per [[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]], a pin
that straddles a markdown wrap fails while the prose is perfectly correct, so the
helper below reports that case explicitly instead of "phrase missing".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_EXECUTE = CRAFT / "skills" / "_shared" / "execute.md"
EXECUTE_SKILL = CRAFT / "skills" / "execute" / "SKILL.md"
STATUS_OWNERSHIP = CRAFT / "skills" / "_shared" / "status-ownership.md"

ESCALATION_HEADING = "## Refine — unresolved"


def _pin(path: Path, phrase: str, why: str) -> None:
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


# --- both files ship -----------------------------------------------------------


def test_shared_execute_procedure_ships():
    assert SHARED_EXECUTE.exists(), f"Expected the single-source execute procedure at {SHARED_EXECUTE}"


def test_execute_wrapper_skill_ships():
    assert EXECUTE_SKILL.exists(), f"Expected the /craft:execute wrapper at {EXECUTE_SKILL}"


# --- the mode table --------------------------------------------------------------


def test_shared_execute_pins_mode_follows_the_caller():
    _pin(
        SHARED_EXECUTE,
        "Mode follows the caller.",
        "refine.md's exact framing for its own mode table — reused verbatim so both "
        "shared procedures read as one family rather than two independent designs.",
    )


def test_shared_execute_names_attended_mode():
    _pin(
        SHARED_EXECUTE,
        "**attended** — today's behavior",
        "The mode table's attended row must name today's behavior explicitly, so a "
        "reader can tell what changed and what did not.",
    )


def test_shared_execute_names_unattended_mode():
    _pin(
        SHARED_EXECUTE,
        "**unattended** — every escalation point re-routes per the table below",
        "The mode table's unattended row is the whole point of the extraction — "
        "without it a reader cannot tell an unattended dispatch is even legal.",
    )


def test_shared_execute_names_escalate_via_park():
    _pin(
        SHARED_EXECUTE,
        "escalate-via-park",
        "Every attended ask-the-user point needs a named unattended re-route; "
        "escalate-via-park is one of the two the mode table offers.",
    )


def test_shared_execute_names_proceed_per_contract():
    _pin(
        SHARED_EXECUTE,
        "proceed-per-contract",
        "The other of the two unattended re-routes — continuing on a decision this "
        "document pre-authorizes, rather than asking a human that is not there.",
    )


# --- unattended mode: the outcome-file write and the park heading ----------------


def test_shared_execute_names_the_outcome_file_write():
    _pin(
        SHARED_EXECUTE,
        "One-token outcome return",
        "An unattended dispatch's whole result channel is the outcome file, never "
        "its reply — the containment boundary a subagent's reply cannot hold.",
    )


def test_shared_execute_names_the_outcome_token_vocabulary():
    _pin(
        SHARED_EXECUTE,
        "`DONE` / `BLOCKED` / `NEEDS_CONTEXT`",
        "The outcome file's whole vocabulary must be named as one closed set, or a "
        "future edit can add a token nothing downstream parses.",
    )


def test_shared_execute_names_the_park_heading_verbatim():
    _pin(
        SHARED_EXECUTE,
        ESCALATION_HEADING,
        "The park section's heading must match the answered-predicate's literal "
        "heading per the ranger drain spec — a later answered-blocked sweep finds a "
        "parked run by grepping for exactly this string.",
    )


def test_shared_execute_writes_the_park_section_as_a_diff_append():
    _pin(
        SHARED_EXECUTE,
        "piping a unified diff that **appends** the section",
        "The park section is appended, never substituted — bare stdin on "
        "`lore record update` is a full-body replace that would destroy the record.",
    )


# --- --vault is mandatory on every lore record update ----------------------------


#: The `lore` subcommands that accept `--vault`. Every literal invocation of one
#: in the shared procedure must name it. `lore task graph`, `lore record create`,
#: and `lore session candidate` are deliberately absent — they offer no `--vault`
#: flag, so mandating one would put a rejected flag in an unattended run's hands
#: (the procedure says so in prose instead).
_VAULT_CAPABLE_LORE_CALLS = ("lore record update", "lore record show")


@pytest.mark.parametrize("command", _VAULT_CAPABLE_LORE_CALLS)
def test_every_vault_capable_lore_call_carries_vault(command):
    """Every literal invocation of a `--vault`-capable `lore` command names it.

    A dispatched agent's cwd is not the operator's, so an unqualified call
    locates a record by a cwd-blind first-match scan across configured vaults —
    an unattended write lands in the wrong vault, and an unattended read answers
    from a different vault's same-named record.
    """
    text = SHARED_EXECUTE.read_text()
    calls = re.findall(rf"{command} (?:task/)?<[^>]+>[^`\n]*", text)
    assert calls, f"expected at least one literal `{command} <...>` call in _shared/execute.md"
    missing = [c for c in calls if "--vault" not in c]
    assert not missing, (
        f"_shared/execute.md has `{command}` calls with no `--vault` flag — "
        f"every literal invocation must carry `--vault <elected-vault>`: {missing}"
    )


def test_shared_execute_says_what_to_do_where_vault_is_not_offered():
    _pin(
        SHARED_EXECUTE,
        "take no `--vault` flag — do not invent",
        "`lore task graph`, `lore record create`, and `lore session candidate` accept no "
        "`--vault`; a mandate that did not carve them out would put a rejected flag in an "
        "unattended run's hands, and the run stops on it.",
    )


def test_shared_execute_states_vault_is_mandatory():
    _pin(
        SHARED_EXECUTE,
        "**`--vault` is mandatory on every `lore record update` and every `lore record show` in",
        "The rule must be stated explicitly, not just followed by example — an "
        "editor adding a new `lore record update` call has no cue to include it "
        "otherwise.",
    )


def test_shared_execute_says_how_an_attended_session_binds_the_elected_vault():
    _pin(
        SHARED_EXECUTE,
        "lore vault resolve --kind task --json",
        "An unattended run is handed `<elected-vault>` at dispatch; an attended one is "
        "handed nothing, so without a named mechanism the flag the procedure calls "
        "mandatory has no value to take — and the reader guesses or drops it.",
    )


# --- the wrapper stays thin -------------------------------------------------------


def test_wrapper_reads_the_shared_procedure():
    _pin(
        EXECUTE_SKILL,
        "../_shared/execute.md",
        "execute/SKILL.md must point at ../_shared/execute.md for the procedure — "
        "the same read-on-reference contract refine/SKILL.md has with "
        "../_shared/refine.md.",
    )


def test_wrapper_does_not_reinline_the_park_section():
    """The thinness guard: one copy of the escalation contract, in _shared."""
    text = EXECUTE_SKILL.read_text()
    assert ESCALATION_HEADING not in text, (
        f"execute/SKILL.md re-inlines the park section ({ESCALATION_HEADING!r}), "
        "which belongs only in _shared/execute.md — duplication is how the wrapper "
        "and an unattended dispatch drift apart"
    )


def test_wrapper_does_not_reinline_the_loop():
    """A `## The Loop` heading in the wrapper is a second copy of the procedure."""
    text = EXECUTE_SKILL.read_text()
    assert "## The Loop" not in text, (
        "execute/SKILL.md re-inlines the per-slice Loop heading, which belongs only "
        "in _shared/execute.md"
    )


def test_wrapper_states_it_always_runs_attended():
    _pin(
        EXECUTE_SKILL,
        "always runs **attended**",
        "The wrapper is the human-invoked entry point; it must say so rather than "
        "leaving mode selection implicit or asking the reader to infer it from the "
        "shared procedure's table.",
    )


# --- status-ownership.md: the two pre-authorized carve-outs -----------------------


def test_status_ownership_pins_the_loop_session_sole_writer_carve_out():
    _pin(
        STATUS_OWNERSHIP,
        "the loop session is the sole task-status writer",
        "The first pre-authorized carve-out: under a loop, the loop session is the "
        "sole task-status writer.",
    )


def test_status_ownership_pins_the_dispatched_executor_never_writes_status():
    _pin(
        STATUS_OWNERSHIP,
        "the dispatched `executor` **never writes status**",
        "Naming the loop session as sole writer is only half the carve-out; the "
        "other half is the negative — a dispatched executor never writes its own "
        "status, under a loop exactly as much as it never does attended.",
    )


def test_status_ownership_pins_the_portage_tail_only_pr_preauthorization():
    _pin(
        STATUS_OWNERSHIP,
        "pre-authorized only into the portage tail",
        "The second pre-authorized carve-out: the PR decision is pre-authorized "
        "only into the portage tail (updater/monitor).",
    )


def test_status_ownership_pins_never_merges_never_decides_elsewhere():
    _pin(
        STATUS_OWNERSHIP,
        "never merges and never decides the PR outside that pipeline",
        "The carve-out's whole safety property is the negative: an unattended run "
        "never merges and never decides the PR anywhere but the portage tail.",
    )
