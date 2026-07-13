"""Green-driver seam: monitor no longer hardcodes craft's code-reviewer/log-sifter
dispatch directly — that responsibility moves to the configurable
`[release].green_driver_agent`, which defaults to portage's own `green-driver` agent.

Locks:
  - monitor.md carries no literal `code-reviewer` / `log-sifter` dispatch text —
    that coupling to craft is what green-driver replaces.
  - green-driver.md exists and is discovered as a portage subagent (the
    `agents/<name>.md` convention `trailhead.capabilities` discovers by).
  - monitor.md documents the `green_driver_agent` config key, its default, and a
    named misconfiguration failure mode (never a silent dispatch failure).
"""

from __future__ import annotations

from pathlib import Path

from trailhead.capabilities import load_manifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PORTAGE_MANIFEST = _REPO_ROOT / "tools" / "portage" / "capabilities.toml"
_AGENTS_DIR = _REPO_ROOT / "tools" / "portage" / "plugins" / "portage" / "agents"
_MONITOR_MD = _AGENTS_DIR / "monitor.md"
_GREEN_DRIVER_MD = _AGENTS_DIR / "green-driver.md"


class TestMonitorNoLongerHardcodesCraftAgents:
    def test_monitor_has_no_literal_code_reviewer_dispatch(self):
        text = _MONITOR_MD.read_text()
        assert "code-reviewer" not in text, (
            "monitor.md still names craft's code-reviewer directly — that dispatch "
            "belongs to the configured green-driver agent now"
        )

    def test_monitor_has_no_literal_log_sifter_dispatch(self):
        text = _MONITOR_MD.read_text()
        assert "log-sifter" not in text, (
            "monitor.md still names craft's log-sifter directly — that dispatch "
            "belongs to the configured green-driver agent now"
        )


class TestGreenDriverAgentExists:
    def test_green_driver_md_exists(self):
        assert _GREEN_DRIVER_MD.is_file(), (
            "expected tools/portage/plugins/portage/agents/green-driver.md to exist"
        )

    def test_green_driver_discovered_as_portage_subagent(self):
        manifest = load_manifest(_PORTAGE_MANIFEST)
        assert "green-driver" in manifest.subagents, (
            f"green-driver not discovered among portage subagents: {sorted(manifest.subagents)}"
        )
        assert manifest.subagents["green-driver"] == "agents/green-driver.md"


class TestMonitorDocumentsGreenDriverConfigSeam:
    def test_monitor_references_green_driver_agent_key(self):
        text = _MONITOR_MD.read_text()
        assert "green_driver_agent" in text

    def test_monitor_documents_a_default(self):
        text = _MONITOR_MD.read_text()
        assert "green-driver" in text
        assert "default" in text.lower()

    def test_monitor_documents_named_misconfiguration_failure(self):
        text = _MONITOR_MD.read_text()
        lowered = text.lower()
        assert "not an installed" in lowered or "not installed" in lowered, (
            "monitor.md should name what's wrong when green_driver_agent points at "
            "a nonexistent agent"
        )
        assert "silent" in lowered, (
            "monitor.md should explicitly rule out a silent dispatch failure for "
            "a misconfigured green_driver_agent"
        )
