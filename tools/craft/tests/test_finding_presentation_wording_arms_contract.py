"""Contract tests for the `finding-presentation-wording` eval's `arms/` and `fixtures/`.

Every arm is the concatenation of source documents, each read at a pinned git revision.
The manifest below names those sources per arm. These tests pin that each committed arm
file is exactly what its manifest composes, that composing from a different revision of a
pinned source does not give the same bytes, that the manifest and the directory list the
same files, and that the grader reads every reviewer label a canned report carries as the
case's pre-registered grammar classes it.

Nothing here asserts the eval's measured result; that is decided by dispatched runs.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest
from test_ritual_output_wording_arms_contract import (
    GIT_ROOT,
    Source,
    _earlier_revision,
    _git,
    arm_matches,
    compose,
    manifest_mismatches,
)

CASE_DIR = (
    GIT_ROOT / "tools" / "craft" / "plugins" / "craft" / "evals" / "finding-presentation-wording"
)
ARMS_DIR = CASE_DIR / "arms"
FIXTURES_DIR = CASE_DIR / "fixtures"
GRADER = GIT_ROOT / "tools" / "craft" / "plugins" / "craft" / "scripts" / "wording_grader.py"

# origin/main when the case was cut: every baseline arm reads every source here.
BASELINE_REVISION = "f68200933f69e0c00bc79634fb4758b8fa039c1c"

# The commit carrying the shared-contract edit: treated arms read `council.md` (and, at
# consult, `consult/SKILL.md`) from here and every other source from the baseline.
EDIT_REVISION = "af083a20b3098f34fb309dc967280cf8d62fe032"

# The commit before the calibration prose edit: the `precal-consult` arm reads `council.md` and
# `consult/SKILL.md` from here and every other source from the baseline.
PRECAL_REVISION = "e88e01303e744f94b60c46e10212d2adf0ca7ed0"

# The commit carrying the calibration prose edit: the `cal-consult` arm reads `council.md` and
# `consult/SKILL.md` from here and every other source from the baseline.
CAL_REVISION = "df65828beb59d1814735a6926f4982eb30a1a087"

# origin/main when the code review sites were cut: the three code review baseline arms read every source here.
CODE_REVIEW_BASELINE_REVISION = "7b5c65f9a00157e0aee884bdfb0f3fe0b99cdc29"

# The commit carrying the code review contract edit: the treated code review arms and the four
# regression arms read `council.md` (and, for the treated arms, their skill file) from here.
CODE_REVIEW_EDIT_REVISION = "5eb4cdc92f7fe4115a641f64c8f1888f5afb4d19"

# origin/main when the fix-shape sites were cut: the seven fix-shape baseline arms read every source here.
FIX_SHAPE_BASELINE_REVISION = "c3f7b21e9a797c2794218cc19ef50a9060cc0b5d"

# The commit carrying the fix-shape contract edit: the seven fix-shape treated arms and the calibrated
# consult regression arm read `council.md` from here.
FIX_SHAPE_EDIT_REVISION = "2fc63f894f8ad944e59d0b8c23af4f526c2dfe0b"

# The commit carrying the sharpened fix-shape contract (commit A2): the seven second-round treated arms
# and the second-round calibrated consult regression arm read `council.md` from here.
FIX_SHAPE_SHARPENED_REVISION = "e568c4e037dbf1ba2d2eb3027fa3f77e03104dae"

PLUGIN = "tools/craft/plugins/craft"
RULES = f"{PLUGIN}/rules.md"
COUNCIL = f"{PLUGIN}/skills/_shared/council.md"
CONSULT_SKILL = f"{PLUGIN}/skills/consult/SKILL.md"
PLAN_SKILL = f"{PLUGIN}/skills/plan/SKILL.md"
GAUNTLET_SKILL = f"{PLUGIN}/skills/gauntlet/SKILL.md"
GAUNTLET_DISPOSITIONS = f"{PLUGIN}/skills/gauntlet/dispositions.md"
DRIVE_SKILL = f"{PLUGIN}/skills/drive/SKILL.md"
REVIEW_SKILL = f"{PLUGIN}/skills/review/SKILL.md"
EXECUTE_SKILL = f"{PLUGIN}/skills/execute/SKILL.md"
EXECUTE_PROCEDURE = f"{PLUGIN}/skills/_shared/execute.md"

ARM_SOURCES: dict[str, list[Source]] = {
    "baseline-consult.md": [
        (RULES, BASELINE_REVISION),
        (CONSULT_SKILL, BASELINE_REVISION),
        (COUNCIL, BASELINE_REVISION),
    ],
    "baseline-plan.md": [
        (RULES, BASELINE_REVISION),
        (PLAN_SKILL, BASELINE_REVISION),
        (COUNCIL, BASELINE_REVISION),
    ],
    "baseline-specreview.md": [
        (RULES, BASELINE_REVISION),
        (GAUNTLET_SKILL, BASELINE_REVISION),
        (GAUNTLET_DISPOSITIONS, BASELINE_REVISION),
        (COUNCIL, BASELINE_REVISION),
    ],
    "baseline-driver.md": [
        (RULES, BASELINE_REVISION),
        (DRIVE_SKILL, BASELINE_REVISION),
        (COUNCIL, BASELINE_REVISION),
    ],
    "treated-consult.md": [
        (RULES, BASELINE_REVISION),
        (CONSULT_SKILL, EDIT_REVISION),
        (COUNCIL, EDIT_REVISION),
    ],
    "treated-plan.md": [
        (RULES, BASELINE_REVISION),
        (PLAN_SKILL, BASELINE_REVISION),
        (COUNCIL, EDIT_REVISION),
    ],
    "treated-specreview.md": [
        (RULES, BASELINE_REVISION),
        (GAUNTLET_SKILL, BASELINE_REVISION),
        (GAUNTLET_DISPOSITIONS, BASELINE_REVISION),
        (COUNCIL, EDIT_REVISION),
    ],
    "treated-driver.md": [
        (RULES, BASELINE_REVISION),
        (DRIVE_SKILL, BASELINE_REVISION),
        (COUNCIL, EDIT_REVISION),
    ],
    "precal-consult.md": [
        (RULES, BASELINE_REVISION),
        (CONSULT_SKILL, PRECAL_REVISION),
        (COUNCIL, PRECAL_REVISION),
    ],
    "cal-consult.md": [
        (RULES, BASELINE_REVISION),
        (CONSULT_SKILL, CAL_REVISION),
        (COUNCIL, CAL_REVISION),
    ],
    "baseline-review.md": [
        (RULES, CODE_REVIEW_BASELINE_REVISION),
        (REVIEW_SKILL, CODE_REVIEW_BASELINE_REVISION),
    ],
    "baseline-buildreview.md": [
        (RULES, CODE_REVIEW_BASELINE_REVISION),
        (EXECUTE_SKILL, CODE_REVIEW_BASELINE_REVISION),
        (EXECUTE_PROCEDURE, CODE_REVIEW_BASELINE_REVISION),
    ],
    "baseline-drivebuild.md": [
        (RULES, CODE_REVIEW_BASELINE_REVISION),
        (DRIVE_SKILL, CODE_REVIEW_BASELINE_REVISION),
        (EXECUTE_PROCEDURE, CODE_REVIEW_BASELINE_REVISION),
    ],
    "treated-review.md": [
        (RULES, CODE_REVIEW_BASELINE_REVISION),
        (REVIEW_SKILL, CODE_REVIEW_EDIT_REVISION),
        (COUNCIL, CODE_REVIEW_EDIT_REVISION),
    ],
    "treated-buildreview.md": [
        (RULES, CODE_REVIEW_BASELINE_REVISION),
        (EXECUTE_SKILL, CODE_REVIEW_EDIT_REVISION),
        (EXECUTE_PROCEDURE, CODE_REVIEW_BASELINE_REVISION),
        (COUNCIL, CODE_REVIEW_EDIT_REVISION),
    ],
    "treated-drivebuild.md": [
        (RULES, CODE_REVIEW_BASELINE_REVISION),
        (DRIVE_SKILL, CODE_REVIEW_EDIT_REVISION),
        (EXECUTE_PROCEDURE, CODE_REVIEW_BASELINE_REVISION),
        (COUNCIL, CODE_REVIEW_EDIT_REVISION),
    ],
    "regress-consult.md": [
        (RULES, BASELINE_REVISION),
        (CONSULT_SKILL, CAL_REVISION),
        (COUNCIL, CODE_REVIEW_EDIT_REVISION),
    ],
    "regress-plan.md": [
        (RULES, BASELINE_REVISION),
        (PLAN_SKILL, BASELINE_REVISION),
        (COUNCIL, CODE_REVIEW_EDIT_REVISION),
    ],
    "regress-specreview.md": [
        (RULES, BASELINE_REVISION),
        (GAUNTLET_SKILL, BASELINE_REVISION),
        (GAUNTLET_DISPOSITIONS, BASELINE_REVISION),
        (COUNCIL, CODE_REVIEW_EDIT_REVISION),
    ],
    "control-specreview.md": [
        (RULES, BASELINE_REVISION),
        (GAUNTLET_SKILL, BASELINE_REVISION),
        (GAUNTLET_DISPOSITIONS, BASELINE_REVISION),
        (COUNCIL, CODE_REVIEW_BASELINE_REVISION),
    ],
    "regress-driver.md": [
        (RULES, BASELINE_REVISION),
        (DRIVE_SKILL, BASELINE_REVISION),
        (COUNCIL, CODE_REVIEW_EDIT_REVISION),
    ],
}

ARM_SOURCES.update(
    {
        "fs-baseline-fsspecreview.md": [
            (RULES, FIX_SHAPE_BASELINE_REVISION),
            (GAUNTLET_SKILL, FIX_SHAPE_BASELINE_REVISION),
            (GAUNTLET_DISPOSITIONS, FIX_SHAPE_BASELINE_REVISION),
            (COUNCIL, FIX_SHAPE_BASELINE_REVISION),
        ],
        "fs-baseline-fsplan.md": [
            (RULES, FIX_SHAPE_BASELINE_REVISION),
            (PLAN_SKILL, FIX_SHAPE_BASELINE_REVISION),
            (COUNCIL, FIX_SHAPE_BASELINE_REVISION),
        ],
        "fs-baseline-fsconsult.md": [
            (RULES, FIX_SHAPE_BASELINE_REVISION),
            (CONSULT_SKILL, FIX_SHAPE_BASELINE_REVISION),
            (COUNCIL, FIX_SHAPE_BASELINE_REVISION),
        ],
        "fs-baseline-fsdriver.md": [
            (RULES, FIX_SHAPE_BASELINE_REVISION),
            (DRIVE_SKILL, FIX_SHAPE_BASELINE_REVISION),
            (COUNCIL, FIX_SHAPE_BASELINE_REVISION),
        ],
        "fs-baseline-fsreview.md": [
            (RULES, FIX_SHAPE_BASELINE_REVISION),
            (REVIEW_SKILL, FIX_SHAPE_BASELINE_REVISION),
            (COUNCIL, FIX_SHAPE_BASELINE_REVISION),
        ],
        "fs-baseline-fsbuildreview.md": [
            (RULES, FIX_SHAPE_BASELINE_REVISION),
            (EXECUTE_SKILL, FIX_SHAPE_BASELINE_REVISION),
            (EXECUTE_PROCEDURE, FIX_SHAPE_BASELINE_REVISION),
            (COUNCIL, FIX_SHAPE_BASELINE_REVISION),
        ],
        "fs-baseline-fsdrivebuild.md": [
            (RULES, FIX_SHAPE_BASELINE_REVISION),
            (DRIVE_SKILL, FIX_SHAPE_BASELINE_REVISION),
            (EXECUTE_PROCEDURE, FIX_SHAPE_BASELINE_REVISION),
            (COUNCIL, FIX_SHAPE_BASELINE_REVISION),
        ],
    }
)


FS_TREATED_SITES: dict[str, list[tuple[str, str]]] = {
    "fsspecreview": [("rules", RULES), ("skill", GAUNTLET_SKILL), ("skill", GAUNTLET_DISPOSITIONS)],
    "fsplan": [("rules", RULES), ("skill", PLAN_SKILL)],
    "fsconsult": [("rules", RULES), ("skill", CONSULT_SKILL)],
    "fsdriver": [("rules", RULES), ("skill", DRIVE_SKILL)],
    "fsreview": [("rules", RULES), ("skill", REVIEW_SKILL)],
    "fsbuildreview": [("rules", RULES), ("skill", EXECUTE_SKILL), ("skill", EXECUTE_PROCEDURE)],
    "fsdrivebuild": [("rules", RULES), ("skill", DRIVE_SKILL), ("skill", EXECUTE_PROCEDURE)],
}

ARM_SOURCES.update(
    {
        f"fs-treated-{site}.md": [
            *[(path, FIX_SHAPE_BASELINE_REVISION) for _, path in parts],
            (COUNCIL, FIX_SHAPE_EDIT_REVISION),
        ]
        for site, parts in FS_TREATED_SITES.items()
    }
)

ARM_SOURCES["regress-cal-consult.md"] = [
    (RULES, BASELINE_REVISION),
    (CONSULT_SKILL, CAL_REVISION),
    (COUNCIL, FIX_SHAPE_EDIT_REVISION),
]

ARM_SOURCES.update(
    {
        f"fs-treated2-{site}.md": [
            *[(path, FIX_SHAPE_BASELINE_REVISION) for _, path in parts],
            (COUNCIL, FIX_SHAPE_SHARPENED_REVISION),
        ]
        for site, parts in FS_TREATED_SITES.items()
    }
)

ARM_SOURCES["regress2-cal-consult.md"] = [
    (RULES, BASELINE_REVISION),
    (CONSULT_SKILL, CAL_REVISION),
    (COUNCIL, FIX_SHAPE_SHARPENED_REVISION),
]

# The commit carrying each fallback site's one point-of-use line in its own ritual file, and the file it edits.
# A fallback arm is the site's second-round treated sources with only that file read from that commit.
FIX_SHAPE_FALLBACK_REVISIONS: dict[str, str] = {
    "fsplan": "f396eb098fd49851341f0f17272de9ac91b4af38",
}
FIX_SHAPE_FALLBACK_FILES = {
    "fsplan": PLAN_SKILL,
    "fsconsult": CONSULT_SKILL,
    "fsdriver": DRIVE_SKILL,
    "fsbuildreview": EXECUTE_PROCEDURE,
}

ARM_SOURCES.update(
    {
        f"fs-fallback-{site}.md": [
            (path, revision_at_fallback if path == FIX_SHAPE_FALLBACK_FILES[site] else revision)
            for path, revision in ARM_SOURCES[f"fs-treated2-{site}.md"]
        ]
        for site, revision_at_fallback in FIX_SHAPE_FALLBACK_REVISIONS.items()
    }
)


@pytest.mark.parametrize("arm_name", sorted(ARM_SOURCES))
def test_arm_composed_from_its_manifest_equals_the_committed_file(arm_name: str):
    sources = ARM_SOURCES[arm_name]
    expected = compose(sources)
    actual = (ARMS_DIR / arm_name).read_text(encoding="utf-8")
    assert actual == expected, (
        f"{arm_name} differs from its manifest sources {sources}: "
        f"expected {len(expected)} chars, committed {len(actual)}"
    )


def _has_earlier_revision(path: str, revision: str) -> bool:
    return len(_git("log", "--format=%H", "-n", "2", revision, "--", path).split()) == 2


# A source with a single revision up to its pin (a file added shortly before the pin) has
# no earlier blob to compose from; the byte-identity test above is what constrains it.
_PINNED_SOURCES = [
    (arm, index)
    for arm, sources in sorted(ARM_SOURCES.items())
    for index, (path, revision) in enumerate(sources)
    if _has_earlier_revision(path, revision)
]


@pytest.mark.parametrize(
    "arm_name,index", _PINNED_SOURCES, ids=[f"{a}:{i}" for a, i in _PINNED_SOURCES]
)
def test_composing_one_source_from_a_different_revision_does_not_match(
    arm_name: str, index: int
):
    sources = list(ARM_SOURCES[arm_name])
    path, revision = sources[index]
    sources[index] = (path, _earlier_revision(path, revision))
    assert not arm_matches(ARMS_DIR / arm_name, sources), (
        f"{arm_name} still matches when source {path} is read from an earlier "
        "revision, so the revision pin does not constrain the arm"
    )

FIX_SHAPE_EDIT_ARMS = [f"fs-treated-{site}.md" for site in FS_TREATED_SITES] + ["regress-cal-consult.md"]


@pytest.mark.parametrize("arm_name", FIX_SHAPE_EDIT_ARMS)
def test_a_fix_shape_edit_arm_differs_from_the_same_sources_with_council_at_the_fix_shape_baseline(
    arm_name: str,
):
    sources = [
        (path, FIX_SHAPE_BASELINE_REVISION if revision == FIX_SHAPE_EDIT_REVISION else revision)
        for path, revision in ARM_SOURCES[arm_name]
    ]
    assert not arm_matches(ARMS_DIR / arm_name, sources), (
        f"{arm_name} matches its sources with council.md at the fix-shape baseline, "
        "so the contract edit is not what the arm varies"
    )


FIX_SHAPE_SHARPENED_ARMS = [f"fs-treated2-{site}.md" for site in FS_TREATED_SITES] + ["regress2-cal-consult.md"]


@pytest.mark.parametrize("arm_name", FIX_SHAPE_SHARPENED_ARMS)
def test_a_sharpened_arm_differs_from_the_same_sources_with_council_at_the_first_fix_shape_edit(
    arm_name: str,
):
    sources = [
        (path, FIX_SHAPE_EDIT_REVISION if revision == FIX_SHAPE_SHARPENED_REVISION else revision)
        for path, revision in ARM_SOURCES[arm_name]
    ]
    assert not arm_matches(ARMS_DIR / arm_name, sources), (
        f"{arm_name} matches its sources with council.md at the first fix-shape edit, "
        "so the sharpened contract is not what the arm varies"
    )


FIX_SHAPE_FALLBACK_ARMS = [f"fs-fallback-{site}.md" for site in FIX_SHAPE_FALLBACK_REVISIONS]


@pytest.mark.parametrize("arm_name", FIX_SHAPE_FALLBACK_ARMS)
def test_a_fallback_arm_differs_from_its_treated_arm_only_in_the_sites_ritual_file(arm_name: str):
    site = arm_name.removeprefix("fs-fallback-").removesuffix(".md")
    treated = (ARMS_DIR / f"fs-treated2-{site}.md").read_text(encoding="utf-8")
    fallback = (ARMS_DIR / arm_name).read_text(encoding="utf-8")
    assert fallback != treated, f"{arm_name} is byte-identical to its treated arm"
    sources = [
        (path, FIX_SHAPE_BASELINE_REVISION if path == FIX_SHAPE_FALLBACK_FILES[site] else revision)
        for path, revision in ARM_SOURCES[arm_name]
    ]
    assert compose(sources) == treated, (
        f"{arm_name} differs from its treated arm in more than {FIX_SHAPE_FALLBACK_FILES[site]}"
    )


def test_every_arm_file_has_a_manifest_entry_and_every_entry_a_file():
    undeclared, missing = manifest_mismatches(ARM_SOURCES, ARMS_DIR)
    assert not undeclared, f"arm file(s) with no manifest entry: {sorted(undeclared)}"
    assert not missing, f"manifest entries with no file on disk: {sorted(missing)}"


def report_labels(text: str) -> list[str]:
    """The `### <label>` headings of a canned reports file, outside fenced blocks."""
    labels, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("### "):
            labels.append(line[4:].strip())
    return labels


def test_report_labels_reads_headings_and_skips_fenced_ones():
    text = "# T\n### Builder\nbody\n```\n### Fenced\n```\n### Security\n"
    assert report_labels(text) == ["Builder", "Security"]


REPORT_FILES = [
    "reports-lenses.md",
    "reports-consult.md",
    "reports-spec-review.md",
    "reports-calibrated.md",
    "fs-reports-lenses.md",
    "fs-reports-consult.md",
    "fs-reports-spec-review.md",
]


@pytest.mark.parametrize("report_file", REPORT_FILES)
def test_the_grader_reads_every_label_a_canned_report_carries_as_the_grammar_implies(report_file: str):
    path = FIXTURES_DIR / report_file
    labels = report_labels(path.read_text(encoding="utf-8"))
    assert labels, f"{report_file} carries no `### <label>` heading"
    for label in labels:
        result = subprocess.run(
            [sys.executable, str(GRADER), "--check", "reviewer-name", "--reports", str(path)],
            input=f"{label} found nothing that blocks the work.\n".encode(),
            capture_output=True,
            timeout=60,
        )
        expected = "plain" if label == "Security" else "internal"
        verdict = f"reviewer-name: {expected}\n".encode()
        assert result.stdout == verdict, (report_file, label, result.stdout, result.stderr)


BARS = GIT_ROOT / "tools" / "craft" / "plugins" / "craft" / "scripts" / "maturity_bars.py"

CALIBRATED_SPEC = (
    "# Spec\n\n## Maturity\n\n- riverside-sheet: {level}\n\n## Non-Goals\n\n"
    "- Waives: backwards compatibility \u2014 the paper booking sheets are not carried over to the new page.\n"
)


def _fenced_block(text: str) -> str:
    lines = text.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("```"))
    last = next(i for i in range(first + 1, len(lines)) if lines[i].startswith("```"))
    return "\n".join(lines[first + 1 : last]) + "\n"


def _render(spec: str) -> str:
    result = subprocess.run(
        [sys.executable, str(BARS)], input=spec.encode(), capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.decode("utf-8")


def test_the_calibrated_reports_carry_the_block_the_renderer_prints_for_a_prototype_spec():
    fixture = (FIXTURES_DIR / "reports-calibrated.md").read_text(encoding="utf-8")
    rendered = _render(CALIBRATED_SPEC.format(level="prototype"))
    assert _fenced_block(fixture) == rendered
    assert _render(CALIBRATED_SPEC.format(level="production")) != rendered


FS_COUNCIL_REPORTS = ["fs-reports-lenses.md", "fs-reports-consult.md", "fs-reports-spec-review.md"]
TAG_LINE = re.compile(r"^\s*[-*+] \[(Critical|Important|Minor)\] ")


def tagged_findings(text: str) -> list[str]:
    """Each `- [Tag] ...` finding of a canned report, wrapped lines joined with one space."""
    findings: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if TAG_LINE.match(line):
            if current:
                findings.append(" ".join(current))
            current = [line.strip()]
        elif current is not None and line.strip() and not line.startswith(("#", "- ", "* ")):
            current.append(line.strip())
        else:
            if current:
                findings.append(" ".join(current))
            current = None
    if current:
        findings.append(" ".join(current))
    return findings


def test_tagged_findings_joins_wrapped_lines_and_stops_at_the_next_item():
    text = "## Findings\n- [Critical] first line\n  wrapped on\n- [Minor] second\n\n## Confidence\nhigh\n"
    assert tagged_findings(text) == ["- [Critical] first line wrapped on", "- [Minor] second"]


def _severity(args: list[str], text: str) -> bytes:
    result = subprocess.run(
        [sys.executable, str(GRADER), "--check", "severity-name", *args],
        input=text.encode(),
        capture_output=True,
        timeout=60,
    )
    return result.stdout


@pytest.mark.parametrize("report_file", FS_COUNCIL_REPORTS)
def test_the_grader_exempts_every_tagged_fs_finding_when_quoted_and_reads_it_when_reworded(report_file: str):
    path = FIXTURES_DIR / report_file
    findings = tagged_findings(path.read_text(encoding="utf-8"))
    assert findings, f"{report_file} carries no tagged finding"
    for finding in findings:
        tag = TAG_LINE.match(finding).group(1)
        quoted = _severity(["--reports", str(path)], finding + "\n")
        assert quoted == b"severity-name: none\n", (report_file, finding, quoted)
        reworded = _severity(
            ["--reports", str(path)], f"- [{tag}] the booking sheet lists nothing a reviewer here wrote\n"
        )
        assert reworded == b"severity-name: internal\n", (report_file, tag, reworded)


LOCATION = re.compile(r"^- (?P<file>[\w./-]+):(?P<line>\d+) \u2014 ", re.MULTILINE)


def cited_locations(text: str) -> list[tuple[str, int]]:
    return [(m["file"], int(m["line"])) for m in LOCATION.finditer(text)]


def test_cited_locations_reads_file_and_line_from_each_bullet():
    text = "Verdict: X\n\nCritical\n- a/b.py:13 \u2014 ask\n\nMinor\n- c.py:2 \u2014 ask\nplain 1:2 \u2014 no\n"
    assert cited_locations(text) == [("a/b.py", 13), ("c.py", 2)]


def test_every_line_the_fs_code_review_report_cites_exists_in_the_fixture_files():
    report = (FIXTURES_DIR / "fs-reports-code-review.md").read_text(encoding="utf-8")
    cited = cited_locations(report)
    assert len(cited) >= 4, cited
    for file, line in cited:
        lines = (FIXTURES_DIR / file).read_text(encoding="utf-8").splitlines()
        assert 1 <= line <= len(lines) and lines[line - 1].strip(), (file, line)


FS_SITES = ["fsspecreview", "fsplan", "fsconsult", "fsdriver", "fsreview", "fsbuildreview", "fsdrivebuild"]
FIXTURE_PATH = re.compile(r"FIXTURES_DIR/([\w./-]+?)(?=[,.]?(?:\s|$))")


def prompt_fixture_paths(text: str) -> list[str]:
    return FIXTURE_PATH.findall(text)


def test_prompt_fixture_paths_reads_each_named_path():
    text = "Read:\n- FIXTURES_DIR/a/b.md, the b\n- FIXTURES_DIR/c.md.\nFIXTURES_DIR/d.py"
    assert prompt_fixture_paths(text) == ["a/b.md", "c.md", "d.py"]


@pytest.mark.parametrize("site", FS_SITES)
def test_each_fs_prompt_names_fixture_files_that_exist(site: str):
    prompt = (FIXTURES_DIR / f"fs-prompt-{site}.md").read_text(encoding="utf-8")
    paths = prompt_fixture_paths(prompt)
    assert paths, f"fs-prompt-{site}.md names no fixture file"
    for rel in paths:
        assert (FIXTURES_DIR / rel).is_file(), (site, rel)
