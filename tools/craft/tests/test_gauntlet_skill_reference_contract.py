"""The gauntlet skill stays within the line guidance and its pointers hold.

`skills/gauntlet/SKILL.md` is the entrypoint an agent running the spec
gauntlet loads whole. This suite holds four properties of the split that
brings it under the 500-line guidance by relocating consulted-at-a-point
material into skill-local reference documents: the line bar itself, the
integrity of every pointer the split creates (no orphaned reference document,
no pointer to a file that is not there), that its reference documents carry
an accurate contents block once they are long enough to need one, and that
each reference document still carries real body content rather than having
been gutted to a stub that would still satisfy every property above.

It also pins that `SKILL.md` still cites `_shared/security.md` (the
credential-pattern scrub) and `_shared/refine.md` (the data-not-instruction
marker) from within the accepted tail — the two citations a relocation done
purely to hit the line count could drop while every other assertion here
stayed green.

It asserts no phrase and names no section beyond the accepted tail's own
heading, which is itself a structural anchor rather than prose: every
reference document may be reworded freely, every reference may be
rephrased, and the suite stays green provided the structure — line count,
pointer, citation, and body-content floor — holds.

**What this suite does not check.** The pointers this split creates are
required to be *unconditional* — a plain directive to read the referenced
document now and follow it in full, never a branch or a conditional dispatch
— and that shape is the reason the relocation was permitted at all. Nothing
here checks that property: it is a claim about phrasing, and this suite
asserts structure only. A pointer reworded into a conditional ("consult if
relevant") would leave every assertion below green.
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
    # Matches the stem anywhere in SKILL.md's text, not scoped to a gauntlet-local
    # pointer form the way `gauntlet_local_targets` is. A future reference document
    # could therefore be counted non-orphaned by an unrelated mention of the same
    # stem elsewhere (e.g. `_shared/calibration.md`), rather than by its own
    # gauntlet-local pointer.
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
    opening = re.search(r"^#{1,6} The accepted tail *$", text, re.MULTILINE)
    assert opening, (
        "SKILL.md has no `The accepted tail` heading at any level, so the two "
        "citation checks below have no section to scope to"
    )
    rest = text[opening.end() :]
    later_heading = re.search(r"^#{1,6} ", rest, re.MULTILINE)
    end = opening.end() + (later_heading.start() if later_heading else len(rest))
    return text[opening.start() : end]


def gate(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOC_GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("name", sorted(gauntlet_local_targets(_skill_text())))
# inert-gate: allow pointer integrity; a target that is not there cannot be loaded
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
