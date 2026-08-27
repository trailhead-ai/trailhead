"""The executor dispatch states an explicit tool timeout above the suite
runtime, mandates a foreground run, and mandates commit-and-report in the
same turn as the final test run.

The Bash tool auto-backgrounds any command past ~120s. A suite that runs
longer than that makes "run the suite in the foreground" impossible to obey
unless the dispatch itself carries an explicit tool timeout above the
suite's measured runtime. Eight of fifteen executor dispatches in one craft
execute run stalled on exactly this — starting the suite as a background job
and parking, waiting on a monitor, instead of reporting its own result.

Every pin here is scoped to the section it guards — extracted by heading,
per [[lesson/mutation-test-a-prose-pin-whose-target-string-occurs-elsewhere-in-the-file]]
— and asserted as a contiguous substring within one physical line, per
[[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]]. Each pin also
requires its phrase to occur EXACTLY ONCE in the section it guards, so the
assertion cannot pass on an incidental duplicate occurrence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_EXECUTE = CRAFT / "skills" / "_shared" / "execute.md"

STEP3_HEADING = "### 3. Dispatch `executor`"
STEP4_HEADING = "### 4. Review (scaled to change size)"


def _step3_section() -> str:
    text = SHARED_EXECUTE.read_text()
    start = text.index(STEP3_HEADING)
    end = text.index(STEP4_HEADING, start)
    return text[start:end]


def _pin_in(section_text: str, path_label: str, phrase: str, why: str) -> None:
    """Assert *phrase* appears inside a single physical line of *section_text*,
    and that it occurs exactly once so the pin cannot pass on the wrong
    occurrence."""
    matching_lines = [line for line in section_text.splitlines() if phrase in line]
    if len(matching_lines) == 1:
        return
    if len(matching_lines) > 1:
        pytest.fail(
            f"{path_label}: the pinned span {phrase!r} occurs {len(matching_lines)} "
            f"times in this section — reword the incidental occurrence so the pin "
            f"guards exactly one line. {why}"
        )
    if phrase in " ".join(section_text.split()):
        pytest.fail(
            f"{path_label}: the pinned span {phrase!r} is present but straddles a "
            f"line wrap — keep it on one physical line. {why}"
        )
    pytest.fail(f"{path_label}: missing the pinned span {phrase!r}. {why}")


def test_step3_pins_explicit_timeout_mandate():
    _pin_in(
        _step3_section(),
        "execute.md#step3",
        "an explicit tool timeout in milliseconds set above the suite's measured runtime",
        "The dispatch payload must state an explicit tool timeout above the "
        "suite's measured runtime — a suite that outruns the Bash tool's "
        "~120s auto-background threshold makes a bare 'run in the foreground' "
        "instruction impossible to obey.",
    )


def test_step3_pins_default_timeout_value():
    _pin_in(
        _step3_section(),
        "execute.md#step3",
        "600000ms",
        "The recorded fix names a concrete default timeout value to fall back "
        "on when the suite's runtime is unmeasured.",
    )


def test_step3_pins_foreground_mandate():
    _pin_in(
        _step3_section(),
        "execute.md#step3",
        "a requirement that the suite run in the foreground",
        "The dispatch payload must require a foreground suite run, not merely "
        "an explicit timeout — the two are the mandate the stalls fixed. This "
        "does not say 'full suite': executor.md's Step 7 scopes the executor "
        "to the focused suite for its slice, and the full gate is the "
        "controller's own Phase 1 job — the dispatch mandate governs "
        "foreground/timeout/commit discipline for whichever suite the "
        "executor runs, not which suite that is.",
    )


def test_step3_foreground_mandate_does_not_say_full_suite():
    section = _step3_section()
    assert "full suite" not in section, (
        "execute.md#step3: the dispatch mandate must not say 'full suite' — "
        "that contradicts executor.md Step 7's focused-suite scoping for the "
        "same agent. The foreground/timeout/commit discipline applies "
        "regardless of which suite (focused, per executor.md) is run."
    )


def test_step3_mandate_does_not_direct_the_executor_to_run_the_full_suite():
    """Negative pin, imperative form.

    The bare `"full suite" not in section` check above misses the hyphenated
    spelling, and the section legitimately uses that spelling for the
    CONTROLLER's own Phase 1 gate ("the controller's own full-suite gate").
    So this bans the imperative form in either spelling rather than the noun
    phrase, which is what would actually contradict executor.md Step 7.
    """
    section = _step3_section()
    for phrase in ("run the full suite", "run the full-suite", "the full suite here",
                   "the full-suite gate here"):
        assert phrase not in section, (
            f"execute.md#step3: the dispatch mandate must not direct the "
            f"executor to run the full suite ({phrase!r}) — executor.md Step 7 "
            f"scopes that agent to the focused suite, and the full gate is the "
            f"controller's Phase 1 job."
        )


def test_step3_pins_commit_and_report_same_turn():
    _pin_in(
        _step3_section(),
        "execute.md#step3",
        "commit-and-report in the same turn as the final test run",
        "The dispatch payload must require commit-and-report in the same turn "
        "as the final test run — the second half of the recorded fix.",
    )


def test_step3_pins_background_anti_pattern_named():
    _pin_in(
        _step3_section(),
        "execute.md#step3",
        "never by starting the suite as a background job and ending the turn waiting on it",
        "Naming the specific anti-pattern the mandate forbids, not only the "
        "mandate itself, is what made the fix land in transcript review.",
    )


def test_step3_pins_auto_background_mechanism():
    _pin_in(
        _step3_section(),
        "execute.md#step3",
        "auto-backgrounds any command that runs past ~120s",
        "The mandate must name the actual mechanism (the Bash tool's "
        "~120s auto-background threshold), not just assert the property "
        "'run in the foreground'.",
    )


def test_step3_pins_obey_it_by_naming_a_timeout():
    _pin_in(
        _step3_section(),
        "execute.md#step3",
        "Obey it by naming a concrete timeout value above the suite's measured runtime",
        "The 'never by starting the suite as a background job' clause must "
        "modify a stated verb ('obey it by …, never by …') rather than "
        "dangling with nothing to attach to.",
    )


def test_step3_pins_ceiling_fallback():
    _pin_in(
        _step3_section(),
        "execute.md#step3",
        "the Bash tool's 600000ms ceiling",
        "A suite whose measured runtime exceeds the Bash tool's own hard "
        "ceiling has no timeout value that can satisfy the mandate — the "
        "dispatch must name a degraded path (split the run, or accept a "
        "scoped suite) rather than leaving an unsatisfiable instruction.",
    )


def test_step3_pins_ceiling_fallback_options():
    _pin_in(
        _step3_section(),
        "execute.md#step3",
        "split the run into a scoped subset of the suite that fits under the ceiling, or dispatch against a scoped suite",  # noqa: E501
        "The ceiling fallback must name concrete degraded paths, not just "
        "assert that one exists.",
    )
