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
        """sdd- prefix must never reappear in tools/ source (we renamed away from it long ago)."""
        files = _collect_files()
        hits = _grep_files(r"sdd-", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'sdd-' — must be zero after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_scout_references(self):
        """The `scout` agent token must not appear in tools/ source after rename to assumption-prover.

        Word-boundary scoped so it matches the agent stem (`scout`, `scout.md`, `scout`'s)
        but not hypothetical unrelated substrings. Verified RED-first: every current hit is
        a reference to the old SDD agent name, none a legitimate English use.
        """
        files = _collect_files()
        hits = _grep_files(r"\bscout\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'scout' — must be 'assumption-prover' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_trailblazer_references(self):
        """The `trailblazer` agent token must not appear in tools/ source after rename to executor.

        Word-boundary scoped to the agent stem. Verified RED-first: every current hit is a
        reference to the old SDD implementer agent, none a legitimate English use.
        """
        files = _collect_files()
        hits = _grep_files(r"\btrailblazer\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'trailblazer' — must be 'executor' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_subagent_driven_development_references(self):
        """The `subagent-driven-development` skill dir/token must not appear in tools/ source
        after rename to skills/execute."""
        files = _collect_files()
        hits = _grep_files(r"subagent-driven-development", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'subagent-driven-development' — must be 'execute' after rename:"]
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

    def test_assumption_prover_agent_exists_with_matching_frontmatter(self):
        """Spec A makes 'assumption-prover' the DESIRED agent name (was 'scout').

        This inverts the prior WS-12 'Assumption-Prover must not appear' assertion:
        the agent file must now exist and its frontmatter name: must match the stem.
        """
        path = _FORGE_PLUGIN_ROOT / "agents" / "assumption-prover.md"
        assert path.exists(), (
            f"agents/assumption-prover.md not found at {path} — rename from agents/scout.md"
        )
        name = _parse_frontmatter_name(path)
        assert name == "assumption-prover", (
            f"agents/assumption-prover.md frontmatter name: is {name!r}, expected 'assumption-prover'"
        )

    def test_executor_role_prose_in_execute_skill(self):
        """The execute SKILL.md must use the new 'Executor' role label and must NOT carry the
        older 'Implementer' nor the now-old 'Trailblazer' role prose.

        Scoped to the renamed skill file (skills/execute/SKILL.md) — the most tightly
        constrained signal for the role label. Forbids:
        - the older '## Handling Implementer Status' header / 'Implementer returns' callout
        - the now-old '## Handling Trailblazer Status' header / 'Trailblazer returns' callout
        and asserts the new 'Executor' role label is present.
        """
        execute_skill = _FORGE_PLUGIN_ROOT / "skills" / "execute" / "SKILL.md"
        assert execute_skill.exists(), (
            f"skills/execute/SKILL.md not found at {execute_skill} — "
            "rename from skills/subagent-driven-development/"
        )
        text = execute_skill.read_text()
        assert "## Handling Implementer Status" not in text, (
            "execute SKILL.md still has the old '## Handling Implementer Status' header"
        )
        assert "Implementer returns" not in text, (
            "execute SKILL.md still has old 'Implementer returns' callouts"
        )
        assert "## Handling Trailblazer Status" not in text, (
            "execute SKILL.md still has the now-old '## Handling Trailblazer Status' header — "
            "rename to '## Handling Executor Status'"
        )
        assert "Trailblazer returns" not in text, (
            "execute SKILL.md still has now-old 'Trailblazer returns' callouts — "
            "rename to 'Executor returns'"
        )
        assert "Executor" in text, (
            "execute SKILL.md must carry the new 'Executor' role label"
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

    def test_forge_execute_references_assumption_prover_and_executor(self):
        """forge execute capability must reference assumption-prover.md and executor.md."""
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["execute"]
        assert "agents/assumption-prover.md" in cap["agents"], (
            f"forge execute must reference 'agents/assumption-prover.md'; got {cap['agents']}"
        )
        assert "agents/executor.md" in cap["agents"], (
            f"forge execute must reference 'agents/executor.md'; got {cap['agents']}"
        )
        assert "agents/scout.md" not in cap["agents"], (
            "forge execute still references old 'agents/scout.md'"
        )
        assert "agents/trailblazer.md" not in cap["agents"], (
            "forge execute still references old 'agents/trailblazer.md'"
        )
        assert "skills/execute" in cap["skills"], (
            f"forge execute must reference 'skills/execute'; got {cap['skills']}"
        )
        assert "skills/subagent-driven-development" not in cap["skills"], (
            "forge execute still references old 'skills/subagent-driven-development'"
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

    def test_forge_execute_compose_includes_assumption_prover_and_executor(self, tmp_path):
        """forge execute compose includes assumption-prover.md and executor.md CopyOps."""
        m = load_manifest(_FORGE_MANIFEST)
        plan = compose_plan(m, {"execute"}, tmp_path / "dest")
        agent_srcs = {op.src.name for op in plan.ops if op.src.is_file()}
        assert "assumption-prover.md" in agent_srcs, (
            f"compose_plan for forge 'execute' must include assumption-prover.md; got {agent_srcs}"
        )
        assert "executor.md" in agent_srcs, (
            f"compose_plan for forge 'execute' must include executor.md; got {agent_srcs}"
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
        ("assumption-prover", _FORGE_PLUGIN_ROOT / "agents" / "assumption-prover.md"),
        ("executor", _FORGE_PLUGIN_ROOT / "agents" / "executor.md"),
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


# ---------------------------------------------------------------------------
# 8. Slice 3 — planning Step-10 handoff names /forge:execute without a
#    self-referential trigger-verb collision (KU — accept-as-risk, by test).
# ---------------------------------------------------------------------------


class TestPlanningExecuteHandoff:
    """The reworded planning Step-10 handoff must keep pulling in the renamed
    `/forge:execute` skill while NOT making the continuation trigger word identical
    to the bare skill name (the old prompt said *reply **execute** to hand off to
    subagent-driven-development* — post-rename the verb would collide with the
    skill name `/forge:execute`).

    These are meaningful WANT-artifact assertions, not vacuous absence checks:
    the handoff target `/forge:execute` must be named, and the trigger phrasing
    must be present in a non-self-referential form.
    """

    def test_planning_names_forge_execute_as_handoff_target(self):
        """planning/SKILL.md must name `/forge:execute` as the skill the handoff pulls in."""
        assert _PLANNING_SKILL.exists(), f"planning/SKILL.md not found at {_PLANNING_SKILL}"
        text = _PLANNING_SKILL.read_text()
        assert "/forge:execute" in text, (
            "planning/SKILL.md Step-10 handoff must name `/forge:execute` as the "
            "skill it hands off to (the renamed subagent-driven-development)."
        )

    def test_planning_handoff_trigger_is_not_self_referential(self):
        """The continuation prompt must not instruct the user to reply with the bare
        skill name as the trigger word (the old 'reply **execute**' collision).

        After rename, 'execute' == the skill name `/forge:execute`. A self-referential
        prompt (*reply execute to hand off to execute*) is ambiguous, so the bold
        'reply **execute**' continuation token must be gone. We assert the OLD
        self-referential token is absent AND a non-colliding continuation verb
        (e.g. **build**) is present, so a regression that re-introduces the bare
        'reply **execute**' phrasing fails.
        """
        assert _PLANNING_SKILL.exists(), f"planning/SKILL.md not found at {_PLANNING_SKILL}"
        text = _PLANNING_SKILL.read_text()
        assert "Reply **execute**" not in text and "reply **execute**" not in text, (
            "planning/SKILL.md still uses the self-referential 'reply **execute**' "
            "continuation token — after the rename the trigger word collides with the "
            "skill name `/forge:execute`. Use a non-colliding verb (e.g. **build**)."
        )
        assert "**build**" in text, (
            "planning/SKILL.md handoff must offer a non-colliding continuation verb "
            "(e.g. **build**) so the trigger word is not identical to the `/forge:execute` "
            "skill name."
        )
