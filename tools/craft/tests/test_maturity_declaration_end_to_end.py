"""End-to-end checks for the `## Project Maturity` declaration convention —
per `spec/project-maturity-levels-calibrate-craft-s-standard-of-care` AC1/AC2 —
and for the invocation-layer guard that keeps a repository with no
agent-instruction file at all off the resolver's fail-closed path.

Four properties, one per `**Test contract:**` bullet of
`task/declare-the-convention-and-classify-trailhead`:

1. The resolver, run against THIS repository's own agent-instruction file
   (`CLAUDE.md`), resolves to `production` — an end-to-end check against the
   real file, never a fixture.
2. The documented convention (`tools/craft/README.md`) names the section
   heading and the closed vocabulary exactly as the resolver itself matches
   them — cross-checked against the resolver module's own constants, so the
   documentation and the parser cannot drift apart silently.
3. `trailhead/compose.py`'s `compose_plan` — never the live composed tree on
   one machine — includes both the resolver script and the edited brainstorm
   skill document when run over this repository's own capabilities.toml.
4. Brainstorm's framing step (`SKILL.md` step 1) checks the agent-instruction
   file exists before invoking the resolver, and on absence takes the
   absence path directly, stating `production`, without invoking the
   resolver at all — anchored against the resolver's own real fail-closed
   behavior on empty stdin (what piping a nonexistent file would produce).
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.parent
CRAFT_TOOL_ROOT = REPO_ROOT / "tools" / "craft"
RESOLVER = CRAFT_TOOL_ROOT / "plugins" / "craft" / "scripts" / "maturity_resolve.py"
BRAINSTORM_SKILL = CRAFT_TOOL_ROOT / "plugins" / "craft" / "skills" / "brainstorm" / "SKILL.md"
README = CRAFT_TOOL_ROOT / "README.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

sys.path.insert(0, str(REPO_ROOT))

from trailhead.capabilities import load_manifest  # noqa: E402
from trailhead.compose import compose_plan  # noqa: E402


def _resolver_module():
    spec = importlib.util.spec_from_file_location("maturity_resolve", RESOLVER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_resolver(stdin_bytes: bytes) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RESOLVER)],
        input=stdin_bytes,
        capture_output=True,
    )


def _skill_text() -> str:
    return BRAINSTORM_SKILL.read_text(encoding="utf-8")


def _frame_step() -> str:
    """Step 1 ("Frame")'s body, up to the next `### N.` heading."""
    text = _skill_text()
    start = text.index("### 1. Frame")
    rest = text[start + len("### 1. Frame"):]
    end = re.search(r"\n### \d+\.", rest)
    return rest[: end.start()] if end else rest


# ---- 1. resolving against this repository's own agent-instruction file ----


def test_resolver_against_this_repositorys_own_claude_md_resolves_to_production():
    stdin_bytes = CLAUDE_MD.read_bytes()
    result = _run_resolver(stdin_bytes)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.decode("utf-8").splitlines()
    assert "level: production" in lines, (
        f"resolving this repository's real CLAUDE.md must yield production, got: {lines}"
    )
    # trailhead DECLARES production (Delivers: "trailhead itself declares
    # production") rather than merely defaulting there via an absent section —
    # a fixture-free proof that the declaration was actually authored, not
    # just that the resolver's default happens to agree with it.
    assert "reason: declared" in lines, (
        "trailhead's own CLAUDE.md must carry an explicit `## Project Maturity` "
        f"declaration, not rely on the section-absent default; got: {lines}"
    )


# ---- 2. documented convention matches the resolver's own constants ----


def _outside_fence_heading_line_indices(text: str) -> list[int]:
    """Line numbers (0-based) of `^## ` headings that are not inside a fenced
    code block — a naive triple-backtick fence tracker, sufficient for a
    README with no nested fences."""
    lines = text.splitlines()
    in_fence = False
    heading_lines: list[int] = []
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and re.match(r"^##\s+\S", line):
            heading_lines.append(i)
    return heading_lines


def test_readme_documents_the_heading_and_vocabulary_the_resolver_itself_matches():
    module = _resolver_module()
    readme_text = README.read_text(encoding="utf-8")
    lines = readme_text.splitlines()

    assert re.search(r"^##\s+Project Maturity\s*$", readme_text, re.MULTILINE), (
        "tools/craft/README.md must document the exact `## Project Maturity` "
        "heading the resolver's own _HEADING_RE matches"
    )

    heading_lines = _outside_fence_heading_line_indices(readme_text)
    own_heading_positions = [
        i for i in heading_lines if re.match(r"^##\s+Project Maturity\s*$", lines[i])
    ]
    assert own_heading_positions, (
        "the real (non-fenced) `## Project Maturity` heading must exist in README.md"
    )
    start = own_heading_positions[0]
    later_headings = [i for i in heading_lines if i > start]
    end = later_headings[0] if later_headings else len(lines)
    # Scope to this convention's own documented section (real headings only,
    # skipping any occurrence inside a fenced example), so a level word
    # appearing elsewhere in the file, or inside the illustrative code fence,
    # can never substitute for a real vocabulary mention in prose.
    section_text = "\n".join(lines[start:end])
    for level in module._LEVELS:
        assert level in section_text, (
            f"tools/craft/README.md's Project Maturity section must name the "
            f"resolver's own vocabulary word {level!r}"
        )


# ---- 3. the compose plan carries the resolver script and the edited skill ----


def test_compose_plan_includes_resolver_script_and_brainstorm_skill():
    manifest = load_manifest(CRAFT_TOOL_ROOT / "capabilities.toml")
    plan = compose_plan(manifest, {}, {"brainstorm": None}, Path("/does/not/matter"))

    srcs = [op.src for op in plan.ops]
    scripts_dir = (CRAFT_TOOL_ROOT / "plugins" / "craft" / "scripts").resolve()
    assert scripts_dir in srcs, (
        "compose_plan's always-on `base` set must include the scripts directory "
        f"carrying maturity_resolve.py; got srcs: {srcs}"
    )
    assert RESOLVER.resolve().is_relative_to(scripts_dir), (
        "fixture assumption: maturity_resolve.py lives directly under the "
        "scripts base directory"
    )

    brainstorm_dir = (CRAFT_TOOL_ROOT / "plugins" / "craft" / "skills" / "brainstorm").resolve()
    assert brainstorm_dir in srcs, (
        "compose_plan for the selected brainstorm skill must include the "
        f"edited brainstorm skill directory; got srcs: {srcs}"
    )


# ---- 4. brainstorm's framing step guards the resolver against an absent file ----


def test_empty_stdin_is_the_resolvers_fail_closed_path_not_the_absence_path():
    """Anchor: piping a nonexistent file yields empty stdin, which the
    resolver treats as fail-closed (exit 2, no `level:` line) — never as an
    absence resolving to production. This is why the guard must live at the
    invocation layer, never inside the resolver."""
    result = _run_resolver(b"")
    assert result.returncode == 2
    assert b"level:" not in result.stdout


def test_frame_step_checks_file_existence_before_invoking_resolver():
    frame_step = _frame_step()
    assert re.search(r"(does\s+not\s+exist|no.{0,20}agent-instruction file)", frame_step, re.IGNORECASE), (
        "step 1 must check whether the agent-instruction file exists before "
        "invoking the resolver"
    )


def test_frame_step_takes_absence_path_directly_stating_production_without_invoking_resolver():
    frame_step = _frame_step()
    # Scoped to the file-existence guard's own clause (terminated by '.'), so a
    # `production` mention belonging to the resolver's own section-absent
    # clause elsewhere in step 1 can never satisfy this — a decoy an unscoped
    # search across the whole step would miss.
    existence_clause_match = re.search(
        r"(does\s+not\s+exist|no.{0,20}agent-instruction file)[^.]*\.", frame_step, re.IGNORECASE
    )
    assert existence_clause_match, "step 1 must have a file-existence guard clause terminated by '.'"
    clause = existence_clause_match.group(0)
    assert "production" in clause, (
        f"the file-existence guard clause must state production directly: {clause!r}"
    )
    assert re.search(r"without invoking|never invok|skip.{0,20}resolver", clause, re.IGNORECASE), (
        f"the file-existence guard clause must state the resolver is not invoked: {clause!r}"
    )
