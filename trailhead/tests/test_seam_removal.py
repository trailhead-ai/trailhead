"""Axiom-1 guard: no harness-CLI launch/enumeration invocation literal survives
outside `trailhead/harness/claude_code.py`.

Axiom 1 (see `trailhead/harness/base.py`) is that harness-specific behavior
lives behind the Harness seam, never leaking into the harness-agnostic core.
The launch trio and enumeration pair introduced argv literals —
`--remote-control`, `--session-id`, `claude agents` — that belong ONLY inside
`claude_code.py`. This sweep pins that: none of those literals may appear
anywhere else in `trailhead/`, excluding `trailhead/tests/` (test fixtures
legitimately reference the literals they exercise) and
`trailhead/harness/claude_code.py` itself (their one sanctioned home).

Follows the pattern of `tools/camp/tests/test_seam_removal.py`
(`TestSeamAbsence.test_no_launch_seam_literals`), but needs its own root
discovery: camp's `_production_sources()` walks the camp plugin tree, which
is the wrong tree for this seam. This sweep walks `trailhead/` proper.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG_DIR = _REPO_ROOT / "trailhead"
_TESTS_DIR = _PKG_DIR / "tests"
_SEAM_MODULE = _PKG_DIR / "harness" / "claude_code.py"

_FORBIDDEN = ("--remote-control", "--session-id", "claude agents")


def _production_sources() -> list[Path]:
    """Every production source in `trailhead/`, excluding `tests/` and the one
    sanctioned home of the launch/enumeration literals."""
    return sorted(
        p for p in _PKG_DIR.rglob("*.py") if _TESTS_DIR not in p.parents and p != _SEAM_MODULE
    )


def _offenders_in(paths: list[Path]) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for p in paths:
        text = p.read_text()
        hits = [tok for tok in _FORBIDDEN if tok in text]
        if hits:
            offenders[str(p)] = hits
    return offenders


class TestSeamAbsence:
    def test_no_launch_seam_literals(self):
        offenders = _offenders_in(_production_sources())
        assert offenders == {}, f"launch-seam literal survived: {offenders}"

    def test_guard_bites_against_a_planted_offender(self, tmp_path):
        """Proves the sweep actually catches something, rather than passing
        forever over an accidentally-empty file set: plant a temp source file
        containing `--remote-control` and assert the SAME scan function reports
        it as an offender."""
        planted = tmp_path / "planted_offender.py"
        planted.write_text('SESSION_ARGS = ["claude", "--remote-control"]\n')

        offenders = _offenders_in([planted])

        assert str(planted) in offenders
        assert "--remote-control" in offenders[str(planted)]
