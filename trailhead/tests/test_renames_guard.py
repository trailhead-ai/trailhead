"""Slice 7 guard tests: UX renames sweep — finish/tend/council/librarian/scout/trailblazer.

TDD contract (R-6 + Slice 7 test contract):
  1. Suite-wide grep guard — zero occurrences of the old identifiers in the live
     source tree under tools/{lore,craft}/ (excludes __pycache__, .pytest_cache,
     .git; preserves craft's unrelated uses of "review" as a capability name).
  2. load_manifest(validate=True) succeeds for both lore and craft post-rename.
  3. R-6 resolve-all-capabilities oracle — compose_plan for every declared
     capability across lore + craft; every CopyOp.src exists on disk.
  4. New agent names appear in craft council/execute compose output.
  5. lore finish skill dir exists; lore tend skill dir is GONE (Slice 7 deletes tend/review).
  6. Agent frontmatter name: fields match the new filenames.

Write BEFORE the renames — these tests must fail RED first, then green after.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest
from trailhead.compose import compose_plan

_REPO_ROOT = Path(__file__).parent.parent.parent
_LORE_MANIFEST = _REPO_ROOT / "tools" / "lore" / "capabilities.toml"
_CRAFT_MANIFEST = _REPO_ROOT / "tools" / "craft" / "capabilities.toml"

_LORE_PLUGIN_ROOT = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore"
_CRAFT_PLUGIN_ROOT = _REPO_ROOT / "tools" / "craft" / "plugins" / "craft"

# Directories to scan for stale identifiers
_SCAN_ROOTS = [
    _REPO_ROOT / "tools" / "lore",
    _REPO_ROOT / "tools" / "craft",
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

    def _assert_no_hits(
        self, pattern: str, description: str, *, exclude_pattern: str | None = None
    ) -> None:
        files = _collect_files()
        hits = _grep_files(pattern, files)
        if exclude_pattern:
            excl_rx = re.compile(exclude_pattern)
            hits = [(f, ln, line) for f, ln, line in hits if not excl_rx.search(line)]
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of {description} — "
                "must be zero after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_council_dash_references(self):
        """council-advocate/builder/reliability/security must not appear in tools/ source
        after rename."""
        # Grep for the specific old agent-name prefixes (not /council-session which is a
        # lore vault type)
        files = _collect_files()
        hits = _grep_files(r"council-(advocate|builder|reliability|security)", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of old council-* agent names — "
                "must be zero after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_circle_dash_agent_references(self):
        """circle-advocate/builder/reliability/security must not appear in tools/ source.

        Permanent defensive forbid: the panel was briefly named `circle` (with bare
        agents) before being renamed back to `council`. Token-scoped to the four
        agent stems so it does NOT flag a hypothetical future `circle` substring in
        unrelated prose — only the hyphenated old agent-name form.
        """
        files = _collect_files()
        hits = _grep_files(r"circle-(advocate|builder|reliability|security)", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of old circle-* agent names — "
                "must be zero (forbidden form):"
            ]
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
        """The `scout` agent token must not appear in tools/ source after rename to
        assumption-prover.

        Word-boundary scoped so it matches the agent stem (`scout`, `scout.md`, `scout`'s)
        but not hypothetical unrelated substrings. Verified RED-first: every current hit is
        a reference to the old SDD agent name, none a legitimate English use.
        """
        files = _collect_files()
        hits = _grep_files(r"\bscout\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'scout' — "
                "must be 'assumption-prover' after rename:"
            ]
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
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'trailblazer' — "
                "must be 'executor' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_subagent_driven_development_references(self):
        """The `subagent-driven-development` skill dir/token must not appear in tools/ source
        after rename to skills/execute."""
        files = _collect_files()
        hits = _grep_files(r"subagent-driven-development", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'subagent-driven-development' — "
                "must be 'execute' after rename:"
            ]
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
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'lore-librarian' — "
                "must be zero (forbidden name):"
            ]
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
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'loremaster' — "
                "must be 'librarian' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_skills_finished_references(self):
        """skills/finished must not appear in capabilities.toml after rename to skills/finish."""
        files = _collect_files()
        hits = _grep_files(r"skills/finished", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'skills/finished' — "
                "must be zero after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_skills_review_lore_reference_in_manifest(self):
        """lore capabilities.toml must not reference skills/review (the tend/review skill is
        deleted)."""
        text = _LORE_MANIFEST.read_text()
        assert "skills/review" not in text, (
            "lore/capabilities.toml still references 'skills/review' — "
            "delete the reference entirely (the tend/review skill was removed in Slice 7)."
        )

    def test_no_skills_review_lore_reference_in_skill_files(self):
        """The old skills/review directory must not exist under lore plugins."""
        old_dir = _LORE_PLUGIN_ROOT / "skills" / "review"
        assert not old_dir.exists(), (
            f"Old skills/review directory still exists at {old_dir} — "
            "delete it (the tend/review skill was removed in Slice 7)."
        )

    def test_no_circle_review_prose(self):
        """'Circle Review' Title-Case prose must not appear in SKILL.md bodies after rename
        to 'Council Review'.

        The panel's review step is now labelled 'Council Review'; the old 'Circle
        Review' label must be gone. Excludes:
        - lines that reference the 'code-reviewer' agent or 'code review' (not the council panel)
        - the experiments/ corpus (frozen)
        """
        files = [
            f for f in _collect_files()
            if "experiments" not in f.parts and f.suffix == ".md"
        ]
        hits = _grep_files(r"Circle Review", files)
        # No exclusions needed: 'Circle Review' with both words Title-Cased is
        # exclusively the old panel-review label.
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'Circle Review' — "
                "must be 'Council Review' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_assumption_prover_agent_exists_with_matching_frontmatter(self):
        """Spec A makes 'assumption-prover' the DESIRED agent name (was 'scout').

        This inverts the prior WS-12 'Assumption-Prover must not appear' assertion:
        the agent file must now exist and its frontmatter name: must match the stem.
        """
        path = _CRAFT_PLUGIN_ROOT / "agents" / "assumption-prover.md"
        assert path.exists(), (
            f"agents/assumption-prover.md not found at {path} — rename from agents/scout.md"
        )
        name = _parse_frontmatter_name(path)
        assert name == "assumption-prover", (
            f"agents/assumption-prover.md frontmatter name: is {name!r}, "
            "expected 'assumption-prover'"
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
        execute_skill = _CRAFT_PLUGIN_ROOT / "skills" / "execute" / "SKILL.md"
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
# 1b. forge → craft rename forbids (RED until the plugin is renamed)
#
# The plugin `forge` is renamed to `craft`: directory `tools/forge` → `tools/craft`,
# inner plugin `plugins/forge` → `plugins/craft`, namespace prefix `forge:` → `craft:`,
# marketplace `forge-local` → `craft-local`, `[tool] name = "forge"` → "craft".
# Hard cutover, no compat alias — matching the prior UX-rename precedent.
#
# CRITICAL scoping: the forbids target the forge IDENTITY only. The bare `\bforge\b`
# forbid would match many lines today (the plugin isn't renamed yet) — that is the
# intended RED. None of these forbids touch the legitimately surviving siblings
# (`lore`/`camp`/`portage`/`landing`) or the `review` capability word.
#
# NOTE (false-GREEN trap): each test calls `_collect_files()` INSIDE the method, not
# at module scope. `_SCAN_ROOTS` now points at `tools/craft`, which does not exist
# until Slice 1 — a module-level file list would be silently empty and pass vacuously.
# ---------------------------------------------------------------------------


class TestForgeToCraftRenameForbids:
    """Zero occurrences of the old `forge` plugin identifier in live craft+lore source.

    Word-boundary / token scoped to the forge identity so it never flags the
    surviving sibling plugins (lore/camp/portage/landing) or the `review`
    capability word. RED on purpose until the plugin is renamed to `craft`.
    """

    def test_no_bare_forge_word(self):
        """The bare token `forge` must not appear in tools/{lore,craft} source.

        Word-boundary scoped (`\\bforge\\b`) so it matches the plugin name, the
        `forge:` namespace stem, `forge-local`, `tools/forge`, `~/.forge`, etc.,
        but not unrelated substrings. RED until the plugin dir + identity rename.
        """
        files = _collect_files()
        hits = _grep_files(r"\bforge\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of the bare word 'forge' — "
                "must be 'craft' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_forge_namespace_prefix(self):
        """The `forge:` namespace prefix must not appear after rename to `craft:`.

        Targets the plugin invocation/namespace form (`forge:planner`, `/forge:plan`,
        `forge:execute`, …). RED until every `forge:` reference is repointed to `craft:`.
        """
        files = _collect_files()
        hits = _grep_files(r"forge:", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of the 'forge:' namespace prefix — "
                "must be 'craft:' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_forge_local_marketplace(self):
        """The `forge-local` marketplace name must not appear after rename to `craft-local`."""
        files = _collect_files()
        hits = _grep_files(r"forge-local", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of the 'forge-local' marketplace name — "
                "must be 'craft-local' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# 2. load_manifest(validate=True) post-rename
# ---------------------------------------------------------------------------


class TestManifestValidation:
    """Manifests must load cleanly — disk paths must match manifest entries."""

    def test_lore_manifest_validates_after_rename(self):
        """lore capabilities.toml must load without error post-rename."""
        m = load_manifest(_LORE_MANIFEST)
        assert m.tool_name == "lore"

    def test_craft_manifest_validates_after_rename(self):
        """craft capabilities.toml must load without error and report tool_name 'craft'.

        RED until tools/craft exists (forge→craft rename, Slice 1). Asserts the
        [tool] name flipped from 'forge' to 'craft'.
        """
        m = load_manifest(_CRAFT_MANIFEST)
        assert m.tool_name == "craft"

    def test_lore_recall_references_tend_skill(self):
        """Slice 7 INVERSION: lore recall must NO LONGER reference skills/tend.

        Slice 4 (WS-12) renamed lore's review→tend; Slice 7 DELETES the tend skill
        entirely (along with reflect). The recall capability's skills list must now
        carry neither skills/tend nor the older skills/review.
        """
        m = load_manifest(_LORE_MANIFEST)
        assert "tend" not in m.skills, (
            f"lore still ships a deleted 'tend' skill; got {sorted(m.skills)}"
        )
        assert "review" not in m.skills, (
            "lore still ships a 'review' skill — the tend/review skill is deleted"
        )
        assert "reflect" not in m.skills, (
            f"lore still ships a deleted 'reflect' skill; got {sorted(m.skills)}"
        )

    def test_lore_recall_references_librarian_agent(self):
        """lore recall capability must reference agents/librarian.md (was agents/loremaster.md).

        Inverts the prior assertion: Spec A makes 'librarian' the desired agent name.
        The old agents/loremaster.md must be gone from the manifest.
        """
        m = load_manifest(_LORE_MANIFEST)
        assert "librarian" in m.subagents, (
            f"lore must ship the 'librarian' subagent; got {sorted(m.subagents)}"
        )
        assert "loremaster" not in m.subagents, (
            "lore still ships old 'loremaster' subagent — renamed to 'librarian'"
        )

    def test_lore_sessions_references_finish_skill(self):
        """lore sessions capability must reference skills/finish (not skills/finished)."""
        m = load_manifest(_LORE_MANIFEST)
        assert "finish" in m.skills, (
            f"lore must ship the 'finish' skill; got {sorted(m.skills)}"
        )
        assert "finished" not in m.skills, (
            "lore still ships 'finished' skill — renamed to 'finish'"
        )

    def test_craft_execute_references_assumption_prover_and_executor(self):
        """craft execute capability must reference assumption-prover.md and executor.md."""
        m = load_manifest(_CRAFT_MANIFEST)
        assert {"assumption-prover", "executor"} <= set(m.subagents), (
            f"craft must ship 'assumption-prover' + 'executor' subagents; got {sorted(m.subagents)}"
        )
        assert "scout" not in m.subagents, (
            "craft still ships old 'scout' subagent — renamed to 'assumption-prover'"
        )
        assert "trailblazer" not in m.subagents, (
            "craft still ships old 'trailblazer' subagent — renamed to 'executor'"
        )
        assert "execute" in m.skills, (
            f"craft must ship the 'execute' skill; got {sorted(m.skills)}"
        )
        assert "subagent-driven-development" not in m.skills, (
            "craft still ships old 'subagent-driven-development' skill — renamed to 'execute'"
        )

    def test_craft_council_references_council_agents(self):
        """craft council capability must reference the bare-named council agents
        (advocate/builder/breaker/attacker)."""
        m = load_manifest(_CRAFT_MANIFEST)
        for stem in ("advocate", "builder", "breaker", "attacker"):
            assert stem in m.subagents, (
                f"craft must ship the {stem!r} council subagent; got {sorted(m.subagents)}"
            )
        for agent in m.subagents:
            assert not agent.startswith("council-"), (
                f"craft still ships old council- agent: {agent}"
            )
            assert not agent.startswith("circle-"), (
                f"craft still ships old circle- agent: {agent}"
            )


def _read_agent_text(agent_file: Path) -> str:
    """Return the agent file's full text, lowercased.

    Reads the WHOLE file (not just the frontmatter `description:` block) — the
    differentiation asserts below substring-match against the entire agent prose,
    so the name must not imply a narrower scope (a latent false-green otherwise).
    """
    text = agent_file.read_text()
    return text.lower()


class TestCouncilAgentStandaloneDescriptions:
    """Renamed council agents must drop the 'use only when ... council review step' gate
    and carry a differentiating standalone 'use when' phrase so natural-language dispatch
    routes them apart from the overlapping troubleshooter / security-auditor agents.
    """

    def test_council_agents_drop_council_only_gate(self):
        """No renamed council agent may keep the 'use only when invoked'
        standalone-blocking clause."""
        for stem in ("advocate", "builder", "breaker", "attacker"):
            path = _CRAFT_PLUGIN_ROOT / "agents" / f"{stem}.md"
            assert path.exists(), f"renamed council agent not found: {path}"
            desc = _read_agent_text(path)
            assert "use only when invoked by a planning skill" not in desc, (
                f"{stem}.md still gates itself to the planning council step — "
                "drop the 'Use only when invoked by a planning skill's council review step' clause."
            )

    def test_breaker_differentiates_from_troubleshooter(self):
        """breaker's description must carry a 'use when' phrase absent from troubleshooter's.

        breaker probes a design for failure modes / edge cases / recovery *before building*;
        troubleshooter diagnoses root cause of an *existing* failure. The differentiating
        phrase must live in breaker and not in troubleshooter.
        """
        breaker = _read_agent_text(_CRAFT_PLUGIN_ROOT / "agents" / "breaker.md")
        troubleshooter = _read_agent_text(_CRAFT_PLUGIN_ROOT / "agents" / "troubleshooter.md")
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
        attacker = _read_agent_text(_CRAFT_PLUGIN_ROOT / "agents" / "attacker.md")
        auditor = _read_agent_text(_CRAFT_PLUGIN_ROOT / "agents" / "security-auditor.md")
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
        """Return list of missing src paths for the full composed inventory."""
        m = load_manifest(manifest_path)
        plan = compose_plan(
            m,
            {n: None for n in m.subagents},
            {n: None for n in m.skills},
            tmp_path / "all",
        )
        missing = [str(op.src) for op in plan.ops if not op.src.exists()]
        return missing

    def test_lore_all_capabilities_resolve_to_existing_src(self, tmp_path):
        """Every CopyOp.src for the full lore inventory must exist on disk."""
        missing = self._check_manifest(_LORE_MANIFEST, tmp_path)
        assert not missing, (
            "R-6: lore compose_plan produced CopyOps with missing src:\n"
            + "\n".join(missing)
        )

    def test_craft_all_capabilities_resolve_to_existing_src(self, tmp_path):
        """Every CopyOp.src for the full craft inventory must exist on disk."""
        missing = self._check_manifest(_CRAFT_MANIFEST, tmp_path)
        assert not missing, (
            "R-6: craft compose_plan produced CopyOps with missing src:\n"
            + "\n".join(missing)
        )


# ---------------------------------------------------------------------------
# 4. New agent names appear in compose output for council/execute
# ---------------------------------------------------------------------------


class TestNewAgentNamesInCompose:
    """compose_plan for craft council/execute resolves new agent names."""

    def test_craft_council_compose_includes_council_agents(self, tmp_path):
        """craft council compose includes the bare-named council agent CopyOps."""
        m = load_manifest(_CRAFT_MANIFEST)
        plan = compose_plan(
            m,
            {"advocate": None, "builder": None, "breaker": None, "attacker": None},
            {},
            tmp_path / "dest",
        )
        agent_srcs = {op.src.name for op in plan.ops if op.src.is_file()}
        for name in ("advocate.md", "builder.md", "breaker.md", "attacker.md"):
            assert name in agent_srcs, (
                f"compose_plan for craft 'council' must include {name}; got {agent_srcs}"
            )

    def test_craft_execute_compose_includes_assumption_prover_and_executor(self, tmp_path):
        """craft execute compose includes assumption-prover.md and executor.md CopyOps."""
        m = load_manifest(_CRAFT_MANIFEST)
        plan = compose_plan(
            m,
            {"assumption-prover": None, "executor": None},
            {"execute": None},
            tmp_path / "dest",
        )
        agent_srcs = {op.src.name for op in plan.ops if op.src.is_file()}
        assert "assumption-prover.md" in agent_srcs, (
            f"compose_plan for craft 'execute' must include assumption-prover.md; got {agent_srcs}"
        )
        assert "executor.md" in agent_srcs, (
            f"compose_plan for craft 'execute' must include executor.md; got {agent_srcs}"
        )

    def test_lore_recall_compose_includes_librarian(self, tmp_path):
        """lore recall compose includes librarian.md CopyOp (was loremaster.md)."""
        m = load_manifest(_LORE_MANIFEST)
        plan = compose_plan(m, {"librarian": None}, {}, tmp_path / "dest")
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
        ("advocate", _CRAFT_PLUGIN_ROOT / "agents" / "advocate.md"),
        ("builder", _CRAFT_PLUGIN_ROOT / "agents" / "builder.md"),
        ("breaker", _CRAFT_PLUGIN_ROOT / "agents" / "breaker.md"),
        ("attacker", _CRAFT_PLUGIN_ROOT / "agents" / "attacker.md"),
        ("assumption-prover", _CRAFT_PLUGIN_ROOT / "agents" / "assumption-prover.md"),
        ("executor", _CRAFT_PLUGIN_ROOT / "agents" / "executor.md"),
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
# 7. Slice 2 — craft:consult skill + single-source council membership
# ---------------------------------------------------------------------------

_CONSULT_SKILL = _CRAFT_PLUGIN_ROOT / "skills" / "consult" / "SKILL.md"
_COUNCIL_INCLUDE = _CRAFT_PLUGIN_ROOT / "skills" / "_shared" / "council.md"
_PLANNING_SKILL = _CRAFT_PLUGIN_ROOT / "skills" / "plan" / "SKILL.md"

# The four council agent stems the membership single-source-of-truth must name,
# each resolving to agents/<stem>.md.
_COUNCIL_STEMS = ("advocate", "builder", "breaker", "attacker")


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


class TestConsultSkillAndSharedCouncil:
    """Slice 2: the craft:consult skill and the single-source council membership include."""

    def test_consult_skill_dir_exists_with_skill_md(self):
        """skills/consult/ must exist with a SKILL.md (new standalone-invocable council skill)."""
        assert _CONSULT_SKILL.exists(), (
            f"skills/consult/SKILL.md not found at {_CONSULT_SKILL} — "
            "create the craft:consult skill that convenes the council panel."
        )

    def test_consult_skill_is_registrable(self):
        """skills/consult/SKILL.md must carry non-empty name: + description: frontmatter.

        Without registrable frontmatter Claude Code will not register it as a
        /craft:consult command (same invariant as test_skills_registrable).
        """
        assert _CONSULT_SKILL.exists(), f"skills/consult/SKILL.md not found at {_CONSULT_SKILL}"
        assert _has_registrable_frontmatter(_CONSULT_SKILL), (
            "skills/consult/SKILL.md must open with frontmatter carrying a non-empty "
            "`name:` and `description:` or Claude Code will not register /craft:consult"
        )

    def test_consult_frontmatter_name_is_consult(self):
        """skills/consult/SKILL.md frontmatter name: must be 'consult'."""
        name = _parse_frontmatter_name(_CONSULT_SKILL)
        assert name == "consult", (
            f"skills/consult/SKILL.md frontmatter name: is {name!r}, expected 'consult'"
        )

    def test_council_include_exists(self):
        """skills/_shared/council.md must exist as the single-source council membership include."""
        assert _COUNCIL_INCLUDE.exists(), (
            f"skills/_shared/council.md not found at {_COUNCIL_INCLUDE} — "
            "create the shared four-agent membership include."
        )

    def test_council_include_names_all_four_agents(self):
        """The shared include must name all four council agent stems."""
        assert _COUNCIL_INCLUDE.exists(), (
            f"skills/_shared/council.md not found at {_COUNCIL_INCLUDE}"
        )
        text = _COUNCIL_INCLUDE.read_text()
        missing = [stem for stem in _COUNCIL_STEMS if stem not in text]
        assert not missing, (
            f"skills/_shared/council.md must name all four council agents; missing: {missing}"
        )

    def test_council_include_stems_resolve_to_agent_files(self):
        """C1: each of the four stems named in the include resolves to agents/<stem>.md.

        This is the anti-drift assertion — the single-source membership cannot silently
        diverge from the renamed agent files that planning + consult both dispatch off it.
        Parses the include for the stems it actually names, then asserts each is an
        existing agent file by exact name.
        """
        assert _COUNCIL_INCLUDE.exists(), (
            f"skills/_shared/council.md not found at {_COUNCIL_INCLUDE}"
        )
        text = _COUNCIL_INCLUDE.read_text()
        for stem in _COUNCIL_STEMS:
            assert stem in text, (
                f"council.md must name the {stem!r} agent (single source of truth)"
            )
            agent_file = _CRAFT_PLUGIN_ROOT / "agents" / f"{stem}.md"
            assert agent_file.exists(), (
                f"council.md names {stem!r} but {agent_file} does not exist — "
                "the membership include drifted from the renamed agent files."
            )

    def test_consult_references_shared_council_include(self):
        """consult must read membership from the shared include (not hardcode its own list)."""
        assert _CONSULT_SKILL.exists(), f"skills/consult/SKILL.md not found at {_CONSULT_SKILL}"
        text = _CONSULT_SKILL.read_text()
        assert "_shared/council.md" in text, (
            "skills/consult/SKILL.md must reference the shared '_shared/council.md' include "
            "as the single source of council membership."
        )

    def test_planning_references_shared_council_include(self):
        """planning's Council Review step must read membership from the shared include.

        Planning must NOT call consult (the unreliable skill->skill chain) — it dispatches
        the council directly off the shared list, same as consult.
        """
        assert _PLANNING_SKILL.exists(), f"planning/SKILL.md not found at {_PLANNING_SKILL}"
        text = _PLANNING_SKILL.read_text()
        assert "_shared/council.md" in text, (
            "planning/SKILL.md Council Review step must reference the shared "
            "'_shared/council.md' include rather than hardcoding the membership."
        )

    def test_planning_dispatches_council_directly_not_via_consult(self):
        """planning must dispatch the four agents directly, not delegate to the consult skill.

        The robust invariant is the presence of the direct-dispatch instruction (parallel
        Agent calls to the four members), NOT the absence of the string 'consult' — planning
        legitimately *mentions* consult to explain it must not delegate to it. We assert the
        direct-dispatch evidence (each member named for an Agent dispatch) so a future rewrite
        that swaps direct dispatch for a `/craft:consult` call would drop these and fail.
        """
        assert _PLANNING_SKILL.exists(), f"planning/SKILL.md not found at {_PLANNING_SKILL}"
        text = _PLANNING_SKILL.read_text()
        assert "Agent` tool calls" in text, (
            "planning/SKILL.md must instruct direct parallel `Agent` tool calls to the council "
            "members — not delegate the panel to the consult skill."
        )
        for stem in _COUNCIL_STEMS:
            assert stem in text, (
                f"planning/SKILL.md must still name {stem!r} for direct council dispatch"
            )

    def test_craft_council_capability_includes_consult_skill(self):
        """craft must ship the consult skill."""
        m = load_manifest(_CRAFT_MANIFEST)
        assert "consult" in m.skills, (
            f"craft must ship the 'consult' skill; got {sorted(m.skills)}"
        )

    def test_craft_council_compose_includes_consult_skill(self, tmp_path):
        """compose_plan of the consult skill must include its dir as a CopyOp."""
        m = load_manifest(_CRAFT_MANIFEST)
        plan = compose_plan(m, {}, {"consult": None}, tmp_path / "dest")
        skill_srcs = {op.src.name for op in plan.ops if op.src.is_dir()}
        assert "consult" in skill_srcs, (
            f"compose_plan for craft 'council' must include the consult skill dir; got {skill_srcs}"
        )


# ---------------------------------------------------------------------------
# 8. Slice 3 — planning Step-10 handoff names /craft:execute without a
#    self-referential trigger-verb collision (KU — accept-as-risk, by test).
# ---------------------------------------------------------------------------


class TestPlanningExecuteHandoff:
    """The reworded planning Step-10 handoff must keep pulling in the renamed
    `/craft:execute` skill while NOT making the continuation trigger word identical
    to the bare skill name (the old prompt said *reply **execute** to hand off to
    subagent-driven-development* — post-rename the verb would collide with the
    skill name `/craft:execute`).

    These are meaningful WANT-artifact assertions, not vacuous absence checks:
    the handoff target `/craft:execute` must be named, and the trigger phrasing
    must be present in a non-self-referential form.
    """

    def test_planning_names_craft_execute_as_handoff_target(self):
        """planning/SKILL.md must name `/craft:execute` as the skill the handoff pulls in."""
        assert _PLANNING_SKILL.exists(), f"planning/SKILL.md not found at {_PLANNING_SKILL}"
        text = _PLANNING_SKILL.read_text()
        assert "/craft:execute" in text, (
            "planning/SKILL.md Step-10 handoff must name `/craft:execute` as the "
            "skill it hands off to (the renamed subagent-driven-development)."
        )

    def test_planning_handoff_trigger_is_not_self_referential(self):
        """The continuation prompt must not instruct the user to reply with the bare
        skill name as the trigger word (the old 'reply **execute**' collision).

        After rename, 'execute' == the skill name `/craft:execute`. A self-referential
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
            "skill name `/craft:execute`. Use a non-colliding verb (e.g. **build**)."
        )
        assert "**build**" in text, (
            "planning/SKILL.md handoff must offer a non-colliding continuation verb "
            "(e.g. **build**) so the trigger word is not identical to the `/craft:execute` "
            "skill name."
        )


# ---------------------------------------------------------------------------
# 9. Slice 4 — craft skill renames: followup→polish, handoff→shelve,
#    planning→plan, requesting-code-review→review.
#
# CRITICAL scoping (Builder/Advocate, per-slice green-bar rule): craft keeps a
# `review` *capability* name and the new `review` *skill* slots under it. The
# `requesting-code-review` forbid greps the LITERAL old token / skill path,
# never a bare `review`. Likewise `plan` is common English — assert the
# `skills/plan/` dir/path, never a bare `plan` word. And `handoff`/`followup`
# survive legitimately as the `lore handoff` CLI subcommand, the
# `handoff_capture.py` script, `~/.craft/handoffs/`, and the plan-brief schema
# tokens `followup-to:` / `-followup-<n>` — so those forbids target the skill
# IDENTITY only (skill path, `name:` frontmatter, `/forge:` invocation), never
# the bare word.
# ---------------------------------------------------------------------------

_POLISH_SKILL = _CRAFT_PLUGIN_ROOT / "skills" / "polish" / "SKILL.md"
_SHELVE_SKILL = _CRAFT_PLUGIN_ROOT / "skills" / "shelve" / "SKILL.md"
_PLAN_SKILL = _CRAFT_PLUGIN_ROOT / "skills" / "plan" / "SKILL.md"
_REVIEW_SKILL = _CRAFT_PLUGIN_ROOT / "skills" / "review" / "SKILL.md"


class TestSlice4SkillRenameForbids:
    """Old forge skill identifiers must be gone from tools/ source.

    Each forbid is token/path-scoped to the skill identity so it never trips a
    legitimate surviving name (the `review`/`council` capabilities, the
    `lore handoff` CLI subcommand, the `handoff_capture.py` script, the
    `followup-to:` plan-brief schema field).
    """

    def test_no_skills_followup_path(self):
        """The `skills/followup` skill path must not appear after rename to skills/polish."""
        files = _collect_files()
        hits = _grep_files(r"skills/followup", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'skills/followup' — "
                "must be 'skills/polish' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_skills_handoff_path(self):
        """The `skills/handoff` skill path must not appear after rename to skills/shelve.

        Token-scoped to the skill path so it does NOT flag the legitimately
        surviving `lore handoff` CLI subcommand, the `handoff_capture.py` script,
        or the `~/.craft/handoffs/` degraded-write location.
        """
        files = _collect_files()
        hits = _grep_files(r"skills/handoff", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'skills/handoff' — "
                "must be 'skills/shelve' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_skills_planning_path(self):
        """The `skills/planning` skill path must not appear after rename to skills/plan."""
        files = _collect_files()
        hits = _grep_files(r"skills/planning", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'skills/planning' — "
                "must be 'skills/plan' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_requesting_code_review_token(self):
        """The literal `requesting-code-review` token must not appear after rename to skills/review.

        Greps the LITERAL old token (path and skill stem) — NEVER a bare `review`,
        which survives as the craft `review` capability name and elsewhere.
        """
        files = _collect_files()
        hits = _grep_files(r"requesting-code-review", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'requesting-code-review' — "
                "must be 'review' (skill) after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_forge_handoff_command_invocation(self):
        """The `/forge:handoff` skill invocation must not appear after rename to /craft:shelve.

        Scoped to the slash-command form so it targets the skill identity, not the
        `lore handoff` subcommand or the `handoff_capture.py` helper name.
        """
        files = _collect_files()
        hits = _grep_files(r"/forge:handoff", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of '/forge:handoff' — "
                "must be '/craft:shelve' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_followup_command_invocation(self):
        """The `/followup` and `/forge:followup` skill invocations must not appear after
        rename to polish.

        Scoped to the slash-command form so it targets the skill identity, NOT the
        `followup-to:` plan-brief frontmatter field or the `-followup-<n>` slug
        convention (those are the brief schema, not the skill name).
        """
        files = _collect_files()
        hits = _grep_files(r"/(forge:)?followup", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of '/followup' invocation — "
                "must be '/polish' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_followup_skill_name_frontmatter(self):
        """No SKILL.md may carry `name: followup` after rename to polish."""
        files = _collect_files()
        hits = _grep_files(r"^name: followup\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of `name: followup` frontmatter — "
                "must be `name: polish` after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_handoff_skill_name_frontmatter(self):
        """No SKILL.md may carry `name: handoff` after rename to shelve."""
        files = _collect_files()
        hits = _grep_files(r"^name: handoff\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of `name: handoff` frontmatter — "
                "must be `name: shelve` after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_planning_skill_name_frontmatter(self):
        """No SKILL.md may carry `name: planning` after rename to plan."""
        files = _collect_files()
        hits = _grep_files(r"^name: planning\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of `name: planning` frontmatter — "
                "must be `name: plan` after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_old_skill_dirs_gone(self):
        """The four old skill directories must not exist under craft plugins."""
        for old in ("followup", "handoff", "planning", "requesting-code-review"):
            old_dir = _CRAFT_PLUGIN_ROOT / "skills" / old
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
            f"or Claude Code will not register /craft:{stem}"
        )
        name = _parse_frontmatter_name(skill_md)
        assert name == stem, (
            f"skills/{stem}/SKILL.md frontmatter name: is {name!r}, expected {stem!r}"
        )


class TestSlice4ManifestRepointed:
    """capabilities.toml must repoint the four renamed skills atomically."""

    def test_base_skills_repointed_to_shelve_and_polish(self):
        """craft must ship the renamed shelve + polish skills (pickup stays); the old
        handoff + followup skills must be gone."""
        m = load_manifest(_CRAFT_MANIFEST)
        skills = m.skills
        assert "shelve" in skills, f"craft must ship the 'shelve' skill; got {sorted(skills)}"
        assert "polish" in skills, f"craft must ship the 'polish' skill; got {sorted(skills)}"
        assert "pickup" in skills, f"craft must keep the 'pickup' skill; got {sorted(skills)}"
        assert "handoff" not in skills, "craft still ships old 'handoff' skill"
        assert "followup" not in skills, "craft still ships old 'followup' skill"

    def test_planning_capability_references_plan_skill(self):
        """craft must ship the planner + architect subagents and the plan skill (not planning)."""
        m = load_manifest(_CRAFT_MANIFEST)
        assert {"planner", "architect"} <= set(m.subagents), (
            f"craft must ship 'planner' + 'architect' subagents; got {sorted(m.subagents)}"
        )
        assert "plan" in m.skills, (
            f"craft must ship the 'plan' skill; got {sorted(m.skills)}"
        )
        assert "planning" not in m.skills, (
            "craft still ships old 'planning' skill — renamed to 'plan'"
        )

    def test_review_capability_references_review_skill(self):
        """craft must ship the code-reviewer subagent and the review skill (not
        requesting-code-review).

        The skill NAME stays `review`; only the old skill path changes.
        """
        m = load_manifest(_CRAFT_MANIFEST)
        assert "code-reviewer" in m.subagents, (
            f"craft must ship the 'code-reviewer' subagent; got {sorted(m.subagents)}"
        )
        assert "review" in m.skills, (
            f"craft must ship the 'review' skill; got {sorted(m.skills)}"
        )
        assert "requesting-code-review" not in m.skills, (
            "craft still ships old 'requesting-code-review' skill — renamed to 'review'"
        )

    def test_renamed_skills_compose_to_existing_src(self, tmp_path):
        """compose_plan for the plan + review skills must resolve the renamed dirs on disk."""
        m = load_manifest(_CRAFT_MANIFEST)
        for skill in ("plan", "review"):
            plan = compose_plan(m, {}, {skill: None}, tmp_path / skill)
            skill_srcs = {op.src.name for op in plan.ops if op.src.is_dir()}
            assert skill in skill_srcs, (
                f"compose({skill}) must include the {skill} skill dir; got {skill_srcs}"
            )


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
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'skills/radar' — "
                "must be 'skills/follow-up' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_skills_check_radar_path(self):
        """`skills/check-radar` skill path must not appear after rename to skills/check-in."""
        files = _collect_files()
        hits = _grep_files(r"skills/check-radar\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'skills/check-radar' — "
                "must be 'skills/check-in' after rename:"
            ]
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
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of the check-radar skill identity — "
                "must be 'check-in' after rename:"
            ]
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
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'radar_due' — "
                "must be 'follow_up_due' after rename:"
            ]
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
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'type: radar' in skills/templates — "
                "must be 'type: follow-up' after rename:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_lore_new_radar_invocation(self):
        """The `lore new radar` CLI invocation must be gone after the bucket rename to follow-up."""
        files = _collect_files()
        hits = _grep_files(r"lore new radar\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'lore new radar' — "
                "must be 'lore new follow-up' after rename:"
            ]
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
    """The renamed follow-up + check-in skill dirs were deleted in S6 Slice 2.

    S6 Slice 2 deleted the 7 obsolete per-kind capture skills (including follow-up
    and check-in), which are now replaced by the `lore record`/`lore session` CLI.
    Guard that they are absent so they cannot be accidentally re-added without
    updating the skill roster.
    """

    @pytest.mark.parametrize("stem,skill_md", [
        ("follow-up", _FOLLOW_UP_SKILL),
        ("check-in", _CHECK_IN_SKILL),
    ])
    def test_renamed_skill_dir_exists_with_skill_md(self, stem: str, skill_md: Path):
        # S6 Slice 2: these skills were deleted — they must no longer exist.
        assert not skill_md.exists(), (
            f"skills/{stem}/SKILL.md found at {skill_md} — "
            f"this skill was deleted in S6 Slice 2 (replaced by lore record CLI). "
            f"Remove it or update the roster."
        )

    @pytest.mark.parametrize("stem,skill_md", [
        ("follow-up", _FOLLOW_UP_SKILL),
        ("check-in", _CHECK_IN_SKILL),
    ])
    def test_renamed_skill_is_registrable_with_matching_name(self, stem: str, skill_md: Path):
        # S6 Slice 2: these skills were deleted — confirm they are absent.
        assert not skill_md.exists(), (
            f"skills/{stem}/SKILL.md found at {skill_md} — "
            f"this skill was deleted in S6 Slice 2 (replaced by lore record CLI)."
        )


class TestSlice6ManifestAndTemplate:
    """lore capabilities.toml no longer includes the two Slice-6-renamed skills.

    S6 Slice 2 deleted follow-up and check-in (they were replaced by lore record CLI).
    The template files still exist (used by the CLI); the skills dirs are gone.
    """

    def test_capture_capability_references_follow_up_and_check_in(self):
        """After S6 Slice 2, follow-up and check-in must NOT be in lore skills
        (they were deleted — replaced by lore record CLI surface)."""
        m = load_manifest(_LORE_MANIFEST)
        assert "follow-up" not in m.skills, (
            f"lore must NOT ship the 'follow-up' skill (deleted in S6 Slice 2); got {sorted(m.skills)}"
        )
        assert "check-in" not in m.skills, (
            f"lore must NOT ship the 'check-in' skill (deleted in S6 Slice 2); got {sorted(m.skills)}"
        )
        assert "radar" not in m.skills, (
            "lore still ships old 'radar' skill — renamed to 'follow-up' then deleted"
        )
        assert "check-radar" not in m.skills, (
            "lore still ships old 'check-radar' skill — renamed to 'check-in' then deleted"
        )

    def test_follow_up_template_exists_with_type_follow_up(self):
        """templates/follow-up.md must exist with `type: follow-up` (was templates/radar.md).
        The skill dir is deleted but the template is retained for `lore record` CLI use."""
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
        """lore manifest validates and session skills compose correctly after S6 Slice 2."""
        m = load_manifest(_LORE_MANIFEST)
        plan = compose_plan(
            m, {}, {"checkpoint": None, "finish": None}, tmp_path / "session"
        )
        skill_srcs = {op.src.name for op in plan.ops if op.src.is_dir()}
        assert "checkpoint" in skill_srcs, (
            f"compose must include the checkpoint skill dir; got {skill_srcs}"
        )
        assert "finish" in skill_srcs, (
            f"compose must include the finish skill dir; got {skill_srcs}"
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
#   - `review`/`tend` survive as craft's `review` *capability* + the new craft
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
    English verb `reflect`, craft's `review` capability + Slice-4 `skills/review`,
    network-`ping` prose).
    """

    # ---- reflect (lore) ----------------------------------------------------

    def test_no_skills_reflect_path(self):
        """`skills/reflect` skill path must be gone (skill deleted)."""
        files = _collect_files()
        hits = _grep_files(r"skills/reflect\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'skills/reflect' — "
                "the reflect skill is deleted:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_reflect_sessions_helper(self):
        """The `reflect_sessions` backing script (filename / import) must be gone."""
        files = _collect_files()
        hits = _grep_files(r"reflect_sessions", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of 'reflect_sessions' — "
                "the script is deleted:"
            ]
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
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of the reflect skill identity — deleted:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    # ---- tend / review (lore, PATH-SCOPED to tools/lore) -------------------

    def test_no_lore_skills_review_or_tend_path(self):
        """`skills/review` / `skills/tend` lore skill paths must be gone.

        PATH-SCOPED to tools/lore/ so it does NOT flag craft's Slice-4 `skills/review`
        (the renamed requesting-code-review skill, a legitimate survivor).
        """
        hits = _grep_files(r"skills/(review|tend)\b", _LORE_FILES)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of lore 'skills/review|skills/tend' — deleted:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_review_helper_script(self):
        """The lore `review.py` backing script (filename / import) must be gone.

        PATH-SCOPED to tools/lore/. Matches the helper identity `review.py` /
        `import review` / `load_script("review")` — not the English word.
        """
        hits = _grep_files(
            r"\breview\.py\b|import review\b|load_script\(\"review\"\)|from review import",
            _LORE_FILES,
        )
        if hits:
            msg_lines = [f"Found {len(hits)} occurrence(s) of the lore review.py helper — deleted:"]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_lore_review_or_tend_command_or_name(self):
        """`/lore:review` / `/lore:tend` invocations and `name: review`/`name: tend`
        frontmatter must be gone (the lore tend/review skill is deleted).

        PATH-SCOPED to tools/lore/ — craft's `review` capability/skill is untouched.
        """
        hits = _grep_files(r"/lore:(review|tend)\b|^name: (review|tend)\b", _LORE_FILES)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of the lore review/tend skill identity — "
                "deleted:"
            ]
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
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of lore 'skills/ping' — "
                "the skill is deleted:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_lore_ping_command_or_name(self):
        """`/lore:ping` invocation and `name: ping` frontmatter must be gone."""
        files = _collect_files()
        hits = _grep_files(r"/lore:ping\b|^name: ping\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of the lore ping skill identity — deleted:"
            ]
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
        assert not (_CRAFT_PLUGIN_ROOT / "agents" / "forge-ping.md").exists(), (
            "agents/forge-ping.md still exists — Slice 7 deletes the forge-ping agent"
        )

    def test_lore_base_drops_ping_skill(self):
        """lore [tool] base must no longer list skills/ping."""
        m = load_manifest(_LORE_MANIFEST)
        assert "skills/ping" not in m.base, (
            f"lore base still references deleted 'skills/ping'; got {m.base}"
        )

    def test_craft_helpers_drops_forge_ping_agent(self):
        """craft must no longer ship the forge-ping subagent."""
        m = load_manifest(_CRAFT_MANIFEST)
        assert "forge-ping" not in m.subagents, (
            f"craft still ships deleted 'forge-ping' subagent; got {sorted(m.subagents)}"
        )

    def test_landing_claims_has_no_dangling_deleted_entries(self):
        """landing_claims.toml must carry no claim pointing at a deleted surface."""
        text = _LANDING_CLAIMS.read_text()
        for ref in ("skills/reflect", "skills/tend", "skills/ping", "agents/forge-ping.md"):
            assert ref not in text, (
                f"landing_claims.toml still has a dangling claim for deleted '{ref}'"
            )


class TestSlice8ArtistCutover:
    """The artist brainstorm cutover has landed (Phase A, in-repo).

    `brainstorm/SKILL.md`'s `design_mockup` extension point now names the craft
    `artist` agent as its concrete default provider, and `design-authoring.md`
    no longer marks the `design_mockup` seam RESERVED / not-yet-wired / a
    follow-up. (External `design-mockup-writer` retirement is Phase B and is
    deliberately out of this guard's scope.)
    """

    _BRAINSTORM_SKILL = (
        _LORE_PLUGIN_ROOT / "skills" / "brainstorm" / "SKILL.md"
    )
    _DESIGN_AUTHORING = (
        _CRAFT_PLUGIN_ROOT / "docs" / "design-authoring.md"
    )

    def test_brainstorm_names_artist_as_design_mockup_provider(self):
        """brainstorm/SKILL.md's design_mockup point must name `artist` as provider."""
        text = self._BRAINSTORM_SKILL.read_text()
        assert "design_mockup" in text, (
            "brainstorm/SKILL.md no longer mentions the design_mockup extension point"
        )
        assert "artist" in text, (
            "brainstorm/SKILL.md does not name the `artist` agent — the cutover "
            "rewires design_mockup to dispatch the craft artist by default"
        )

    def test_design_authoring_seam_no_longer_reserved(self):
        """design-authoring.md must not mark the design_mockup seam RESERVED/follow-up."""
        text = self._DESIGN_AUTHORING.read_text()
        for stale in ("RESERVED", "not yet wired", "cutover is a follow-up"):
            assert stale not in text, (
                f"design-authoring.md still marks the seam {stale!r}; the brainstorm "
                "cutover has landed — flip the seam to LIVE"
            )


# ---------------------------------------------------------------------------
# 12. Slice 9 — comprehensive cross-tool finisher.
#
# After Slice 6 (radar→follow-up data layer) and Slice 7 (reflect/tend/review
# deletions), the LAST legitimate survivors of the word `radar` in tools/{lore,
# craft} are the one-shot vault migration script + its test (the migration
# FROM-side). Everything else — harvest emitters, dead command refs, taxonomy
# prose, stale fixture dir names — must be gone. This consolidates the bare
# `\bradar\b == 0` guard the earlier slices deferred, plus the dead-command and
# harvest-typed-prefix forbids and the design-mockup-writer == 0 sweep.
#
# Allowlists are FILENAME-scoped (never bare-word), so the forbid stays a hard
# zero everywhere it should:
#   - `migrate_radar_to_follow_ups.py` + `test_migrate_radar_to_follow_ups.py`
#     legitimately name `radar` (the migration FROM-side).
#   - this guard file (`test_renames_guard.py`) names the forbidden tokens in
#     its own forbid/allowlist lines.
# The `/experiments/` frozen corpus is excluded defensively (mirrors
# test_no_council_review_prose) though no such dir exists under tools/ today.
# ---------------------------------------------------------------------------

# Filenames that legitimately carry the forbidden `radar` token.
_RADAR_ALLOWLIST_FILENAMES = {
    "migrate_radar_to_follow_ups.py",
    "test_migrate_radar_to_follow_ups.py",
    "test_renames_guard.py",
}

# Filenames that legitimately carry the `design-mockup-writer` token: the
# absence-assertion guard (`test_lore_skills_generic.py` — a ref that asserts
# the name is GONE, not a live routing ref) and the historical de-zenith test
# docstring.
_DESIGN_MOCKUP_WRITER_ALLOWLIST_FILENAMES = {
    "test_lore_skills_generic.py",
    "test_artist_dezenithed.py",
    "test_renames_guard.py",
}


def _scan_files_excluding_experiments() -> list[Path]:
    """Scannable tools/ source files, defensively excluding any frozen experiments corpus."""
    return [f for f in _collect_files() if "experiments" not in f.parts]


class TestSlice9RadarFullForbid:
    """Comprehensive radar sweep — `\\bradar\\b == 0` across tools/{lore,craft}
    except the migration script + its test (the legit FROM-side)."""

    def test_no_bare_radar_word_anywhere(self):
        """The bare word `radar` must not appear in tools/{lore,craft} source.

        Token-scoped allowlist by FILENAME for the migration script + its test
        (legit FROM-side) and this guard file's own forbid lines. Everything
        else — harvest emitters, taxonomy prose, dead command refs, stale
        fixture dir names — must read `follow-up`/`follow-ups` after Slice 9.
        """
        files = [
            f for f in _scan_files_excluding_experiments()
            if f.name not in _RADAR_ALLOWLIST_FILENAMES
        ]
        hits = _grep_files(r"\bradar\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of the bare word 'radar' — "
                "must be zero (→ follow-up):"
            ]
            for f, ln, line in hits[:20]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_lore_radar_command_ref(self):
        """`/lore:radar` dead-command ref must be gone (repointed to /lore:follow-up)."""
        files = _scan_files_excluding_experiments()
        hits = _grep_files(r"/lore:radar\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of dead command '/lore:radar' — "
                "must be '/lore:follow-up':"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_lore_check_radar_command_ref(self):
        """`/lore:check-radar` dead-command ref must be gone (repointed to /lore:check-in).

        (test_no_check_radar_command_token already forbids it via the same form;
        this is the Slice-9 explicit dead-command sweep assertion.)
        """
        files = _scan_files_excluding_experiments()
        hits = _grep_files(r"/lore:check-radar\b", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of dead command '/lore:check-radar' — "
                "must be '/lore:check-in':"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))

    def test_no_radar_harvest_typed_prefix(self):
        """The `radar:` harvest typed-prefix must be gone from agent/skill bodies.

        lore drops the `radar` harvest type entirely (Slice 6); emitters must now
        instruct `follow-up:`. Matches the typed-prefix forms the harvest parser
        keys on — a backtick-wrapped or bare `radar:` list bullet — not arbitrary
        prose ending in a colon.
        """
        files = _scan_files_excluding_experiments()
        hits = _grep_files(r"`radar:`|^\s*-\s*\*\*radar\*\*|^\s*-\s*radar:", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of the `radar:` harvest typed-prefix — "
                "must be `follow-up:`:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))


class TestSlice9DesignMockupWriterForbid:
    """design-mockup-writer == 0 across tools/ except the absence-assertion guard
    (test_lore_skills_generic.py) and the historical de-zenith test docstring."""

    def test_no_design_mockup_writer_live_ref(self):
        """`design-mockup-writer` must not appear as a live ref in tools/ source.

        FILENAME-allowlisted: `test_lore_skills_generic.py` (asserts the name is
        ABSENT — a guard, not a routing ref) and `test_artist_dezenithed.py`
        (historical docstring). Everything else — including any lingering
        retirement prose in design-authoring.md — must drop the token.
        """
        files = [
            f for f in _scan_files_excluding_experiments()
            if f.name not in _DESIGN_MOCKUP_WRITER_ALLOWLIST_FILENAMES
        ]
        hits = _grep_files(r"design-mockup-writer", files)
        if hits:
            msg_lines = [
                f"Found {len(hits)} occurrence(s) of live 'design-mockup-writer' ref — "
                "must be zero:"
            ]
            for f, ln, line in hits[:10]:
                msg_lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# 13. Slice 5 (Spec B) — old release identifiers absent from portage + landing
# ---------------------------------------------------------------------------

# Old release identifiers that must not appear in tools/portage or tools/landing.
# They legitimately still exist in tools/craft until Slice 6 deletes them — so
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
    """Collect all relevant source files in portage + landing (not craft)."""
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

    These identifiers legitimately still exist in tools/craft (until Slice 6
    deletes them). The assertions are scoped to portage+landing only so the
    existing craft tests remain unaffected.

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
# 14. Slice 6 (Spec B) — craft release cluster is GONE (the hard cut)
# ---------------------------------------------------------------------------

# The 8 release scripts moved to trailhead/vcs/ + landing — they must be ABSENT
# from tools/craft after Slice 6's deletion.
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

# The 7 release skill dirs deleted from craft.
_DELETED_RELEASE_SKILLS = [
    "create-pr",
    "update-pr",
    "watch-pr",
    "watch-preview",
    "merge-pr",
    "github-pr",
    "post-merge-decide",
]

# The 5 release agents deleted from craft (incl. pr-summarizer → portage's summarizer).
_DELETED_RELEASE_AGENTS = [
    "pr-updater.md",
    "watch-pr.md",
    "watch-preview.md",
    "diagnose-preview.md",
    "pr-summarizer.md",
]


class TestCraftReleaseClusterDeleted:
    """Slice 6 hard cut: craft no longer exposes a `release` capability and the
    moved release scripts/skills/agents are absent from tools/craft.

    portage + landing now own shipping + deploy; this locks the deletion so a
    revert (or a stray reintroduction) is caught by the suite.
    """

    def test_craft_manifest_has_no_release_capability(self):
        """craft must not ship a `release` skill or subagent."""
        m = load_manifest(_CRAFT_MANIFEST)
        assert "release" not in m.skills and "release" not in m.subagents, (
            "craft still ships a `release` skill/agent — Slice 6 deletes it; "
            "portage owns PR lifecycle and landing owns deploy soak now."
        )

    def test_craft_helpers_no_longer_lists_pr_summarizer(self):
        """craft must not ship the pr-summarizer subagent (→ portage summarizer)."""
        m = load_manifest(_CRAFT_MANIFEST)
        assert "pr-summarizer" not in m.subagents, (
            "craft still ships the 'pr-summarizer' subagent — it became "
            "portage's `summarizer`; remove agents/pr-summarizer.md."
        )

    @pytest.mark.parametrize("script", _MOVED_RELEASE_SCRIPTS)
    def test_moved_release_script_absent_from_craft(self, script: str):
        """Each moved release script must be absent from tools/craft."""
        path = _CRAFT_PLUGIN_ROOT / "scripts" / script
        assert not path.exists(), (
            f"{path.relative_to(_REPO_ROOT)} still exists — it moved to "
            "trailhead/vcs/ (or landing) in the extraction; delete the craft copy."
        )

    @pytest.mark.parametrize("skill", _DELETED_RELEASE_SKILLS)
    def test_deleted_release_skill_dir_absent_from_craft(self, skill: str):
        """Each deleted release skill directory must be absent from tools/craft."""
        path = _CRAFT_PLUGIN_ROOT / "skills" / skill
        assert not path.exists(), (
            f"craft skills/{skill}/ still exists — Slice 6 deletes the release "
            "skill cluster (portage/landing own it now)."
        )

    @pytest.mark.parametrize("agent", _DELETED_RELEASE_AGENTS)
    def test_deleted_release_agent_absent_from_craft(self, agent: str):
        """Each deleted release agent file must be absent from tools/craft."""
        path = _CRAFT_PLUGIN_ROOT / "agents" / agent
        assert not path.exists(), (
            f"craft agents/{agent} still exists — Slice 6 deletes the release "
            "agent cluster (portage/landing own it now)."
        )
