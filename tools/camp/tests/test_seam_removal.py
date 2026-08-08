"""camp carries no launch/resume/session seam.

The harness surface is `harness_profile.py` — its profile-config surface
(resolve_harness_profile + HarnessProfile config fields). There is no
`session_lock.py` or `session_identity.py`. These guards keep it that way:

- import-lint: harness_profile + each consumer module import cleanly (a stray
  `harness_launch` import would ImportError at collection).
- no stray references: no production module imports session_lock/session_identity
  or names a `harness_launch` module; no launch-seam literal (os.execvp / claude
  --resume / --session-id) appears.
- no argv composition or exec in core: camp neither builds the harness command
  line nor runs it. `camp resume` prints what a shell wrapper should exec, and
  takes the argv itself from the harness seam whole.
- pretrust: the default claude profile pretrusts — should_pretrust() reads the
  binary basename, which the _CLAUDE_DEFAULT / HarnessProfile.binary field feeds.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CAMP_PKG_DIR = _PLUGIN_DIR / "camp"
_CLI_CAMP = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "cli" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _production_sources() -> list[Path]:
    """Every production source in the camp plugin (camp/**/*.py + the cli/camp script)."""
    sources = sorted(_CAMP_PKG_DIR.rglob("*.py"))
    sources.append(_CLI_CAMP)
    return sources


# ---------------------------------------------------------------------------
# import-lint — harness_profile + each consumer import cleanly
# ---------------------------------------------------------------------------


class TestImportLint:
    def test_harness_profile_imports(self):
        import camp.launch.profile as harness_profile

        assert hasattr(harness_profile, "resolve_harness_profile")
        assert hasattr(harness_profile, "HarnessProfile")

    def test_consumer_modules_import(self):
        # A stale `from harness_launch import …` left in any of these would raise
        # ImportError here (workspace_doc imports it at module scope).
        import camp.provision.activation as activation  # noqa: F401
        import camp.provision.provision as provision  # noqa: F401
        import camp.workspace.doc as workspace_doc  # noqa: F401

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

    def test_no_module_composes_or_runs_a_command(self):
        """camp core neither BUILDS a command line nor RUNS one for the harness.

        `camp resume` prints what a shell wrapper should exec; the argv itself
        comes from the harness seam whole, and the exec belongs to the wrapper.
        Both halves are easy to quietly re-absorb into core — a `["claude", …]`
        literal here, an `execv` there — so both are pinned.

        This scans the camp package sources ONLY. The harness seam
        (`trailhead/harness/claude_code.py`) is deliberately outside it: composing
        the argv is exactly its job, and the whole point of the boundary is that
        the literals live there and nowhere else.
        """
        # Parsed, not grepped. Several modules legitimately DESCRIBE the wrapper's
        # exec in prose, and `"claude"` on its own is the configured harness binary
        # NAME (launch/profile.py's default) — both are text about the boundary,
        # not a crossing of it. Only real syntax counts: an `os.exec*` call, or
        # that name in ARGV position as the head of a list literal.
        offenders: dict[str, list[str]] = {}
        for p in _production_sources():
            hits: list[str] = []
            for node in ast.walk(ast.parse(p.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr.startswith("exec")
                ):
                    hits.append(f"exec call: {node.func.attr}")
                if (
                    isinstance(node, ast.List)
                    and node.elts
                    and isinstance(node.elts[0], ast.Constant)
                    and node.elts[0].value == "claude"
                ):
                    hits.append("argv literal")
            if hits:
                offenders[p.name] = sorted(set(hits))
        assert offenders == {}, f"argv-composition/exec surface in camp core: {offenders}"

    def test_resume_takes_its_argv_from_the_seam(self):
        """`camp resume` asks the harness for the command; it does not build one.

        A future edit that inlines the flag spelling here — rather than widening
        the seam — is exactly the regression the boundary exists to prevent, and
        it would not trip the literal scans above if the harness were renamed.
        """
        source = (_CAMP_PKG_DIR / "bookmark" / "resume.py").read_text()
        assert "harness.session_resume(" in source


# ---------------------------------------------------------------------------
# the default claude profile pretrusts
# ---------------------------------------------------------------------------


class TestPretrustSurvivesStrip:
    def test_default_profile_should_pretrust(self):
        from camp.launch.profile import resolve_harness_profile

        group = {"group": {"name": "g"}, "members": [{"name": "r", "repo_root": "/tmp/r"}]}
        profile = resolve_harness_profile(group)
        assert profile.should_pretrust() is True
