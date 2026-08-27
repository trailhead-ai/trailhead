"""Pins the repo-wide absence of the install-manifest surface.

`install_manifest.toml`, `trailhead/fetch.py`, and the GPG-verification flow
identified by the signing key fingerprint `74AEB40C93C4250A` describe install
behavior trailhead does not implement. This sweep asserts no file references
them, so the surface cannot reappear undetected.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SELF = Path(__file__).resolve()

_EXCLUDED_DIR_NAMES = frozenset(
    {".git", ".venv", "trailhead.egg-info", "__pycache__", "node_modules"}
)

_FORBIDDEN = ("install_manifest.toml", "trailhead/fetch.py", "74AEB40C93C4250A")


def _repo_files(root: Path = _REPO_ROOT) -> list[Path]:
    """Every regular file in the repo, excluding VCS/venv/build directories and
    this guard's own source (which names the forbidden tokens to scan for)."""
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p != _SELF
        and not any(part in _EXCLUDED_DIR_NAMES for part in p.parts)
    )


def _offenders_in(paths: list[Path]) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = [tok for tok in _FORBIDDEN if tok in text]
        if hits:
            offenders[str(p)] = hits
    return offenders


class TestInstallManifestVaporwareAbsent:
    def test_no_file_references_retired_install_manifest_surface(self):
        offenders = _offenders_in(_repo_files())
        assert offenders == {}, f"retired install-manifest reference(s) survived: {offenders}"

    def test_guard_bites_against_a_planted_offender(self, tmp_path):
        """Proves the sweep actually scans content rather than passing vacuously:
        plant a temp file containing a forbidden token and assert the same scan
        function reports it as an offender."""
        planted = tmp_path / "planted_offender.md"
        planted.write_text("See `trailhead/fetch.py` for details.\n")

        offenders = _offenders_in([planted])

        assert str(planted) in offenders
        assert "trailhead/fetch.py" in offenders[str(planted)]
