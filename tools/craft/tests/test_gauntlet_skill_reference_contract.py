"""The gauntlet skill stays within the line guidance and its pointers hold.

`skills/gauntlet/SKILL.md` is the entrypoint an agent running the spec
gauntlet loads whole. This suite holds three properties of the split that
brings it under the 500-line guidance by relocating consulted-at-a-point
material into skill-local reference documents: the line bar itself, the
integrity of every pointer the split creates (no orphaned reference document,
no pointer to a file that is not there), and that its reference documents
carry an accurate contents block once they are long enough to need one.

It also pins that `SKILL.md` still cites `_shared/execute.md` (the
credential-pattern scrub) and `_shared/refine.md` (the data-not-instruction
marker) from within the accepted tail — the two citations a relocation done
purely to hit the line count could drop while every other assertion here
stayed green.

It asserts no phrase and names no section beyond the accepted tail's own
heading, which is itself a structural anchor rather than prose: every
reference document may be reworded freely, every reference may be
rephrased, and the suite stays green provided the structure — line count,
pointer, and citation — holds.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
GAUNTLET = REPO_ROOT / "plugins" / "craft" / "skills" / "gauntlet"
SKILL_MD = GAUNTLET / "SKILL.md"
TOC_GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "toc_gate.py"

LINE_LIMIT = 500

# A document long enough that a reader cannot hold its shape in view at once
# needs a map. Matches the bar `test_shared_docs_toc_contract.py` uses.
LONG_DOCUMENT_LINES = 100

# A `.md` path mention, in any surrounding form: a bare filename ("dispositions.md")
# or a path with directory components ("gauntlet/dispositions.md",
# "_shared/execute.md", "tools/craft/MANUAL-EVAL.md"). The character immediately
# before and after the match must not itself be part of a longer token, so a
# path embedded inside an unrelated longer name is not a match.
_MD_PATH_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.md)(?![\w-])")


def _skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def reference_documents() -> list[Path]:
    """Every `.md` file in the gauntlet directory other than `SKILL.md` itself."""
    return sorted(p for p in GAUNTLET.glob("*.md") if p.name != "SKILL.md")


def _stem_pattern(stem: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(stem)}\.md(?![\w-])")


def gauntlet_local_targets(text: str) -> set[str]:
    """Every `.md` filename `text` names that resolves inside the gauntlet
    directory itself — a bare filename, or a path whose only directory
    component is `gauntlet` (the convention `skills/review/SKILL.md` already
    uses for its own local reference, `review/code-reviewer.md`).

    A path naming any other directory (`_shared/...`, `skills/<other>/...`,
    `tools/craft/...`) is a real pointer but not a gauntlet-local one, and is
    out of scope for the no-orphan and pointer-resolution properties below.
    """
    targets: set[str] = set()
    for match in _MD_PATH_RE.finditer(text):
        parts = match.group(1).split("/")
        if len(parts) == 1:
            targets.add(parts[0])
        elif len(parts) == 2 and parts[0] == "gauntlet":
            targets.add(parts[1])
    return targets


def _accepted_tail_section(text: str) -> str:
    """The `#### The accepted tail` section, from its heading to the next
    heading of any level. Both cited paths sit inside this section by design
    (the plan's delta design keeps the accepted tail inline while relocating
    everything else), so scoping the citation checks to it is what tells a
    citation dropped or moved out of the accepted tail apart from one that is
    merely reworded in place.
    """
    heading = "#### The accepted tail"
    start = text.index(heading)
    later_heading = re.search(r"^#{1,6} ", text[start + len(heading) :], re.MULTILINE)
    end = start + len(heading) + later_heading.start() if later_heading else len(text)
    return text[start:end]


def gate(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOC_GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


def test_skill_md_stays_within_the_500_line_guidance():
    lines = _skill_text().splitlines()
    assert len(lines) <= LINE_LIMIT, (
        f"{SKILL_MD} is {len(lines)} lines, over the {LINE_LIMIT}-line guidance"
    )


def test_there_is_a_reference_document_beside_skill_md():
    """Guards the relational assertions below against passing vacuously on
    an empty directory. Without this, `test_every_reference_document_is_named`
    and `test_every_gauntlet_local_pointer_resolves` would both parametrize
    over nothing and report clean while proving nothing at all.
    """
    assert reference_documents(), (
        f"no reference document beside SKILL.md found in {GAUNTLET}"
    )


@pytest.mark.parametrize("path", reference_documents(), ids=lambda p: p.name)
def test_every_reference_document_is_named_by_skill_md(path):
    """No orphan reference document: a file nothing points at is unreachable
    in one hop from the entrypoint an agent actually loads.
    """
    assert _stem_pattern(path.stem).search(_skill_text()), (
        f"{path.name} exists in {GAUNTLET} but SKILL.md does not name it"
    )


@pytest.mark.parametrize("name", sorted(gauntlet_local_targets(_skill_text())))
def test_every_gauntlet_local_pointer_resolves(name):
    """A count of pointers is not evidence the pointee is there — assert the
    target's existence directly rather than relying on another gate.
    """
    assert (GAUNTLET / name).is_file(), (
        f"SKILL.md names {name!r} but {GAUNTLET / name} does not exist"
    )


@pytest.mark.parametrize(
    "path",
    [p for p in reference_documents() if len(p.read_text(encoding="utf-8").splitlines()) > LONG_DOCUMENT_LINES],
    ids=lambda p: p.name,
)
def test_toc_gate_is_clean_for_long_reference_documents(path):
    result = gate(path)
    assert result.returncode == 0, (
        f"{path.name}'s contents block no longer matches its headings:\n{result.stderr}"
    )


def test_execute_md_credential_scrub_is_cited_in_the_accepted_tail():
    section = _accepted_tail_section(_skill_text())
    assert "_shared/execute.md" in section, (
        "the accepted tail no longer cites `_shared/execute.md` "
        "(the credential-pattern scrub)"
    )


def test_refine_md_data_not_instruction_marker_is_cited_in_the_accepted_tail():
    section = _accepted_tail_section(_skill_text())
    assert "_shared/refine.md" in section, (
        "the accepted tail no longer cites `_shared/refine.md` "
        "(the data-not-instruction marker)"
    )
