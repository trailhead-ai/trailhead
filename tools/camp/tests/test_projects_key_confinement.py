"""Structural check: the projects-key path encoding stays inside the harness
boundary.

This project's standing rule is that a test must execute its subject and must
never assert over source text. An AST checker satisfies both at once when it
genuinely runs against a tree whose content decides the answer: the checker is
the subject, the swept package is the input that varies, and every assertion
below is on what the checker returned -- never on a sentence read out of a
file.

The rule the checker enforces: a module derives the projects-key encoding by
chaining `.replace(".", "-")` directly onto `.replace("/", "-")`. A module that
only calls a harness method that does this internally has built no such chain
and is not an offender -- otherwise the one legitimate production caller at
`camp/cli/session.py` (which calls `session_transcript_path`, never rebuilds
the path itself) would be condemned for using the seam correctly.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_CAMP_PROD_ROOT = REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "camp"
_HARNESS_MODULE = REPO_ROOT / "trailhead" / "harness" / "claude_code.py"


def _is_replace_call(node: ast.AST, first: str, second: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "replace"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == first
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == second
    )


def _projects_key_offenders(tree: ast.Module) -> list[int]:
    """Line numbers of every site in *tree* that re-derives the encoding."""
    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_replace_call(node, ".", "-"):
            if _is_replace_call(node.func.value, "/", "-"):
                offenders.append(node.lineno)
    return offenders


def _sweep(root: Path) -> dict[str, list[int]]:
    """Every offender found under *root*, keyed by path relative to *root*.

    Only files inside *root* are ever visited -- a module moved outside the
    root this is called with is invisible to it, which is exactly why the
    root a caller sweeps with is part of the check's contract, not incidental.
    """
    found: dict[str, list[int]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders = _projects_key_offenders(tree)
        if offenders:
            found[str(path.relative_to(root))] = offenders
    return found


_REDERIVING_MODULE = '''
def compose(path):
    return str(path).replace("/", "-").replace(".", "-")
'''

_CLEAN_MODULE = '''
def compose(path):
    return str(path).upper()
'''

_SEAM_CALLING_MODULE = '''
from trailhead.harness.claude_code import ClaudeCodeHarness

def compose(harness, session_id, workspace):
    return harness.session_transcript_path(session_id, workspace)


def sanitize_label(label):
    # An incidental, UNCHAINED single .replace() call -- half the guarded
    # pattern, on purpose, so the checker must be chain-aware rather than
    # firing on either half alone.
    return label.replace(".", "-")
'''


class TestOffenderDetection:
    def test_a_module_that_rederives_the_rule_is_named_as_an_offender(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "offender.py").write_text(_REDERIVING_MODULE, encoding="utf-8")

        result = _sweep(pkg)

        assert list(result) == ["offender.py"]

    def test_a_package_with_no_rederivation_reports_none(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "clean.py").write_text(_CLEAN_MODULE, encoding="utf-8")

        assert _sweep(pkg) == {}

    def test_calling_through_the_seam_is_not_an_offender(self, tmp_path):
        """A fixture that calls the harness method, alongside one that rebuilds
        the path itself, proves the checker tells the two apart rather than
        flagging every mention of the harness by association."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "caller.py").write_text(_SEAM_CALLING_MODULE, encoding="utf-8")
        (pkg / "offender.py").write_text(_REDERIVING_MODULE, encoding="utf-8")

        result = _sweep(pkg)

        assert list(result) == ["offender.py"]


class TestSweepScope:
    def test_the_sweep_is_bounded_to_the_root_it_is_given(self, tmp_path):
        """A module placed outside the swept root is invisible to it -- the
        scope is a real boundary the sweep enforces, not an assumption that
        the right directory happens to get walked."""
        root = tmp_path / "src"
        root.mkdir()
        (root / "inside.py").write_text(_REDERIVING_MODULE, encoding="utf-8")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "moved.py").write_text(_REDERIVING_MODULE, encoding="utf-8")

        result = _sweep(root)

        assert list(result) == ["inside.py"]

    def test_the_camp_tests_directory_is_outside_the_swept_root(self):
        """The camp test fixtures legitimately re-derive the encoding today
        (see the module docstring's rationale); the sweep must not condemn
        them. This proves the actual test directory sits outside the actual
        production root the real sweep below uses, so re-deriving the rule
        there is invisible to the check by construction, not by luck."""
        result = _sweep(_CAMP_PROD_ROOT)

        assert not any(name.startswith("tests" + "/") or name == "tests" for name in result)
        camp_tests_dir = REPO_ROOT / "tools" / "camp" / "tests"
        assert camp_tests_dir.is_dir()
        assert not str(camp_tests_dir).startswith(str(_CAMP_PROD_ROOT) + "/")


class TestProductionTreeIsClean:
    def test_the_camp_production_tree_derives_the_rule_nowhere(self):  # inert-gate: allow real production tree swept whole, no fixture input to vary
        assert _sweep(_CAMP_PROD_ROOT) == {}

    def test_the_harness_module_derives_the_rule_exactly_once(self):  # inert-gate: allow real production file swept whole, no fixture input to vary
        tree = ast.parse(_HARNESS_MODULE.read_text(encoding="utf-8"), filename=str(_HARNESS_MODULE))

        assert len(_projects_key_offenders(tree)) == 1
