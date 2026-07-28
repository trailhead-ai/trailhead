"""Cross-plugin `_bootstrap.py` parity test.

One independent copy of `_bootstrap.py` exists per CLI-bearing tool (camp, lore,
portage, ranger) because each one must work *before* `sys.path` is set up — at
that point none of them can import a shared helper (see each copy's own module
docstring). That chicken-and-egg necessity means the files can never be
deduplicated via a shared import, so a bugfix applied to one copy and forgotten
in the others would otherwise drift silently. This test converts that silent
drift into a caught failure: every copy must be byte-identical except for the
handful of lines that legitimately reference the owning tool's own name.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_BOOTSTRAP_PATHS = {
    "camp": _REPO_ROOT / "tools/camp/plugins/camp/_bootstrap.py",
    "lore": _REPO_ROOT / "tools/lore/plugins/lore/_bootstrap.py",
    "portage": _REPO_ROOT / "tools/portage/plugins/portage/_bootstrap.py",
    "ranger": _REPO_ROOT / "tools/ranger/plugins/ranger/_bootstrap.py",
}

# 0-based line indices that legitimately differ per copy: the docstring's
# self-reference to its own file path (line 10) and the two error-message
# lines naming the owning tool (lines 72-73). Confirmed by diffing every
# copy pairwise — every other line is byte-identical.
_TOOL_SPECIFIC_LINE_INDICES = (9, 71, 72)


def _masked_lines(path: Path) -> list[str]:
    lines = path.read_text().splitlines()
    for i in _TOOL_SPECIFIC_LINE_INDICES:
        lines[i] = "<tool-specific>"
    return lines


def test_bootstrap_copies_are_identical_besides_tool_name():
    masked = {tool: _masked_lines(path) for tool, path in _BOOTSTRAP_PATHS.items()}

    lengths = {tool: len(lines) for tool, lines in masked.items()}
    assert len(set(lengths.values())) == 1, f"line-count drift between copies: {lengths}"

    baseline_tool, baseline_lines = next(iter(masked.items()))
    for tool, lines in masked.items():
        assert lines == baseline_lines, (
            f"{tool}'s _bootstrap.py has drifted from {baseline_tool}'s outside "
            "the expected tool-specific lines"
        )
