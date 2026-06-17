"""Cross-plugin contract: post_merge_handoff marker field-parity guard.

Asserts that the `post_merge_handoff` marker fields documented in
tools/portage/plugins/portage/agents/monitor.md (the producer) are a SUPERSET
of the required inputs documented in
tools/landing/plugins/landing/agents/soaker.md (the consumer).

This guards the boundary introduced when Slice 6b deleted the old craft
test_handoff_field_parity.py: the contract survived the deletion because the
producer/consumer moved to portage monitor → landing soaker.

The original bug it prevents: consumer requires `group_toml_path` to locate
[release].soak_health_command; producer forgets to emit it → auto-dispatched
soak can't find the group TOML.

Strategy:
  - Parse the handoff JSON shape from monitor.md's "Post-merge handoff marker"
    section (the JSON example line).
  - Parse the required inputs from soaker.md's "Inputs" section (lines
    starting with `- ` and containing a backtick-quoted field name).
  - Assert every required soaker input is either present in the handoff
    marker fields OR is marked as optional/context-only.

Hermetic: reads only the two markdown files under their respective plugin trees.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent
MONITOR_MD = REPO_ROOT / "tools" / "portage" / "plugins" / "portage" / "agents" / "monitor.md"
SOAKER_MD = REPO_ROOT / "tools" / "landing" / "plugins" / "landing" / "agents" / "soaker.md"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_handoff_marker_fields(monitor_text: str) -> set[str]:
    """Extract top-level fields from the post_merge_handoff JSON example in monitor.md.

    Finds the JSON line like:
      {"post_merge_handoff": {"merge_pairs": [...], "manifest_path": "...", ...}}
    and returns the set of keys inside `post_merge_handoff` (the outer wrapper).

    Strategy: find the substring starting after `"post_merge_handoff": {` and
    extract the direct child keys (those at depth=1 inside that object).
    Uses a depth-tracking scan to find keys only at the immediate top level
    of the `post_merge_handoff` object, not nested within arrays or sub-objects.
    """
    for line in monitor_text.splitlines():
        stripped = line.strip()
        if '"post_merge_handoff"' not in stripped:
            continue
        # Find the start of the post_merge_handoff value object
        marker = '"post_merge_handoff"'
        idx = stripped.find(marker)
        if idx < 0:
            continue
        # Advance past the marker and any whitespace/:/{
        rest = stripped[idx + len(marker):]
        colon_pos = rest.find("{")
        if colon_pos < 0:
            continue
        inner = rest[colon_pos + 1:]  # content after the opening {

        # Extract keys at depth 0 of the inner object
        # (depth increments on { or [, decrements on } or ])
        keys: set[str] = set()
        depth = 0
        i = 0
        while i < len(inner):
            ch = inner[i]
            if ch in ("{", "["):
                depth += 1
                i += 1
            elif ch in ("}", "]"):
                if depth == 0:
                    break  # end of post_merge_handoff object
                depth -= 1
                i += 1
            elif ch == '"' and depth == 0:
                # Possible key at top level
                end = inner.find('"', i + 1)
                if end > i:
                    key = inner[i + 1:end]
                    # Check it's followed by a colon (is actually a key, not a value)
                    after = inner[end + 1:].lstrip()
                    if after.startswith(":"):
                        keys.add(key)
                    i = end + 1
                else:
                    i += 1
            else:
                i += 1
        if keys:
            return keys
    return set()


def _parse_required_inputs(soaker_text: str) -> set[str]:
    """Extract the required input field names from the Inputs section of soaker.md.

    Looks for the '## Inputs' section and reads bullet lines like:
      - `field_name` — description
    Returns the set of backtick-quoted names.
    """
    in_inputs = False
    fields: set[str] = set()
    for line in soaker_text.splitlines():
        if re.match(r"^##\s+Inputs", line):
            in_inputs = True
            continue
        if in_inputs:
            # Stop at the next ## section
            if re.match(r"^##", line):
                break
            # Match bullet lines with backtick-quoted field names
            m = re.match(r"^\s*-\s+`([^`]+)`", line)
            if m:
                fields.add(m.group(1))
    return fields


# ---------------------------------------------------------------------------
# The parity test
# ---------------------------------------------------------------------------

class TestPostMergeHandoffParity:
    """post_merge_handoff marker (portage monitor) must cover all required inputs of
    landing soaker."""

    @pytest.fixture
    def monitor_text(self) -> str:
        assert MONITOR_MD.exists(), f"monitor.md not found at {MONITOR_MD}"
        return MONITOR_MD.read_text(encoding="utf-8")

    @pytest.fixture
    def soaker_text(self) -> str:
        assert SOAKER_MD.exists(), f"soaker.md not found at {SOAKER_MD}"
        return SOAKER_MD.read_text(encoding="utf-8")

    def test_handoff_marker_has_group_toml_path(
        self, monitor_text: str
    ) -> None:
        """Parity guard: the post_merge_handoff marker must include group_toml_path.

        group_toml_path is the field whose omission caused the original bug:
        the auto-dispatched soaker can't find [release].soak_health_command
        without it.
        """
        fields = _parse_handoff_marker_fields(monitor_text)
        assert fields, (
            "Could not parse post_merge_handoff fields from monitor.md "
            "— check the JSON example format"
        )
        assert "group_toml_path" in fields, (
            f"post_merge_handoff marker is missing 'group_toml_path' — "
            f"soaker requires it but the marker only has: {sorted(fields)}\n"
            f"(the auto-dispatched soak can't find the group TOML without this field)"
        )

    def test_handoff_marker_covers_all_required_inputs(
        self, monitor_text: str, soaker_text: str
    ) -> None:
        """Producer marker fields ⊇ consumer required inputs.

        merge_pairs is excluded: it's 'optional — for context only' per soaker.md,
        so it doesn't need to be in the handoff marker shape (it's passed
        differently as context). This mirrors the original test's treatment of
        merge_pairs.
        """
        marker_fields = _parse_handoff_marker_fields(monitor_text)
        required_inputs = _parse_required_inputs(soaker_text)

        assert marker_fields, (
            "Could not parse post_merge_handoff fields from monitor.md"
        )
        assert required_inputs, (
            "Could not parse required inputs from soaker.md Inputs section"
        )

        # merge_pairs is optional in soaker.md ("for context only") — it may be
        # threaded separately as formatted context rather than a raw marker field
        optional_context_fields = {"merge_pairs"}

        missing = required_inputs - marker_fields - optional_context_fields
        assert not missing, (
            f"soaker.md requires inputs that are NOT in the post_merge_handoff marker:\n"
            f"  missing: {sorted(missing)}\n"
            f"  marker fields: {sorted(marker_fields)}\n"
            f"  required inputs: {sorted(required_inputs)}\n"
            f"Add the missing fields to the post_merge_handoff marker in monitor.md."
        )

    def test_handoff_section_exists_in_monitor(self, monitor_text: str) -> None:
        """The post_merge_handoff JSON marker must exist in monitor.md."""
        assert "post_merge_handoff" in monitor_text, (
            "monitor.md must contain the post_merge_handoff JSON marker"
        )

    def test_inputs_section_exists_in_soaker(self, soaker_text: str) -> None:
        """The '## Inputs' section must exist in soaker.md."""
        assert "## Inputs" in soaker_text, (
            "soaker.md must have an '## Inputs' section"
        )
