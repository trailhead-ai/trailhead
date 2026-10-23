"""Structural hygiene for every tool's behavioural eval corpus.

`docs/eval-protocol.md` defines the shape an eval case takes: a directory holding
an `expected.md` written before any arm runs, plus a non-empty `fixtures/` the run
is pointed at. This suite pins that shape across every tool, so a case that would
be unrunnable — or worse, one whose verdict was written after the fact and cannot
be told apart from one that was not — fails the test suite and CI.

**What this cannot check.** Whether a case is worth running, whether its fixture is
contaminated by the prose's own worked example, and whether `expected.md` was
genuinely written first are all judgment calls that no structural check reaches.
They are the reader's job, and `docs/eval-protocol.md` says how to do it. This
suite is the floor, not the standard.

A tool with no cases is not a failure — an empty corpus is an honest report that
nobody has written an eval for that tool yet. The `README.md` each corpus carries
is scaffolding, not a case, and is skipped.

**Runner shape.** A new case carries a `case.yaml` and is run by the plugin eval
runner. Every case directory under `tools/*/plugins/*/evals/` either has one or is
named in `_LEGACY_HAND_SHAPED`, the shrink-only list of cases written before the
runner existed. An entry whose case gained a `case.yaml`, or whose directory is
gone, fails until it is removed, so the list can only get shorter. This is a
hygiene check on case shape, not a security control; it fails the test suite and
CI, not a commit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = ["lore", "camp", "craft", "portage", "outpost", "trailhead"]
_PROTOCOL = _REPO_ROOT / "docs" / "eval-protocol.md"

_LEGACY_HAND_SHAPED = frozenset(
    {
        "craft:compound-criterion-detection",
        "craft:execute-cuts-a-task-branch-off-base",
        "craft:execute-preflight-refuses-stale-base",
        "craft:finding-presentation-wording",
        "craft:gate-reads-the-evidence-artifact",
        "craft:observation-point-enumeration",
        "craft:prototype-plan-carries-no-migration",
        "craft:ritual-deliverable-names-its-record",
        "craft:ritual-output-wording",
        "craft:scrub-citation-vs-inline",
        "craft:tidy-followups-builds-one-parent",
        "craft:tidy-followups-drops-and-merges",
        "craft:tidy-followups-presents-a-finished-spec",
        "craft:tidy-followups-reports-an-unmet-criterion",
        "craft:tidy-followups-resumes-or-drops-a-parent",
        "craft:unconditional-citation-vs-inline",
        "craft:waived-concern-stands-down",
        "craft:waived-excerpt-does-not-bend-a-lens",
        "lore:bash-write-gate",
        "lore:carve-out-does-not-generalise",
        "outpost:follow-up-round-on-an-open-pad",
        "outpost:pad-on-visual-question",
        "outpost:publish-routing",
        "outpost:record-link-rendering",
        "portage:approval-is-human-only",
    }
)


_RUNNER_GUIDANCE = (
    "write a runner case per docs/eval-protocol.md; prose outside a plugin (a user "
    "ruleset, CLAUDE.md) cannot be loaded by the runner, so record that limitation "
    "in the task instead of writing a hand-run case"
)


def _check_runner_shape(root: Path, legacy: frozenset[str]) -> list[str]:
    """Violations of the rule that every case is runner-shaped or listed in `legacy`.

    `root` is a repository root; cases are the directories under
    `tools/*/plugins/*/evals/`. Plain files beside them (README.md, eval-gate.json,
    scaffold.sh) are not cases.
    """
    found: dict[str, bool] = {}
    for evals in sorted(root.glob("tools/*/plugins/*/evals")):
        tool = evals.parent.parent.parent.name
        for case in sorted(d for d in evals.iterdir() if d.is_dir()):
            found[f"{tool}:{case.name}"] = (case / "case.yaml").is_file()

    violations = []
    for key, runner_shaped in found.items():
        if key in legacy and runner_shaped:
            violations.append(
                f"{key} now has a case.yaml: ported, remove it from the list "
                "_LEGACY_HAND_SHAPED"
            )
        elif key not in legacy and not runner_shaped:
            violations.append(f"{key} has no case.yaml; {_RUNNER_GUIDANCE}")
    for key in sorted(legacy - found.keys()):
        violations.append(
            f"stale entry {key} in _LEGACY_HAND_SHAPED: no such case directory, "
            "remove it from the list"
        )
    return violations


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


def _case(root: Path, tool: str, name: str, *, runner: bool) -> Path:
    case = root / "tools" / tool / "plugins" / tool / "evals" / name
    (case / "fixtures").mkdir(parents=True)
    (case / "expected.md").write_text("under test: x\npass condition: y\n")
    (case / "fixtures" / "f.txt").write_text("x")
    if runner:
        (case / "case.yaml").write_text("name: x\n")
    return case


def test_unlisted_hand_shaped_case_is_a_violation(tmp_path: Path):
    _case(tmp_path, "lore", "new-case", runner=False)
    violations = _check_runner_shape(tmp_path, frozenset())
    assert len(violations) == 1
    assert "lore:new-case" in violations[0]
    assert "runner case" in violations[0]
    assert "docs/eval-protocol.md" in violations[0]
    assert "outside a plugin" in violations[0]
    assert "record that limitation" in violations[0]


def test_listed_hand_shaped_case_is_allowed(tmp_path: Path):
    _case(tmp_path, "lore", "old-case", runner=False)
    assert _check_runner_shape(tmp_path, frozenset({"lore:old-case"})) == []


def test_runner_shaped_case_needs_no_listing(tmp_path: Path):
    _case(tmp_path, "craft", "ported-case", runner=True)
    assert _check_runner_shape(tmp_path, frozenset()) == []


def test_listed_case_that_gained_a_case_yaml_must_leave_the_list(tmp_path: Path):
    _case(tmp_path, "craft", "ported-case", runner=True)
    violations = _check_runner_shape(tmp_path, frozenset({"craft:ported-case"}))
    assert len(violations) == 1
    assert "craft:ported-case" in violations[0]
    assert "ported, remove it from the list" in violations[0]


def test_listed_entry_with_no_directory_is_stale(tmp_path: Path):
    _case(tmp_path, "craft", "real-case", runner=True)
    violations = _check_runner_shape(tmp_path, frozenset({"lore:gone-case"}))
    assert len(violations) == 1
    assert "lore:gone-case" in violations[0]
    assert "stale entry" in violations[0]


def test_corpus_readme_and_gate_file_are_not_cases(tmp_path: Path):
    evals = tmp_path / "tools" / "lore" / "plugins" / "lore" / "evals"
    evals.mkdir(parents=True)
    (evals / "README.md").write_text("scaffolding")
    (evals / "eval-gate.json").write_text("{}")
    (evals / "scaffold.sh").write_text("#!/bin/sh")
    assert _check_runner_shape(tmp_path, frozenset()) == []


# inert-gate: allow the real corpus is the seam: it proves the checker is wired to the tree and the list
def test_real_corpus_is_runner_shaped_or_listed():
    assert _check_runner_shape(_REPO_ROOT, _LEGACY_HAND_SHAPED) == []
