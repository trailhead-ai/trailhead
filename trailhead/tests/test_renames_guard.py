"""Slice 7 guard tests: UX renames sweep — finish/tend/circle/loremaster/scout/trailblazer.

TDD contract (R-6 + Slice 7 test contract):
  1. Suite-wide grep guard — zero occurrences of the old identifiers in the live
     source tree under tools/{lore,forge}/ (excludes __pycache__, .pytest_cache,
     .git; preserves forge's unrelated uses of "review" as a capability name).
  2. load_manifest(validate=True) succeeds for both lore and forge post-rename.
  3. R-6 resolve-all-capabilities oracle — compose_plan for every declared
     capability across lore + forge; every CopyOp.src exists on disk.
  4. New agent names appear in forge circle/execute compose output.
  5. lore finish skill dir exists and lore tend skill dir exists.
  6. Agent frontmatter name: fields match the new filenames.

Write BEFORE the renames — these tests must fail RED first, then green after.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest
from trailhead.compose import compose_plan

_REPO_ROOT = Path(__file__).parent.parent.parent
_LORE_MANIFEST = _REPO_ROOT / "tools" / "lore" / "capabilities.toml"
_FORGE_MANIFEST = _REPO_ROOT / "tools" / "forge" / "capabilities.toml"

_LORE_PLUGIN_ROOT = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore"
_FORGE_PLUGIN_ROOT = _REPO_ROOT / "tools" / "forge" / "plugins" / "forge"

# Directories to scan for stale identifiers
_SCAN_ROOTS = [
    _REPO_ROOT / "tools" / "lore",
    _REPO_ROOT / "tools" / "forge",
]

# Suffixes to scan
_SCAN_SUFFIXES = {".md", ".py", ".toml"}

# Directories to exclude from scan
_EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".git"}


def _collect_files() -> list[Path]:
    """Collect all relevant source files in the scan roots."""
    files: list[Path] = []
    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if f.suffix not in _SCAN_SUFFIXES:
                continue
            # Exclude blocked dirs
            if any(part in _EXCLUDE_DIRS for part in f.parts):
                continue
            if f.is_file():
                files.append(f)
    return files


def _grep_files(pattern: str, files: list[Path]) -> list[tuple[Path, int, str]]:
    """Return list of (file, lineno, line) for files containing pattern."""
    hits: list[tuple[Path, int, str]] = []
    rx = re.compile(pattern)
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append((f, i, line.strip()))
    return hits


# ---------------------------------------------------------------------------
# 1. Suite-wide grep guard
# ---------------------------------------------------------------------------


class TestGrepGuard:
    """Zero occurrences of old identifiers in the live source tree."""

    def _assert_no_hits(self, pattern: str, description: str, *, exclude_pattern: str | None = None) -> None:
        files = _collect_files()
        hits = _grep_files(pattern, files)
        if exclude_pattern:
            excl_rx = re.compile(exclude_pattern)
            hits = [(f, ln, line) for f, ln, line in hits if not excl_rx.search(line)]
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of {description} — must be zero after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_council_dash_references(self):
        """council-advocate/builder/reliability/security must not appear in tools/ source after rename."""
        # Grep for the specific old agent-name prefixes (not /council-session which is a lore vault type)
        files = _collect_files()
        hits = _grep_files(r"council-(advocate|builder|reliability|security)", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of old council-* agent names — must be zero after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_circle_dash_agent_references(self):
        """circle-advocate/builder/reliability/security must not appear in tools/ source after rename to bare names.

        Token-scoped to the four old circle agent stems so it does NOT flag:
        - the `circle` *capability* name (capabilities.toml, landing_claims.toml)
        - `circle-*.md` generic glob descriptions in test comments
        - the `council-session` lore vault type (already covered by its own forbid)
        """
        files = _collect_files()
        hits = _grep_files(r"circle-(advocate|builder|reliability|security)", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of old circle-* agent names — must be zero after rename to bare names:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_sdd_dash_references(self):
        """sdd- prefix must not appear in tools/ source after rename to scout/trailblazer."""
        files = _collect_files()
        hits = _grep_files(r"sdd-", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'sdd-' — must be zero after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_lore_librarian_references(self):
        """lore-librarian must not appear in tools/ source after rename to loremaster."""
        files = _collect_files()
        hits = _grep_files(r"lore-librarian", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'lore-librarian' — must be zero after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_skills_finished_references(self):
        """skills/finished must not appear in capabilities.toml after rename to skills/finish."""
        files = _collect_files()
        hits = _grep_files(r"skills/finished", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'skills/finished' — must be zero after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_skills_review_lore_reference_in_manifest(self):
        """lore capabilities.toml must not reference skills/review after rename to skills/tend."""
        text = _LORE_MANIFEST.read_text()
        assert "skills/review" not in text, (
            "lore/capabilities.toml still references 'skills/review' — "
            "update to 'skills/tend' after renaming the skill directory."
        )

    def test_no_skills_review_lore_reference_in_skill_files(self):
        """The old skills/review directory must not exist under lore plugins."""
        old_dir = _LORE_PLUGIN_ROOT / "skills" / "review"
        assert not old_dir.exists(), (
            f"Old skills/review directory still exists at {old_dir} — "
            "rename it to skills/tend."
        )

    def test_no_council_review_prose(self):
        """'Council Review' Title-Case prose must not appear in SKILL.md bodies after rename to 'Circle Review'.

        Excludes:
        - lines that reference the 'code-reviewer' agent or 'code review' (not the circle panel)
        - the 'council-session' lore vault type (a different concept)
        - the experiments/ corpus (frozen)
        """
        files = [
            f for f in _collect_files()
            if "experiments" not in f.parts and f.suffix == ".md"
        ]
        hits = _grep_files(r"Council Review", files)
        # No exclusions needed: 'Council Review' with both words Title-Cased is
        # exclusively the old circle-review panel label.
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'Council Review' — must be 'Circle Review' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_assumption_prover_title_case_prose(self):
        """'Assumption-Prover' Title-Case prose must not appear in SKILL.md bodies after rename to 'scout'.

        Excludes the experiments/ corpus (frozen).
        The lowercase 'assumption-prover' identifier guard is already in test_no_sdd_dash_references
        (via sdd-); this covers the prose Title-Case form which the identifier guard misses.
        """
        files = [
            f for f in _collect_files()
            if "experiments" not in f.parts and f.suffix == ".md"
        ]
        hits = _grep_files(r"Assumption-Prover", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'Assumption-Prover' Title-Case — must be 'scout' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_implementer_role_prose_in_sdd_skill(self):
        """Role-sense 'Implementer' (Title-Case section headers + 'Implementer returns' callouts)
        must not appear in the SDD SKILL.md after rename to 'trailblazer'.

        Scoped to the SDD skill file only — the most tightly constrained signal for the role label.
        Generic uses of lowercase 'implementer' elsewhere (researcher.md, troubleshooter.md) are
        NOT renamed and are not checked here.
        """
        sdd_skill = _FORGE_PLUGIN_ROOT / "skills" / "subagent-driven-development" / "SKILL.md"
        if not sdd_skill.exists():
            pytest.skip(f"SDD skill not found at {sdd_skill}")
        text = sdd_skill.read_text()
        # Check for the section-header form: "## Handling Implementer Status"
        assert "## Handling Implementer Status" not in text, (
            "SDD SKILL.md still has '## Handling Implementer Status' — "
            "rename section header to '## Handling Trailblazer Status'"
        )
        # Check for the "Implementer returns" callout form (Title-Case role label)
        assert "Implementer returns" not in text, (
            "SDD SKILL.md still has 'Implementer returns' callouts — "
            "rename to 'Trailblazer returns' after rename"
        )


# ---------------------------------------------------------------------------
# 2. load_manifest(validate=True) post-rename
# ---------------------------------------------------------------------------


class TestManifestValidation:
    """Manifests must load cleanly — disk paths must match manifest entries."""

    def test_lore_manifest_validates_after_rename(self):
        """lore capabilities.toml must load without error post-rename."""
        m = load_manifest(_LORE_MANIFEST)
        assert m.tool_name == "lore"

    def test_forge_manifest_validates_after_rename(self):
        """forge capabilities.toml must load without error post-rename."""
        m = load_manifest(_FORGE_MANIFEST)
        assert m.tool_name == "forge"

    def test_lore_recall_references_tend_skill(self):
        """lore recall capability must reference skills/tend (not skills/review)."""
        m = load_manifest(_LORE_MANIFEST)
        cap = m.capabilities["recall"]
        assert "skills/tend" in cap["skills"], (
            f"lore recall must reference 'skills/tend'; got {cap['skills']}"
        )
        assert "skills/review" not in cap["skills"], (
            "lore recall still references 'skills/review' — update to 'skills/tend'"
        )

    def test_lore_recall_references_loremaster_agent(self):
        """lore recall capability must reference agents/loremaster.md."""
        m = load_manifest(_LORE_MANIFEST)
        cap = m.capabilities["recall"]
        assert "agents/loremaster.md" in cap["agents"], (
            f"lore recall must reference 'agents/loremaster.md'; got {cap['agents']}"
        )
        assert "agents/lore-librarian.md" not in cap["agents"], (
            "lore recall still references 'agents/lore-librarian.md' — update to 'agents/loremaster.md'"
        )

    def test_lore_sessions_references_finish_skill(self):
        """lore sessions capability must reference skills/finish (not skills/finished)."""
        m = load_manifest(_LORE_MANIFEST)
        cap = m.capabilities["sessions"]
        assert "skills/finish" in cap["skills"], (
            f"lore sessions must reference 'skills/finish'; got {cap['skills']}"
        )
        assert "skills/finished" not in cap["skills"], (
            "lore sessions still references 'skills/finished' — update to 'skills/finish'"
        )

    def test_forge_execute_references_scout_and_trailblazer(self):
        """forge execute capability must reference scout.md and trailblazer.md."""
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["execute"]
        assert "agents/scout.md" in cap["agents"], (
            f"forge execute must reference 'agents/scout.md'; got {cap['agents']}"
        )
        assert "agents/trailblazer.md" in cap["agents"], (
            f"forge execute must reference 'agents/trailblazer.md'; got {cap['agents']}"
        )
        assert "agents/sdd-assumption-prover.md" not in cap["agents"], (
            "forge execute still references old 'sdd-assumption-prover.md'"
        )
        assert "agents/sdd-implementer.md" not in cap["agents"], (
            "forge execute still references old 'sdd-implementer.md'"
        )

    def test_forge_circle_references_circle_agents(self):
        """forge circle capability must reference the bare-named circle agents (advocate/builder/breaker/attacker)."""
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["circle"]
        agents = cap["agents"]
        assert "agents/advocate.md" in agents, (
            f"forge circle must reference 'agents/advocate.md'; got {agents}"
        )
        assert "agents/builder.md" in agents, (
            f"forge circle must reference 'agents/builder.md'; got {agents}"
        )
        assert "agents/breaker.md" in agents, (
            f"forge circle must reference 'agents/breaker.md'; got {agents}"
        )
        assert "agents/attacker.md" in agents, (
            f"forge circle must reference 'agents/attacker.md'; got {agents}"
        )
        for agent in agents:
            assert "council-" not in agent, (
                f"forge circle still references old council- agent: {agent}"
            )
            assert "circle-" not in agent, (
                f"forge circle still references old circle- agent: {agent}"
            )


def _read_agent_text(agent_file: Path) -> str:
    """Return the agent file's full text, lowercased.

    Reads the WHOLE file (not just the frontmatter `description:` block) — the
    differentiation asserts below substring-match against the entire agent prose,
    so the name must not imply a narrower scope (a latent false-green otherwise).
    """
    text = agent_file.read_text()
    return text.lower()


class TestCircleAgentStandaloneDescriptions:
    """Renamed circle agents must drop the 'use only when ... circle review step' gate
    and carry a differentiating standalone 'use when' phrase so natural-language dispatch
    routes them apart from the overlapping troubleshooter / security-auditor agents.
    """

    def test_circle_agents_drop_circle_only_gate(self):
        """No renamed circle agent may keep the 'use only when invoked' standalone-blocking clause."""
        for stem in ("advocate", "builder", "breaker", "attacker"):
            path = _FORGE_PLUGIN_ROOT / "agents" / f"{stem}.md"
            assert path.exists(), f"renamed circle agent not found: {path}"
            desc = _read_agent_text(path)
            assert "use only when invoked by a planning skill" not in desc, (
                f"{stem}.md still gates itself to the planning circle step — "
                "drop the 'Use only when invoked by a planning skill's circle review step' clause."
            )

    def test_breaker_differentiates_from_troubleshooter(self):
        """breaker's description must carry a 'use when' phrase absent from troubleshooter's.

        breaker probes a design for failure modes / edge cases / recovery *before building*;
        troubleshooter diagnoses root cause of an *existing* failure. The differentiating
        phrase must live in breaker and not in troubleshooter.
        """
        breaker = _read_agent_text(_FORGE_PLUGIN_ROOT / "agents" / "breaker.md")
        troubleshooter = _read_agent_text(_FORGE_PLUGIN_ROOT / "agents" / "troubleshooter.md")
        phrase = "before building"
        assert phrase in breaker, (
            f"breaker.md description must carry the differentiating phrase {phrase!r}"
        )
        assert phrase not in troubleshooter, (
            f"differentiating phrase {phrase!r} must be absent from troubleshooter.md "
            "so natural-language dispatch routes breaker vs troubleshooter correctly"
        )

    def test_attacker_differentiates_from_security_auditor(self):
        """attacker's description must carry a 'use when' phrase absent from security-auditor's.

        attacker red-teams a *design or change* for the threat model up front; security-auditor
        audits an existing diff/PR/module against OWASP. The differentiating phrase must live in
        attacker and not in security-auditor.
        """
        attacker = _read_agent_text(_FORGE_PLUGIN_ROOT / "agents" / "attacker.md")
        auditor = _read_agent_text(_FORGE_PLUGIN_ROOT / "agents" / "security-auditor.md")
        phrase = "red-team a design"
        assert phrase in attacker, (
            f"attacker.md description must carry the differentiating phrase {phrase!r}"
        )
        assert phrase not in auditor, (
            f"differentiating phrase {phrase!r} must be absent from security-auditor.md "
            "so natural-language dispatch routes attacker vs security-auditor correctly"
        )


# ---------------------------------------------------------------------------
# 3. R-6 resolve-all-capabilities oracle
# ---------------------------------------------------------------------------


class TestResolveAllCapabilitiesOracle:
    """After rename, compose_plan for every capability must have all CopyOp.src on disk."""

    def _check_manifest(self, manifest_path: Path, tmp_path: Path) -> list[str]:
        """Return list of missing src paths for all capabilities in manifest."""
        m = load_manifest(manifest_path)
        missing: list[str] = []
        for cap_name in m.capabilities:
            dest = tmp_path / cap_name
            plan = compose_plan(m, {cap_name}, dest)
            for op in plan.ops:
                if not op.src.exists():
                    missing.append(
                        f"  {manifest_path.name}::{cap_name}: {op.src} does not exist"
                    )
        return missing

    def test_lore_all_capabilities_resolve_to_existing_src(self, tmp_path):
        """Every CopyOp.src for every lore capability must exist on disk."""
        missing = self._check_manifest(_LORE_MANIFEST, tmp_path)
        assert not missing, (
            "R-6: lore capability compose_plan produced CopyOps with missing src:\n"
            + "\n".join(missing)
        )

    def test_forge_all_capabilities_resolve_to_existing_src(self, tmp_path):
        """Every CopyOp.src for every forge capability must exist on disk."""
        missing = self._check_manifest(_FORGE_MANIFEST, tmp_path)
        assert not missing, (
            "R-6: forge capability compose_plan produced CopyOps with missing src:\n"
            + "\n".join(missing)
        )


# ---------------------------------------------------------------------------
# 4. New agent names appear in compose output for circle/execute
# ---------------------------------------------------------------------------


class TestNewAgentNamesInCompose:
    """compose_plan for forge circle/execute resolves new agent names."""

    def test_forge_circle_compose_includes_circle_agents(self, tmp_path):
        """forge circle compose includes the bare-named circle agent CopyOps."""
        m = load_manifest(_FORGE_MANIFEST)
        plan = compose_plan(m, {"circle"}, tmp_path / "dest")
        agent_srcs = {op.src.name for op in plan.ops if op.src.is_file()}
        for name in ("advocate.md", "builder.md", "breaker.md", "attacker.md"):
            assert name in agent_srcs, (
                f"compose_plan for forge 'circle' must include {name}; got {agent_srcs}"
            )

    def test_forge_execute_compose_includes_scout_and_trailblazer(self, tmp_path):
        """forge execute compose includes scout.md and trailblazer.md CopyOps."""
        m = load_manifest(_FORGE_MANIFEST)
        plan = compose_plan(m, {"execute"}, tmp_path / "dest")
        agent_srcs = {op.src.name for op in plan.ops if op.src.is_file()}
        assert "scout.md" in agent_srcs, (
            f"compose_plan for forge 'execute' must include scout.md; got {agent_srcs}"
        )
        assert "trailblazer.md" in agent_srcs, (
            f"compose_plan for forge 'execute' must include trailblazer.md; got {agent_srcs}"
        )

    def test_lore_recall_compose_includes_loremaster(self, tmp_path):
        """lore recall compose includes loremaster.md CopyOp."""
        m = load_manifest(_LORE_MANIFEST)
        plan = compose_plan(m, {"recall"}, tmp_path / "dest")
        agent_srcs = {op.src.name for op in plan.ops if op.src.is_file()}
        assert "loremaster.md" in agent_srcs, (
            f"compose_plan for lore 'recall' must include loremaster.md; got {agent_srcs}"
        )


# ---------------------------------------------------------------------------
# 5. Skill directory existence (lore finish + tend)
# ---------------------------------------------------------------------------


class TestSkillDirsExist:
    """Renamed skill directories must exist under the plugin root."""

    def test_lore_finish_skill_dir_exists(self):
        """skills/finish/ must exist after rename from skills/finished/."""
        finish_dir = _LORE_PLUGIN_ROOT / "skills" / "finish"
        assert finish_dir.exists() and finish_dir.is_dir(), (
            f"skills/finish/ not found at {finish_dir} — rename from skills/finished/"
        )
        assert (finish_dir / "SKILL.md").exists(), (
            "skills/finish/SKILL.md missing after rename"
        )

    def test_lore_finished_dir_gone(self):
        """skills/finished/ must not exist after rename."""
        old_dir = _LORE_PLUGIN_ROOT / "skills" / "finished"
        assert not old_dir.exists(), (
            f"Old skills/finished/ still exists at {old_dir} — rename to skills/finish/"
        )

    def test_lore_tend_skill_dir_exists(self):
        """skills/tend/ must exist after rename from skills/review/."""
        tend_dir = _LORE_PLUGIN_ROOT / "skills" / "tend"
        assert tend_dir.exists() and tend_dir.is_dir(), (
            f"skills/tend/ not found at {tend_dir} — rename from skills/review/"
        )
        assert (tend_dir / "SKILL.md").exists(), (
            "skills/tend/SKILL.md missing after rename"
        )


# ---------------------------------------------------------------------------
# 6. Agent frontmatter name: fields match new filenames
# ---------------------------------------------------------------------------


def _parse_frontmatter_name(agent_file: Path) -> str | None:
    """Extract the name: field from YAML frontmatter."""
    text = agent_file.read_text()
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    frontmatter = text[3:end]
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.split(":", 1)[1].strip()
    return None


class TestFrontmatterNameMatchesFilename:
    """Agent frontmatter name: field must match the new filename stem."""

    @pytest.mark.parametrize("stem,path", [
        ("loremaster", _LORE_PLUGIN_ROOT / "agents" / "loremaster.md"),
        ("advocate", _FORGE_PLUGIN_ROOT / "agents" / "advocate.md"),
        ("builder", _FORGE_PLUGIN_ROOT / "agents" / "builder.md"),
        ("breaker", _FORGE_PLUGIN_ROOT / "agents" / "breaker.md"),
        ("attacker", _FORGE_PLUGIN_ROOT / "agents" / "attacker.md"),
        ("scout", _FORGE_PLUGIN_ROOT / "agents" / "scout.md"),
        ("trailblazer", _FORGE_PLUGIN_ROOT / "agents" / "trailblazer.md"),
    ])
    def test_agent_frontmatter_name_matches_filename(self, stem: str, path: Path):
        """Agent frontmatter name: must match the new filename stem."""
        assert path.exists(), f"Agent file not found: {path}"
        name = _parse_frontmatter_name(path)
        assert name == stem, (
            f"{path.name} frontmatter name: is {name!r}, expected {stem!r}. "
            "Frontmatter name must match the filename stem after rename."
        )


# ---------------------------------------------------------------------------
# 7. Slice 2 — forge:consult skill + single-source circle membership
# ---------------------------------------------------------------------------

_CONSULT_SKILL = _FORGE_PLUGIN_ROOT / "skills" / "consult" / "SKILL.md"
_CIRCLE_INCLUDE = _FORGE_PLUGIN_ROOT / "skills" / "_shared" / "circle.md"
_PLANNING_SKILL = _FORGE_PLUGIN_ROOT / "skills" / "planning" / "SKILL.md"

# The four circle agent stems the membership single-source-of-truth must name,
# each resolving to agents/<stem>.md.
_CIRCLE_STEMS = ("advocate", "builder", "breaker", "attacker")


def _has_registrable_frontmatter(skill_md: Path) -> bool:
    """Return True if SKILL.md opens with frontmatter carrying non-empty name + description."""
    text = skill_md.read_text()
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---", 3)
    if end < 0:
        return False
    frontmatter = text[3:end]

    def _has(field: str) -> bool:
        return any(
            ln.strip().startswith(f"{field}:") and ln.split(":", 1)[1].strip()
            for ln in frontmatter.splitlines()
        )

    return _has("name") and _has("description")


class TestConsultSkillAndSharedCircle:
    """Slice 2: the forge:consult skill and the single-source circle membership include."""

    def test_consult_skill_dir_exists_with_skill_md(self):
        """skills/consult/ must exist with a SKILL.md (new standalone-invocable circle skill)."""
        assert _CONSULT_SKILL.exists(), (
            f"skills/consult/SKILL.md not found at {_CONSULT_SKILL} — "
            "create the forge:consult skill that convenes the circle panel."
        )

    def test_consult_skill_is_registrable(self):
        """skills/consult/SKILL.md must carry non-empty name: + description: frontmatter.

        Without registrable frontmatter Claude Code will not register it as a
        /forge:consult command (same invariant as test_skills_registrable).
        """
        assert _CONSULT_SKILL.exists(), f"skills/consult/SKILL.md not found at {_CONSULT_SKILL}"
        assert _has_registrable_frontmatter(_CONSULT_SKILL), (
            "skills/consult/SKILL.md must open with frontmatter carrying a non-empty "
            "`name:` and `description:` or Claude Code will not register /forge:consult"
        )

    def test_consult_frontmatter_name_is_consult(self):
        """skills/consult/SKILL.md frontmatter name: must be 'consult'."""
        name = _parse_frontmatter_name(_CONSULT_SKILL)
        assert name == "consult", (
            f"skills/consult/SKILL.md frontmatter name: is {name!r}, expected 'consult'"
        )

    def test_circle_include_exists(self):
        """skills/_shared/circle.md must exist as the single-source circle membership include."""
        assert _CIRCLE_INCLUDE.exists(), (
            f"skills/_shared/circle.md not found at {_CIRCLE_INCLUDE} — "
            "create the shared four-agent membership include."
        )

    def test_circle_include_names_all_four_agents(self):
        """The shared include must name all four circle agent stems."""
        assert _CIRCLE_INCLUDE.exists(), f"skills/_shared/circle.md not found at {_CIRCLE_INCLUDE}"
        text = _CIRCLE_INCLUDE.read_text()
        missing = [stem for stem in _CIRCLE_STEMS if stem not in text]
        assert not missing, (
            f"skills/_shared/circle.md must name all four circle agents; missing: {missing}"
        )

    def test_circle_include_stems_resolve_to_agent_files(self):
        """C1: each of the four stems named in the include resolves to agents/<stem>.md.

        This is the anti-drift assertion — the single-source membership cannot silently
        diverge from the renamed agent files that planning + consult both dispatch off it.
        Parses the include for the stems it actually names, then asserts each is an
        existing agent file by exact name.
        """
        assert _CIRCLE_INCLUDE.exists(), f"skills/_shared/circle.md not found at {_CIRCLE_INCLUDE}"
        text = _CIRCLE_INCLUDE.read_text()
        for stem in _CIRCLE_STEMS:
            assert stem in text, (
                f"circle.md must name the {stem!r} agent (single source of truth)"
            )
            agent_file = _FORGE_PLUGIN_ROOT / "agents" / f"{stem}.md"
            assert agent_file.exists(), (
                f"circle.md names {stem!r} but {agent_file} does not exist — "
                "the membership include drifted from the renamed agent files."
            )

    def test_consult_references_shared_circle_include(self):
        """consult must read membership from the shared include (not hardcode its own list)."""
        assert _CONSULT_SKILL.exists(), f"skills/consult/SKILL.md not found at {_CONSULT_SKILL}"
        text = _CONSULT_SKILL.read_text()
        assert "_shared/circle.md" in text, (
            "skills/consult/SKILL.md must reference the shared '_shared/circle.md' include "
            "as the single source of circle membership."
        )

    def test_planning_references_shared_circle_include(self):
        """planning's Circle Review step must read membership from the shared include.

        Planning must NOT call consult (the unreliable skill->skill chain) — it dispatches
        the circle directly off the shared list, same as consult.
        """
        assert _PLANNING_SKILL.exists(), f"planning/SKILL.md not found at {_PLANNING_SKILL}"
        text = _PLANNING_SKILL.read_text()
        assert "_shared/circle.md" in text, (
            "planning/SKILL.md Circle Review step must reference the shared "
            "'_shared/circle.md' include rather than hardcoding the membership."
        )

    def test_planning_dispatches_circle_directly_not_via_consult(self):
        """planning must dispatch the four agents directly, not delegate to the consult skill.

        The robust invariant is the presence of the direct-dispatch instruction (parallel
        Agent calls to the four members), NOT the absence of the string 'consult' — planning
        legitimately *mentions* consult to explain it must not delegate to it. We assert the
        direct-dispatch evidence (each member named for an Agent dispatch) so a future rewrite
        that swaps direct dispatch for a `/forge:consult` call would drop these and fail.
        """
        assert _PLANNING_SKILL.exists(), f"planning/SKILL.md not found at {_PLANNING_SKILL}"
        text = _PLANNING_SKILL.read_text()
        assert "Agent` tool calls" in text, (
            "planning/SKILL.md must instruct direct parallel `Agent` tool calls to the circle "
            "members — not delegate the panel to the consult skill."
        )
        for stem in _CIRCLE_STEMS:
            assert stem in text, (
                f"planning/SKILL.md must still name {stem!r} for direct circle dispatch"
            )

    def test_forge_circle_capability_includes_consult_skill(self):
        """forge circle capability must list skills/consult."""
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["circle"]
        assert "skills/consult" in cap["skills"], (
            f"forge circle capability must reference 'skills/consult'; got {cap['skills']}"
        )

    def test_forge_circle_compose_includes_consult_skill(self, tmp_path):
        """compose_plan({'circle'}) must include the consult skill dir as a CopyOp."""
        m = load_manifest(_FORGE_MANIFEST)
        plan = compose_plan(m, {"circle"}, tmp_path / "dest")
        skill_srcs = {op.src.name for op in plan.ops if op.src.is_dir()}
        assert "consult" in skill_srcs, (
            f"compose_plan for forge 'circle' must include the consult skill dir; got {skill_srcs}"
        )
