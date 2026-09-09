"""craft's record templates and the note_store contract, run through their real consumers.

Four consumers read what this module governs, and each one executes here:

- lore's parent-completion guard reads the parent-task body's ``## Flow-out`` section
  (:func:`lore.record.guards.body_has_flow_out`);
- craft's criterion gate and candidate-set deriver read a spec body's
  ``## Acceptance Criteria`` heading, failing closed when it is absent;
- craft's maturity stamper parses a spec body's ``## Maturity`` section
  (:func:`maturity_stamp.parse_entries`);
- lore's CLI parser accepts the invocations ``_shared/note-storage.md`` tells the
  planning skills to run (:func:`lore.cli.dispatch.build_parser`).

The templates and the contract document are the INPUT. Their headings, worked
examples, stated grammar, and spelled-out commands are lifted out and fed to the
consumer that has to make sense of them, rather than scanned for sentences. A
template whose heading a gate can no longer find, a worked example the gate does
not actually grade the way the template promises, or a documented flag lore has
since renamed, all turn this module red.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
CRAFT_PLUGIN = REPO_ROOT / "plugins" / "craft"
TEMPLATES_DIR = CRAFT_PLUGIN / "templates"
SCRIPTS_DIR = CRAFT_PLUGIN / "scripts"
NOTE_STORAGE_MD = CRAFT_PLUGIN / "skills" / "_shared" / "note-storage.md"

_LORE_PLUGIN = REPO_ROOT.parent / "lore" / "plugins" / "lore"
for _path in (str(SCRIPTS_DIR), str(_LORE_PLUGIN)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import maturity_stamp  # noqa: E402
from lore.cli.dispatch import build_parser  # noqa: E402
from lore.record.guards import body_has_flow_out  # noqa: E402
from lore.record.model import STATUS_VOCAB  # noqa: E402


def _template(stem: str) -> str:
    return (TEMPLATES_DIR / f"{stem}.md").read_text(encoding="utf-8")


def _section(text: str, heading: str, next_heading: str) -> str:
    return text[text.index(heading) : text.index(next_heading)]


def _run_gate(script: str, body: str) -> subprocess.CompletedProcess[str]:
    """Run one of craft's spec gates over `body` on stdin, as its callers do."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / f"{script}.py")],
        input=body,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# The parent-task body, read by lore's completion guard
# ---------------------------------------------------------------------------


def test_the_parent_task_body_suppresses_lores_flow_out_reminder():
    """The `## Flow-out` section exists so that a parent task completed with this
    body does NOT draw lore's flow-out reminder. lore owns that decision, so the
    template is put to lore's own guard rather than to a copy of its regex."""
    assert body_has_flow_out(_template("plan")), (
        "craft's parent-task body no longer satisfies lore's flow-out guard — a plan "
        "created from it would draw a flow-out reminder on completion"
    )


# ---------------------------------------------------------------------------
# The spec body, read by the gates that grade a spec
# ---------------------------------------------------------------------------

# Both gates fail closed on a spec they cannot find the criteria heading in, and
# say so with this text. A shipped template the gates can still read reaches a
# *later* refusal instead — it declares no criteria yet, which is what an unfilled
# template should look like.
_NO_AC_HEADING = "no '## Acceptance Criteria' heading"


@pytest.mark.parametrize("gate", ["criterion_gate", "candidate_set"])
def test_every_gate_that_grades_a_spec_finds_the_shipped_templates_criteria_heading(gate: str):
    result = _run_gate(gate, _template("spec"))
    output = result.stdout + result.stderr
    assert _NO_AC_HEADING not in output, (
        f"{gate} cannot find `## Acceptance Criteria` in craft's shipped spec template — "
        f"a spec written from it fails closed before it is even filled in:\n{output}"
    )
    assert "zero-criterion-identifiers" in output, (
        f"{gate} reached an unexpected verdict on the unfilled spec template; expected it "
        f"to find the heading and report no criteria declared yet:\n{output}"
    )


# The template teaches the criterion grammar with worked examples, each a quoted
# `- **ACn.**` bullet labelled with the verdict it is supposed to draw. Anchoring
# the capture on the `**AC` bullet prefix (rather than on the quote alone) keeps
# the neighbouring Compound / Not-compound prose examples — which are criteria
# text, not whole bullets — out of the set.
# The label is captured as any bare word rather than as an alternation of the two
# known verdicts. Matching only `Refused|Conformant` would let a relabelled example
# fall out of the set silently — the parametrization would just get shorter and stay
# green — so every labelled example is captured and its label is checked below.
_WORKED_EXAMPLE = re.compile(r'-\s+([A-Z][a-z]+)\b[^:\n]*:\s*"(-\s+\*\*AC.+?)"', re.S)
_VERDICTS = ("Refused", "Conformant")


def _criterion_examples() -> list[tuple[str, str]]:
    """(verdict, criterion bullet) for every worked example the spec template states."""
    section = _section(_template("spec"), "## Acceptance Criteria", "## Required Interfaces")
    return [(verdict, " ".join(body.split())) for verdict, body in _WORKED_EXAMPLE.findall(section)]


def _spec_declaring(criterion: str) -> str:
    """The shipped spec template with `criterion` as its one declared criterion."""
    text = _template("spec")
    start, end = text.index("## Acceptance Criteria"), text.index("## Required Interfaces")
    return f"{text[:start]}## Acceptance Criteria\n\n{criterion}\n\n{text[end:]}"


def test_every_worked_example_the_spec_template_states_carries_a_known_verdict():
    """Non-vacuity guard on the extraction below. Capturing the label as a bare word
    and checking it here — rather than only matching the two known verdicts — is what
    makes the guard total: an example relabelled to anything else fails by name
    instead of quietly leaving the parametrization one case shorter."""
    verdicts = [verdict for verdict, _ in _criterion_examples()]
    unknown = sorted({v for v in verdicts if v not in _VERDICTS})
    assert not unknown, (
        f"the spec template labels worked examples with verdicts the gate cannot grade: "
        f"{unknown} (expected only {list(_VERDICTS)})"
    )
    assert set(verdicts) == set(_VERDICTS), (
        "expected the `## Acceptance Criteria` comment to carry worked examples of both "
        f"verdicts, extracted: {sorted(set(verdicts))}"
    )


@pytest.mark.parametrize(
    "verdict,criterion",
    _criterion_examples(),
    ids=[f"{verdict}[{i}]" for i, (verdict, _) in enumerate(_criterion_examples())],
)
def test_the_criterion_grammar_the_template_teaches_is_the_one_the_gate_enforces(
    verdict: str, criterion: str
):
    """Each worked example is graded by the real gate and must draw the verdict the
    template labelled it with. This is the only thing that ties the template's
    teaching to the rule: a gate whose bar moves, or an example edited until it no
    longer illustrates the bar, stops agreeing here."""
    result = _run_gate("criterion_gate", _spec_declaring(criterion))
    if verdict == "Conformant":
        assert result.returncode == 0, (
            f"the spec template offers this as a conformant criterion, but criterion_gate "
            f"refuses it (exit {result.returncode}):\n{criterion}\n{result.stderr}"
        )
    else:
        assert result.returncode == 1, (
            f"the spec template offers this as a refused criterion, but criterion_gate "
            f"returned {result.returncode}:\n{criterion}\n{result.stdout}{result.stderr}"
        )


# ---------------------------------------------------------------------------
# The spec body's `## Maturity` section, read by the maturity stamper
# ---------------------------------------------------------------------------


def _maturity_section() -> str:
    return _section(_template("spec"), "## Maturity", "## Acceptance Criteria")


def test_the_shipped_maturity_section_reads_as_empty_rather_than_malformed():
    """The section ships carrying only comments. The stamper must read that as an
    *empty* section — a section awaiting entries — because the reminder comment has
    to survive in a written spec, and a carrier the stamper called malformed could
    not."""
    with pytest.raises(maturity_stamp.StampError) as caught:
        maturity_stamp.parse_entries(_template("spec"))
    assert caught.value.args[0] == "empty-section", (
        f"the shipped `## Maturity` section is not read as empty: {caught.value.args}"
    )


def test_ordinary_prose_in_the_maturity_section_is_rejected():
    """Why the reminder has to be a comment at all: any non-bullet line the stamper
    can see is a malformed entry, so a comment is the only carrier the section
    tolerates. That is what makes the comment's keep-marker load-bearing rather
    than decorative."""
    text = _template("spec").replace("## Maturity\n", "## Maturity\nthis is ordinary prose\n", 1)
    with pytest.raises(maturity_stamp.StampError) as caught:
        maturity_stamp.parse_entries(text)
    assert caught.value.args[0] == "malformed-entry", caught.value.args


# The per-line grammar the section's own comment states, e.g. `- <member-name>: <level>`.
_GRAMMAR = re.compile(r"-\s*<([a-z-]*member[a-z-]*)>\s*:\s*<([a-z-]*level[a-z-]*)>")
# The closed vocabulary, as the comment spells it: `prototype` / `early` / `production`.
_VOCABULARY = re.compile(r"`(prototype|early|production)`")


def test_the_maturity_grammar_the_template_states_is_the_one_the_stamper_parses():
    """The grammar and the closed vocabulary are lifted out of the template's own
    comment and used to write entries, which the real stamper then has to parse
    back. A comment that states a grammar the stamper does not accept — a renamed
    separator, a level dropped from the vocabulary — fails here instead of being
    discovered by whoever wrote a spec against it."""
    section = _maturity_section()
    assert _GRAMMAR.search(section), f"the `## Maturity` comment states no per-line grammar: {section!r}"
    levels = sorted(set(_VOCABULARY.findall(section)))
    assert levels == ["early", "production", "prototype"], (
        f"the `## Maturity` comment must state the closed vocabulary, found: {levels}"
    )

    entries = {f"member-{i}": level for i, level in enumerate(levels)}
    body = _template("spec").replace(
        "## Maturity\n",
        "## Maturity\n" + "".join(f"- {name}: {level}\n" for name, level in entries.items()),
        1,
    )
    assert maturity_stamp.parse_entries(body) == entries


# ---------------------------------------------------------------------------
# The note_store contract's commands, parsed by lore's real CLI
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:sh|bash)\n(.*?)```", re.DOTALL)
# Placeholders the contract writes into its recipes, and a value that satisfies
# the CLI's own validation for that position.
_PLACEHOLDERS = {
    "<topic>": "a-topic",
    "<plan topic>": "a-plan-topic",
    "<task topic>": "a-task-topic",
    "<parent-name>": "a-parent",
    "<earlier-task-name>": "an-earlier-task",
    "<spec-name>": "a-spec",
    "<name>": "a-name",
}


def _documented_invocations() -> list[list[str]]:
    """Every `lore …` command line the note_store contract spells out, as argv."""
    found: list[list[str]] = []
    for block in _FENCE.findall(NOTE_STORAGE_MD.read_text(encoding="utf-8")):
        for line in block.replace("\\\n", " ").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            # Recipes that pipe a rendered body in name the CLI after the pipe.
            _, _, command = line.rpartition("| ")
            command = command.strip()
            if not command.startswith("lore "):
                continue
            for placeholder, value in _PLACEHOLDERS.items():
                command = command.replace(placeholder, value)
            found.append(shlex.split(command)[1:])
    return found


def test_the_note_store_contract_spells_out_invocations_to_check():
    """Non-vacuity guard: an extractor that stops matching the fenced recipes would
    otherwise leave the parse test below iterating over nothing."""
    assert len(_documented_invocations()) >= 4, (
        f"expected the note_store contract to spell out several `lore` recipes, "
        f"extracted: {_documented_invocations()}"
    )


@pytest.mark.parametrize(
    "argv", _documented_invocations(), ids=[" ".join(a[:3]) for a in _documented_invocations()]
)
def test_every_documented_invocation_parses_on_lores_real_cli(argv: list[str]):
    """The contract's whole job is telling the planning skills what to run. A
    command lore no longer registers, or a flag it has since renamed, reads fine in
    the document and fails at the skill's first attempt to run it — so the real
    parser is the thing that has to accept these, not a reader's eye."""
    build_parser().parse_args(argv)


# The contract states each kind's status vocabulary as an arrow chain plus an
# off-path list, e.g. "task: `open → ready → in-progress → done`, off-path
# `blocked` / `dropped` / `superseded`".
_CHAIN = re.compile(r"\b(task|spec):\s*`([a-z-]+(?:\s*→\s*[a-z-]+)+)`")


def _documented_chains() -> list[tuple[str, list[str]]]:
    text = NOTE_STORAGE_MD.read_text(encoding="utf-8")
    return [(kind, [s.strip() for s in chain.split("→")]) for kind, chain in _CHAIN.findall(text)]


def test_the_note_store_contract_states_a_status_chain_for_both_kinds():
    """Non-vacuity guard on the chain extraction."""
    assert {kind for kind, _ in _documented_chains()} == {"task", "spec"}, _documented_chains()


@pytest.mark.parametrize("kind,chain", _documented_chains(), ids=lambda v: v if isinstance(v, str) else "")
# inert-gate: allow graded against STATUS_VOCAB imported from lore itself
def test_every_status_the_contract_teaches_is_real_and_in_lores_own_order(kind: str, chain: list[str]):
    """The chain is the order a skill walks a record through. Checking it against
    lore's own vocabulary catches both a status lore never had and one whose
    position moved — either would send a skill's `--status` write to a refusal."""
    vocabulary = STATUS_VOCAB[kind]
    unknown = [status for status in chain if status not in vocabulary]
    assert not unknown, f"note-storage.md teaches {kind} statuses lore does not define: {unknown}"
    positions = [vocabulary.index(status) for status in chain]
    assert positions == sorted(positions), (
        f"note-storage.md's {kind} chain {chain} runs against lore's own order {list(vocabulary)}"
    )
