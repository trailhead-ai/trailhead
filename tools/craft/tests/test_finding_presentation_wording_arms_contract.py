"""Contract tests for the `finding-presentation-wording` eval's `arms/` and `fixtures/`.

Every arm is a committed copy of the concatenation of source documents, each as it stood
at a past commit. The manifest below records those sources per arm; the case's
`expected.md` cites it as the arms' provenance. That text exists only in repository
history, so nothing here recomposes an arm. These tests pin that the manifest and the
directory list the same files, and that the grader reads every reviewer label a canned
report carries as the case's pre-registered grammar classes it.

Nothing here asserts the eval's measured result; that is decided by dispatched runs.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_ritual_output_wording_arms_contract import manifest_mismatches

GIT_ROOT = Path(__file__).resolve().parents[3]

# A source is (repo-relative path, the commit the arm copied it from).
Source = tuple[str, str]

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

# The commit carrying the sharpened fix-shape contract: the seven second-round treated arms
# and the second-round calibrated consult regression arm read `council.md` from here.
FIX_SHAPE_SHARPENED_REVISION = "e568c4e037dbf1ba2d2eb3027fa3f77e03104dae"

# The commit rewording the kept consult line so a small fix carries an unsettled ask as "How a finding reads" says: the
# two reworded-consult arms read `consult/SKILL.md` from here.
CONSULT_REWORDING_REVISION = "3d423811493b6e776ef8007faee3db3f02a8f1ca"

# origin/main when the summary measurement was cut: the `summary-guard-specreview` arm reads every source here except
# `rules.md`, which it reads from `SUMMARY_RULES_REVISION`.
SUMMARY_BASE_REVISION = "d860c95a7d470be857e01f04c55c986abadab916"

# The commit "feat(craft): summaries and closing reports open with what needs the reader", which added the summary
# paragraph to `rules.md` and was reverted later on the same branch. It was a branch-local SHA at the time of the
# measurement, so after a rebase merge find it by that subject line.
SUMMARY_RULES_REVISION = "d6eb84540f8e5b959554bc1578b0ece26154a76f"

# origin/main when the plain-words sites were cut: the three plain-words baseline arms read every source here.
PLAIN_WORDS_BASELINE_REVISION = "4722bd0b45f838ecd02b17baa90931e4d47166e6"

# The commit carrying the plain-words ritual edit: the three plain-words treated arms read the two gauntlet files from
# here and every other source from `PLAIN_WORDS_BASELINE_REVISION`.
PLAIN_WORDS_EDIT_REVISION = "9bae50c390aaa2f91f1a4dd3dac2eb7ea9da30ef"

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

ARM_SOURCES["summary-guard-specreview.md"] = [
    (RULES, SUMMARY_RULES_REVISION),
    (GAUNTLET_SKILL, SUMMARY_BASE_REVISION),
    (GAUNTLET_DISPOSITIONS, SUMMARY_BASE_REVISION),
    (COUNCIL, SUMMARY_BASE_REVISION),
]

# Each fix-shape site's sources other than `council.md`, in arm order.
FS_SITE_SOURCES: dict[str, list[str]] = {
    "fsspecreview": [RULES, GAUNTLET_SKILL, GAUNTLET_DISPOSITIONS],
    "fsplan": [RULES, PLAN_SKILL],
    "fsconsult": [RULES, CONSULT_SKILL],
    "fsdriver": [RULES, DRIVE_SKILL],
    "fsreview": [RULES, REVIEW_SKILL],
    "fsbuildreview": [RULES, EXECUTE_SKILL, EXECUTE_PROCEDURE],
    "fsdrivebuild": [RULES, DRIVE_SKILL, EXECUTE_PROCEDURE],
}

# Each fix-shape round's arm prefix and the `council.md` revision its arms read; every other source of a fix-shape
# arm is at the fix-shape baseline.
FS_ROUNDS = {
    "fs-baseline": FIX_SHAPE_BASELINE_REVISION,
    "fs-treated": FIX_SHAPE_EDIT_REVISION,
    "fs-treated2": FIX_SHAPE_SHARPENED_REVISION,
}

ARM_SOURCES.update(
    {
        f"{prefix}-{site}.md": [
            *[(path, FIX_SHAPE_BASELINE_REVISION) for path in paths],
            (COUNCIL, council_revision),
        ]
        for prefix, council_revision in FS_ROUNDS.items()
        for site, paths in FS_SITE_SOURCES.items()
    }
)

# The calibrated consult regression arms: the `cal-consult` composition with `council.md` at each fix-shape edit.
ARM_SOURCES["regress-cal-consult.md"] = [
    (RULES, BASELINE_REVISION),
    (CONSULT_SKILL, CAL_REVISION),
    (COUNCIL, FIX_SHAPE_EDIT_REVISION),
]
ARM_SOURCES["regress2-cal-consult.md"] = [
    (RULES, BASELINE_REVISION),
    (CONSULT_SKILL, CAL_REVISION),
    (COUNCIL, FIX_SHAPE_SHARPENED_REVISION),
]

# Each fallback site's ritual file and the commit carrying its one point-of-use line there. A fallback arm is the
# site's second-round treated sources with only that file read from that commit.
FIX_SHAPE_FALLBACKS: dict[str, Source] = {
    "fsplan": (PLAN_SKILL, "f396eb098fd49851341f0f17272de9ac91b4af38"),
    "fsconsult": (CONSULT_SKILL, "3b50cc4ae78b43fb5494fdb2fe5d600230042284"),
    "fsdriver": (DRIVE_SKILL, "1819cbf6f7180643d607a7ad4d313389e144ec52"),
    "fsbuildreview": (EXECUTE_PROCEDURE, "f485a84a72fbbcb6fd0211075aea451f0c37a93c"),
}

ARM_SOURCES.update(
    {
        f"fs-fallback-{site}.md": [
            fallback if path == fallback[0] else (path, revision)
            for path, revision in ARM_SOURCES[f"fs-treated2-{site}.md"]
        ]
        for site, fallback in FIX_SHAPE_FALLBACKS.items()
    }
)

# Each reworded-consult arm and the arm whose sources it repeats with only `consult/SKILL.md` read from the rewording.
CONSULT_REWORDED_ARMS = {
    "fs-fallback2-fsconsult.md": "fs-fallback-fsconsult.md",
    "regress3-cal-consult.md": "regress2-cal-consult.md",
}

ARM_SOURCES.update(
    {
        arm: [
            (path, CONSULT_REWORDING_REVISION if path == CONSULT_SKILL else revision)
            for path, revision in ARM_SOURCES[predecessor]
        ]
        for arm, predecessor in CONSULT_REWORDED_ARMS.items()
    }
)


# The plain-words sites each run the spec review's baseline composition, which is the same four sources at each site.
PLAIN_WORDS_SITES = ("pwopen", "pwdetail", "pwsaved")
ARM_SOURCES.update(
    {
        f"pw-baseline-{site}.md": [
            (RULES, PLAIN_WORDS_BASELINE_REVISION),
            (GAUNTLET_SKILL, PLAIN_WORDS_BASELINE_REVISION),
            (GAUNTLET_DISPOSITIONS, PLAIN_WORDS_BASELINE_REVISION),
            (COUNCIL, PLAIN_WORDS_BASELINE_REVISION),
        ]
        for site in PLAIN_WORDS_SITES
    }
)
ARM_SOURCES.update(
    {
        f"pw-treated-{site}.md": [
            (RULES, PLAIN_WORDS_BASELINE_REVISION),
            (GAUNTLET_SKILL, PLAIN_WORDS_EDIT_REVISION),
            (GAUNTLET_DISPOSITIONS, PLAIN_WORDS_EDIT_REVISION),
            (COUNCIL, PLAIN_WORDS_BASELINE_REVISION),
        ]
        for site in PLAIN_WORDS_SITES
    }
)


def test_every_arm_file_has_a_manifest_entry_and_every_entry_a_file():
    undeclared, missing = manifest_mismatches(set(ARM_SOURCES), ARMS_DIR)
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


PW_REPORTS = "pw-reports-spec-review.md"
FS_COUNCIL_REPORTS = ["fs-reports-lenses.md", "fs-reports-consult.md", "fs-reports-spec-review.md", PW_REPORTS]
REPORT_FILES = [
    "reports-lenses.md",
    "reports-consult.md",
    "reports-spec-review.md",
    "reports-calibrated.md",
    *FS_COUNCIL_REPORTS,
]


def _grade(check: str, reports: Path, text: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(GRADER), "--check", check, "--reports", str(reports)],
        input=text.encode(),
        capture_output=True,
        timeout=60,
    )


@pytest.mark.parametrize("report_file", REPORT_FILES)
def test_the_grader_reads_every_label_a_canned_report_carries_as_the_grammar_implies(report_file: str):
    path = FIXTURES_DIR / report_file
    labels = report_labels(path.read_text(encoding="utf-8"))
    assert labels, f"{report_file} carries no `### <label>` heading"
    for label in labels:
        result = _grade("reviewer-name", path, f"{label} found nothing that blocks the work.\n")
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


@pytest.mark.parametrize("report_file", FS_COUNCIL_REPORTS)
def test_the_grader_exempts_every_tagged_fs_finding_when_quoted_and_reads_it_when_reworded(report_file: str):
    path = FIXTURES_DIR / report_file
    findings = tagged_findings(path.read_text(encoding="utf-8"))
    assert findings, f"{report_file} carries no tagged finding"
    for finding in findings:
        tag = TAG_LINE.match(finding).group(1)
        quoted = _grade("severity-name", path, finding + "\n").stdout
        assert quoted == b"severity-name: none\n", (report_file, finding, quoted)
        reworded = _grade(
            "severity-name", path, f"- [{tag}] the booking sheet lists nothing a reviewer here wrote\n"
        ).stdout
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


FIXTURE_PATH = re.compile(r"FIXTURES_DIR/([\w./-]+?)(?=[,.]?(?:\s|$))")


def prompt_fixture_paths(text: str) -> list[str]:
    return FIXTURE_PATH.findall(text)


def test_prompt_fixture_paths_reads_each_named_path():
    text = "Read:\n- FIXTURES_DIR/a/b.md, the b\n- FIXTURES_DIR/c.md.\nFIXTURES_DIR/d.py"
    assert prompt_fixture_paths(text) == ["a/b.md", "c.md", "d.py"]


@pytest.mark.parametrize("site", FS_SITE_SOURCES)
def test_each_fs_prompt_names_fixture_files_that_exist(site: str):
    prompt = (FIXTURES_DIR / f"fs-prompt-{site}.md").read_text(encoding="utf-8")
    paths = prompt_fixture_paths(prompt)
    assert paths, f"fs-prompt-{site}.md names no fixture file"
    for rel in paths:
        assert (FIXTURES_DIR / rel).is_file(), (site, rel)


PW_SPEC = FIXTURES_DIR / "pw-spec.md"
ONE_LINE = 80


def test_each_pw_prompt_names_fixture_files_that_exist():
    for site in PLAIN_WORDS_SITES:
        prompt = (FIXTURES_DIR / f"pw-prompt-{site}.md").read_text(encoding="utf-8")
        paths = prompt_fixture_paths(prompt)
        assert paths, f"pw-prompt-{site}.md names no fixture file"
        for rel in paths:
            assert (FIXTURES_DIR / rel).is_file(), (site, rel)


@pytest.mark.parametrize("site", PLAIN_WORDS_SITES)
@pytest.mark.parametrize("check", ["review-name", "outcome-word"])
def test_no_pw_prompt_names_the_review_or_shows_an_outcome(site: str, check: str):
    path = FIXTURES_DIR / f"pw-prompt-{site}.md"
    result = _grade(check, FIXTURES_DIR / PW_REPORTS, path.read_text(encoding="utf-8"))
    assert result.stdout == f"{check}: none\n".encode(), (site, result.stdout, result.stderr)


def prompt_headlines(text: str) -> list[str]:
    """The three finding headlines a pwdetail or pwsaved prompt gives as the opening summary's content."""
    found = re.search(r"decision(?:, in this order)?: (.+?)\. The operator has replied", text, re.DOTALL)
    return [part.strip() for part in found.group(1).split(";")] if found else []


def test_prompt_headlines_reads_each_headline_of_the_opening_summary():
    text = "findings that need the operator's decision: first one; second one; third. The operator has replied: x"
    assert prompt_headlines(text) == ["first one", "second one", "third"]
    assert prompt_headlines("no list here") == []


@pytest.mark.parametrize("site", ["pwdetail", "pwsaved"])
def test_each_fixture_headline_fits_one_line_with_its_id_appended(site: str):
    prompt = (FIXTURES_DIR / f"pw-prompt-{site}.md").read_text(encoding="utf-8")
    headlines = prompt_headlines(prompt)
    assert len(headlines) == 3, (site, headlines)
    for number, headline in enumerate(headlines, start=1):
        assert len(f"{headline} (C{number})") <= ONE_LINE, (site, headline)
    longest = max(headlines, key=len)
    assert len(f"{longest} (C{len(headlines)})") <= ONE_LINE


def test_the_pw_headlines_of_the_two_prompts_are_the_same_three_findings():
    texts = [(FIXTURES_DIR / f"pw-prompt-{site}.md").read_text(encoding="utf-8") for site in ("pwdetail", "pwsaved")]
    assert prompt_headlines(texts[0]) == prompt_headlines(texts[1])


def acceptance_criteria_by_section(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            sections[current] = []
        elif current is not None and re.match(r"- \*\*AC\d+\.\*\*", line):
            sections[current].append(line)
        elif current is not None and line.startswith("## "):
            current = None
        elif current is not None and sections[current] and line.startswith("  ") and line.strip():
            sections[current][-1] += " " + line.strip()
    return sections


def test_acceptance_criteria_by_section_groups_each_criterion_under_its_heading():
    text = "## A\n### One\n- **AC1.** x\n### Two\n- **AC2.** y\n- **AC3.** z\n## Next\n- **AC4.** w\n"
    assert acceptance_criteria_by_section(text) == {"One": ["- **AC1.** x"], "Two": ["- **AC2.** y", "- **AC3.** z"]}
    wrapped = "### One\n- **AC1.** x\n  and more\n- **AC2.** y\n"
    assert acceptance_criteria_by_section(wrapped) == {"One": ["- **AC1.** x and more", "- **AC2.** y"]}


def test_the_pw_spec_holds_its_criteria_under_two_sections_of_similar_length():
    sections = acceptance_criteria_by_section(PW_SPEC.read_text(encoding="utf-8"))
    assert len(sections) >= 2 and all(sections.values()), sections
    result = subprocess.run(
        [sys.executable, str(GRADER), "--check", "criterion-mention", "--criteria", str(PW_SPEC)],
        input=b"AC1", capture_output=True, timeout=60,
    )
    assert result.stdout == b"criterion-mention: bare\n", result.stderr
    words = [len(line.split()) for lines in sections.values() for line in lines]
    assert max(words) <= 1.5 * min(words), words
