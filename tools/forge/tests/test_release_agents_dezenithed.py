"""Tests for de-zenithed release agents: pr-updater.md + watch-pr.md (Slice 5).

Contract assertions (D-1, D-3, D-7, S-3, A-2):

  - D-7/S-3: leak_gate.py over both agents with an ephemeral Step-6 denylist
    (written to tmp_path, never depends on ~/.claude/leak-gate.denylist) → exit 0.
    Token list: cortana(-zh)?, asana, asana_sync, dash0, .workspace-manifest,
    \bzenith\b, \bplatform\b, mobile-app, admin-preview, preview*(url|server|host),
    manifest-get.

  - Registrable: frontmatter name: matches filename, description present, tools present.

  - Generic: no structural brain seams, no middle-band app-flavored tokens.

  - Prose contract (watch-pr.md):
    - References "external_tracker" / inert tracker seam (D-3).
    - References camp manifest and prs.json sidecar (D-1).
    - References camp (group, slug) — NOT worktree_root / .workspace-manifest.json.
    - Contains NO Asana/Cortana/dash0/manifest-get prose.
    - Contains the A-2 config-summary-on-launch line ("release config:").

  - Prose contract (pr-updater.md):
    - References detect_repos.py (the Slice-4 script).
    - References camp manifest and prs.json sidecar (D-1).
    - References camp (group, slug) — NOT .workspace-manifest.json.
    - Has NO zenith-PR-privileged-member step (no "zenith PR", no Step 5 manifest-only).

Hermeticity: tmp_path-based ephemeral denylist; no real ~/.claude/ dependency;
no network; stdlib only.
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
GATE = SCRIPTS_DIR / "leak_gate.py"

PR_UPDATER_MD = AGENTS_DIR / "pr-updater.md"
WATCH_PR_MD = AGENTS_DIR / "watch-pr.md"

# ---------------------------------------------------------------------------
# Step-6 ephemeral denylist tokens (D-7 / S-3)
# Structurally-observable zenith tokens — safe to name in tracked test source
# per D-7 (amended): these are business-context strings, not secrets.
# ---------------------------------------------------------------------------

_STEP6_DENYLIST_TOKENS = [
    r"zenithhealth",
    r"\bzenith\b",
    r"\basana\b",
    r"asana_sync",
    r"dash0",
    r"cortana(-zh)?",
    r"\.workspace-manifest",
    r"\bplatform\b",
    r"mobile-app",
    r"admin-preview",
    r"preview\s*(url|server|host)",
    r"manifest-get",
    r"brain/(designs|chrome|specs|plans|sessions)",
]


def _write_ephemeral_denylist(p: Path) -> Path:
    """Write the Step-6 denylist to tmp_path (S-3: never depend on machine-local)."""
    dl = p / "step6-agents-denylist.txt"
    dl.write_text("\n".join(_STEP6_DENYLIST_TOKENS) + "\n", encoding="utf-8")
    return dl


def _run_gate(trees: list[Path], denylist: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE), *[str(t) for t in trees], "--denylist", str(denylist)]
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pr_updater_text() -> str:
    if not PR_UPDATER_MD.exists():
        pytest.skip("pr-updater.md not yet implemented")
    return PR_UPDATER_MD.read_text(encoding="utf-8")


@pytest.fixture
def watch_pr_text() -> str:
    if not WATCH_PR_MD.exists():
        pytest.skip("watch-pr.md not yet implemented")
    return WATCH_PR_MD.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# D-7 / S-3: Leak gate — both agents must be clean
# ---------------------------------------------------------------------------

class TestLeakGate:
    def test_pr_updater_is_clean(self, tmp_path: Path) -> None:
        """pr-updater.md must have no Step-6 zenith tokens (D-7/S-3)."""
        if not PR_UPDATER_MD.exists():
            pytest.skip("pr-updater.md not yet implemented")
        denylist = _write_ephemeral_denylist(tmp_path)
        r = _run_gate([PR_UPDATER_MD], denylist)
        assert r.returncode == 0, (
            f"pr-updater.md contains forbidden Step-6 zenith tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_watch_pr_is_clean(self, tmp_path: Path) -> None:
        """watch-pr.md must have no Step-6 zenith tokens (D-7/S-3)."""
        if not WATCH_PR_MD.exists():
            pytest.skip("watch-pr.md not yet implemented")
        denylist = _write_ephemeral_denylist(tmp_path)
        r = _run_gate([WATCH_PR_MD], denylist)
        assert r.returncode == 0, (
            f"watch-pr.md contains forbidden Step-6 zenith tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_both_agents_as_group_are_clean(self, tmp_path: Path) -> None:
        """Both release agents must pass the leak gate together (D-7/S-3)."""
        if not PR_UPDATER_MD.exists() or not WATCH_PR_MD.exists():
            pytest.skip("release agents not yet implemented")
        denylist = _write_ephemeral_denylist(tmp_path)
        for agent in [PR_UPDATER_MD, WATCH_PR_MD]:
            r = _run_gate([agent], denylist)
            assert r.returncode == 0, (
                f"{agent.name} contains forbidden Step-6 zenith tokens:\n{r.stdout}\n{r.stderr}"
            )

    def test_gate_correctly_detects_zenith_token(self, tmp_path: Path) -> None:
        """Verify the denylist is effective: a file with bare 'zenith' triggers gate exit 1."""
        denylist = _write_ephemeral_denylist(tmp_path)
        dirty = tmp_path / "dirty"
        dirty.mkdir()
        (dirty / "bad.md").write_text("# the zenith repo\n")
        r = _run_gate([dirty], denylist)
        assert r.returncode == 1, "Gate should have detected 'zenith' token but returned 0"

    def test_gate_correctly_detects_cortana_token(self, tmp_path: Path) -> None:
        """Verify the denylist is effective: a file with cortana-zh triggers gate exit 1."""
        denylist = _write_ephemeral_denylist(tmp_path)
        dirty = tmp_path / "dirty"
        dirty.mkdir()
        (dirty / "bad.md").write_text('reviewer = "cortana-zh"\n')
        r = _run_gate([dirty], denylist)
        assert r.returncode == 1, "Gate should have detected 'cortana-zh' token but returned 0"

    def test_gate_correctly_detects_asana_token(self, tmp_path: Path) -> None:
        """Verify the denylist is effective: a file with bare 'asana' triggers gate exit 1."""
        denylist = _write_ephemeral_denylist(tmp_path)
        dirty = tmp_path / "dirty"
        dirty.mkdir()
        (dirty / "bad.md").write_text("call the asana API\n")
        r = _run_gate([dirty], denylist)
        assert r.returncode == 1, "Gate should have detected 'asana' token but returned 0"


# ---------------------------------------------------------------------------
# Registration: frontmatter name: matches filename, description + tools present
# ---------------------------------------------------------------------------

class TestRegistrable:
    def _parse_frontmatter(self, text: str, filename: str) -> str:
        assert text.startswith("---\n"), (
            f"{filename} must open with a YAML frontmatter block"
        )
        end = text.find("\n---", 3)
        assert end > 0, f"{filename} frontmatter block is not closed"
        return text[3:end]

    def _has_field(self, frontmatter: str, field: str) -> bool:
        return any(
            ln.strip().startswith(f"{field}:") and ln.split(":", 1)[1].strip()
            for ln in frontmatter.splitlines()
        )

    def test_pr_updater_name_matches_filename(self, pr_updater_text: str) -> None:
        fm = self._parse_frontmatter(pr_updater_text, "pr-updater.md")
        name_lines = [
            ln for ln in fm.splitlines()
            if ln.strip().startswith("name:") and ln.split(":", 1)[1].strip()
        ]
        assert name_lines, "pr-updater.md frontmatter must carry name:"
        name = name_lines[0].split(":", 1)[1].strip()
        assert name == "pr-updater", (
            f"pr-updater.md frontmatter name: must be 'pr-updater', got {name!r}"
        )

    def test_pr_updater_has_description(self, pr_updater_text: str) -> None:
        fm = self._parse_frontmatter(pr_updater_text, "pr-updater.md")
        assert self._has_field(fm, "description"), (
            "pr-updater.md frontmatter must carry a non-empty description:"
        )

    def test_pr_updater_has_tools(self, pr_updater_text: str) -> None:
        fm = self._parse_frontmatter(pr_updater_text, "pr-updater.md")
        assert self._has_field(fm, "tools"), (
            "pr-updater.md frontmatter must carry a tools: line"
        )

    def test_watch_pr_name_matches_filename(self, watch_pr_text: str) -> None:
        fm = self._parse_frontmatter(watch_pr_text, "watch-pr.md")
        name_lines = [
            ln for ln in fm.splitlines()
            if ln.strip().startswith("name:") and ln.split(":", 1)[1].strip()
        ]
        assert name_lines, "watch-pr.md frontmatter must carry name:"
        name = name_lines[0].split(":", 1)[1].strip()
        assert name == "watch-pr", (
            f"watch-pr.md frontmatter name: must be 'watch-pr', got {name!r}"
        )

    def test_watch_pr_has_description(self, watch_pr_text: str) -> None:
        fm = self._parse_frontmatter(watch_pr_text, "watch-pr.md")
        assert self._has_field(fm, "description"), (
            "watch-pr.md frontmatter must carry a non-empty description:"
        )

    def test_watch_pr_has_tools(self, watch_pr_text: str) -> None:
        fm = self._parse_frontmatter(watch_pr_text, "watch-pr.md")
        assert self._has_field(fm, "tools"), (
            "watch-pr.md frontmatter must carry a tools: line"
        )

    def test_pr_updater_model_is_sonnet(self, pr_updater_text: str) -> None:
        """pr-updater.md must use sonnet model (matching source agent)."""
        fm = self._parse_frontmatter(pr_updater_text, "pr-updater.md")
        model_lines = [
            ln for ln in fm.splitlines()
            if ln.strip().startswith("model:")
        ]
        assert model_lines, "pr-updater.md frontmatter must carry model:"
        assert "sonnet" in model_lines[0].lower(), (
            f"pr-updater.md model should be sonnet, got {model_lines[0]!r}"
        )

    def test_watch_pr_model_is_sonnet(self, watch_pr_text: str) -> None:
        """watch-pr.md must use sonnet model (matching source agent)."""
        fm = self._parse_frontmatter(watch_pr_text, "watch-pr.md")
        model_lines = [
            ln for ln in fm.splitlines()
            if ln.strip().startswith("model:")
        ]
        assert model_lines, "watch-pr.md frontmatter must carry model:"
        assert "sonnet" in model_lines[0].lower(), (
            f"watch-pr.md model should be sonnet, got {model_lines[0]!r}"
        )


# ---------------------------------------------------------------------------
# Prose contract: watch-pr.md
# ---------------------------------------------------------------------------

class TestWatchPrProseContract:
    def test_references_external_tracker_seam(self, watch_pr_text: str) -> None:
        """watch-pr.md must reference the external_tracker seam (D-3)."""
        assert "external_tracker" in watch_pr_text, (
            "watch-pr.md must reference 'external_tracker' — the inert D-3 seam"
        )

    def test_external_tracker_default_none(self, watch_pr_text: str) -> None:
        """watch-pr.md must state the external_tracker defaults to none (D-3)."""
        text_lower = watch_pr_text.lower()
        has_default_none = (
            "default: none" in text_lower
            or "default none" in text_lower
            or "if any" in text_lower
        )
        assert has_default_none, (
            "watch-pr.md must state that the external_tracker defaults to none (D-3)"
        )

    def test_references_camp_manifest(self, watch_pr_text: str) -> None:
        """watch-pr.md must reference the camp manifest (not .workspace-manifest)."""
        assert "camp manifest" in watch_pr_text.lower() or "manifest" in watch_pr_text.lower(), (
            "watch-pr.md must reference the camp manifest for reading group/slug context"
        )

    def test_references_prs_json_sidecar(self, watch_pr_text: str) -> None:
        """watch-pr.md must reference prs.json sidecar (D-1)."""
        assert "prs.json" in watch_pr_text, (
            "watch-pr.md must reference the prs.json sidecar (D-1)"
        )

    def test_references_group_slug(self, watch_pr_text: str) -> None:
        """watch-pr.md must use camp (group, slug) vocabulary — NOT worktree_root."""
        # Must reference the group/slug pairing
        text_lower = watch_pr_text.lower()
        assert "group" in text_lower and "slug" in text_lower, (
            "watch-pr.md must use the camp (group, slug) vocabulary (not worktree_root)"
        )

    def test_no_workspace_manifest_reference(self, watch_pr_text: str) -> None:
        """watch-pr.md must NOT reference .workspace-manifest.json (the retired zenith artifact)."""
        assert ".workspace-manifest" not in watch_pr_text, (
            "watch-pr.md must not reference .workspace-manifest.json (retired zenith artifact)"
        )

    def test_no_asana_prose(self, watch_pr_text: str) -> None:
        """watch-pr.md must contain NO Asana prose."""
        assert "asana" not in watch_pr_text.lower(), (
            "watch-pr.md must not contain any Asana prose (stripped in de-zenithing)"
        )

    def test_no_cortana_prose(self, watch_pr_text: str) -> None:
        """watch-pr.md must contain NO Cortana/cortana-zh prose."""
        assert "cortana" not in watch_pr_text.lower(), (
            "watch-pr.md must not contain any Cortana prose (stripped in de-zenithing)"
        )

    def test_no_dash0_prose(self, watch_pr_text: str) -> None:
        """watch-pr.md must contain NO dash0 prose."""
        assert "dash0" not in watch_pr_text.lower(), (
            "watch-pr.md must not contain any dash0 prose (stripped in de-zenithing)"
        )

    def test_no_manifest_get_prose(self, watch_pr_text: str) -> None:
        """watch-pr.md must contain NO manifest-get prose (zenith-specific CLI)."""
        assert "manifest-get" not in watch_pr_text.lower(), (
            "watch-pr.md must not contain manifest-get prose (zenith-specific CLI)"
        )

    def test_a2_config_summary_on_launch(self, watch_pr_text: str) -> None:
        """watch-pr.md must contain the A-2 config-summary-on-launch instruction.

        A-2 requires that on launch watch-pr emits a one-line summary like:
          'release config: review_bot=none, soak=none, tracker=none — configure in [release]...'
        This makes the triple-inert state legible and surfaces the knob names.
        """
        assert "release config:" in watch_pr_text.lower() or "release config:" in watch_pr_text, (
            "watch-pr.md must contain the A-2 config-summary-on-launch instruction "
            "('release config: ...' summary line on launch)"
        )

    def test_references_detect_repos_script(self, watch_pr_text: str) -> None:
        """watch-pr.md must reference detect_repos.py (the Slice-4 script)."""
        assert "detect_repos" in watch_pr_text, (
            "watch-pr.md must reference detect_repos.py for detecting active repos"
        )

    def test_references_merge_prs_script(self, watch_pr_text: str) -> None:
        """watch-pr.md must reference merge_prs.py for merge ordering."""
        assert "merge_prs" in watch_pr_text, (
            "watch-pr.md must reference merge_prs.py for the merge step"
        )

    def test_post_merge_handoff_references_manifest_and_sidecar(self, watch_pr_text: str) -> None:
        """watch-pr.md must reference both the camp manifest path and prs.json sidecar
        in its post-merge handoff marker (so watch-preview can pick up — Slice 6)."""
        assert "post_merge_handoff" in watch_pr_text, (
            "watch-pr.md must emit a post_merge_handoff marker for watch-preview dispatch"
        )
        # The handoff must reference the manifest
        assert "manifest_path" in watch_pr_text, (
            "watch-pr.md post_merge_handoff must include manifest_path"
        )

    def test_watch_pr_harvest_candidates_section(self, watch_pr_text: str) -> None:
        """watch-pr.md must retain the harvest candidates section (genericized, no brain-vault path)."""
        assert "## Harvest candidates" in watch_pr_text, (
            "watch-pr.md must carry the '## Harvest candidates' section for the lore hook"
        )

    def test_no_harvest_protocol_path(self, watch_pr_text: str) -> None:
        """watch-pr.md must NOT reference the brain-vault harvest-protocol.md path."""
        assert "harvest-protocol.md" not in watch_pr_text, (
            "watch-pr.md must not reference harvest-protocol.md (brain-vault path — house rule)"
        )

    def test_dispatches_code_reviewer(self, watch_pr_text: str) -> None:
        """watch-pr.md must dispatch code-reviewer for review actions."""
        assert "code-reviewer" in watch_pr_text, (
            "watch-pr.md must dispatch code-reviewer for review feedback handling"
        )

    def test_dispatches_log_sifter(self, watch_pr_text: str) -> None:
        """watch-pr.md must dispatch log-sifter for CI fix actions."""
        assert "log-sifter" in watch_pr_text, (
            "watch-pr.md must dispatch log-sifter for CI log triage"
        )

    def test_dispatches_pr_summarizer(self, watch_pr_text: str) -> None:
        """watch-pr.md must dispatch pr-summarizer for blocked status."""
        assert "pr-summarizer" in watch_pr_text, (
            "watch-pr.md must dispatch pr-summarizer to compose the blocker report"
        )

    def test_three_fix_cycles_cap(self, watch_pr_text: str) -> None:
        """watch-pr.md must state the 3 fix-cycles-then-blocked cap."""
        assert "3" in watch_pr_text and (
            "fix cycle" in watch_pr_text.lower() or "cycles" in watch_pr_text.lower()
        ), (
            "watch-pr.md must state the 3 fix cycles cap before declaring blocked"
        )


# ---------------------------------------------------------------------------
# Prose contract: pr-updater.md
# ---------------------------------------------------------------------------

class TestPrUpdaterProseContract:
    def test_references_detect_repos_script(self, pr_updater_text: str) -> None:
        """pr-updater.md must reference detect_repos.py for detecting active repos."""
        assert "detect_repos" in pr_updater_text, (
            "pr-updater.md must reference detect_repos.py (Slice-4 script)"
        )

    def test_references_camp_manifest(self, pr_updater_text: str) -> None:
        """pr-updater.md must reference the camp manifest."""
        text_lower = pr_updater_text.lower()
        assert "camp manifest" in text_lower or "manifest" in text_lower, (
            "pr-updater.md must reference the camp manifest for reading repos"
        )

    def test_references_group_slug(self, pr_updater_text: str) -> None:
        """pr-updater.md must use camp (group, slug) vocabulary — NOT worktree_root."""
        text_lower = pr_updater_text.lower()
        assert "group" in text_lower and "slug" in text_lower, (
            "pr-updater.md must use the camp (group, slug) vocabulary"
        )

    def test_references_prs_json_sidecar(self, pr_updater_text: str) -> None:
        """pr-updater.md must reference the prs.json sidecar (D-1)."""
        assert "prs.json" in pr_updater_text, (
            "pr-updater.md must reference the prs.json sidecar (D-1)"
        )

    def test_no_zenith_pr_privileged_member_step(self, pr_updater_text: str) -> None:
        """pr-updater.md must NOT have a zenith-PR step (no privileged member in a camp group)."""
        # The old Step 5 was 'Open the zenith PR (manifest mode only)' — must be gone
        assert "zenith PR" not in pr_updater_text, (
            "pr-updater.md must not contain a zenith-PR step (no privileged member in a camp group)"
        )
        # Also must not reference worktree_root
        assert "worktree_root" not in pr_updater_text, (
            "pr-updater.md must not reference worktree_root (zenith manifest-mode concept)"
        )

    def test_no_workspace_manifest_reference(self, pr_updater_text: str) -> None:
        """pr-updater.md must NOT reference .workspace-manifest.json."""
        assert ".workspace-manifest" not in pr_updater_text, (
            "pr-updater.md must not reference .workspace-manifest.json (retired zenith artifact)"
        )

    def test_returns_pr_pairs_for_caller(self, pr_updater_text: str) -> None:
        """pr-updater.md must return pr_pairs for the caller to launch watch-pr."""
        assert "pr_pairs" in pr_updater_text, (
            "pr-updater.md must return pr_pairs for the caller to launch watch-pr"
        )

    def test_no_watch_pr_dispatch_from_inside(self, pr_updater_text: str) -> None:
        """pr-updater.md must NOT dispatch watch-pr from inside (caller launches it)."""
        # The agent must explicitly state watch-pr is launched by the CALLER, not this agent
        text_lower = pr_updater_text.lower()
        assert "caller" in text_lower, (
            "pr-updater.md must instruct the caller to launch watch-pr, not dispatch it internally"
        )

    def test_all_repos_are_peers(self, pr_updater_text: str) -> None:
        """pr-updater.md must treat all camp group repos as peers (no privileged member)."""
        text_lower = pr_updater_text.lower()
        assert "peer" in text_lower or "every member" in text_lower or "all members" in text_lower, (
            "pr-updater.md must state that all camp group members are peers (no privileged member)"
        )

    def test_writes_prs_sidecar(self, pr_updater_text: str) -> None:
        """pr-updater.md must reference writing to the prs.json sidecar."""
        text_lower = pr_updater_text.lower()
        assert "sidecar" in text_lower or "prs.json" in pr_updater_text, (
            "pr-updater.md must reference writing PR associations to the prs.json sidecar"
        )
