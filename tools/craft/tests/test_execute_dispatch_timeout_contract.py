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
        "a requirement that the full suite run in the foreground",
        "The dispatch payload must require a foreground suite run, not merely "
        "an explicit timeout — the two are the mandate the stalls fixed.",
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
