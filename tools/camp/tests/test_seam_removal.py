"""The launch/resume/session seam is deleted.

`harness_launch.py` is renamed `harness_profile.py` and stripped to its
profile-config surface (resolve_harness_profile + HarnessProfile config fields);
`session_lock.py` and `session_identity.py` are removed entirely. These guards
lock the removal in place:

- import-lint: harness_profile + each consumer module import cleanly (a stale
  `harness_launch` import would ImportError at collection).
- absence: no production module imports session_lock/session_identity or names
  the old harness_launch module; no launch-seam literal (os.execvp / claude
  --resume / --session-id) survives.
- Regression guard: the default claude profile still pretrusts — should_pretrust()
  reads the binary basename, which the retained _CLAUDE_DEFAULT / HarnessProfile.binary
  field feeds.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
_CLI_CAMP = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "cli" / "camp"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _production_sources() -> list[Path]:
    """Every production source in the camp plugin (scripts/*.py + the cli/camp script)."""
    sources = sorted(_SCRIPTS_DIR.glob("*.py"))
    sources.append(_CLI_CAMP)
    return sources


# ---------------------------------------------------------------------------
# import-lint — harness_profile + each consumer import cleanly
# ---------------------------------------------------------------------------


class TestImportLint:
    def test_harness_profile_imports(self):
        import harness_profile

        assert hasattr(harness_profile, "resolve_harness_profile")
        assert hasattr(harness_profile, "HarnessProfile")

    def test_consumer_modules_import(self):
        # A stale `from harness_launch import …` left in any of these would raise
        # ImportError here (workspace_doc imports it at module scope).
        import activation  # noqa: F401
        import provision  # noqa: F401
        import workspace_doc  # noqa: F401

    def test_no_module_names_old_harness_launch(self):
        offenders = [p.name for p in _production_sources() if "harness_launch" in p.read_text()]
        assert offenders == [], f"stale harness_launch reference in: {offenders}"


# ---------------------------------------------------------------------------
# absence — session modules + launch seam are gone
# ---------------------------------------------------------------------------


class TestSeamAbsence:
    def test_session_modules_deleted(self):
        assert not (_SCRIPTS_DIR / "session_lock.py").exists()
        assert not (_SCRIPTS_DIR / "session_identity.py").exists()

    def test_no_module_imports_session_lock_or_identity(self):
        offenders = [
            p.name
            for p in _production_sources()
            if "session_lock" in p.read_text() or "session_identity" in p.read_text()
        ]
        assert offenders == [], f"stale session-module reference in: {offenders}"

    def test_no_launch_seam_literals(self):
        forbidden = ("os.execvp(", "--session-id", "--resume")
        offenders: dict[str, list[str]] = {}
        for p in _production_sources():
            text = p.read_text()
            hits = [tok for tok in forbidden if tok in text]
            if hits:
                offenders[p.name] = hits
        assert offenders == {}, f"launch-seam literal survived: {offenders}"


# ---------------------------------------------------------------------------
# Regression guard — the default claude profile still pretrusts
# ---------------------------------------------------------------------------


class TestPretrustSurvivesStrip:
    def test_default_profile_should_pretrust(self):
        from harness_profile import resolve_harness_profile

        group = {"group": {"name": "g"}, "members": [{"name": "r", "repo_root": "/tmp/r"}]}
        profile = resolve_harness_profile(group)
        assert profile.should_pretrust() is True
