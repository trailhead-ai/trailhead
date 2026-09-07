"""Every spec writer must stamp-and-certify, not just brainstorm.

Discovers every live spec-writer call site by pattern — `grep -rl "lore record
create --kind spec"` under `plugins/craft/` — rather than naming brainstorm and
planner by hand, so a third writer added later is caught by this test instead of
silently shipping an uncertified `## Maturity` section (the regression this test
guards against: planner rendered the template's `## Maturity` heading without
ever filling or certifying it, producing `empty-section` on every planner-written
spec instead of the pre-template `section-absent` the spec's own Non-Goals bless).

Each discovered file must, before its `lore record create --kind spec` call site,
both fill the `## Maturity` section and certify the drafted body through
`maturity_stamp.py` — a non-zero certify exit must refuse the create.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"

_CREATE_MARKER = "lore record create --kind spec"
_CERTIFY_MARKER = "maturity_stamp.py"
_FILL_MARKER = "## Maturity"


def _discover_spec_writers() -> list[Path]:
    result = subprocess.run(
        ["grep", "-rl", _CREATE_MARKER, str(CRAFT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), (
        f"grep failed unexpectedly: {result.stderr}"
    )
    paths = [Path(line) for line in result.stdout.splitlines() if line.strip()]
    assert paths, "expected at least one live spec-writer call site under plugins/craft/"
    return paths


def test_at_least_two_spec_writers_are_discovered_by_pattern():
    """Fixture assumption for the tests below: this repo has more than one live
    spec writer, so a per-writer contract check actually proves something."""
    writers = _discover_spec_writers()
    assert len(writers) >= 2, (
        f"expected brainstorm and planner as live spec writers, found: {writers}"
    )


def test_every_discovered_spec_writer_certifies_before_creating():
    """For each discovered writer, the certify marker must appear in the file
    text before the create call site — 'before' meaning textual order in the
    document, matching how an operator reading the file top-to-bottom would
    encounter the steps."""
    for path in _discover_spec_writers():
        text = path.read_text(encoding="utf-8")
        create_index = text.index(_CREATE_MARKER)
        assert _CERTIFY_MARKER in text, (
            f"{path} creates a spec record but never mentions the certify "
            f"reader ({_CERTIFY_MARKER})"
        )
        certify_index = text.index(_CERTIFY_MARKER)
        assert certify_index < create_index, (
            f"{path} must certify the drafted body ({_CERTIFY_MARKER}) before "
            f"the create call site, found certify at {certify_index} and create "
            f"at {create_index}"
        )


def test_every_discovered_spec_writer_fills_the_maturity_section():
    for path in _discover_spec_writers():
        text = path.read_text(encoding="utf-8")
        assert _FILL_MARKER in text, (
            f"{path} creates a spec record but never mentions filling "
            f"{_FILL_MARKER}"
        )


def test_every_discovered_spec_writer_refuses_the_write_on_non_zero_certify():
    for path in _discover_spec_writers():
        text = path.read_text(encoding="utf-8")
        assert "non-zero" in text.lower() or "nonzero" in text.lower(), (
            f"{path} must name a non-zero-refuses-the-write behavior for the "
            f"certify step"
        )
