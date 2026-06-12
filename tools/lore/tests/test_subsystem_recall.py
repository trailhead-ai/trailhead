"""Rederived recall + session-context integration tests (D23 area semantics).

These tests cover the D23 recall API (build_area_map, recall_areas) and
the session-context pointer integration. The old subsystem-API tests
(derive_subsystem_keywords, infer_subsystems, render_subsystem_block) were
replaced by test_recall_core.py in Slice 0.

This file is retained to assert the session-context.py integration contract:
- A compact area POINTER (count + lore areas cue) is emitted at SessionStart,
  not the full area map and not an auto-inject recall block. The agent calls
  `lore areas` to list them, then `lore recall --areas` explicitly.
- No automatic "Recalled (...)" block appears regardless of branch name.
- A branch that contains an area keyword still only shows the pointer, not
  matched content (the anti-injection regression guard).
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


def load_script(name: str):
    """Load a module from plugins/lore/scripts/ by stem, freshly each call."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    for cached in (name, "vault", "frontmatter", "status_validator",
                   "regenerate_indices", "sessions"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec so @dataclass can resolve cls.__module__
    sys.modules[name] = mod
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


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for d in ("areas", "deferred", "dead-ends", "lessons", "sessions"):
        (vault / d).mkdir(parents=True)
    return vault


def _write_area(vault: Path, name: str, keywords: list[str]) -> Path:
    p = vault / "areas" / f"{name}.md"
    kw_str = "[" + ", ".join(keywords) + "]"
    p.write_text(
        f"---\ntype: area\nname: {name}\nkeywords: {kw_str}\n---\n\n"
        f"## Overview\nThis is the {name} area.\n"
    )
    return p


def _run_session_context(stdin_payload: dict, env: dict, cwd: Path):
    mod = load_hook("session-context")
    out = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
            with mock.patch("sys.stdout", out):
                with mock.patch.object(os, "getcwd", return_value=str(cwd)):
                    mod.main()
    return out.getvalue()


# ---------------------------------------------------------------------------
# D23 API shape: old subsystem functions are absent
# ---------------------------------------------------------------------------

class TestD23ApiShape:
    """Guard against accidentally resurrecting the old subsystem API."""

    def test_derive_subsystem_keywords_not_present(self):
        """The old branch-keyword matching API must not exist."""
        recall = load_script("recall")
        assert not hasattr(recall, "derive_subsystem_keywords"), (
            "derive_subsystem_keywords was deleted in the subsystems→areas rename; "
            "it must not reappear"
        )

    def test_infer_subsystems_not_present(self):
        """The old auto-inject infer step must not exist."""
        recall = load_script("recall")
        assert not hasattr(recall, "infer_subsystems"), (
            "infer_subsystems was deleted; use recall_areas + lore recall --areas"
        )

    def test_render_subsystem_block_not_present(self):
        """The old render primitive must not exist."""
        recall = load_script("recall")
        assert not hasattr(recall, "render_subsystem_block"), (
            "render_subsystem_block was deleted; use render_recall_banner"
        )

    def test_new_api_present(self):
        """D23 replacement functions exist."""
        recall = load_script("recall")
        assert hasattr(recall, "build_area_map")
        assert hasattr(recall, "recall_areas")
        assert hasattr(recall, "render_recall_banner")
        assert hasattr(recall, "render_area_menu")


# ---------------------------------------------------------------------------
# Integration: session-context.py emits the area MAP menu, not auto-inject
# ---------------------------------------------------------------------------

class TestSessionContextWithSubsystemBlock:
    def test_matching_branch_emits_area_pointer_not_auto_inject(self, tmp_path):
        """A branch whose name contains an area keyword must show the area POINTER,
        NOT an auto-injected 'Recalled (...)' block. The agent runs `lore areas`
        to list them, then calls 'lore recall --areas' explicitly.

        This is the anti-injection regression guard (the noise that got the old
        recall deleted) rederived for D23: pointer ≠ matched content.
        """
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"])
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
        ctx = data["hookSpecificOutput"]["additionalContext"]
        # The area pointer must be present (count + lore areas cue)
        assert "lore areas" in ctx, (
            f"Area pointer absent from context. Context:\n{ctx}"
        )
        # But NO auto-inject recall block — the agent must call lore recall explicitly
        assert "Recalled (" not in ctx, (
            "Auto-inject recall block must not appear; only the pointer should be "
            f"emitted at SessionStart. Context:\n{ctx}"
        )

    def test_non_matching_branch_only_baseline(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "payments", ["stripe"])
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
        # Area pointer is still present (always loaded, regardless of branch)
        assert "lore areas" in ctx
        # No auto-inject recall block
        assert "Recalled (" not in ctx

    def test_no_crash_with_empty_areas_dir(self, tmp_path):
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
