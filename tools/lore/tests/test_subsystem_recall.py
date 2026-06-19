"""Recall API shape tests (D23 area semantics).

Covers the D23 recall API (build_area_map, render_area_menu, render_area_pointer).
The old subsystem-API tests (derive_subsystem_keywords, infer_subsystems,
render_subsystem_block) were replaced by test_recall_core.py in Slice 0.

The SessionStart hook integration tests were removed in Slice 2, S5 (F5: no
SessionStart hook; lore is fully pull — orientation lives in agent-rules +
S6 skill descriptions).
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

    def test_recall_command_path_not_present(self):
        """The recall COMMAND path was retired in Slice 5 (S3) — `lore search` is
        the query interface. Its symbols must not reappear."""
        recall = load_script("recall")
        for name in ("recall_areas", "render_recall_banner",
                     "RecallItem", "RecallResult"):
            assert not hasattr(recall, name), (
                f"recall.{name} is part of the retired recall command path "
                "(Slice 5); use `lore search` instead."
            )

    def test_area_map_api_present(self):
        """The kept area-map path (serves `lore areas` + the SessionStart pointer)."""
        recall = load_script("recall")
        assert hasattr(recall, "build_area_map")
        assert hasattr(recall, "render_area_menu")
        assert hasattr(recall, "render_area_pointer")


