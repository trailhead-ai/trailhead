"""`_shared/council.md` states the finding-attribution rule for a
`highest-stamped` review: a finding is rated at the severity of the
repository named by the leading camp member name segment of a path it
cites, and the highest stamped level governs only a finding that cannot be
attributed to exactly one repository.

These tests bind that rule *relationally* against `scripts/maturity_bars.py`'s
real output, never by copying a severity value into the test. The document
states seven distinct properties of the attribution rule:

    1. matching uses the LEADING path segment
    2. the match is EXACT (whole-segment, not a prefix)
    3. the match is CASE-SENSITIVE
    4. EXACTLY ONE match is required to attribute
    5. no match falls back
    6. two-or-more matches falls back
    7. no cited path at all falls back

Each is extracted from the document by its own marker substring — never one
marker standing in for the whole paragraph — and each gates only the branch
of `documented_severity()` that depends on it. Removing or contradicting a
single property in the document must turn red only the test(s) that
property actually governs, proven per-property by the mutation transcript
in the commit body — with two named exceptions, neither separable by any
single external observation: properties 2 and 3 are governed by one shared
bullet in the document but gated as two independent code-level flags here;
properties 4 and 6, being the positive and negative statements of the same
"how many matched" boundary, only ever turn red together (see the commit
body for why).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COUNCIL_MD = REPO_ROOT / "plugins" / "craft" / "skills" / "_shared" / "council.md"
SKILLS_DIR = REPO_ROOT / "plugins" / "craft" / "skills"

_FILLING_SECTION_HEADING = "### Filling the calibration token"

_CONCERN = "backwards compatibility"

# Two repositories, deliberately at different levels, so the same concern
# resolves to two different severities and the attribution differential is
# observable.
_MEMBER_A, _LEVEL_A = "lookout", "prototype"
_MEMBER_B, _LEVEL_B = "trailhead", "production"

_TWO_REPO_SPEC = (
    "# Some Spec\n\n## Maturity\n\n"
    f"- {_MEMBER_A}: {_LEVEL_A}\n"
    f"- {_MEMBER_B}: {_LEVEL_B}\n"
)


def council_text() -> str:
    return COUNCIL_MD.read_text(encoding="utf-8")


def filling_section(text: str) -> str:
    start = text.index(_FILLING_SECTION_HEADING)
    end = text.index("\n## ", start)
    return text[start:end]


# ---- extracting the documented invocation, and executing it as written ----


def extract_invocation(section: str) -> tuple[str, str]:
    """Pull the script's relative path and the agent-instruction-file flag
    name out of the section's own prose, so the invocation this test runs is
    the one the document currently names rather than one hand-typed here."""
    script_match = re.search(r"`(scripts/[\w./-]+\.py)`", section)
    flag_match = re.search(r"`(--[\w-]+) <repo-root>", section)
    assert script_match, "documented invocation names no `scripts/*.py` renderer"
    assert flag_match, "documented invocation names no `--flag <repo-root>...` agent-instruction input"
    return script_match.group(1), flag_match.group(1)


def run_documented_invocation(spec_text: str, agent_instruction_file: Path) -> subprocess.CompletedProcess:
    section = filling_section(council_text())
    script_rel_path, flag_name = extract_invocation(section)
    script_path = REPO_ROOT / "plugins" / "craft" / script_rel_path
    return subprocess.run(
        [sys.executable, str(script_path), flag_name, str(agent_instruction_file)],
        input=spec_text.encode("utf-8"),
        capture_output=True,
    )


# ---- parsing the rendered matrix (producer side, never hand-authored) ----


def _unlabel(text: str) -> str:
    """Strip the renderer's own backtick delimiter off an interpolated
    member name, read straight out of its stdout — never a hardcoded
    delimiter choice independent of what the renderer actually emits."""
    return text.strip("`")


def parse_matrix(stdout: str, concern: str) -> tuple[list[str], dict[str, str], str]:
    """Returns `(members, {member: severity}, fallback_level)`, all read
    straight out of the renderer's own stdout."""
    lines = stdout.splitlines()
    header = next(line for line in lines if line.startswith("concern x repository:"))
    members = [_unlabel(m.strip()) for m in header.split(":", 1)[1].split(",")]
    fallback_level = re.search(r"maturity: (\w+) \(basis: highest-stamped\)", stdout).group(1)
    row = next(line for line in lines if line.startswith(f"- {concern}:"))
    cells = row.split(":", 1)[1].split(",")
    severities = {}
    for cell in cells:
        member, _, severity = cell.strip().partition("=")
        severities[_unlabel(member)] = severity
    return members, severities, fallback_level


# ---- the documented attribution procedure, gated one marker per property --
#
# Each marker is a substring naming ONE property the document states, none
# standing in for another. A property's absence flips the one branch of
# `documented_severity` that depends on it to the *opposite* rule, rather
# than to a generic "give up" — so the mutation transcript can show a
# specific, wrong, observable answer rather than an assertion error inside
# this helper.

_PROPERTY_MARKERS = {
    "leading": "leading camp member name segment",
    "exact": "match it exactly",
    "case_sensitive": "case-sensitive",
    "exactly_one": "Exactly one match",
    "fallback_no_match": "No match,",
    "fallback_multiple": "two or more distinct matches",
    "fallback_no_path": "no cited path at all",
}


def extract_property_markers(section: str) -> dict[str, bool]:
    normalized = re.sub(r"\s+", " ", section)
    return {name: marker in normalized for name, marker in _PROPERTY_MARKERS.items()}


def _segment(path: str, markers: dict[str, bool]) -> str:
    parts = path.split("/")
    return parts[0] if markers["leading"] else parts[-1]


def _is_match(path: str, member: str, markers: dict[str, bool]) -> bool:
    seg = _segment(path, markers)
    lhs, rhs = (seg, member) if markers["case_sensitive"] else (seg.casefold(), member.casefold())
    return lhs == rhs if markers["exact"] else lhs.startswith(rhs)


def documented_severity(
    paths: list[str],
    members: list[str],
    severities: dict[str, str],
    fallback_severity: str,
    markers: dict[str, bool],
) -> str:
    if not paths:
        return fallback_severity if markers["fallback_no_path"] else severities[members[0]]

    matched = {member for member in members if any(_is_match(p, member, markers) for p in paths)}

    if len(matched) == 0:
        return fallback_severity if markers["fallback_no_match"] else severities[members[0]]

    if len(matched) >= 2:
        # properties 4 (exactly-one) and 6 (fallback-on-multiple) are the
        # positive and negative statements of the same boundary — either
        # one's absence must flip this branch.
        if markers["fallback_multiple"] and markers["exactly_one"]:
            return fallback_severity
        return severities[next(iter(sorted(matched)))]

    return severities[next(iter(matched))]


def _fixture(tmp_path: Path) -> tuple[list[str], dict[str, str], str, dict[str, bool]]:
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text("production\n", encoding="utf-8")
    result = run_documented_invocation(_TWO_REPO_SPEC, agent_file)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    members, severities, fallback_level = parse_matrix(result.stdout.decode("utf-8"), _CONCERN)
    fallback_severity = severities[_MEMBER_B] if fallback_level == _LEVEL_B else severities[_MEMBER_A]
    markers = extract_property_markers(filling_section(council_text()))
    return members, severities, fallback_severity, markers

# ---- contract item 1: relational binding, for both members ---------------


def test_attribution_selects_each_members_own_severity_from_the_real_matrix(tmp_path):
    members, severities, fallback_severity, markers = _fixture(tmp_path)

    selected_a = documented_severity([f"{_MEMBER_A}/some/path"], members, severities, fallback_severity, markers)
    selected_b = documented_severity([f"{_MEMBER_B}/some/path"], members, severities, fallback_severity, markers)

    assert selected_a == severities[_MEMBER_A]
    assert selected_b == severities[_MEMBER_B]
    assert selected_a != selected_b, "fixture must stamp the two members at different severities"


# ---- contract item 3: all four path shapes, each asserted positively ------
# (plus one extra positive fixture pinning the EXACT property independently
#  of case-sensitivity: a leading segment that is a member name's prefix.)


def test_path_matching_exactly_one_member_selects_that_members_severity(tmp_path):
    members, severities, fallback_severity, markers = _fixture(tmp_path)
    selected = documented_severity(
        [f"{_MEMBER_A}/nested/thing.py"], members, severities, fallback_severity, markers
    )
    assert selected == severities[_MEMBER_A]


def test_paths_matching_two_members_falls_back(tmp_path):
    members, severities, fallback_severity, markers = _fixture(tmp_path)
    selected = documented_severity(
        [f"{_MEMBER_A}/x", f"{_MEMBER_B}/y"], members, severities, fallback_severity, markers
    )
    assert selected == fallback_severity


def test_path_matching_no_member_falls_back(tmp_path):
    members, severities, fallback_severity, markers = _fixture(tmp_path)
    selected = documented_severity(["some-unrelated-repo/x"], members, severities, fallback_severity, markers)
    assert selected == fallback_severity


def test_no_path_at_all_falls_back(tmp_path):
    members, severities, fallback_severity, markers = _fixture(tmp_path)
    selected = documented_severity([], members, severities, fallback_severity, markers)
    assert selected == fallback_severity


def test_case_mismatched_path_falls_back_rather_than_matching(tmp_path):
    members, severities, fallback_severity, markers = _fixture(tmp_path)
    mismatched = _MEMBER_A[0].upper() + _MEMBER_A[1:]
    assert mismatched != _MEMBER_A
    selected = documented_severity([f"{mismatched}/x"], members, severities, fallback_severity, markers)
    assert selected == fallback_severity


def test_path_with_member_name_as_a_prefix_falls_back_rather_than_matching(tmp_path):
    members, severities, fallback_severity, markers = _fixture(tmp_path)
    prefixed = f"{_MEMBER_A}2"
    assert prefixed != _MEMBER_A and prefixed.startswith(_MEMBER_A)
    selected = documented_severity([f"{prefixed}/x"], members, severities, fallback_severity, markers)
    assert selected == fallback_severity


# ---- contract item 4: producer scan, not a hand-written list --------------


def discover_maturity_calibration_producers(skills_dir: Path = SKILLS_DIR) -> list[Path]:
    return sorted(
        skill_md
        for skill_md in skills_dir.glob("*/SKILL.md")
        if "<maturity-calibration>" in skill_md.read_text(encoding="utf-8")
    )


_DEFERS_TO_RENDERER_MARKER = "`scripts/maturity_bars.py` renders"


def producer_defers(path: Path) -> bool:
    """A producer defers when its text names `scripts/maturity_bars.py` as
    what renders the calibration block. This is a positive presence check
    for that one marker phrase, not a scan for the absence of restated
    rating logic: a producer that both names the renderer AND separately
    restates the attribution rule in its own prose still passes this check,
    since it names a renderer to defer to either way."""
    return _DEFERS_TO_RENDERER_MARKER in path.read_text(encoding="utf-8")


# ---- contract item 5: the ladder table is unchanged ------------------------


# ---- contract item 6: the fallback-severity pointer names a severity, ----
#      not the header line, which only ever names a level -----------------
