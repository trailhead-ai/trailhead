"""Scoped-per-slice-test-runs contract.

The executor dispatch template in `execute.md`'s step 3 states scoped
per-slice test runs as the **default**, not merely as an escape hatch for
when a suite's measured runtime exceeds the Bash tool's 600000ms ceiling:
the dispatch names the blast radius (the changed file's tests, its
module's tests, and any suite exercising a caller of what was touched) as
the command an executor runs after each slice. The dispatch also states
the per-slice-scoped vs Phase-1-full distinction explicitly, so the
controller's own Phase 1 full-suite gate cannot be read as licence for
per-slice full runs — that gate is correct and stays; the failure mode
this guards against is an executor reading it as house style for every
slice. `test-runner.md` gains a scoped worked example among its "Good
fits" so its own description (currently full-suite shaped throughout)
stops pulling an executor toward full-suite runs.

Every pin here is scoped to the section it guards — extracted by heading
(or an exact-text boundary where a section has no heading of its own), per
[[lesson/mutation-test-a-prose-pin-whose-target-string-occurs-elsewhere-in-the-file]]
— and asserted as a contiguous substring within one physical line, per
[[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]].
"""

from __future__ import annotations

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_EXECUTE = CRAFT / "skills" / "_shared" / "execute.md"
TEST_RUNNER_AGENT = CRAFT / "agents" / "test-runner.md"

DISPATCH_HEADING = "### 3. Dispatch `executor`"
REVIEW_HEADING = "### 4. Review"

DISPATCH_EXPECTS_START = "The agent expects"
DISPATCH_EXPECTS_END = "Personal-vault lessons are fenced"

GOOD_FITS_START = "Good fits:"
BAD_FITS_START = "Bad fits:"


class SectionBoundaryError(Exception):
    """Raised when a boundary string used to slice out a section can no
    longer be found in the source text — e.g. a paragraph the boundary
    quotes verbatim got reworded elsewhere in the file."""


def _section(text: str, start_heading: str, end_heading: str, *, context: str) -> str:
    try:
        start = text.index(start_heading)
    except ValueError:
        raise SectionBoundaryError(
            f"{context}: start boundary {start_heading!r} not found in the source "
            f"text — this section can no longer be located."
        ) from None
    try:
        end = text.index(end_heading, start)
    except ValueError:
        raise SectionBoundaryError(
            f"{context}: end boundary {end_heading!r} not found in the source "
            f"text — this section can no longer be located."
        ) from None
    return text[start:end]


def _dispatch_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, DISPATCH_HEADING, REVIEW_HEADING, context="execute.md dispatch step")


def _dispatch_expects_section() -> str:
    """Narrower than `_dispatch_section` — bounded to just the 'The agent
    expects:' list plus the test-run-mandate prose that immediately
    follows it, excluding the model-escalation prose and the trailing
    `Returns:` line further down in the same step."""
    return _section(
        _dispatch_section(),
        DISPATCH_EXPECTS_START,
        DISPATCH_EXPECTS_END,
        context="execute.md dispatch step's 'The agent expects:' list",
    )


def _test_runner_good_fits_section() -> str:
    text = TEST_RUNNER_AGENT.read_text()
    return _section(text, GOOD_FITS_START, BAD_FITS_START, context="test-runner.md 'Good fits' list")


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


# --- Contract item A: the dispatch template names scoped per-slice runs as default ---


def test_dispatch_names_scoped_blast_radius_as_the_default_run():
    _pin_in(
        _dispatch_expects_section(),
        "execute.md#3",
        "name the blast radius as the default scoped command",
        "The dispatch template's test-run mandate must state scoping to the "
        "blast radius as the default per-slice run, not only as a fallback "
        "for when the suite's measured runtime exceeds the Bash timeout "
        "ceiling.",
    )


def test_dispatch_defines_blast_radius_components():
    _pin_in(
        _dispatch_expects_section(),
        "execute.md#3",
        "the changed file's tests, its module's tests, and any suite exercising a caller of what was touched",
        "Naming 'the blast radius' without spelling out what it means "
        "leaves an executor to guess scope — the dispatch must enumerate "
        "the same three components the global TDD rule names.",
    )


# --- Contract item B: the dispatch states the per-slice-scoped vs Phase-1-full distinction ---


def test_dispatch_states_scoped_vs_phase1_full_distinction():
    _pin_in(
        _dispatch_expects_section(),
        "execute.md#3",
        "The controller's own Phase 1 full-suite gate is not licence for per-slice full runs",
        "Without the contrast stated explicitly, the fact that the "
        "controller runs a full-suite gate at Phase 1 reasonably implies "
        "full-suite runs are the house style for every slice too — the "
        "dispatch must say in words that per-slice runs stay scoped even "
        "though the end-of-run gate is full.",
    )


# --- Contract item D: the "entire suite" claim states a scoping rule, not a false count ---


def test_dispatch_does_not_claim_the_entire_suite_runs_an_exact_count():
    section = _dispatch_expects_section()
    assert "runs exactly once" not in section, (
        "execute.md#3: the scoped-vs-Phase-1-full paragraph must not claim the "
        "entire suite 'runs exactly once' — this same document's Phase 2 has "
        "simplifier re-green the full suite, and Phase 3 requires every "
        "correctness fix to re-pass the Phase 1 gate, so a run with a simplify "
        "commit and one fix round greens the whole suite three times. A false "
        "count invites a controller to read it as licence to skip the "
        "post-simplify and post-fix re-gates."
    )


def test_dispatch_states_entire_suite_never_runs_per_slice():
    _pin_in(
        _dispatch_expects_section(),
        "execute.md#3",
        "the entire suite is never run per slice",
        "The scoping claim must be about what does NOT happen per slice, not "
        "about how many times the whole pipeline runs the entire suite overall "
        "— Phase 2's simplify re-green and Phase 3's per-fix re-gate mean that "
        "count is more than one.",
    )


# --- Contract item E: the executor widens a controller-named scoped command that under-scopes ---


def test_dispatch_states_executor_widens_named_command_past_named_scope():
    _pin_in(
        _dispatch_expects_section(),
        "execute.md#3",
        "the executor widens that named command mid-build if its edits reach past what it names",
        "Naming the scoped command asks the controller to fix the blast radius "
        "up front, before the build — but which callers get touched is only "
        "knowable to the executor mid-build. Per the recorded precedent, a "
        "dispatch's test-run mandate overrides the agent's own scoping rule "
        "(executor.md Step 7), so an under-scoped named command would "
        "otherwise silently skip caller suites rather than being widened.",
    )


# --- Contract item C: test-runner.md's examples include a scoped invocation ---


def test_test_runner_good_fits_includes_a_scoped_example():
    _pin_in(
        _test_runner_good_fits_section(),
        "test-runner.md#good-fits",
        '"Run the scoped suite for this slice\'s blast radius and tell me what failed"',
        "test-runner.md's worked examples are otherwise full-suite shaped "
        "('Run the test suite...', 'Run the full CI check...'), which pulls "
        "an executor toward full-suite runs. The Good fits list must "
        "include a scoped-invocation example so the agent's own "
        "description stops pulling that way.",
    )


# --- test-file infrastructure: _section raises a named, explanatory error ---


def test_section_raises_named_error_when_boundary_missing():
    with pytest.raises(SectionBoundaryError, match=r"nonexistent-boundary.*not found"):
        _section("some prose with a start marker in it", "start", "nonexistent-boundary", context="a test fixture")
