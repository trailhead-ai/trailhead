"""Slice 2 — session-context.py area-map menu integration.

TDD — written BEFORE render_area_menu exists and BEFORE build_context is wired.
Every test here must go RED on first run.

Covers (rederived from D7/D8a/Slice-2 invariants):

  render_area_menu:
    - empty list -> empty string (hook omits block)
    - non-empty  -> contains D-7 structural label (--- lore area map ---)
    - non-empty  -> contains "match … then `lore recall`" instruction
    - non-empty  -> contains each area name
    - non-empty  -> does NOT contain "Recalled (" (not a recall banner)

  build_context (hook integration):
    - with >=1 area in fixture vault: output contains the menu block (area names
      present) and the D-7 area-map header label
    - with >=1 area: block is labeled as the area map, NOT as "Recalled (...)"
    - with zero areas: output contains the baseline vault index (session-note
      pointer / capture reminder), no menu block, no raise
    - malformed area file: does NOT crash build_context (menu degrades gracefully)
    - D-8a (CRITICAL): when build_area_map is monkeypatched to raise, build_context
      still returns the vault index (NOT empty, NOT "{}"); menu failure must NOT
      propagate to main()'s outer guard
    - no automatic Recalled (...) block on any branch (anti-regression: the noise
      that got recall deleted — there is no match-inject path)
    - main() still prints {} on a forced exception in the core path (the existing
      never-raise contract is intact for genuine context failures)
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


# ---------------------------------------------------------------------------
# Module loaders
# ---------------------------------------------------------------------------

def load_recall():
    """Load recall freshly, registering in sys.modules for @dataclass safety."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    for cached in ("recall", "vault", "frontmatter", "status_validator",
                   "regenerate_indices", "sessions"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location("recall", SCRIPTS_DIR / "recall.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["recall"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_hook(name: str):
    """Load a hook from hooks/ by stem, freshly each call.

    Applies the Python-3.13 @dataclass gotcha: register the module in
    sys.modules BEFORE exec_module so the dataclass decorator can resolve
    cls.__module__ on its parent (recall) at decoration time.
    """
    for d in (str(HOOKS_DIR), str(SCRIPTS_DIR)):
        if d not in sys.path:
            sys.path.insert(0, d)
    for cached in (name, "sessions", "vault", "frontmatter",
                   "status_validator", "recall", "regenerate_indices"):
        sys.modules.pop(cached, None)
    # Pre-load recall into sys.modules so @dataclass resolves correctly
    recall_spec = importlib.util.spec_from_file_location(
        "recall", SCRIPTS_DIR / "recall.py"
    )
    recall_mod = importlib.util.module_from_spec(recall_spec)
    sys.modules["recall"] = recall_mod
    recall_spec.loader.exec_module(recall_mod)

    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixture vault helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for d in ("areas", "sessions"):
        (vault / d).mkdir(parents=True)
    return vault


def _write_area(vault: Path, name: str, keywords: list[str],
                summary: str | None = None) -> Path:
    p = vault / "areas" / f"{name}.md"
    kw_str = "[" + ", ".join(keywords) + "]"
    summary_line = f"summary: {summary}\n" if summary else ""
    p.write_text(
        f"---\ntype: area\nname: {name}\nkeywords: {kw_str}\n{summary_line}---\n\n"
        f"## Overview\n\nThis is the {name} area.\n"
    )
    return p


def _run_session_context(stdin_payload: dict, env: dict, cwd: Path):
    """Run main() of session-context.py and return stdout as string."""
    mod = load_hook("session-context")
    out = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
            with mock.patch("sys.stdout", out):
                with mock.patch.object(os, "getcwd", return_value=str(cwd)):
                    with mock.patch("subprocess.run") as mock_run:
                        mock_run.return_value = mock.Mock(
                            returncode=0, stdout="main\n", stderr=""
                        )
                        mod.main()
    return out.getvalue()


def _base_env(vault: Path) -> dict:
    return {
        "LORE_VAULT": str(vault),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }


# ---------------------------------------------------------------------------
# render_area_menu unit tests
# ---------------------------------------------------------------------------

class TestRenderAreaMenu:
    def test_empty_list_returns_empty_string(self):
        recall = load_recall()
        result = recall.render_area_menu([])
        assert result == ""

    def test_non_empty_contains_d7_structural_label(self):
        recall = load_recall()
        entry = recall.AreaEntry(name="workflow-dev-env", one_liner="camp dev envs", keywords=["camp"])
        result = recall.render_area_menu([entry])
        assert "lore area map" in result

    def test_non_empty_contains_match_instruction(self):
        recall = load_recall()
        entry = recall.AreaEntry(name="auth", one_liner="oauth flows", keywords=["oauth"])
        result = recall.render_area_menu([entry])
        assert "lore recall" in result

    def test_non_empty_contains_area_names(self):
        recall = load_recall()
        entries = [
            recall.AreaEntry(name="auth", one_liner="oauth flows", keywords=["oauth"]),
            recall.AreaEntry(name="flow-penny", one_liner="conversation engine", keywords=["penny"]),
        ]
        result = recall.render_area_menu(entries)
        assert "auth" in result
        assert "flow-penny" in result

    def test_non_empty_does_not_contain_recalled_prefix(self):
        recall = load_recall()
        entry = recall.AreaEntry(name="auth", one_liner="oauth flows", keywords=["oauth"])
        result = recall.render_area_menu([entry])
        assert "Recalled (" not in result

    def test_contains_area_count(self):
        recall = load_recall()
        entries = [
            recall.AreaEntry(name="auth", one_liner="one", keywords=[]),
            recall.AreaEntry(name="billing", one_liner="two", keywords=[]),
        ]
        result = recall.render_area_menu(entries)
        assert "2" in result

    def test_one_liner_present_in_output(self):
        recall = load_recall()
        entry = recall.AreaEntry(name="auth", one_liner="oauth and jwt flows", keywords=[])
        result = recall.render_area_menu([entry])
        assert "oauth and jwt flows" in result

    def test_has_open_and_close_frame(self):
        recall = load_recall()
        entry = recall.AreaEntry(name="auth", one_liner="", keywords=[])
        result = recall.render_area_menu([entry])
        # Should have both an opening and closing fence
        lines = result.splitlines()
        assert any("lore area map" in l for l in lines)
        assert any("end" in l for l in lines)


# ---------------------------------------------------------------------------
# build_context hook-integration tests
# ---------------------------------------------------------------------------

class TestBuildContextMenu:
    def test_with_areas_contains_menu_block(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth and JWT flows")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "auth" in ctx
        assert "lore area map" in ctx

    def test_with_areas_block_labeled_as_menu_not_recalled(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "Recalled (" not in ctx

    def test_zero_areas_emits_baseline_no_menu(self, tmp_path):
        vault = _make_vault(tmp_path)
        # No areas/ files — just the empty dir
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        # Baseline must be present (the capture reminder is always emitted)
        assert "/lore:defer" in ctx or "lore" in ctx
        # No menu block when zero areas
        assert "lore area map" not in ctx

    def test_zero_areas_does_not_raise(self, tmp_path):
        vault = _make_vault(tmp_path)
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        # Must not raise; must produce valid JSON
        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        assert "hookSpecificOutput" in data

    def test_malformed_area_file_does_not_crash(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        # Write a binary/malformed area file alongside the valid one
        bad = vault / "areas" / "bad-area.md"
        bad.write_bytes(b"\xff\xfe malformed binary content \x00\x01")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        # Should not crash; valid areas still appear
        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "auth" in ctx

    def test_d8a_menu_crash_leaves_vault_index_intact(self, tmp_path):
        """D-8a CRITICAL: a menu-build crash must leave the vault index, not {}.

        Monkeypatches recall.build_area_map to raise, then verifies build_context
        still returns the vault index content (the session-note pointer / capture
        reminder) — NOT an empty string, NOT the {} fallback that the outer
        main() guard would emit on a genuine core failure.
        """
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        mod = load_hook("session-context")
        recall_mod = sys.modules.get("recall")

        out = io.StringIO()
        with mock.patch.dict(os.environ, _base_env(vault), clear=True):
            with mock.patch("sys.stdin", io.StringIO(json.dumps({"session_id": "d8a"}))):
                with mock.patch("sys.stdout", out):
                    with mock.patch.object(os, "getcwd", return_value=str(cwd)):
                        with mock.patch("subprocess.run") as mock_run:
                            mock_run.return_value = mock.Mock(
                                returncode=0, stdout="main\n", stderr=""
                            )
                            # Patch build_area_map to raise inside the recall module
                            with mock.patch.object(
                                recall_mod, "build_area_map",
                                side_effect=RuntimeError("simulated menu crash")
                            ):
                                mod.main()

        raw = out.getvalue()
        data = json.loads(raw)

        # Must NOT be {}
        assert data != {}, "D-8a violated: menu crash caused {} fallback (cold session)"
        assert "hookSpecificOutput" in data, "D-8a violated: hookSpecificOutput missing"

        ctx = data["hookSpecificOutput"]["additionalContext"]
        # Vault index must be intact: capture reminder always present
        assert ctx, "D-8a violated: context is empty after menu crash"
        # The menu block itself should be absent (gracefully degraded)
        assert "lore area map" not in ctx

    def test_no_automatic_recalled_block(self, tmp_path):
        """Anti-regression: no match-inject path; Recalled (...) never appears."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "Recalled (" not in ctx

    def test_main_prints_empty_json_on_core_failure(self, tmp_path):
        """main() still emits {} when build_context itself raises (outer guard)."""
        vault = _make_vault(tmp_path)
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        mod = load_hook("session-context")
        out = io.StringIO()
        with mock.patch.dict(os.environ, _base_env(vault), clear=True):
            with mock.patch("sys.stdin", io.StringIO(json.dumps({"session_id": "err"}))):
                with mock.patch("sys.stdout", out):
                    with mock.patch.object(os, "getcwd", return_value=str(cwd)):
                        # Force the entire build_context to raise
                        with mock.patch.object(
                            mod, "build_context",
                            side_effect=RuntimeError("core failure")
                        ):
                            mod.main()

        data = json.loads(out.getvalue())
        assert data == {}
