"""Structural hygiene for every tool's behavioural eval corpus.

`docs/eval-protocol.md` defines the shape an eval case takes: a directory holding
an `expected.md` written before any arm runs, plus a non-empty `fixtures/` the run
is pointed at. This suite pins that shape across every tool, so a case that would
be unrunnable — or worse, one whose verdict was written after the fact and cannot
be told apart from one that was not — is caught at commit time.

**What this cannot check.** Whether a case is worth running, whether its fixture is
contaminated by the prose's own worked example, and whether `expected.md` was
genuinely written first are all judgment calls that no structural check reaches.
They are the reader's job, and `docs/eval-protocol.md` says how to do it. This
suite is the floor, not the standard.

A tool with no cases is not a failure — an empty corpus is an honest report that
nobody has written an eval for that tool yet. The `README.md` each corpus carries
is scaffolding, not a case, and is skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = ["lore", "camp", "craft", "portage", "outpost", "trailhead"]
_PROTOCOL = _REPO_ROOT / "docs" / "eval-protocol.md"


def _corpus(tool: str) -> Path:
    return _REPO_ROOT / "tools" / tool / "plugins" / tool / "evals"


def _cases(tool: str) -> list[Path]:
    corpus = _corpus(tool)
    if not corpus.is_dir():
        return []
    return sorted(d for d in corpus.iterdir() if d.is_dir())


_CASES = [(tool, case) for tool in _TOOLS for case in _cases(tool)]


@pytest.mark.parametrize(
    "tool,case", _CASES, ids=[f"{t}:{c.name}" for t, c in _CASES] or None
)
# inert-gate: allow eval corpus is test material; its shape has no runnable consumer
def test_every_eval_case_is_runnable(tool: str, case: Path):
    """A case missing either half cannot be dispatched at all."""
    expected = case / "expected.md"
    assert expected.is_file(), (
        f"{tool}:{case.name} has no expected.md — the pass condition is what "
        "stops a result being retrofitted into a pass"
    )
    fixtures = case / "fixtures"
    assert fixtures.is_dir(), f"{tool}:{case.name} has no fixtures/ directory"
    assert any(fixtures.iterdir()), (
        f"{tool}:{case.name} has an empty fixtures/, so an arm has nothing to run against"
    )


@pytest.mark.parametrize(
    "tool,case", _CASES, ids=[f"{t}:{c.name}" for t, c in _CASES] or None
)
# inert-gate: allow eval corpus is test material; its shape has no runnable consumer
def test_every_expected_verdict_states_its_subject_and_pass_condition(tool: str, case: Path):
    """An `expected.md` that names neither is not a pre-registered verdict — it is
    a note, and it cannot be graded against.
    """
    text = (case / "expected.md").read_text(encoding="utf-8").lower()
    assert "under test" in text, (
        f"{tool}:{case.name}'s expected.md never says what is under test"
    )
    assert "pass condition" in text or "pass:" in text, (
        f"{tool}:{case.name}'s expected.md states no pass condition, so a run "
        "cannot be graded against it"
    )


# inert-gate: allow eval corpus is test material; its shape has no runnable consumer
def test_the_protocol_the_corpora_point_at_exists():
    """Every MANUAL-EVAL.md sends its reader here; a dead link makes the rules
    unreachable at exactly the moment someone is authoring a case.
    """
    assert _PROTOCOL.is_file(), f"missing {_PROTOCOL.relative_to(_REPO_ROOT)}"
