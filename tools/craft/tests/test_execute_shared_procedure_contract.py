"""The shared execute procedure contract — one document, one thin wrapper.

Execute's ritual lives in `skills/_shared/execute.md` (refine.md's shape): a single
source of truth that `execute/SKILL.md` reads rather than restates, so the wrapper
and the procedure cannot drift apart.

Pinned here, using the wrap-aware `_pin` helper:

  - The shared procedure ships at `skills/_shared/execute.md`.
  - `execute/SKILL.md` references the shared procedure and re-inlines none of its
    steps (the thinness guard `test_refine_contract.py` already applies to
    `refine/SKILL.md`).
  - Every `--vault`-capable `lore` invocation in the shared procedure carries
    `--vault` — a dispatched agent's cwd is not the operator's, so an unqualified
    write lands in the wrong vault and an unqualified read answers from a
    different vault's same-named record. `lore task graph`, `lore record create`,
    and `lore session candidate` accept `--vault NAME` too; the procedure mandates
    it in prose on the two write commands and leaves the graph render unpinned.
  - `status-ownership.md` keeps the PR decision inside the portage tail.

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


# --- --vault is mandatory on every lore record update ----------------------------


#: The `lore` subcommands that accept `--vault`. Every literal invocation of one
#: in the shared procedure must name it. `lore task graph`, `lore record create`,
#: and `lore session candidate` accept `--vault` too, but their literal call sites
#: do not fit this regex's `<name>`-first shape, so the procedure mandates them in
#: prose instead (see the two pins below).
_VAULT_CAPABLE_LORE_CALLS = ("lore record update", "lore record show")


@pytest.mark.parametrize("command", _VAULT_CAPABLE_LORE_CALLS)
def test_every_vault_capable_lore_call_carries_vault(command):
    """Every literal invocation of a `--vault`-capable `lore` command names it.

    A dispatched agent's cwd is not the operator's, so an unqualified call
    locates a record by a cwd-blind first-match scan across configured vaults —
    a write lands in the wrong vault, and a read answers from a different
    vault's same-named record.
    """
    text = SHARED_EXECUTE.read_text()
    calls = re.findall(rf"{command} (?:task/)?<[^>]+>[^`\n]*", text)
    assert calls, f"expected at least one literal `{command} <...>` call in _shared/execute.md"
    missing = [c for c in calls if "--vault" not in c]
    assert not missing, (
        f"_shared/execute.md has `{command}` calls with no `--vault` flag — "
        f"every literal invocation must carry `--vault <elected-vault>`: {missing}"
    )


def test_shared_execute_states_the_remaining_commands_accept_vault():
    _pin(
        SHARED_EXECUTE,
        "each accept `--vault NAME`",
        "`lore task graph`, `lore record create`, and `lore session candidate` all accept "
        "`--vault NAME` against the current CLI; prose claiming otherwise is why the "
        "lesson write went to the default vault.",
    )


def test_shared_execute_mandates_vault_on_the_remaining_writes():
    _pin(
        SHARED_EXECUTE,
        "Name `<elected-vault>` on every `lore record create` and `lore session candidate`",
        "An unqualified write resolves to the default vault, not the elected one — which "
        "is exactly how a lesson lands where the claim-time query cannot find it.",
    )


def test_shared_execute_states_vault_is_mandatory():
    _pin(
        SHARED_EXECUTE,
        "**`--vault` is mandatory on every `lore record update` and every `lore record show` in",
        "The rule must be stated explicitly, not just followed by example — an "
        "editor adding a new `lore record update` call has no cue to include it "
        "otherwise.",
    )


def test_shared_execute_says_how_a_run_binds_the_elected_vault():
    _pin(
        SHARED_EXECUTE,
        "lore vault resolve --kind task --json",
        "Without a named mechanism for binding it, the flag the procedure calls "
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
        "and the procedure drift apart"
    )


def test_wrapper_does_not_reinline_the_loop():
    """A `## The Loop` heading in the wrapper is a second copy of the procedure."""
    text = EXECUTE_SKILL.read_text()
    assert "## The Loop" not in text, (
        "execute/SKILL.md re-inlines the per-slice Loop heading, which belongs only "
        "in _shared/execute.md"
    )


def test_status_ownership_pins_the_portage_tail_only_pr_preauthorization():
    _pin(
        STATUS_OWNERSHIP,
        "it belongs **only** to the portage",
        "The PR decision belongs only to the portage tail (updater/monitor) — no "
        "session and no dispatched agent applies a merge decision itself.",
    )
