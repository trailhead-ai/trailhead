"""Tests for trailhead/harness/ — the harness interface, factory, and detection."""


import pytest

from trailhead.harness import (
    ClaudeCodeHarness,
    Harness,
    HarnessError,
    canonical_name,
    detect_harnesses,
    get_harness,
    known_harness_names,
    resolve_harnesses,
)


class TestFactory:
    def test_known_names_includes_claude_code(self):
        assert "claude_code" in known_harness_names()

    def test_get_harness_returns_instance(self):
        h = get_harness("claude_code")
        assert isinstance(h, ClaudeCodeHarness)
        assert isinstance(h, Harness)
        assert h.name == "claude_code"

    def test_alias_claude_resolves_to_claude_code(self):
        assert canonical_name("claude") == "claude_code"
        assert isinstance(get_harness("claude"), ClaudeCodeHarness)

    def test_unknown_harness_raises(self):
        with pytest.raises(HarnessError, match="codex"):
            get_harness("codex")

    def test_resolve_harnesses_dedupes_by_canonical_name(self):
        out = resolve_harnesses(["claude", "claude_code", "claude-code"])
        assert len(out) == 1
        assert out[0].name == "claude_code"

    def test_resolve_harnesses_preserves_order(self):
        out = resolve_harnesses(["claude_code"])
        assert [h.name for h in out] == ["claude_code"]


class TestDetection:
    def test_detect_true_when_claude_dir_present(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        assert ClaudeCodeHarness.detect({"HOME": str(tmp_path)}) is True

    def test_detect_false_when_absent(self, tmp_path):
        assert ClaudeCodeHarness.detect({"HOME": str(tmp_path)}) is False

    def test_detect_honors_explicit_override(self, tmp_path):
        cdir = tmp_path / "custom-claude"
        cdir.mkdir()
        assert ClaudeCodeHarness.detect({"TRAILHEAD_CLAUDE_DIR": str(cdir)}) is True
        assert ClaudeCodeHarness.detect({"TRAILHEAD_CLAUDE_DIR": str(tmp_path / "nope")}) is False

    def test_detect_harnesses_finds_claude_code(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        found = detect_harnesses({"HOME": str(tmp_path)})
        assert [h.name for h in found] == ["claude_code"]

    def test_detect_harnesses_empty_when_none_present(self, tmp_path):
        assert detect_harnesses({"HOME": str(tmp_path)}) == []


class TestComposedRoot:
    def test_per_harness_root(self, tmp_path):
        h = ClaudeCodeHarness()
        assert h.composed_root(tmp_path) == tmp_path / "composed" / "claude_code"


class TestMarkers:
    def test_is_registered_reads_marker(self, tmp_path):
        h = ClaudeCodeHarness()
        assert h.is_registered(tmp_path) is False
        (tmp_path / ".trailhead-registered").write_text("{}")
        assert h.is_registered(tmp_path) is True

    def test_is_installed_reads_per_tool_marker(self, tmp_path):
        h = ClaudeCodeHarness()
        assert h.is_installed("lore", tmp_path) is False
        (tmp_path / ".trailhead-installed-lore").write_text("{}")
        assert h.is_installed("lore", tmp_path) is True


class TestDelegationToRegistry:
    def test_generate_manifest_writes_marketplace(self, tmp_path):
        ClaudeCodeHarness().generate_manifest(["lore"], tmp_path)
        mkt = tmp_path / ".claude-plugin" / "marketplace.json"
        assert mkt.exists()

    def test_register_and_install_use_runner(self, tmp_path):
        calls = []
        def runner(args, **kw):
            return calls.append(list(args))
        h = ClaudeCodeHarness()
        h.register(tmp_path, runner=runner)
        h.install_tool("lore", tmp_path, runner=runner)
        assert any("marketplace" in c and "add" in c for c in calls)
        assert any("install" in c and "lore@trailhead" in c for c in calls)
        # markers written after success
        assert h.is_registered(tmp_path)
        assert h.is_installed("lore", tmp_path)
