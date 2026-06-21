"""Slice 4: dev-layer consolidation structural tests.

TDD contract:
  1. Root /.claude-plugin/marketplace.json exists, parses, name == "trailhead-local",
     plugins[] has exactly 5 entries, every source starts with ./tools/ and resolves
     to an existing plugins/<tool>/.claude-plugin/plugin.json under the repo root.
  2. No tools/*/.claude-plugin/marketplace.json remains.
  3. Repo-wide grep guard: no trailhead-{lore,camp,craft,portage,landing} marketplace
     names and no @trailhead-<tool> / <tool>-local install refs remain in source/docs
     (excluding bin/migrate-marketplaces.sh and trailhead/tests/).

Write BEFORE implementation — these tests must fail RED first, then pass GREEN after.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_ROOT_MARKETPLACE = _REPO_ROOT / ".claude-plugin" / "marketplace.json"
_TOOLS = ["lore", "camp", "craft", "portage", "landing"]

# Files/dirs to exclude from the grep guard
_GREP_EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}

# Exclude the migration script (it intentionally references old names to remove them),
# the trailhead/tests/ dir (contains intentional negative assertions, not install refs),
# and tools/lore/tests/ (contains expected path strings being tested, not stale refs).
_GREP_EXCLUDE_PATHS = {
    _REPO_ROOT / "bin" / "migrate-marketplaces.sh",
    _REPO_ROOT / "trailhead" / "tests",
    _REPO_ROOT / "tools" / "lore" / "tests",
}


def _is_excluded(path: Path) -> bool:
    for exc in _GREP_EXCLUDE_PATHS:
        try:
            path.relative_to(exc)
            return True
        except ValueError:
            pass
    for part in path.parts:
        if part in _GREP_EXCLUDE_DIRS:
            return True
    return False


def _collect_grep_files() -> list[Path]:
    """Collect source/doc files for the grep guard, excluding tests and migration script."""
    suffixes = {".md", ".json", ".py", ".toml", ".sh"}
    files: list[Path] = []
    for f in _REPO_ROOT.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix not in suffixes:
            continue
        if _is_excluded(f):
            continue
        files.append(f)
    return files


# ---------------------------------------------------------------------------
# T-D1: Root marketplace.json exists and has the right shape
# ---------------------------------------------------------------------------


class TestRootMarketplaceShape:
    def test_root_marketplace_exists(self):
        assert _ROOT_MARKETPLACE.exists(), (
            f"Root marketplace not found: {_ROOT_MARKETPLACE}\n"
            "Expected /.claude-plugin/marketplace.json at repo root"
        )

    def test_root_marketplace_parses(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        assert isinstance(data, dict), "marketplace.json must be a JSON object"

    def test_root_marketplace_name_is_trailhead_local(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        assert data.get("name") == "trailhead-local", (
            f"Expected name='trailhead-local', got: {data.get('name')!r}"
        )

    def test_root_marketplace_has_five_plugins(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        plugins = data.get("plugins", [])
        assert len(plugins) == 5, (
            f"Expected 5 plugin entries, got {len(plugins)}: "
            f"{[p.get('name') for p in plugins]}"
        )

    def test_root_marketplace_plugin_names(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        names = {p.get("name") for p in data.get("plugins", [])}
        assert names == set(_TOOLS), (
            f"Expected plugin names {set(_TOOLS)}, got {names}"
        )

    def test_every_source_starts_with_tools(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        for entry in data.get("plugins", []):
            src = entry.get("source", "")
            assert src.startswith("./tools/"), (
                f"Plugin {entry.get('name')!r}: source must start with './tools/', got {src!r}"
            )

    def test_every_source_resolves_to_plugin_json(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        for entry in data.get("plugins", []):
            tool = entry.get("name")
            src = entry.get("source", "")
            # source is relative to repo root; resolve the plugin.json path
            plugin_json = _REPO_ROOT / src / ".claude-plugin" / "plugin.json"
            assert plugin_json.exists(), (
                f"Plugin {tool!r}: source={src!r} does not resolve to an existing "
                f"plugins/{tool}/.claude-plugin/plugin.json.\n"
                f"Expected: {plugin_json}"
            )


# ---------------------------------------------------------------------------
# T-D2: Per-tool source marketplaces are deleted
# ---------------------------------------------------------------------------


class TestPerToolMarketplacesDeleted:
    @pytest.mark.parametrize("tool", _TOOLS)
    def test_per_tool_marketplace_absent(self, tool: str):
        path = _REPO_ROOT / "tools" / tool / ".claude-plugin" / "marketplace.json"
        assert not path.exists(), (
            f"Stale per-tool marketplace still present: {path}\n"
            "Expected this file to be deleted as part of dev-layer consolidation."
        )


# ---------------------------------------------------------------------------
# T-D3: Repo-wide grep guard — no stale marketplace name refs in source/docs
# ---------------------------------------------------------------------------


class TestRepoWideGrepGuard:
    """Regression oracle: no old per-tool marketplace names remain in source/docs.

    Excludes:
    - bin/migrate-marketplaces.sh (legitimately references old names to remove them)
    - trailhead/tests/ (contains intentional negative-assertion strings)
    - .git/, __pycache__, etc.
    """

    def _grep_files(self, pattern: str, files: list[Path]) -> list[tuple[Path, int, str]]:
        hits: list[tuple[Path, int, str]] = []
        import re
        rx = re.compile(pattern)
        for f in files:
            try:
                for lineno, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append((f, lineno, line.strip()))
            except OSError:
                pass
        return hits

    def test_no_trailhead_tool_marketplace_names(self):
        """No trailhead-{lore,camp,craft,portage,landing} marketplace names remain."""
        files = _collect_grep_files()
        pattern = r"trailhead-(?:lore|camp|craft|portage|landing)"
        hits = self._grep_files(pattern, files)
        if hits:
            lines = [
                f"Found {len(hits)} occurrence(s) of stale trailhead-<tool> marketplace names:"
            ]
            for f, ln, line in hits[:10]:
                lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(lines))

    def test_no_at_trailhead_tool_install_refs(self):
        """No @trailhead-<tool> install refs remain (e.g. craft@trailhead-craft)."""
        files = _collect_grep_files()
        pattern = r"@trailhead-(?:lore|camp|craft|portage|landing)"
        hits = self._grep_files(pattern, files)
        if hits:
            lines = [
                f"Found {len(hits)} occurrence(s) of @trailhead-<tool> install refs:"
            ]
            for f, ln, line in hits[:10]:
                lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(lines))

    def test_no_tool_local_install_refs_in_docs(self):
        """No <tool>-local install refs remain in .md or .json files.

        Note: test_renames_guard.py mentions 'craft-local' in historical rename
        comments — those are excluded via the trailhead/tests/ exclusion.
        """
        files = [
            f for f in _collect_grep_files()
            if f.suffix in {".md", ".json"}
        ]
        # Match <tool>-local as a marketplace name or @<tool>-local install ref
        pattern = r"(?:lore|camp|craft|portage|landing)-local"
        hits = self._grep_files(pattern, files)
        if hits:
            lines = [
                f"Found {len(hits)} occurrence(s) of <tool>-local marketplace refs in docs/json:"
            ]
            for f, ln, line in hits[:10]:
                lines.append(f"  {f.relative_to(_REPO_ROOT)}:{ln}: {line}")
            pytest.fail("\n".join(lines))
