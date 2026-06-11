"""M-2: post_merge_handoff marker field-parity guard.

Asserts that the `post_merge_handoff` marker fields documented in watch-pr.md
are a SUPERSET of the required inputs documented in watch-preview.md (agent).

This is a structural guard: producer (watch-pr) ⊇ consumer (watch-preview).
Adding a required input to watch-preview without adding the matching field to
the watch-pr handoff marker must make this test fail, preventing I-1 class regressions.

Strategy:
  - Parse the handoff JSON shape from watch-pr.md's "Post-merge handoff marker"
    section (the JSON example line).
  - Parse the required inputs from watch-preview.md's "Inputs" section (lines
    starting with `- ` and containing a backtick-quoted field name).
  - Assert every required watch-preview input is either present in the handoff
    marker fields OR is marked as derivable from manifest (merge_pairs context
    is excluded — it's context-only per the agent doc).

Hermetic: reads only the two markdown files under plugins/forge/agents/.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
AGENTS_DIR = REPO_ROOT / "plugins" / "forge" / "agents"
WATCH_PR_MD = AGENTS_DIR / "watch-pr.md"
WATCH_PREVIEW_MD = AGENTS_DIR / "watch-preview.md"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_handoff_marker_fields(watch_pr_text: str) -> set[str]:
    """Extract top-level fields from the post_merge_handoff JSON example in watch-pr.md.

    Finds the JSON line like:
      {"post_merge_handoff": {"merge_pairs": [...], "manifest_path": "...", ...}}
    and returns the set of keys inside `post_merge_handoff` (the outer wrapper).

    Strategy: find the substring starting after `"post_merge_handoff": {` and
    extract the direct child keys (those at depth=1 inside that object).
    Uses a depth-tracking scan to find keys only at the immediate top level
    of the `post_merge_handoff` object, not nested within arrays or sub-objects.
    """
    for line in watch_pr_text.splitlines():
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


def _parse_required_inputs(watch_preview_text: str) -> set[str]:
    """Extract the required input field names from the Inputs section of watch-preview.md.

    Looks for the '## Inputs' section and reads bullet lines like:
      - `field_name` — description
    Returns the set of backtick-quoted names.
    """
    in_inputs = False
    fields: set[str] = set()
    for line in watch_preview_text.splitlines():
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

class TestHandoffFieldParity:
    """post_merge_handoff marker (watch-pr) must cover all required inputs of watch-preview."""

    @pytest.fixture
    def watch_pr_text(self) -> str:
        assert WATCH_PR_MD.exists(), f"watch-pr.md not found at {WATCH_PR_MD}"
        return WATCH_PR_MD.read_text(encoding="utf-8")

    @pytest.fixture
    def watch_preview_text(self) -> str:
        assert WATCH_PREVIEW_MD.exists(), f"watch-preview.md not found at {WATCH_PREVIEW_MD}"
        return WATCH_PREVIEW_MD.read_text(encoding="utf-8")

    def test_handoff_marker_has_group_toml_path(
        self, watch_pr_text: str
    ) -> None:
        """I-1 guard: the post_merge_handoff marker must include group_toml_path."""
        fields = _parse_handoff_marker_fields(watch_pr_text)
        assert fields, (
            "Could not parse post_merge_handoff fields from watch-pr.md "
            "— check the JSON example format"
        )
        assert "group_toml_path" in fields, (
            f"post_merge_handoff marker is missing 'group_toml_path' — "
            f"watch-preview requires it but the marker only has: {sorted(fields)}\n"
            f"(I-1: the auto-dispatched soak can't find the group TOML without this field)"
        )

    def test_handoff_marker_covers_all_required_inputs(
        self, watch_pr_text: str, watch_preview_text: str
    ) -> None:
        """M-2: producer marker fields ⊇ consumer required inputs.

        merge_pairs is excluded: it's 'optional — for context only' per watch-preview.md,
        so it doesn't need to be in the handoff marker shape (it's passed differently).
        """
        marker_fields = _parse_handoff_marker_fields(watch_pr_text)
        required_inputs = _parse_required_inputs(watch_preview_text)

        assert marker_fields, (
            "Could not parse post_merge_handoff fields from watch-pr.md"
        )
        assert required_inputs, (
            "Could not parse required inputs from watch-preview.md Inputs section"
        )

        # merge_pairs is optional in watch-preview.md and not a marker field
        # (it's thread separately as a formatted string in the dispatch prompt)
        optional_context_fields = {"merge_pairs"}

        missing = required_inputs - marker_fields - optional_context_fields
        assert not missing, (
            f"watch-preview.md requires inputs that are NOT in the post_merge_handoff marker:\n"
            f"  missing: {sorted(missing)}\n"
            f"  marker fields: {sorted(marker_fields)}\n"
            f"  required inputs: {sorted(required_inputs)}\n"
            f"Add the missing fields to the post_merge_handoff marker in watch-pr.md."
        )

    def test_handoff_section_exists_in_watch_pr(self, watch_pr_text: str) -> None:
        """The 'Post-merge handoff marker' section must exist in watch-pr.md."""
        assert "post_merge_handoff" in watch_pr_text, (
            "watch-pr.md must contain the post_merge_handoff JSON marker"
        )

    def test_inputs_section_exists_in_watch_preview(self, watch_preview_text: str) -> None:
        """The '## Inputs' section must exist in watch-preview.md."""
        assert "## Inputs" in watch_preview_text, (
            "watch-preview.md must have an '## Inputs' section"
        )
