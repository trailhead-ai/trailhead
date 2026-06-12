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

Slice 5 additions:
  7. Old release identifiers are absent from tools/portage and tools/landing.
     (They legitimately still exist in tools/forge until Slice 6.)

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
        """forge circle capability must reference circle-*.md (not council-*.md)."""
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["circle"]
        agents = cap["agents"]
        assert "agents/circle-advocate.md" in agents, (
            f"forge circle must reference 'agents/circle-advocate.md'; got {agents}"
        )
        assert "agents/circle-builder.md" in agents, (
            f"forge circle must reference 'agents/circle-builder.md'; got {agents}"
        )
        assert "agents/circle-reliability.md" in agents, (
            f"forge circle must reference 'agents/circle-reliability.md'; got {agents}"
        )
        assert "agents/circle-security.md" in agents, (
            f"forge circle must reference 'agents/circle-security.md'; got {agents}"
        )
        for agent in agents:
            assert "council-" not in agent, (
                f"forge circle still references old council- agent: {agent}"
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
        """forge circle compose includes circle-*.md agent CopyOps."""
        m = load_manifest(_FORGE_MANIFEST)
        plan = compose_plan(m, {"circle"}, tmp_path / "dest")
        agent_srcs = {op.src.name for op in plan.ops if op.src.is_file()}
        for name in ("circle-advocate.md", "circle-builder.md",
                     "circle-reliability.md", "circle-security.md"):
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
        ("circle-advocate", _FORGE_PLUGIN_ROOT / "agents" / "circle-advocate.md"),
        ("circle-builder", _FORGE_PLUGIN_ROOT / "agents" / "circle-builder.md"),
        ("circle-reliability", _FORGE_PLUGIN_ROOT / "agents" / "circle-reliability.md"),
        ("circle-security", _FORGE_PLUGIN_ROOT / "agents" / "circle-security.md"),
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
# 7. Slice 5 — old release identifiers absent from portage + landing
# ---------------------------------------------------------------------------

# Old release identifiers that must not appear in tools/portage or tools/landing.
# They legitimately still exist in tools/forge until Slice 6 deletes them — so
# the scan roots here are scoped to portage and landing only.
_RELEASE_OLD_IDENTIFIERS = [
    "pr-summarizer",
    "pr-updater",
    "watch-pr",
    "watch-preview",
    "diagnose-preview",
    "create-pr",
    "update-pr",
    "merge-pr",
    "github-pr",
    "post-merge-decide",
]

_PORTAGE_ROOT = _REPO_ROOT / "tools" / "portage"
_LANDING_ROOT = _REPO_ROOT / "tools" / "landing"


def _collect_portage_landing_files() -> list[Path]:
    """Collect all relevant source files in portage + landing (not forge)."""
    files: list[Path] = []
    for root in (_PORTAGE_ROOT, _LANDING_ROOT):
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if f.suffix not in _SCAN_SUFFIXES:
                continue
            if any(part in _EXCLUDE_DIRS for part in f.parts):
                continue
            if f.is_file():
                files.append(f)
    return files


class TestOldReleaseIdentifiersAbsentFromPortageLanding:
    """Old release identifiers must not appear in tools/portage or tools/landing.

    These identifiers legitimately still exist in tools/forge (until Slice 6
    deletes them). The assertions are scoped to portage+landing only so the
    existing forge tests remain unaffected.

    Identifiers checked: pr-summarizer, pr-updater, watch-pr, watch-preview,
    diagnose-preview, create-pr, update-pr, merge-pr, github-pr, post-merge-decide.
    """

    @pytest.mark.parametrize("identifier", _RELEASE_OLD_IDENTIFIERS)
    def test_old_identifier_absent_from_portage_and_landing(self, identifier: str):
        """Old release identifier must not appear in tools/portage or tools/landing."""
        files = _collect_portage_landing_files()
        hits = _grep_files(re.escape(identifier), files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of old release identifier "
                f"{identifier!r} in portage/landing — must be absent:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# 8. Slice 6 — forge release cluster is GONE (the hard cut)
# ---------------------------------------------------------------------------

# The 8 release scripts moved to trailhead/vcs/ + landing — they must be ABSENT
# from tools/forge after Slice 6's deletion.
_MOVED_RELEASE_SCRIPTS = [
    "detect_repos.py",
    "check_pr_status.py",
    "pr_evaluate_status.py",
    "merge_prs.py",
    "release_prs_sidecar.py",
    "wait_for_actionable.py",
    "runner_protocol.py",
    "soak_health.py",
]

# The 7 release skill dirs deleted from forge.
_DELETED_RELEASE_SKILLS = [
    "create-pr",
    "update-pr",
    "watch-pr",
    "watch-preview",
    "merge-pr",
    "github-pr",
    "post-merge-decide",
]

# The 5 release agents deleted from forge (incl. pr-summarizer → portage's summarizer).
_DELETED_RELEASE_AGENTS = [
    "pr-updater.md",
    "watch-pr.md",
    "watch-preview.md",
    "diagnose-preview.md",
    "pr-summarizer.md",
]


class TestForgeReleaseClusterDeleted:
    """Slice 6 hard cut: forge no longer exposes a `release` capability and the
    moved release scripts/skills/agents are absent from tools/forge.

    portage + landing now own shipping + deploy; this locks the deletion so a
    revert (or a stray reintroduction) is caught by the suite.
    """

    def test_forge_manifest_has_no_release_capability(self):
        """forge capabilities.toml must not declare a `release` capability."""
        m = load_manifest(_FORGE_MANIFEST)
        assert "release" not in m.capabilities, (
            "forge still exposes a `release` capability — Slice 6 deletes it; "
            "portage owns PR lifecycle and landing owns deploy soak now."
        )

    def test_forge_helpers_no_longer_lists_pr_summarizer(self):
        """forge helpers must not reference agents/pr-summarizer.md (→ portage summarizer)."""
        m = load_manifest(_FORGE_MANIFEST)
        helpers = m.capabilities["helpers"]
        assert "agents/pr-summarizer.md" not in helpers["agents"], (
            "forge helpers still references agents/pr-summarizer.md — it became "
            "portage's `summarizer`; remove it from [capabilities.helpers]."
        )

    @pytest.mark.parametrize("script", _MOVED_RELEASE_SCRIPTS)
    def test_moved_release_script_absent_from_forge(self, script: str):
        """Each moved release script must be absent from tools/forge."""
        path = _FORGE_PLUGIN_ROOT / "scripts" / script
        assert not path.exists(), (
            f"{path.relative_to(_REPO_ROOT)} still exists — it moved to "
            "trailhead/vcs/ (or landing) in the extraction; delete the forge copy."
        )

    @pytest.mark.parametrize("skill", _DELETED_RELEASE_SKILLS)
    def test_deleted_release_skill_dir_absent_from_forge(self, skill: str):
        """Each deleted release skill directory must be absent from tools/forge."""
        path = _FORGE_PLUGIN_ROOT / "skills" / skill
        assert not path.exists(), (
            f"forge skills/{skill}/ still exists — Slice 6 deletes the release "
            "skill cluster (portage/landing own it now)."
        )

    @pytest.mark.parametrize("agent", _DELETED_RELEASE_AGENTS)
    def test_deleted_release_agent_absent_from_forge(self, agent: str):
        """Each deleted release agent file must be absent from tools/forge."""
        path = _FORGE_PLUGIN_ROOT / "agents" / agent
        assert not path.exists(), (
            f"forge agents/{agent} still exists — Slice 6 deletes the release "
            "agent cluster (portage/landing own it now)."
        )
