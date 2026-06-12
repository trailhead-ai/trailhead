"""Slice 7 guard tests: UX renames sweep — finish/tend/circle/librarian/scout/trailblazer.

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
        """lore-librarian must NEVER appear in tools/ source.

        This was a prior forbidden name (the agent was briefly called lore-librarian
        before loremaster). Spec A renames loremaster→librarian — the new bare name is
        `librarian`, NOT `lore-librarian`. This forbid stays so the hyphenated form is
        never reintroduced.
        """
        files = _collect_files()
        hits = _grep_files(r"lore-librarian", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'lore-librarian' — must be zero (forbidden name):"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_loremaster_references(self):
        """The `loremaster` agent token must not appear in tools/ source after rename to librarian.

        Token-scoped to the old agent stem. The new name is `librarian` (the lore vault
        search/synthesis agent) — never `lore-librarian` (a separately forbidden name).
        Verified RED-first: every current hit is a reference to the old agent name.
        """
        files = _collect_files()
        hits = _grep_files(r"loremaster", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'loremaster' — must be 'librarian' after rename:"]
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
        """Slice 7 INVERSION: lore recall must NO LONGER reference skills/tend.

        Slice 4 (WS-12) renamed lore's review→tend; Slice 7 DELETES the tend skill
        entirely (along with reflect). The recall capability's skills list must now
        carry neither skills/tend nor the older skills/review.
        """
        m = load_manifest(_LORE_MANIFEST)
        cap = m.capabilities["recall"]
        assert "skills/tend" not in cap["skills"], (
            f"lore recall still references deleted 'skills/tend'; got {cap['skills']}"
        )
        assert "skills/review" not in cap["skills"], (
            "lore recall still references 'skills/review' — the tend/review skill is deleted"
        )
        assert "skills/reflect" not in cap["skills"], (
            f"lore recall still references deleted 'skills/reflect'; got {cap['skills']}"
        )

    def test_lore_recall_references_librarian_agent(self):
        """lore recall capability must reference agents/librarian.md (was agents/loremaster.md).

        Inverts the prior assertion: Spec A makes 'librarian' the desired agent name.
        The old agents/loremaster.md must be gone from the manifest.
        """
        m = load_manifest(_LORE_MANIFEST)
        cap = m.capabilities["recall"]
        assert "agents/librarian.md" in cap["agents"], (
            f"lore recall must reference 'agents/librarian.md'; got {cap['agents']}"
        )
        assert "agents/loremaster.md" not in cap["agents"], (
            "lore recall still references old 'agents/loremaster.md' — update to 'agents/librarian.md'"
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

    def test_lore_recall_compose_includes_librarian(self, tmp_path):
        """lore recall compose includes librarian.md CopyOp (was loremaster.md)."""
        m = load_manifest(_LORE_MANIFEST)
        plan = compose_plan(m, {"recall"}, tmp_path / "dest")
        agent_srcs = {op.src.name for op in plan.ops if op.src.is_file()}
        assert "librarian.md" in agent_srcs, (
            f"compose_plan for lore 'recall' must include librarian.md; got {agent_srcs}"
        )
        assert "loremaster.md" not in agent_srcs, (
            f"compose_plan for lore 'recall' still includes old loremaster.md; got {agent_srcs}"
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

    def test_lore_finish_frontmatter_name_matches_dir(self):
        """Slice 7 finish/finished fix: the finish skill dir is finish/ but its frontmatter
        still said name: finished. Align it so dir↔name agree (registers as /lore:finish)."""
        finish_skill = _LORE_PLUGIN_ROOT / "skills" / "finish" / "SKILL.md"
        assert finish_skill.exists(), f"skills/finish/SKILL.md not found at {finish_skill}"
        name = _parse_frontmatter_name(finish_skill)
        assert name == "finish", (
            f"skills/finish/SKILL.md frontmatter name: is {name!r}, expected 'finish' "
            "(dir↔name must agree so it registers as /lore:finish)"
        )

    def test_lore_finished_dir_gone(self):
        """skills/finished/ must not exist after rename."""
        old_dir = _LORE_PLUGIN_ROOT / "skills" / "finished"
        assert not old_dir.exists(), (
            f"Old skills/finished/ still exists at {old_dir} — rename to skills/finish/"
        )

    def test_lore_tend_skill_dir_exists(self):
        """Slice 7 INVERSION: skills/tend/ must be GONE (the tend/review skill is deleted)."""
        tend_dir = _LORE_PLUGIN_ROOT / "skills" / "tend"
        assert not tend_dir.exists(), (
            f"skills/tend/ still exists at {tend_dir} — Slice 7 deletes the tend/review skill"
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
        ("librarian", _LORE_PLUGIN_ROOT / "agents" / "librarian.md"),
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
_PLANNING_SKILL = _FORGE_PLUGIN_ROOT / "skills" / "plan" / "SKILL.md"

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


# ---------------------------------------------------------------------------
# 9. Slice 4 — forge skill renames: followup→polish, handoff→shelve,
#    planning→plan, requesting-code-review→review.
#
# CRITICAL scoping (Builder/Advocate, per-slice green-bar rule): forge keeps a
# `review` *capability* name and the new `review` *skill* slots under it. The
# `requesting-code-review` forbid greps the LITERAL old token / skill path,
# never a bare `review`. Likewise `plan` is common English — assert the
# `skills/plan/` dir/path, never a bare `plan` word. And `handoff`/`followup`
# survive legitimately as the `lore handoff` CLI subcommand, the
# `handoff_capture.py` script, `~/.forge/handoffs/`, and the plan-brief schema
# tokens `followup-to:` / `-followup-<n>` — so those forbids target the skill
# IDENTITY only (skill path, `name:` frontmatter, `/forge:` invocation), never
# the bare word.
# ---------------------------------------------------------------------------

_POLISH_SKILL = _FORGE_PLUGIN_ROOT / "skills" / "polish" / "SKILL.md"
_SHELVE_SKILL = _FORGE_PLUGIN_ROOT / "skills" / "shelve" / "SKILL.md"
_PLAN_SKILL = _FORGE_PLUGIN_ROOT / "skills" / "plan" / "SKILL.md"
_REVIEW_SKILL = _FORGE_PLUGIN_ROOT / "skills" / "review" / "SKILL.md"


class TestSlice4SkillRenameForbids:
    """Old forge skill identifiers must be gone from tools/ source.

    Each forbid is token/path-scoped to the skill identity so it never trips a
    legitimate surviving name (the `review`/`circle` capabilities, the
    `lore handoff` CLI subcommand, the `handoff_capture.py` script, the
    `followup-to:` plan-brief schema field).
    """

    def test_no_skills_followup_path(self):
        """The `skills/followup` skill path must not appear after rename to skills/polish."""
        files = _collect_files()
        hits = _grep_files(r"skills/followup", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'skills/followup' — must be 'skills/polish' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_skills_handoff_path(self):
        """The `skills/handoff` skill path must not appear after rename to skills/shelve.

        Token-scoped to the skill path so it does NOT flag the legitimately
        surviving `lore handoff` CLI subcommand, the `handoff_capture.py` script,
        or the `~/.forge/handoffs/` degraded-write location.
        """
        files = _collect_files()
        hits = _grep_files(r"skills/handoff", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'skills/handoff' — must be 'skills/shelve' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_skills_planning_path(self):
        """The `skills/planning` skill path must not appear after rename to skills/plan."""
        files = _collect_files()
        hits = _grep_files(r"skills/planning", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'skills/planning' — must be 'skills/plan' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_requesting_code_review_token(self):
        """The literal `requesting-code-review` token must not appear after rename to skills/review.

        Greps the LITERAL old token (path and skill stem) — NEVER a bare `review`,
        which survives as the forge `review` capability name and elsewhere.
        """
        files = _collect_files()
        hits = _grep_files(r"requesting-code-review", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'requesting-code-review' — must be 'review' (skill) after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_forge_handoff_command_invocation(self):
        """The `/forge:handoff` skill invocation must not appear after rename to /forge:shelve.

        Scoped to the slash-command form so it targets the skill identity, not the
        `lore handoff` subcommand or the `handoff_capture.py` helper name.
        """
        files = _collect_files()
        hits = _grep_files(r"/forge:handoff", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of '/forge:handoff' — must be '/forge:shelve' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_followup_command_invocation(self):
        """The `/followup` and `/forge:followup` skill invocations must not appear after rename to polish.

        Scoped to the slash-command form so it targets the skill identity, NOT the
        `followup-to:` plan-brief frontmatter field or the `-followup-<n>` slug
        convention (those are the brief schema, not the skill name).
        """
        files = _collect_files()
        hits = _grep_files(r"/(forge:)?followup", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of '/followup' invocation — must be '/polish' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_followup_skill_name_frontmatter(self):
        """No SKILL.md may carry `name: followup` after rename to polish."""
        files = _collect_files()
        hits = _grep_files(r"^name: followup\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of `name: followup` frontmatter — must be `name: polish` after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_handoff_skill_name_frontmatter(self):
        """No SKILL.md may carry `name: handoff` after rename to shelve."""
        files = _collect_files()
        hits = _grep_files(r"^name: handoff\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of `name: handoff` frontmatter — must be `name: shelve` after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_planning_skill_name_frontmatter(self):
        """No SKILL.md may carry `name: planning` after rename to plan."""
        files = _collect_files()
        hits = _grep_files(r"^name: planning\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of `name: planning` frontmatter — must be `name: plan` after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_old_skill_dirs_gone(self):
        """The four old skill directories must not exist under forge plugins."""
        for old in ("followup", "handoff", "planning", "requesting-code-review"):
            old_dir = _FORGE_PLUGIN_ROOT / "skills" / old
            assert not old_dir.exists(), (
                f"Old skills/{old}/ still exists at {old_dir} — rename it."
            )


class TestSlice4RenamedSkillsExistAndRegistrable:
    """The four renamed skill dirs must exist + be registrable with matching `name:`.

    The `skills/plan/` and `skills/review/` assertions key on the exact skill DIR
    path — never a bare `plan`/`review` word — so they can't be satisfied by the
    surviving `plan`/`review` capability tokens.
    """

    @pytest.mark.parametrize("stem,skill_md", [
        ("polish", _POLISH_SKILL),
        ("shelve", _SHELVE_SKILL),
        ("plan", _PLAN_SKILL),
        ("review", _REVIEW_SKILL),
    ])
    def test_renamed_skill_dir_exists_with_skill_md(self, stem: str, skill_md: Path):
        assert skill_md.exists(), (
            f"skills/{stem}/SKILL.md not found at {skill_md} — rename the old skill dir."
        )

    @pytest.mark.parametrize("stem,skill_md", [
        ("polish", _POLISH_SKILL),
        ("shelve", _SHELVE_SKILL),
        ("plan", _PLAN_SKILL),
        ("review", _REVIEW_SKILL),
    ])
    def test_renamed_skill_is_registrable_with_matching_name(self, stem: str, skill_md: Path):
        assert skill_md.exists(), f"skills/{stem}/SKILL.md not found at {skill_md}"
        assert _has_registrable_frontmatter(skill_md), (
            f"skills/{stem}/SKILL.md must open with non-empty name: + description: frontmatter "
            f"or Claude Code will not register /forge:{stem}"
        )
        name = _parse_frontmatter_name(skill_md)
        assert name == stem, (
            f"skills/{stem}/SKILL.md frontmatter name: is {name!r}, expected {stem!r}"
        )


class TestSlice4ManifestRepointed:
    """capabilities.toml must repoint the four renamed skills atomically."""

    def test_base_skills_repointed_to_shelve_and_polish(self):
        """[tool] base must list skills/shelve + skills/polish (pickup stays); the old
        skills/handoff + skills/followup must be gone."""
        m = load_manifest(_FORGE_MANIFEST)
        base = m.base
        assert "skills/shelve" in base, f"forge base must reference 'skills/shelve'; got {base}"
        assert "skills/polish" in base, f"forge base must reference 'skills/polish'; got {base}"
        assert "skills/pickup" in base, f"forge base must keep 'skills/pickup'; got {base}"
        assert "skills/handoff" not in base, "forge base still references old 'skills/handoff'"
        assert "skills/followup" not in base, "forge base still references old 'skills/followup'"

    def test_planning_capability_references_plan_skill(self):
        """forge planning capability must reference skills/plan (not skills/planning)."""
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["planning"]
        assert "skills/plan" in cap["skills"], (
            f"forge planning must reference 'skills/plan'; got {cap['skills']}"
        )
        assert "skills/planning" not in cap["skills"], (
            "forge planning still references old 'skills/planning'"
        )

    def test_review_capability_references_review_skill(self):
        """forge review capability must reference skills/review (not skills/requesting-code-review).

        The capability NAME stays `review`; only the skill path changes.
        """
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["review"]
        assert "skills/review" in cap["skills"], (
            f"forge review must reference 'skills/review'; got {cap['skills']}"
        )
        assert "skills/requesting-code-review" not in cap["skills"], (
            "forge review still references old 'skills/requesting-code-review'"
        )

    def test_renamed_skills_compose_to_existing_src(self, tmp_path):
        """compose_plan for planning/review/base must resolve the renamed skill dirs on disk."""
        m = load_manifest(_FORGE_MANIFEST)
        for cap in ("planning", "review"):
            plan = compose_plan(m, {cap}, tmp_path / cap)
            skill_srcs = {op.src.name for op in plan.ops if op.src.is_dir()}
            if cap == "planning":
                assert "plan" in skill_srcs, f"compose({cap}) must include the plan skill dir; got {skill_srcs}"
            if cap == "review":
                assert "review" in skill_srcs, f"compose({cap}) must include the review skill dir; got {skill_srcs}"


# ---------------------------------------------------------------------------
# 10. Slice 6 — lore radar→follow-up + check-radar→check-in skill-data layer.
#
# CRITICAL scoping (Slice 6 council + per-slice green-bar rule): `radar` survives
# legitimately as a vault DATA-LAYER concept — the harvest `radar:` typed-prefix,
# the `radar/` vault directory in agent/starter prose, the `harvest.py` /
# `review.py` taxonomy maps, and figurative "on the radar" body prose. The guard
# must therefore target the SKILL-DATA-LAYER IDENTITY only:
#   - the skill dir paths `skills/radar` / `skills/check-radar`
#   - the renamed helper `radar_due` (filename / import)
#   - `type: radar` frontmatter ONLY inside skills/ and templates/ (the renamed
#     surface), never in test fixtures or prose corpora
#   - the `lore new radar` CLI invocation string
# NEVER a bare `\bradar\b` global forbid (would flag the vault taxonomy, the
# harvest layer, and the deletion-target review.py — all legitimately surviving
# this slice).
# ---------------------------------------------------------------------------

_LORE_SKILLS = _LORE_PLUGIN_ROOT / "skills"
_LORE_TEMPLATES = _LORE_PLUGIN_ROOT / "templates"
_FOLLOW_UP_SKILL = _LORE_SKILLS / "follow-up" / "SKILL.md"
_CHECK_IN_SKILL = _LORE_SKILLS / "check-in" / "SKILL.md"


def _files_under(root: Path) -> list[Path]:
    """Collect scannable source files under a single root (md/py/toml)."""
    out: list[Path] = []
    if not root.exists():
        return out
    for f in root.rglob("*"):
        if f.suffix not in _SCAN_SUFFIXES:
            continue
        if any(part in _EXCLUDE_DIRS for part in f.parts):
            continue
        if f.is_file():
            out.append(f)
    return out


class TestSlice6RadarSkillDataForbids:
    """Old radar/check-radar SKILL-DATA-LAYER identifiers must be gone from tools/lore.

    Each forbid is token/path-scoped to the skill identity so it never trips the
    legitimately surviving vault-taxonomy uses (harvest `radar:` prefix, the
    `radar/` vault dir prose, the Slice-7 deletion-target `review.py`).
    """

    def test_no_skills_radar_path(self):
        """`skills/radar` skill path must not appear after rename to skills/follow-up."""
        files = _collect_files()
        hits = _grep_files(r"skills/radar\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'skills/radar' — must be 'skills/follow-up' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_skills_check_radar_path(self):
        """`skills/check-radar` skill path must not appear after rename to skills/check-in."""
        files = _collect_files()
        hits = _grep_files(r"skills/check-radar\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'skills/check-radar' — must be 'skills/check-in' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_check_radar_command_token(self):
        """`/lore:check-radar` and the `check-radar` skill `name:` must be gone.

        Scoped to the slash-command invocation and the frontmatter `name:` — the
        skill identity — not a bare `check-radar` (which is the same string but we
        assert via the two identity-bearing forms so the intent is explicit).
        """
        files = _collect_files()
        hits = _grep_files(r"/lore:check-radar|^name: check-radar\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of the check-radar skill identity — must be 'check-in' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_radar_due_helper_reference(self):
        """The `radar_due` helper (filename / import) must be gone after rename to follow_up_due.

        Token-scoped to the helper identity. The renamed helper is
        `scripts/follow_up_due.py` exposing `follow_up_notes_due`.
        """
        files = _collect_files()
        hits = _grep_files(r"radar_due", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'radar_due' — must be 'follow_up_due' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_type_radar_in_skills_or_templates(self):
        """`type: radar` frontmatter must be gone from the renamed skill/template surface.

        SCOPED to files under skills/ and templates/ ONLY — the migration target.
        Test fixtures (test_harvest_expand, test_p1e/_p1f) and docs legitimately
        still carry `type: radar` (vault-data / deletion-target taxonomy) this slice.
        """
        files = _files_under(_LORE_SKILLS) + _files_under(_LORE_TEMPLATES)
        hits = _grep_files(r"^type: radar\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'type: radar' in skills/templates — must be 'type: follow-up' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_lore_new_radar_invocation(self):
        """The `lore new radar` CLI invocation must be gone after the bucket rename to follow-up."""
        files = _collect_files()
        hits = _grep_files(r"lore new radar\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'lore new radar' — must be 'lore new follow-up' after rename:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_old_radar_skill_dirs_gone(self):
        """The old skills/radar and skills/check-radar directories must not exist."""
        for old in ("radar", "check-radar"):
            old_dir = _LORE_SKILLS / old
            assert not old_dir.exists(), (
                f"Old skills/{old}/ still exists at {old_dir} — rename it."
            )


class TestSlice6RenamedSkillsExistAndRegistrable:
    """The renamed follow-up + check-in skill dirs must exist + be registrable.

    Assertions key on the exact skill DIR path / `name:` — never a bare
    `follow-up`/`check-in` word — so they can't be falsely satisfied.
    """

    @pytest.mark.parametrize("stem,skill_md", [
        ("follow-up", _FOLLOW_UP_SKILL),
        ("check-in", _CHECK_IN_SKILL),
    ])
    def test_renamed_skill_dir_exists_with_skill_md(self, stem: str, skill_md: Path):
        assert skill_md.exists(), (
            f"skills/{stem}/SKILL.md not found at {skill_md} — rename the old skill dir."
        )

    @pytest.mark.parametrize("stem,skill_md", [
        ("follow-up", _FOLLOW_UP_SKILL),
        ("check-in", _CHECK_IN_SKILL),
    ])
    def test_renamed_skill_is_registrable_with_matching_name(self, stem: str, skill_md: Path):
        assert skill_md.exists(), f"skills/{stem}/SKILL.md not found at {skill_md}"
        assert _has_registrable_frontmatter(skill_md), (
            f"skills/{stem}/SKILL.md must open with non-empty name: + description: frontmatter "
            f"or Claude Code will not register /lore:{stem}"
        )
        name = _parse_frontmatter_name(skill_md)
        assert name == stem, (
            f"skills/{stem}/SKILL.md frontmatter name: is {name!r}, expected {stem!r}"
        )


class TestSlice6ManifestAndTemplate:
    """lore capabilities.toml must repoint the two renamed skills + the template renamed."""

    def test_capture_capability_references_follow_up_and_check_in(self):
        """lore capture capability must reference skills/follow-up + skills/check-in,
        not the old skills/radar + skills/check-radar."""
        m = load_manifest(_LORE_MANIFEST)
        cap = m.capabilities["capture"]
        assert "skills/follow-up" in cap["skills"], (
            f"lore capture must reference 'skills/follow-up'; got {cap['skills']}"
        )
        assert "skills/check-in" in cap["skills"], (
            f"lore capture must reference 'skills/check-in'; got {cap['skills']}"
        )
        assert "skills/radar" not in cap["skills"], (
            "lore capture still references old 'skills/radar'"
        )
        assert "skills/check-radar" not in cap["skills"], (
            "lore capture still references old 'skills/check-radar'"
        )

    def test_follow_up_template_exists_with_type_follow_up(self):
        """templates/follow-up.md must exist with `type: follow-up` (was templates/radar.md)."""
        tmpl = _LORE_TEMPLATES / "follow-up.md"
        assert tmpl.exists(), (
            f"templates/follow-up.md not found at {tmpl} — rename from templates/radar.md"
        )
        text = tmpl.read_text()
        assert re.search(r"^type: follow-up\b", text, re.MULTILINE), (
            "templates/follow-up.md must carry `type: follow-up` frontmatter"
        )
        assert not (_LORE_TEMPLATES / "radar.md").exists(), (
            "old templates/radar.md still exists — rename to follow-up.md"
        )

    def test_lore_manifest_validates_and_composes_capture(self, tmp_path):
        """lore manifest validates and capture composes the renamed skill dirs on disk."""
        m = load_manifest(_LORE_MANIFEST)
        plan = compose_plan(m, {"capture"}, tmp_path / "capture")
        skill_srcs = {op.src.name for op in plan.ops if op.src.is_dir()}
        assert "follow-up" in skill_srcs, (
            f"compose(capture) must include the follow-up skill dir; got {skill_srcs}"
        )
        assert "check-in" in skill_srcs, (
            f"compose(capture) must include the check-in skill dir; got {skill_srcs}"
        )


# ---------------------------------------------------------------------------
# 11. Slice 7 — FULL deletions: reflect, tend/review, forge-ping, lore:ping.
#
# CRITICAL scoping (Builder/Advocate, per-slice green-bar rule): each forbid
# targets the SKILL/AGENT IDENTITY of the deleted surface so it never trips a
# legitimate survivor:
#   - `reflect` survives as an English verb ("filenames reflect", "update to
#     reflect outcomes", `/lore:monthly-reflection` ROADMAP idea) → forbid the
#     skill-identity forms only: `skills/reflect`, `reflect_sessions`,
#     `/lore:reflect`, `name: reflect`.
#   - `review`/`tend` survive as forge's `review` *capability* + the new forge
#     `skills/review` (Slice 4) and as English ("weekly review", "review note")
#     → forbid is PATH-SCOPED to tools/lore/ and targets the lore skill identity
#     (`skills/review`/`skills/tend` dir, `review.py` helper, `/lore:review`/
#     `/lore:tend`, `name: review`/`name: tend`), never a bare word.
#   - `ping` survives in unrelated network/health contexts → forbid the lore
#     skill identity only: `skills/ping`, `/lore:ping`, `name: ping`.
#   - `forge-ping` is a distinctive token → forbid `forge-ping`,
#     `agents/forge-ping.md`, `/forge:forge-ping`.
# ---------------------------------------------------------------------------

_LANDING_CLAIMS = _REPO_ROOT / "trailhead" / "landing_claims.toml"

# Path-scoped file sets for the lore-only review/tend/ping/reflect forbids.
_LORE_FILES = [
    f for f in _collect_files()
    if (_REPO_ROOT / "tools" / "lore") in f.parents or f.parent == (_REPO_ROOT / "tools" / "lore")
]


class TestSlice7DeletionForbids:
    """Old reflect / tend-review / forge-ping / lore:ping identifiers must be GONE.

    Each forbid is identity-scoped so it can't trip a legitimate survivor (the
    English verb `reflect`, forge's `review` capability + Slice-4 `skills/review`,
    network-`ping` prose).
    """

    # ---- reflect (lore) ----------------------------------------------------

    def test_no_skills_reflect_path(self):
        """`skills/reflect` skill path must be gone (skill deleted)."""
        files = _collect_files()
        hits = _grep_files(r"skills/reflect\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'skills/reflect' — the reflect skill is deleted:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_reflect_sessions_helper(self):
        """The `reflect_sessions` backing script (filename / import) must be gone."""
        files = _collect_files()
        hits = _grep_files(r"reflect_sessions", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'reflect_sessions' — the script is deleted:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_lore_reflect_command_or_name(self):
        """`/lore:reflect` invocation and `name: reflect` frontmatter must be gone.

        Scoped to the slash-command and frontmatter identity forms — never a bare
        `reflect` (a common English verb; `/lore:monthly-reflection` is a separate
        unrelated ROADMAP idea token).
        """
        files = _collect_files()
        hits = _grep_files(r"/lore:reflect\b|^name: reflect\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of the reflect skill identity — deleted:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    # ---- tend / review (lore, PATH-SCOPED to tools/lore) -------------------

    def test_no_lore_skills_review_or_tend_path(self):
        """`skills/review` / `skills/tend` lore skill paths must be gone.

        PATH-SCOPED to tools/lore/ so it does NOT flag forge's Slice-4 `skills/review`
        (the renamed requesting-code-review skill, a legitimate survivor).
        """
        hits = _grep_files(r"skills/(review|tend)\b", _LORE_FILES)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of lore 'skills/review|skills/tend' — deleted:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_review_helper_script(self):
        """The lore `review.py` backing script (filename / import) must be gone.

        PATH-SCOPED to tools/lore/. Matches the helper identity `review.py` /
        `import review` / `load_script("review")` — not the English word.
        """
        hits = _grep_files(r"\breview\.py\b|import review\b|load_script\(\"review\"\)|from review import", _LORE_FILES)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of the lore review.py helper — deleted:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_lore_review_or_tend_command_or_name(self):
        """`/lore:review` / `/lore:tend` invocations and `name: review`/`name: tend`
        frontmatter must be gone (the lore tend/review skill is deleted).

        PATH-SCOPED to tools/lore/ — forge's `review` capability/skill is untouched.
        """
        hits = _grep_files(r"/lore:(review|tend)\b|^name: (review|tend)\b", _LORE_FILES)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of the lore review/tend skill identity — deleted:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    # ---- forge-ping (forge agent) ------------------------------------------

    def test_no_forge_ping_references(self):
        """`forge-ping`, `agents/forge-ping.md`, `/forge:forge-ping` must all be gone."""
        files = _collect_files()
        hits = _grep_files(r"forge-ping", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of 'forge-ping' — the agent is deleted:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    # ---- lore:ping (lore skill) --------------------------------------------

    def test_no_lore_skills_ping_path(self):
        """`skills/ping` lore skill path must be gone.

        PATH-SCOPED to tools/lore/ — `ping` survives in unrelated network/health
        contexts elsewhere; this targets the lore skill dir identity only.
        """
        hits = _grep_files(r"skills/ping\b", _LORE_FILES)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of lore 'skills/ping' — the skill is deleted:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_lore_ping_command_or_name(self):
        """`/lore:ping` invocation and `name: ping` frontmatter must be gone."""
        files = _collect_files()
        hits = _grep_files(r"/lore:ping\b|^name: ping\b", files)
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of the lore ping skill identity — deleted:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))


class TestSlice7DeletedSurfacesGone:
    """All four deleted dirs/files are gone on disk and their manifest/claim entries removed."""

    def test_reflect_skill_dir_gone(self):
        assert not (_LORE_PLUGIN_ROOT / "skills" / "reflect").exists(), (
            "skills/reflect/ still exists — Slice 7 deletes the reflect skill"
        )

    def test_reflect_sessions_script_gone(self):
        assert not (_LORE_PLUGIN_ROOT / "scripts" / "reflect_sessions.py").exists(), (
            "scripts/reflect_sessions.py still exists — Slice 7 deletes it"
        )

    def test_tend_skill_dir_gone(self):
        assert not (_LORE_PLUGIN_ROOT / "skills" / "tend").exists(), (
            "skills/tend/ still exists — Slice 7 deletes the tend/review skill"
        )

    def test_review_helper_script_gone(self):
        assert not (_LORE_PLUGIN_ROOT / "scripts" / "review.py").exists(), (
            "scripts/review.py still exists — Slice 7 deletes it"
        )

    def test_lore_ping_skill_dir_gone(self):
        assert not (_LORE_PLUGIN_ROOT / "skills" / "ping").exists(), (
            "skills/ping/ still exists — Slice 7 deletes the lore:ping skill"
        )

    def test_forge_ping_agent_gone(self):
        assert not (_FORGE_PLUGIN_ROOT / "agents" / "forge-ping.md").exists(), (
            "agents/forge-ping.md still exists — Slice 7 deletes the forge-ping agent"
        )

    def test_lore_base_drops_ping_skill(self):
        """lore [tool] base must no longer list skills/ping."""
        m = load_manifest(_LORE_MANIFEST)
        assert "skills/ping" not in m.base, (
            f"lore base still references deleted 'skills/ping'; got {m.base}"
        )

    def test_forge_helpers_drops_forge_ping_agent(self):
        """forge helpers capability must no longer list agents/forge-ping.md."""
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["helpers"]
        assert "agents/forge-ping.md" not in cap["agents"], (
            f"forge helpers still references deleted 'agents/forge-ping.md'; got {cap['agents']}"
        )

    def test_landing_claims_has_no_dangling_deleted_entries(self):
        """landing_claims.toml must carry no claim pointing at a deleted surface."""
        text = _LANDING_CLAIMS.read_text()
        for ref in ("skills/reflect", "skills/tend", "skills/ping", "agents/forge-ping.md"):
            assert ref not in text, (
                f"landing_claims.toml still has a dangling claim for deleted '{ref}'"
            )
