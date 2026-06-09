"""Slice 5 tests: portable SessionStart subsystem recall.

Covers (TDD — written before recall.py):
- derive_subsystem_keywords: reads inline keywords: from subsystem profiles;
  profiles with absent or empty keywords are excluded.
- infer_subsystems: branch name substring matching; non-keyword subsystem
  never matches; empty subsystems/ yields [] without error.
- render_subsystem_block: loads profile body + related open deferred (via
  surfaces overlap) + dead-end + active lesson + recent session; returns None
  when nothing substantive is found.
- Project filter: deferred for a different project is excluded; a
  project-agnostic note (no project field) is included.
- U3 perf: building the keyword map over >=30 fixture profiles completes
  well within a 200ms budget.
- Integration: session-context.py with a branch matching a fixture subsystem
  emits the subsystem block in additionalContext; a non-matching branch emits
  only the baseline index (no crash).
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def load_script(name: str):
    """Load a module from plugins/lore/scripts/ by stem, freshly each call."""
    for d in (str(SCRIPTS_DIR),):
        if d not in sys.path:
            sys.path.insert(0, d)
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_hook(name: str):
    """Load a hook module from hooks/ by stem, freshly each call."""
    for d in (str(HOOKS_DIR), str(SCRIPTS_DIR)):
        if d not in sys.path:
            sys.path.insert(0, d)
    for cached in (name, "sessions", "vault", "frontmatter", "status_validator", "recall"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixture vault helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for d in ("subsystems", "deferred", "dead-ends", "lessons", "sessions"):
        (vault / d).mkdir(parents=True)
    return vault


def _write_subsystem(vault: Path, name: str, keywords: list[str]) -> Path:
    p = vault / "subsystems" / f"{name}.md"
    kw_str = "[" + ", ".join(keywords) + "]"
    p.write_text(
        f"---\ntype: subsystem\nname: {name}\nkeywords: {kw_str}\n---\n\n"
        f"## Overview\nThis is the {name} subsystem.\n"
    )
    return p


def _write_deferred(
    vault: Path, name: str, surfaces: list[str], project: str | None = None
) -> Path:
    p = vault / "deferred" / f"{name}.md"
    surfaces_str = "[" + ", ".join(surfaces) + "]"
    proj_line = f"project: {project}\n" if project else ""
    p.write_text(
        f"---\ntype: deferred\nstatus: open\nsurfaces: {surfaces_str}\n{proj_line}"
        f"next-check: 2026-07-01\n---\n\n# {name}\n\nSomething to do.\n"
    )
    return p


def _write_dead_end(vault: Path, name: str, subsystems: list[str]) -> Path:
    p = vault / "dead-ends" / f"{name}.md"
    subs_str = "[" + ", ".join(subsystems) + "]"
    p.write_text(
        f"---\ntype: dead-end\nsubsystems: {subs_str}\nrevive-condition: never\n---\n\n"
        f"# {name}\n\nThis approach failed.\n"
    )
    return p


def _write_lesson(vault: Path, name: str, subsystems: list[str], status: str = "active") -> Path:
    p = vault / "lessons" / f"{name}.md"
    subs_str = "[" + ", ".join(subsystems) + "]"
    p.write_text(
        f"---\ntype: lesson\nstatus: {status}\nsubsystems: {subs_str}\nseverity: medium\n---\n\n"
        f"# {name}\n\nLesson body.\n"
    )
    return p


def _write_session(vault: Path, name: str, subsystems: list[str], project: str = "proj") -> Path:
    p = vault / "sessions" / f"{name}.md"
    subs_str = "[" + ", ".join(subsystems) + "]"
    p.write_text(
        f"---\ntype: session\nstatus: complete\nproject: {project}\n"
        f"subsystems: {subs_str}\n---\n\n# Session\n\nDid stuff.\n"
    )
    return p


# ---------------------------------------------------------------------------
# derive_subsystem_keywords
# ---------------------------------------------------------------------------

class TestDeriveSubsystemKeywords:
    def test_reads_keywords_from_profile(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "oauth-flow", ["oauth", "login"])
        recall = load_script("recall")
        result = recall.derive_subsystem_keywords(vault)
        assert result["oauth-flow"] == ["oauth", "login"]

    def test_excludes_profile_with_empty_keywords(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "no-keywords", [])
        recall = load_script("recall")
        result = recall.derive_subsystem_keywords(vault)
        assert "no-keywords" not in result

    def test_excludes_profile_with_absent_keywords(self, tmp_path):
        vault = _make_vault(tmp_path)
        p = vault / "subsystems" / "legacy.md"
        p.write_text("---\ntype: subsystem\nname: legacy\n---\n\n## Overview\nOld.\n")
        recall = load_script("recall")
        result = recall.derive_subsystem_keywords(vault)
        assert "legacy" not in result

    def test_multiple_profiles(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth", "jwt"])
        _write_subsystem(vault, "payments", ["stripe", "billing"])
        _write_subsystem(vault, "empty-one", [])
        recall = load_script("recall")
        result = recall.derive_subsystem_keywords(vault)
        assert set(result.keys()) == {"auth", "payments"}

    def test_empty_subsystems_dir(self, tmp_path):
        vault = _make_vault(tmp_path)
        recall = load_script("recall")
        result = recall.derive_subsystem_keywords(vault)
        assert result == {}

    def test_missing_subsystems_dir(self, tmp_path):
        vault = tmp_path / "empty-vault"
        vault.mkdir()
        recall = load_script("recall")
        result = recall.derive_subsystem_keywords(vault)
        assert result == {}


# ---------------------------------------------------------------------------
# infer_subsystems
# ---------------------------------------------------------------------------

class TestInferSubsystems:
    def test_branch_matching_keyword_returns_subsystem(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "oauth-flow", ["oauth"])
        recall = load_script("recall")
        result = recall.infer_subsystems(vault, "feature/oauth-login")
        assert "oauth-flow" in result

    def test_case_insensitive_match(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth-system", ["Auth"])
        recall = load_script("recall")
        # Branch lowercased before comparison, keyword also lowercased
        result = recall.infer_subsystems(vault, "feature/AUTH-refresh")
        assert "auth-system" in result

    def test_no_keyword_subsystem_never_matches(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "silent", [])
        recall = load_script("recall")
        result = recall.infer_subsystems(vault, "feature/silent-change")
        assert "silent" not in result

    def test_empty_subsystems_dir_returns_empty_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        recall = load_script("recall")
        result = recall.infer_subsystems(vault, "feature/anything")
        assert result == []

    def test_no_error_when_branch_matches_nothing(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "payments", ["stripe"])
        recall = load_script("recall")
        result = recall.infer_subsystems(vault, "feature/auth-update")
        assert result == []

    def test_multiple_subsystems_can_match(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        _write_subsystem(vault, "payments", ["billing"])
        recall = load_script("recall")
        result = recall.infer_subsystems(vault, "feature/oauth-billing-overhaul")
        assert "auth" in result
        assert "payments" in result


# ---------------------------------------------------------------------------
# render_subsystem_block
# ---------------------------------------------------------------------------

class TestRenderSubsystemBlock:
    def test_includes_profile_body(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["auth"], project=None)
        assert result is not None
        assert "auth" in result

    def test_includes_related_open_deferred(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        _write_deferred(vault, "revisit-token-refresh", surfaces=["auth"])
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["auth"], project=None)
        assert result is not None
        assert "revisit-token-refresh" in result

    def test_includes_related_dead_end(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        _write_dead_end(vault, "jwt-blacklist-failed", subsystems=["auth"])
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["auth"], project=None)
        assert result is not None
        assert "jwt-blacklist-failed" in result

    def test_includes_active_lesson(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        _write_lesson(vault, "always-validate-tokens", subsystems=["auth"], status="active")
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["auth"], project=None)
        assert result is not None
        assert "always-validate-tokens" in result

    def test_excludes_inactive_lesson(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        _write_lesson(vault, "stale-lesson", subsystems=["auth"], status="graduated")
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["auth"], project=None)
        # Only the profile body was found — graduated lessons are excluded
        # Result may still be non-None (profile was loaded), but stale-lesson
        # should not appear
        if result is not None:
            assert "stale-lesson" not in result

    def test_includes_recent_session_for_matching_project(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        _write_session(vault, "2026-06-01-1000-wt", subsystems=["auth"], project="myapp")
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["auth"], project="myapp")
        assert result is not None
        assert "2026-06-01-1000-wt" in result

    def test_returns_none_when_no_profile_exists(self, tmp_path):
        vault = _make_vault(tmp_path)
        # "ghost" has no profile file
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["ghost"], project=None)
        assert result is None

    def test_returns_none_when_nothing_substantive(self, tmp_path):
        vault = _make_vault(tmp_path)
        # No subsystems arg
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, [], project=None)
        assert result is None

    def test_profile_content_in_output(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["auth"], project=None)
        assert result is not None
        assert "This is the auth subsystem." in result


# ---------------------------------------------------------------------------
# Project filter
# ---------------------------------------------------------------------------

class TestProjectFilter:
    def test_deferred_for_different_project_excluded(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        _write_deferred(vault, "oauth-work", surfaces=["auth"], project="other-project")
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["auth"], project="myapp")
        # The deferred for "other-project" must not appear
        if result is not None:
            assert "oauth-work" not in result

    def test_project_agnostic_deferred_included(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        _write_deferred(vault, "universal-oauth-task", surfaces=["auth"], project=None)
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["auth"], project="myapp")
        assert result is not None
        assert "universal-oauth-task" in result

    def test_dead_ends_universal_no_project_filter(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        _write_dead_end(vault, "failed-approach", subsystems=["auth"])
        recall = load_script("recall")
        # Dead-ends are project-agnostic by design
        result = recall.render_subsystem_block(vault, ["auth"], project="completely-different")
        assert result is not None
        assert "failed-approach" in result

    def test_session_for_different_project_excluded(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        _write_session(vault, "2026-06-01-1000-wt", subsystems=["auth"], project="other")
        recall = load_script("recall")
        result = recall.render_subsystem_block(vault, ["auth"], project="myapp")
        if result is not None:
            assert "2026-06-01-1000-wt" not in result


# ---------------------------------------------------------------------------
# U3: Perf — keyword map over >=30 profiles < 200ms
# ---------------------------------------------------------------------------

class TestU3Perf:
    def test_keyword_map_30_profiles_under_200ms(self, tmp_path):
        vault = _make_vault(tmp_path)
        for i in range(35):
            _write_subsystem(vault, f"subsystem-{i:02d}", [f"kw{i}", f"alias{i}"])
        recall = load_script("recall")
        start = time.perf_counter()
        result = recall.derive_subsystem_keywords(vault)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 200, f"took {elapsed_ms:.1f}ms — over budget"
        assert len(result) == 35


# ---------------------------------------------------------------------------
# Integration: session-context.py with subsystem block
# ---------------------------------------------------------------------------

def _run_session_context(stdin_payload: dict, env: dict, cwd: Path):
    mod = load_hook("session-context")
    out = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
            with mock.patch("sys.stdout", out):
                with mock.patch.object(os, "getcwd", return_value=str(cwd)):
                    mod.main()
    return out.getvalue()


class TestSessionContextWithSubsystemBlock:
    def test_matching_branch_emits_subsystem_block(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "auth", ["oauth"])
        cwd = tmp_path / "my-worktree"
        cwd.mkdir()
        env = {
            "LORE_VAULT": str(vault),
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }
        # Patch get_branch_name to return a branch containing "oauth"
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0, stdout="feature/oauth-login\n", stderr=""
            )
            out = _run_session_context({"session_id": "abc"}, env, cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        # The subsystem block must appear — with the profile heading marker
        assert "Subsystem profiles" in ctx
        assert "subsystems/auth.md" in ctx

    def test_non_matching_branch_only_baseline(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_subsystem(vault, "payments", ["stripe"])
        cwd = tmp_path / "my-worktree"
        cwd.mkdir()
        env = {
            "LORE_VAULT": str(vault),
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0, stdout="feature/completely-unrelated\n", stderr=""
            )
            out = _run_session_context({"session_id": "abc"}, env, cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        # Baseline index should be present
        assert "/lore:defer" in ctx
        # No subsystem profile block
        assert "Subsystem profiles" not in ctx

    def test_no_crash_with_empty_subsystems_dir(self, tmp_path):
        vault = _make_vault(tmp_path)
        cwd = tmp_path / "my-worktree"
        cwd.mkdir()
        env = {
            "LORE_VAULT": str(vault),
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0, stdout="feature/oauth-login\n", stderr=""
            )
            out = _run_session_context({"session_id": "abc"}, env, cwd)
        data = json.loads(out)
        # No crash — valid JSON with hookSpecificOutput
        assert "hookSpecificOutput" in data
