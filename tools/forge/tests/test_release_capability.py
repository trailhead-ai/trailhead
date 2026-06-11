"""Slice-6 release capability tests.

Test contract (B-4 + D-7/S-3 + registrable/generic):

  - B-4: load_manifest(tools/forge/capabilities.toml, validate=True) →
    [capabilities.release] resolves all 4 agents AND all 7 skills to
    EXISTING files/dirs (the relative-path-from-capabilities-root resolution
    is the actual risk — assert each resolves).

  - D-7/S-3: Leak gate over the soak agents (watch-preview.md, diagnose-preview.md)
    + soak_health.py + the 7 re-homed skills with an ephemeral tmp_path denylist
    → exit 0. Step-6 denylist tokens: dash0, SoakLease, preview*(url|server|host),
    admin-preview, \\bplatform\\b, mobile-app, cortana(-zh)?, asana, \\bzenith\\b,
    .workspace-manifest. No machine-local ~/.claude/leak-gate.denylist dependency (S-3).

  - Registrable/generic: test_agents_registrable.py / test_agents_generic.py pass
    for the 2 new soak agents (frontmatter name: matches filename, description present,
    tools present, no structural brain seams, no middle-band app tokens).

  - Agent↔script fidelity (Slice-5 test_agent_script_fidelity.py auto-discovers new
    agents; this file extends coverage for watch-preview.md's invocation of
    soak_health.py).

  - A-2 config-summary-on-launch: watch-preview.md carries the config-summary line.

Hermeticity: tmp_path-based ephemeral denylist; no real ~/.claude/; no network.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "forge" / "scripts"
AGENTS_DIR = REPO_ROOT / "plugins" / "forge" / "agents"
SKILLS_DIR = REPO_ROOT / "plugins" / "forge" / "skills"
CAPABILITIES_TOML = REPO_ROOT / "capabilities.toml"
GATE = SCRIPTS_DIR / "leak_gate.py"

WATCH_PREVIEW_MD = AGENTS_DIR / "watch-preview.md"
DIAGNOSE_PREVIEW_MD = AGENTS_DIR / "diagnose-preview.md"
SOAK_SCRIPT = SCRIPTS_DIR / "soak_health.py"

# The 7 release skills
_RELEASE_SKILLS = [
    "create-pr",
    "update-pr",
    "watch-pr",
    "watch-preview",
    "merge-pr",
    "github-pr",
    "post-merge-decide",
]

# The 4 release agents
_RELEASE_AGENTS = [
    "agents/pr-updater.md",
    "agents/watch-pr.md",
    "agents/watch-preview.md",
    "agents/diagnose-preview.md",
]


# ---------------------------------------------------------------------------
# B-4: capabilities.toml [capabilities.release] resolves all agents + skills
# ---------------------------------------------------------------------------

class TestReleaseCapabilityResolves:
    """B-4: every [capabilities.release] entry must resolve to an existing file/dir.

    M-1 note: trailhead.capabilities.load_manifest (the real validating loader from
    ce0de78) is NOT importable from the forge test harness — the forge pyproject.toml
    does not declare trailhead as a dependency and the trailhead package is not on
    sys.path when forge tests run (confirmed: `python3 -c "from trailhead.capabilities
    import load_manifest"` fails with ModuleNotFoundError from the forge test cwd).
    We use stdlib tomllib + path-existence checks instead. The real loader also
    validates confinement (D-F) and schema correctness — those are covered by
    trailhead/tests/test_capabilities.py over the live capabilities.toml.
    """

    def _load_capabilities(self) -> dict:
        """Load capabilities.toml and return the parsed dict (stdlib tomllib)."""
        import tomllib
        with open(CAPABILITIES_TOML, "rb") as f:
            return tomllib.load(f)

    def test_capabilities_toml_exists(self) -> None:
        assert CAPABILITIES_TOML.exists(), (
            f"capabilities.toml not found at {CAPABILITIES_TOML}"
        )

    def test_release_capability_section_exists(self) -> None:
        """[capabilities.release] section must exist in capabilities.toml."""
        caps = self._load_capabilities()
        assert "capabilities" in caps, "capabilities.toml must have a [capabilities] section"
        assert "release" in caps["capabilities"], (
            "capabilities.toml must have a [capabilities.release] section"
        )

    def test_release_agents_list_is_filled(self) -> None:
        """[capabilities.release].agents must not be empty (the empty stub is filled)."""
        caps = self._load_capabilities()
        agents = caps["capabilities"]["release"].get("agents", [])
        assert agents, (
            f"[capabilities.release].agents must not be empty — fill the WS-3 stub"
        )

    def test_release_skills_list_is_filled(self) -> None:
        """[capabilities.release].skills must not be empty (the empty stub is filled)."""
        caps = self._load_capabilities()
        skills = caps["capabilities"]["release"].get("skills", [])
        assert skills, (
            f"[capabilities.release].skills must not be empty — fill the WS-3 stub"
        )

    def test_all_four_release_agents_declared(self) -> None:
        """[capabilities.release].agents must include all 4 expected agents."""
        caps = self._load_capabilities()
        agents = caps["capabilities"]["release"].get("agents", [])
        for expected in _RELEASE_AGENTS:
            assert expected in agents, (
                f"[capabilities.release].agents missing {expected!r} — "
                f"declared agents: {agents}"
            )

    def test_all_seven_release_skills_declared(self) -> None:
        """[capabilities.release].skills must include all 7 expected skills."""
        caps = self._load_capabilities()
        skills = caps["capabilities"]["release"].get("skills", [])
        for skill in _RELEASE_SKILLS:
            # Skills are referenced as 'skills/<name>' per existing capabilities pattern
            expected = f"skills/{skill}"
            assert expected in skills, (
                f"[capabilities.release].skills missing {expected!r} — "
                f"declared skills: {skills}"
            )

    def test_all_release_agents_resolve_to_existing_files(self) -> None:
        """B-4: every declared agent path resolves to an existing file under the plugin root."""
        caps = self._load_capabilities()
        plugin_root = REPO_ROOT / "plugins" / "forge"
        agents = caps["capabilities"]["release"].get("agents", [])
        for agent_rel in agents:
            resolved = plugin_root / agent_rel
            assert resolved.exists(), (
                f"[capabilities.release].agents entry {agent_rel!r} resolves to "
                f"{resolved} which does NOT exist (dangling reference)"
            )

    def test_all_release_skills_resolve_to_existing_dirs(self) -> None:
        """B-4: every declared skill path resolves to an existing SKILL.md under the plugin root.

        The relative-path-from-capabilities-root resolution is the actual risk — assert each resolves.
        """
        caps = self._load_capabilities()
        plugin_root = REPO_ROOT / "plugins" / "forge"
        skills = caps["capabilities"]["release"].get("skills", [])
        for skill_rel in skills:
            # Each entry is 'skills/<name>' — the SKILL.md must exist inside
            skill_dir = plugin_root / skill_rel
            skill_md = skill_dir / "SKILL.md"
            assert skill_md.exists(), (
                f"[capabilities.release].skills entry {skill_rel!r} resolves to "
                f"{skill_md} which does NOT exist (dangling reference)"
            )


# ---------------------------------------------------------------------------
# Step-6 ephemeral denylist (D-7 / S-3)
# ---------------------------------------------------------------------------

_STEP6_DENYLIST_TOKENS = [
    r"zenithhealth",
    r"\bzenith\b",
    r"SoakLease",
    r"dash0",
    r"cortana(-zh)?",
    r"\basana\b",
    r"asana_sync",
    r"\.workspace-manifest",
    r"\bplatform\b",
    r"mobile-app",
    r"admin-preview",
    r"preview\s*(url|server|host)",
    r"brain/(designs|chrome|specs|plans|sessions)",
]


def _write_ephemeral_denylist(p: Path) -> Path:
    dl = p / "step6-soak-denylist.txt"
    dl.write_text("\n".join(_STEP6_DENYLIST_TOKENS) + "\n", encoding="utf-8")
    return dl


def _run_gate(trees: list[Path], denylist: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE), *[str(t) for t in trees], "--denylist", str(denylist)]
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# D-7 / S-3: Leak gate over soak agents + soak script + 7 skills
# ---------------------------------------------------------------------------

class TestSoakLeakGate:
    """D-7/S-3: no Step-6 zenith tokens in soak agents, script, or re-homed skills."""

    def test_watch_preview_is_clean(self, tmp_path: Path) -> None:
        if not WATCH_PREVIEW_MD.exists():
            pytest.skip("watch-preview.md not yet implemented")
        denylist = _write_ephemeral_denylist(tmp_path)
        r = _run_gate([WATCH_PREVIEW_MD], denylist)
        assert r.returncode == 0, (
            f"watch-preview.md contains forbidden Step-6 tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_diagnose_preview_is_clean(self, tmp_path: Path) -> None:
        if not DIAGNOSE_PREVIEW_MD.exists():
            pytest.skip("diagnose-preview.md not yet implemented")
        denylist = _write_ephemeral_denylist(tmp_path)
        r = _run_gate([DIAGNOSE_PREVIEW_MD], denylist)
        assert r.returncode == 0, (
            f"diagnose-preview.md contains forbidden Step-6 tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_soak_health_script_is_clean(self, tmp_path: Path) -> None:
        if not SOAK_SCRIPT.exists():
            pytest.skip("soak_health.py not yet implemented")
        denylist = _write_ephemeral_denylist(tmp_path)
        r = _run_gate([SOAK_SCRIPT], denylist)
        assert r.returncode == 0, (
            f"soak_health.py contains forbidden Step-6 tokens:\n{r.stdout}\n{r.stderr}"
        )

    @pytest.mark.parametrize("skill_name", _RELEASE_SKILLS)
    def test_release_skill_is_clean(self, skill_name: str, tmp_path: Path) -> None:
        skill_md = SKILLS_DIR / skill_name / "SKILL.md"
        if not skill_md.exists():
            pytest.skip(f"skills/{skill_name}/SKILL.md not yet implemented")
        denylist = _write_ephemeral_denylist(tmp_path)
        r = _run_gate([skill_md], denylist)
        assert r.returncode == 0, (
            f"skills/{skill_name}/SKILL.md contains forbidden Step-6 tokens:\n"
            f"{r.stdout}\n{r.stderr}"
        )

    def test_all_soak_surface_as_group_is_clean(self, tmp_path: Path) -> None:
        """The full Slice-6 surface (agents + script + skills) must be leak-gate clean."""
        denylist = _write_ephemeral_denylist(tmp_path)
        targets = []
        for path in [WATCH_PREVIEW_MD, DIAGNOSE_PREVIEW_MD, SOAK_SCRIPT]:
            if path.exists():
                targets.append(path)
        for skill_name in _RELEASE_SKILLS:
            skill_md = SKILLS_DIR / skill_name / "SKILL.md"
            if skill_md.exists():
                targets.append(skill_md)
        if not targets:
            pytest.skip("Slice-6 surface not yet implemented")
        for target in targets:
            r = _run_gate([target], denylist)
            assert r.returncode == 0, (
                f"{target.name} contains forbidden Step-6 tokens:\n{r.stdout}\n{r.stderr}"
            )

    def test_gate_effective_soaklease(self, tmp_path: Path) -> None:
        """Verify the denylist catches 'SoakLease' as a canary."""
        denylist = _write_ephemeral_denylist(tmp_path)
        dirty = tmp_path / "dirty"
        dirty.mkdir()
        (dirty / "bad.md").write_text("class SoakLease:\n    pass\n")
        r = _run_gate([dirty], denylist)
        assert r.returncode == 1, "Gate must detect SoakLease token"

    def test_gate_effective_dash0(self, tmp_path: Path) -> None:
        """Verify the denylist catches 'dash0' as a canary."""
        denylist = _write_ephemeral_denylist(tmp_path)
        dirty = tmp_path / "dirty2"
        dirty.mkdir()
        (dirty / "bad.md").write_text("# fetch from dash0\n")
        r = _run_gate([dirty], denylist)
        assert r.returncode == 1, "Gate must detect dash0 token"


# ---------------------------------------------------------------------------
# watch-preview.md registrable + prose contract
# ---------------------------------------------------------------------------

class TestWatchPreviewRegistrable:
    def _parse_frontmatter(self, text: str) -> str:
        assert text.startswith("---\n"), "watch-preview.md must open with YAML frontmatter"
        end = text.find("\n---", 3)
        assert end > 0, "watch-preview.md frontmatter block must be closed"
        return text[3:end]

    def _has_field(self, fm: str, field: str) -> bool:
        return any(
            ln.strip().startswith(f"{field}:") and ln.split(":", 1)[1].strip()
            for ln in fm.splitlines()
        )

    @pytest.fixture
    def watch_preview_text(self) -> str:
        if not WATCH_PREVIEW_MD.exists():
            pytest.skip("watch-preview.md not yet implemented")
        return WATCH_PREVIEW_MD.read_text(encoding="utf-8")

    def test_name_is_watch_preview(self, watch_preview_text: str) -> None:
        fm = self._parse_frontmatter(watch_preview_text)
        name_lines = [ln for ln in fm.splitlines() if ln.strip().startswith("name:")]
        assert name_lines
        assert name_lines[0].split(":", 1)[1].strip() == "watch-preview"

    def test_has_description(self, watch_preview_text: str) -> None:
        fm = self._parse_frontmatter(watch_preview_text)
        assert self._has_field(fm, "description")

    def test_has_tools(self, watch_preview_text: str) -> None:
        fm = self._parse_frontmatter(watch_preview_text)
        assert self._has_field(fm, "tools")

    def test_references_soak_health_command(self, watch_preview_text: str) -> None:
        """watch-preview.md must reference soak_health_command (the D-3 seam)."""
        assert "soak_health_command" in watch_preview_text, (
            "watch-preview.md must reference 'soak_health_command' — the D-3 seam"
        )

    def test_references_soak_health_script(self, watch_preview_text: str) -> None:
        """watch-preview.md must reference soak_health.py (the thin soak script)."""
        assert "soak_health.py" in watch_preview_text, (
            "watch-preview.md must reference soak_health.py for running the soak"
        )

    def test_inert_default_documented(self, watch_preview_text: str) -> None:
        """watch-preview.md must document the inert-by-default behavior (D-3)."""
        assert "n/a" in watch_preview_text.lower() or "no health command" in watch_preview_text.lower(), (
            "watch-preview.md must document the 'n/a — no health command configured' inert default"
        )

    def test_a2_config_summary_on_launch(self, watch_preview_text: str) -> None:
        """A-2: watch-preview.md must carry the config-summary-on-launch line."""
        assert "soak config:" in watch_preview_text.lower(), (
            "watch-preview.md must contain the A-2 config-summary line "
            "('soak config: ...' — e.g. 'soak config: health_command=none')"
        )

    def test_dispatches_diagnose_preview(self, watch_preview_text: str) -> None:
        """watch-preview.md must dispatch diagnose-preview on soak failure."""
        assert "diagnose-preview" in watch_preview_text, (
            "watch-preview.md must dispatch diagnose-preview on a health check regression"
        )

    def test_no_soak_lease_reference(self, watch_preview_text: str) -> None:
        """watch-preview.md must NOT reference SoakLease (zenith-specific — stripped)."""
        assert "SoakLease" not in watch_preview_text and "soaklease" not in watch_preview_text.lower(), (
            "watch-preview.md must not reference SoakLease (zenith-specific — stripped)"
        )

    def test_no_workspace_manifest_reference(self, watch_preview_text: str) -> None:
        """watch-preview.md must NOT reference .workspace-manifest (zenith artifact)."""
        assert ".workspace-manifest" not in watch_preview_text

    def test_no_dash0_reference(self, watch_preview_text: str) -> None:
        """watch-preview.md must NOT reference dash0 (zenith observability vendor)."""
        assert "dash0" not in watch_preview_text.lower()

    def test_references_group_toml(self, watch_preview_text: str) -> None:
        """watch-preview.md must reference the group TOML for health command config (B-1)."""
        assert "group_toml" in watch_preview_text.lower() or "group toml" in watch_preview_text.lower() or "[release]" in watch_preview_text, (
            "watch-preview.md must reference the group TOML [release] block for config"
        )


# ---------------------------------------------------------------------------
# diagnose-preview.md registrable + prose contract
# ---------------------------------------------------------------------------

class TestDiagnosePreviewRegistrable:
    def _parse_frontmatter(self, text: str) -> str:
        assert text.startswith("---\n"), "diagnose-preview.md must open with YAML frontmatter"
        end = text.find("\n---", 3)
        assert end > 0, "diagnose-preview.md frontmatter block must be closed"
        return text[3:end]

    def _has_field(self, fm: str, field: str) -> bool:
        return any(
            ln.strip().startswith(f"{field}:") and ln.split(":", 1)[1].strip()
            for ln in fm.splitlines()
        )

    @pytest.fixture
    def diagnose_preview_text(self) -> str:
        if not DIAGNOSE_PREVIEW_MD.exists():
            pytest.skip("diagnose-preview.md not yet implemented")
        return DIAGNOSE_PREVIEW_MD.read_text(encoding="utf-8")

    def test_name_is_diagnose_preview(self, diagnose_preview_text: str) -> None:
        fm = self._parse_frontmatter(diagnose_preview_text)
        name_lines = [ln for ln in fm.splitlines() if ln.strip().startswith("name:")]
        assert name_lines
        assert name_lines[0].split(":", 1)[1].strip() == "diagnose-preview"

    def test_has_description(self, diagnose_preview_text: str) -> None:
        fm = self._parse_frontmatter(diagnose_preview_text)
        assert self._has_field(fm, "description")

    def test_has_tools(self, diagnose_preview_text: str) -> None:
        fm = self._parse_frontmatter(diagnose_preview_text)
        assert self._has_field(fm, "tools")

    def test_references_generic_investigation(self, diagnose_preview_text: str) -> None:
        """diagnose-preview.md must describe investigating a configured health command regression."""
        text_lower = diagnose_preview_text.lower()
        assert "health" in text_lower or "regression" in text_lower or "investigate" in text_lower, (
            "diagnose-preview.md must describe investigating a health command regression"
        )

    def test_dispatches_log_sifter_or_troubleshooter(self, diagnose_preview_text: str) -> None:
        """diagnose-preview.md must dispatch log-sifter or troubleshooter (generic forge agents)."""
        text_lower = diagnose_preview_text.lower()
        assert "log-sifter" in text_lower or "troubleshooter" in text_lower, (
            "diagnose-preview.md must dispatch log-sifter and/or troubleshooter "
            "(the generic forge diagnosis agents)"
        )

    def test_no_dash0_reference(self, diagnose_preview_text: str) -> None:
        """diagnose-preview.md must NOT reference dash0."""
        assert "dash0" not in diagnose_preview_text.lower()

    def test_no_soak_lease_reference(self, diagnose_preview_text: str) -> None:
        """diagnose-preview.md must NOT reference SoakLease."""
        assert "SoakLease" not in diagnose_preview_text and "soaklease" not in diagnose_preview_text.lower()

    def test_no_workspace_manifest_reference(self, diagnose_preview_text: str) -> None:
        """diagnose-preview.md must NOT reference .workspace-manifest."""
        assert ".workspace-manifest" not in diagnose_preview_text


# ---------------------------------------------------------------------------
# soak_health.py agent↔script fidelity: watch-preview passes correct flags
# ---------------------------------------------------------------------------

class TestWatchPreviewScriptFidelity:
    """watch-preview.md must pass only real flags to soak_health.py."""

    def test_soak_health_script_exists(self) -> None:
        assert SOAK_SCRIPT.exists(), (
            f"soak_health.py must exist at {SOAK_SCRIPT}"
        )

    def test_watch_preview_passes_real_soak_flags(self) -> None:
        """All flags in watch-preview.md soak_health.py invocations must exist in the script."""
        if not WATCH_PREVIEW_MD.exists():
            pytest.skip("watch-preview.md not yet implemented")
        import re, importlib.util, argparse

        text = WATCH_PREVIEW_MD.read_text(encoding="utf-8")
        # Find lines that invoke soak_health.py
        invocation_lines = [
            ln.strip() for ln in text.splitlines()
            if re.search(r"python3\s+.*soak_health\.py", ln.strip())
        ]
        if not invocation_lines:
            pytest.skip("No soak_health.py invocations found in watch-preview.md")

        # Load the soak_health module to get its parser
        spec = importlib.util.spec_from_file_location("soak_health", SOAK_SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass

        # Get parser flags
        parser = None
        for attr in ("_build_parser", "build_parser", "get_parser"):
            fn = getattr(mod, attr, None)
            if callable(fn):
                try:
                    parser = fn()
                    break
                except Exception:
                    pass

        if parser is None:
            captured: list[argparse.ArgumentParser] = []
            orig = argparse.ArgumentParser.parse_args

            def capturing(self, args=None, namespace=None):
                captured.append(self)
                raise SystemExit(0)

            argparse.ArgumentParser.parse_args = capturing  # type: ignore[method-assign]
            try:
                main = getattr(mod, "main", None)
                if callable(main):
                    main([])
            except (SystemExit, Exception):
                pass
            finally:
                argparse.ArgumentParser.parse_args = orig  # type: ignore[method-assign]
            parser = captured[0] if captured else None

        if parser is None:
            pytest.skip("Could not extract ArgumentParser from soak_health.py")

        known_flags: set[str] = set()
        for action in parser._actions:
            for opt in action.option_strings:
                if opt.startswith("--"):
                    known_flags.add(opt)

        for line in invocation_lines:
            flags = re.findall(r"(--[a-zA-Z][a-zA-Z0-9-]+)", line)
            for flag in flags:
                assert flag in known_flags, (
                    f"watch-preview.md invokes soak_health.py with '{flag}' which "
                    f"is NOT in the script's argparse (known: {known_flags})\n  line: {line}"
                )
