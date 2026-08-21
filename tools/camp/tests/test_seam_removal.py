"""camp carries no launch/resume/session seam.

The harness surface is `harness_profile.py` — its profile-config surface
(resolve_harness_profile + HarnessProfile config fields). There is no
`session_lock.py` or `session_identity.py`. These guards keep it that way:

- import-lint: harness_profile + each consumer module import cleanly (a stray
  `harness_launch` import would ImportError at collection).
- no stray references: no production module imports session_lock/session_identity
  or names a `harness_launch` module; no bare `os.execvp(` literal appears.
- no argv composition or exec in core: camp neither builds the harness command
  line nor runs it. `camp resume` prints what a shell wrapper should exec, and
  takes the argv itself from the harness seam whole. `--resume` / `--session-id`
  are forbidden only where they appear as an element of an argv list/tuple
  literal under construction — legal everywhere else, including a `==`
  comparison against a parsed CLI arg, a help string, or a docstring.
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


_FORBIDDEN_ARGV_FLAGS = ("--resume", "--session-id")

# Methods that append to a sequence already under construction. A flag constant
# handed to one of these builds an argv just as surely as a list literal does.
# Plain-function calls are deliberately NOT covered: `_consume_flag_value(rest,
# "--group")` is how camp parses its own flags, and that spelling must stay legal.
_SEQUENCE_GROWERS = frozenset({"append", "insert", "extend", "add"})


def _argv_composition_offenders(source: str) -> list[str]:
    """AST-scan one module's source for camp-core argv composition or exec.

    Parsed, not grepped, precisely so that prose about the boundary is not a
    crossing of it. A hit requires real syntax:

    - an `os.exec*`-shaped call
    - a list/tuple literal whose first element is the constant `"claude"`
      (an argv head)
    - a `--resume` or `--session-id` constant appearing as an element of a
      list/tuple literal (argv under construction), or handed to a sequence
      -growing method such as `.append()` / `.insert()` (the same argv, built
      one element at a time)

    The flag literals are legal everywhere else — a `==` comparison against a
    parsed CLI arg, a help string, a docstring, or an argument to camp's own
    flag parser — because none of those compose an argv.

    Known gap, accepted deliberately: a command assembled as a STRING
    (`f"claude --resume {sid}"`, `%`-formatting) is not detected. Narrowing
    that without flagging every help string is not worth the false positives,
    and camp hands argv to `subprocess` as a list — it never builds a shell
    string. The positive pins (a resume argv must come from
    `harness.session_resume(`) are what actually carry that case.
    """
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("exec")
        ):
            hits.append(f"exec call: {node.func.attr}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SEQUENCE_GROWERS
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value in _FORBIDDEN_ARGV_FLAGS:
                    hits.append(f"argv literal: {arg.value}")
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            if isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "claude":
                hits.append("argv literal")
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and elt.value in _FORBIDDEN_ARGV_FLAGS:
                    hits.append(f"argv literal: {elt.value}")
    return sorted(set(hits))


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
        forbidden = ("os.execvp(",)
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
        literal here, an `execv` there, a `--resume`/`--session-id` flag folded
        into an argv list — so all three are pinned.

        This scans the camp package sources ONLY. The harness seam
        (`trailhead/harness/claude_code.py`) is deliberately outside it: composing
        the argv is exactly its job, and the whole point of the boundary is that
        the literals live there and nowhere else.
        """
        offenders: dict[str, list[str]] = {}
        for p in _production_sources():
            hits = _argv_composition_offenders(p.read_text())
            if hits:
                offenders[p.name] = hits
        assert offenders == {}, f"argv-composition/exec surface in camp core: {offenders}"

    def test_resume_takes_its_argv_from_the_seam(self):
        """`camp resume` asks the harness for the command; it does not build one.

        A future edit that inlines the flag spelling here — rather than widening
        the seam — is exactly the regression the boundary exists to prevent, and
        it would not trip the literal scans above if the harness were renamed.
        """
        source = (_CAMP_PKG_DIR / "bookmark" / "resume.py").read_text()
        assert "harness.session_resume(" in source

    def test_launch_session_has_no_argv_list_literal(self):
        """launch/session.py builds no harness argv list/tuple literal.

        The only argv it spells is tmux's own, and the harness half of that
        command line is spliced in whole from the seam.
        """
        source = (_CAMP_PKG_DIR / "launch" / "session.py").read_text()
        assert _argv_composition_offenders(source) == []

    def test_launch_session_takes_its_resume_argv_from_the_seam(self):
        """The launch engine asks the harness for a resume command line.

        The mirror of the `bookmark/resume.py` pin above, and the half the
        literal scans structurally cannot carry: an engine that spelled the
        resume flag itself — or grew the argv element by element — would still
        pass every negative scan if the harness were simply never asked.
        """
        source = (_CAMP_PKG_DIR / "launch" / "session.py").read_text()
        assert "harness.session_resume(" in source


# ---------------------------------------------------------------------------
# the --resume / --session-id AST rule: argv position only
# ---------------------------------------------------------------------------


class TestArgvFlagLiteralScope:
    """`--resume` / `--session-id` are offenders only as argv-list elements.

    A `==` comparison against a parsed CLI arg, a help string, or a docstring
    all need to spell the flag as text to do their job — none of them compose
    an argv, so none of them trip the rule.
    """

    def test_resume_flag_inside_argv_list_is_an_offender(self):
        source = 'sid = "abc"\nargv = ["claude", "--resume", sid]\n'
        hits = _argv_composition_offenders(source)
        assert any("--resume" in hit for hit in hits), hits

    def test_resume_flag_in_comparison_and_help_text_is_not_an_offender(self):
        source = (
            '"""Usage: pass --resume to reattach to a prior session."""\n'
            "def handle(arg):\n"
            '    if arg == "--resume":\n'
            "        return True\n"
            "    return False\n"
        )
        assert _argv_composition_offenders(source) == []

    def test_session_id_flag_inside_argv_list_is_an_offender(self):
        source = 'sid = "abc"\nargv = ["claude", "--session-id", sid]\n'
        hits = _argv_composition_offenders(source)
        assert any("--session-id" in hit for hit in hits), hits

    def test_flag_appended_to_an_argv_under_construction_is_an_offender(self):
        """Building the same argv one element at a time is the same crossing.

        A list literal is the obvious spelling, not the only one — an argv grown
        by `.append()` / `.insert()` / `.extend()` composes exactly as much of a
        harness command line, and would otherwise slip past a literal-only scan.
        """
        for build in (
            'argv.append("--resume")',
            'argv.insert(1, "--resume")',
            'argv.extend("--session-id")',
            'parts.add("--session-id")',
        ):
            source = f"argv = []\nparts = set()\n{build}\n"
            hits = _argv_composition_offenders(source)
            assert hits != [], f"missed argv growth: {build}"

    def test_flag_passed_to_camps_own_flag_parser_is_not_an_offender(self):
        """Consuming a flag from argv is the opposite of composing one.

        `_consume_flag_value(rest, "--resume")` is how camp reads its OWN CLI
        surface, and it spells the flag as a plain call argument. Only
        sequence-growing methods count, precisely so this idiom stays legal —
        a rule that flagged every call argument would forbid camp from parsing
        the flags it owns.
        """
        source = (
            "def handle(rest):\n"
            '    ref = _consume_flag_value(rest, "--resume")\n'
            '    _consume_flag_value(rest, "--session-id")\n'
            "    return ref\n"
        )
        assert _argv_composition_offenders(source) == []

    def test_session_id_flag_in_comparison_and_help_text_is_not_an_offender(self):
        source = (
            '"""Usage: pass --session-id to pin a session identifier."""\n'
            "def handle(arg):\n"
            '    if arg == "--session-id":\n'
            "        return True\n"
            "    return False\n"
        )
        assert _argv_composition_offenders(source) == []

    def test_execvp_call_is_still_an_offender(self):
        source = 'import os\nos.execvp("claude", ["claude"])\n'
        hits = _argv_composition_offenders(source)
        assert any(hit.startswith("exec call") for hit in hits), hits


# ---------------------------------------------------------------------------
# the default claude profile pretrusts
# ---------------------------------------------------------------------------


class TestPretrustSurvivesStrip:
    def test_default_profile_should_pretrust(self):
        from camp.launch.profile import resolve_harness_profile

        group = {"group": {"name": "g"}, "members": [{"name": "r", "repo_root": "/tmp/r"}]}
        profile = resolve_harness_profile(group)
        assert profile.should_pretrust() is True
