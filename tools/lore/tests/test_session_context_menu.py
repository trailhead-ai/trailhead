"""Slice 2 — session-context.py area pointer injection.

After Slice 2 the hook no longer inlines the full area menu. Instead it emits
a single-line pointer (via render_area_pointer) that names the count and the
trigger cue for running `lore areas`. The full menu is still available via
`lore areas` (Slice 1) but is no longer injected at session start.

Covers (rederived from D7/D8a/Slice-2 invariants):

  render_area_pointer:
    - 0 areas -> empty string (hook omits block)
    - N areas -> contains count + "lore areas" cue + "lore recall" + trigger cue

  build_context (hook integration):
    - with >=1 area in fixture vault: output contains the pointer (area count,
      "lore areas" literal, trigger cue, "lore recall"); does NOT contain
      "lore area map" nor any individual area one-liner
    - count equals len(build_area_map(vault))
    - with zero areas: output contains the baseline vault index, no pointer line
    - D-8a (CRITICAL): when build_area_map is monkeypatched to raise, build_context
      still returns the vault index (NOT empty, NOT "{}"); pointer failure must NOT
      propagate to main()'s outer guard
    - no automatic Recalled (...) block on any branch (anti-regression)
    - main() still prints {} on a forced exception in the core path (outer guard intact)
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
# render_area_pointer unit tests
# ---------------------------------------------------------------------------

class TestRenderAreaPointer:
    def test_zero_areas_returns_empty_string(self, tmp_path):
        """0-areas vault -> empty string so the hook omits the block."""
        recall = load_recall()
        vault = _make_vault(tmp_path)
        # No area files — just the empty dir
        result = recall.render_area_pointer(vault)
        assert result == ""

    def test_n_areas_contains_count(self, tmp_path):
        """N-areas -> pointer contains the area count."""
        recall = load_recall()
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        _write_area(vault, "billing", ["stripe"], summary="Payment processing")
        result = recall.render_area_pointer(vault)
        assert "2" in result

    def test_n_areas_contains_lore_areas_command(self, tmp_path):
        """N-areas -> pointer contains the literal 'lore areas'."""
        recall = load_recall()
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        result = recall.render_area_pointer(vault)
        assert "lore areas" in result

    def test_n_areas_contains_trigger_cue(self, tmp_path):
        """N-areas -> pointer contains the 'unfamiliar topic' trigger cue."""
        recall = load_recall()
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        result = recall.render_area_pointer(vault)
        assert "unfamiliar" in result

    def test_n_areas_contains_lore_recall(self, tmp_path):
        """N-areas -> pointer contains 'lore recall' so agent knows the follow-up."""
        recall = load_recall()
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        result = recall.render_area_pointer(vault)
        assert "lore recall" in result

    def test_count_matches_build_area_map_length(self, tmp_path):
        """Count in the pointer equals len(build_area_map(vault))."""
        recall = load_recall()
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        _write_area(vault, "billing", ["stripe"], summary="Payments")
        _write_area(vault, "devops", ["ci"], summary="CI/CD")
        entries = recall.build_area_map(vault)
        result = recall.render_area_pointer(vault)
        assert str(len(entries)) in result

    def test_does_not_contain_area_names(self, tmp_path):
        """Pointer emits only the count — untrusted area names must not reach injection."""
        recall = load_recall()
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        result = recall.render_area_pointer(vault)
        # The pointer should NOT include area names (only count)
        assert "auth" not in result

    def test_is_single_line(self, tmp_path):
        """Pointer is a single line (compact, not a menu block)."""
        recall = load_recall()
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        result = recall.render_area_pointer(vault)
        assert len(result.strip().splitlines()) == 1

    def test_propagates_build_area_map_exception(self, tmp_path):
        """render_area_pointer propagates when build_area_map raises.

        The inner try/except was removed (PR #3 item 1) so the sole caller
        build_context can handle it via the outer D-8a guard. This pins the
        new contract: render_area_pointer no longer swallows failures silently.
        """
        recall = load_recall()
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")

        with mock.patch.object(recall, "build_area_map", side_effect=RuntimeError("boom")):
            try:
                recall.render_area_pointer(vault)
                raise AssertionError("Expected RuntimeError to propagate but it was swallowed")
            except RuntimeError as exc:
                assert "boom" in str(exc)


# ---------------------------------------------------------------------------
# build_context hook-integration tests
# ---------------------------------------------------------------------------

class TestBuildContextMenu:
    def test_with_areas_contains_pointer_not_full_menu(self, tmp_path):
        """With areas: context has the pointer (lore areas + cue), not the full menu."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth and JWT flows")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "lore areas" in ctx
        assert "unfamiliar" in ctx
        # Full menu frame must NOT be present
        assert "lore area map" not in ctx

    def test_with_areas_context_contains_area_count(self, tmp_path):
        """With areas: context includes the area count."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth and JWT flows")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "1" in ctx

    def test_with_areas_count_matches_build_area_map(self, tmp_path):
        """Count in context equals len(build_area_map(vault))."""
        recall_mod = load_recall()
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        _write_area(vault, "billing", ["stripe"], summary="Payments")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        entries = recall_mod.build_area_map(vault)
        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert str(len(entries)) in ctx

    def test_with_areas_context_contains_lore_recall(self, tmp_path):
        """Pointer includes 'lore recall' so agent knows the follow-up command."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth and JWT flows")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "lore recall" in ctx

    def test_with_areas_does_not_contain_individual_area_one_liners(self, tmp_path):
        """Individual area one-liners must NOT appear in the injection (pointer only)."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth and JWT flows")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        # The specific one-liner from the fixture must NOT appear inline
        assert "OAuth and JWT flows" not in ctx

    def test_with_areas_block_not_labeled_as_recalled(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="OAuth flows")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "Recalled (" not in ctx

    def test_zero_areas_emits_baseline_no_pointer(self, tmp_path):
        vault = _make_vault(tmp_path)
        # No areas/ files — just the empty dir
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        # Baseline must be present (the capture reminder is always emitted)
        assert "/lore:defer" in ctx or "lore" in ctx
        # No pointer and no menu when zero areas
        assert "lore areas" not in ctx
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

        # Should not crash; pointer for valid areas still appears
        out = _run_session_context({"session_id": "s1"}, _base_env(vault), cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "lore areas" in ctx

    def test_d8a_pointer_crash_leaves_vault_index_intact(self, tmp_path):
        """D-8a CRITICAL (OUTER guard): build_area_map raise propagates through
        render_area_pointer and is caught by build_context's D-8a try/except,
        leaving the vault index intact — NOT {}.

        After PR #3 item 1, render_area_pointer no longer swallows the exception
        internally; the outer D-8a guard in build_context is what catches it and
        emits the stderr diagnostic. This test exercises that outer guard path.
        Monkeypatches build_area_map to raise and verifies build_context still
        returns vault index content — NOT empty, NOT the {} fallback that main()'s
        outer guard would emit on a genuine core failure.
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
                                side_effect=RuntimeError("simulated pointer crash")
                            ):
                                mod.main()

        raw = out.getvalue()
        data = json.loads(raw)

        # Must NOT be {}
        assert data != {}, "D-8a violated: pointer crash caused {} fallback (cold session)"
        assert "hookSpecificOutput" in data, "D-8a violated: hookSpecificOutput missing"

        ctx = data["hookSpecificOutput"]["additionalContext"]
        # Vault index must be intact: capture reminder always present
        assert ctx, "D-8a violated: context is empty after pointer crash"
        # Neither pointer nor menu should be present (gracefully degraded)
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
