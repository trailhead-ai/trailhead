"""camp carries no launch/resume/session seam.

The harness surface is `harness_profile.py` — its profile-config surface
(resolve_harness_profile + HarnessProfile config fields). There is no
`session_lock.py` or `session_identity.py`. These guards keep it that way:

- import-lint: harness_profile + each consumer module import cleanly (a stray
  `harness_launch` import would ImportError at collection).
- no stray references: no production module imports session_lock/session_identity
  or names a `harness_launch` module; no launch-seam literal (os.execvp / claude
  --resume / --session-id) appears.
- pretrust: the default claude profile pretrusts — should_pretrust() reads the
  binary basename, which the _CLAUDE_DEFAULT / HarnessProfile.binary field feeds.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_SCRIPTS_DIR = _PLUGIN_DIR / "scripts"
_CLI_CAMP = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "cli" / "camp"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


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
        import camp.harness.profile as harness_profile

        assert hasattr(harness_profile, "resolve_harness_profile")
        assert hasattr(harness_profile, "HarnessProfile")

    def test_consumer_modules_import(self):
        # A stale `from harness_launch import …` left in any of these would raise
        # ImportError here (workspace_doc imports it at module scope).
        import camp.provision.activation as activation  # noqa: F401
        import camp.provision.provision as provision  # noqa: F401
        import workspace_doc  # noqa: F401

    def test_no_module_names_old_harness_launch(self):
        offenders = [p.name for p in _production_sources() if "harness_launch" in p.read_text()]
        assert offenders == [], f"stale harness_launch reference in: {offenders}"


# ---------------------------------------------------------------------------
# no stray references — session modules + launch seam
# ---------------------------------------------------------------------------


class TestSeamAbsence:
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
# the default claude profile pretrusts
# ---------------------------------------------------------------------------


class TestPretrustSurvivesStrip:
    def test_default_profile_should_pretrust(self):
        from camp.harness.profile import resolve_harness_profile

        group = {"group": {"name": "g"}, "members": [{"name": "r", "repo_root": "/tmp/r"}]}
        profile = resolve_harness_profile(group)
        assert profile.should_pretrust() is True
