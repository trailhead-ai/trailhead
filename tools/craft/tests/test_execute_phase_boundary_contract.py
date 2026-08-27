"""The `craft/phase-boundary` label contract on execute.md's resumability section.

`### Phase progress and resumability` prescribes an `## End Phases` checklist that
records progress, and its clean-working-tree resume precondition reverts a dirty
tree to "the last recorded phase boundary" — a value nothing wrote. This pins the
fix: each end phase upserts `craft/phase-boundary=<sha>` (`<sha>` being `HEAD`
after that phase's commits land) onto the parent task record, and the resume
precondition reads it back structurally, failing closed when it is absent rather
than reverting to a guessed target.

Every prose pin here is scoped to the `### Phase progress and resumability`
section specifically — extracted by heading, per
[[lesson/mutation-test-a-prose-pin-whose-target-string-occurs-elsewhere-in-the-file]]
— and asserted as a contiguous substring within one physical line, per
[[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]], so a matching
sentence appearing anywhere else in the file cannot satisfy the pin.

The label round trip is not a prose assertion: it runs the real `lore` CLI
against a throwaway vault in `tmp_path`, mirroring
`tools/craft/tests/test_prior_art_label_contract.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_EXECUTE = CRAFT / "skills" / "_shared" / "execute.md"
SCHEMA_FIXTURE = Path(__file__).parent / "fixtures" / "phase_boundary_label_schema.txt"

PHASE_PROGRESS_HEADING = "### Phase progress and resumability"
MODEL_SELECTION_HEADING = "## Model Selection"

LORE_TESTS_DIR = Path(__file__).parent.parent.parent / "lore" / "tests"
sys.path.insert(0, str(LORE_TESTS_DIR))

from conftest import make_vault, run_cli  # noqa: E402


def _section(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _phase_progress_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, PHASE_PROGRESS_HEADING, MODEL_SELECTION_HEADING)


def _pin_in(section_text: str, path_label: str, phrase: str, why: str) -> None:
    """Assert *phrase* appears inside a single physical line of *section_text*,
    and that it occurs on exactly one line so the pin cannot pass on the wrong
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


# --- fixture ships ------------------------------------------------------------


def test_schema_fixture_ships():
    assert SCHEMA_FIXTURE.exists(), f"Expected the canonical phase-boundary label schema fixture at {SCHEMA_FIXTURE}"


# --- fixture literal is pinned in the section ----------------------------------


def test_fixture_literal_appears_in_phase_progress_section():
    schema = SCHEMA_FIXTURE.read_text().strip()
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        schema,
        "The canonical `craft/phase-boundary=<sha>` literal must be byte-identical "
        "between the fixture and the section that prescribes writing it, so a "
        "later reader is pinned to the same source rather than an "
        "independently-worded restatement.",
    )


# --- writer mandate -------------------------------------------------------------


def test_writer_mandate_states_upsert_at_tick():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "at the tick, upserts the phase boundary onto the parent task record",
        "The section must instruct that each phase upserts the label at the "
        "moment it ticks its `## End Phases` line.",
    )


def test_writer_mandate_names_head_after_commits_land():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "being `HEAD` **after** that phase's commits land",
        "The section must name the label's value as `HEAD` taken *after* the "
        "phase's own commits land, not before — otherwise the boundary points "
        "at the wrong tree.",
    )


# --- reader precondition, fail-closed --------------------------------------------


def test_reader_reads_structured_json_label():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        '`.sidecar.labels["craft/phase-boundary"]`',
        "The resume precondition must read the boundary structurally via "
        "`lore record show --json`, not by parsing prose.",
    )


def test_reader_fails_closed_when_label_absent():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "resume stops and reports rather than reverting to a guessed target",
        "Absent the label, resume must stop and report — never revert to a "
        "guessed target. This is the regression the task exists to prevent.",
    )


# --- label round trip through the real CLI --------------------------------------


def test_phase_boundary_label_round_trips_and_upserts(tmp_path):
    """Five properties in one round trip:

    1. `record create --label craft/phase-boundary=<sha>` is accepted by the
       write-time reserved-key guard.
    2. `search 'has:label.craft.phase-boundary'` finds it.
    3. `search 'label.craft.phase-boundary:<sha>'` matches it exactly.
    4. A second `record update --label craft/phase-boundary=<sha2>` upserts:
       the record ends holding exactly one value, the newer one, with no
       history of the first.
    5. The bare key `phase` is refused (non-zero exit) while
       `craft/phase-boundary` is accepted — through the CLI's actual refusal,
       not by importing the predicate.
    """
    vault, state = make_vault(tmp_path)
    sha1 = "aaaaaaa1111111111111111111111111111111"
    sha2 = "bbbbbbb2222222222222222222222222222222"

    create = run_cli(
        [
            "record",
            "create",
            "--kind",
            "task",
            "--title",
            "Phase Boundary Probe Task",
            "--keyword",
            "probe",
            "--label",
            f"craft/phase-boundary={sha1}",
        ],
        vault=vault,
        state_dir=state,
        stdin_text="body\n",
    )
    assert create.returncode == 0, create.stderr  # write-time reserved-key guard accepted it
    record_id = create.stdout.strip()
    assert record_id.startswith("task/"), f"expected task/<name>, got {record_id!r}"
    name = record_id.split("/", 1)[1]

    exists_search = run_cli(
        ["search", "has:label.craft.phase-boundary"],
        vault=vault,
        state_dir=state,
    )
    assert exists_search.returncode == 0, exists_search.stderr
    assert name in exists_search.stdout, (  # existence lookup
        f"expected {name!r} in search output for has:label.craft.phase-boundary, "
        f"got: {exists_search.stdout!r}"
    )

    eq_search = run_cli(
        ["search", f"label.craft.phase-boundary:{sha1}"],
        vault=vault,
        state_dir=state,
    )
    assert eq_search.returncode == 0, eq_search.stderr
    assert name in eq_search.stdout, (  # exact-value lookup
        f"expected {name!r} in search output for label.craft.phase-boundary:{sha1}, "
        f"got: {eq_search.stdout!r}"
    )

    update = run_cli(
        ["record", "update", record_id, "--label", f"craft/phase-boundary={sha2}"],
        vault=vault,
        state_dir=state,
    )
    assert update.returncode == 0, update.stderr

    show = run_cli(
        ["record", "show", record_id, "--json"],
        vault=vault,
        state_dir=state,
    )
    assert show.returncode == 0, show.stderr
    import json

    payload = json.loads(show.stdout)
    labels = payload["sidecar"]["labels"]
    # exact final map, not just "the new value is present" — an appending CLI
    # would also pass a bare containment check
    assert labels == {"craft/phase-boundary": sha2}, (
        f"expected the label to hold exactly the newer value with no history "
        f"of the first, got {labels!r}"
    )

    refused = run_cli(
        ["record", "update", record_id, "--label", f"phase={sha2}"],
        vault=vault,
        state_dir=state,
    )
    assert refused.returncode != 0, (
        "the bare key 'phase' shadows a KQL query field and must be refused "
        "at write time"
    )
