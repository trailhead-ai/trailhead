"""The shipped eval corpora and their ``eval-gate.json`` mappings.

Every tool corpus that holds a runner case (a ``case.yaml``) must load clean through
the real ``portage.eval_gate`` loader, map every one of its runner cases, and select
exactly the cases that measure a prose file when that file changes. The expected
selection is derived from each case's ``expected.md`` "Under test" section, never from
the mapping under test.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.cli import dispatch
from portage.eval_gate import unmapped_runner_cases
from portage.eval_gate.mapping import load_mapping
from test_portage_eval_gate_plan import _git

REPO_ROOT = Path(__file__).resolve().parents[3]

# Prose a case's "Under test" section can name without a path: the skill is known by
# what it is about. Anything else is named by a backticked path.
KEYWORD_PROSE = {
    "skills/correct/SKILL.md": re.compile(r"\bcorrect", re.I),
    "skills/record/SKILL.md": re.compile(r"\bcapturing\b|deferred work", re.I),
}


def _corpora() -> list[Path]:
    return sorted(
        p for p in (REPO_ROOT / "tools").glob("*/plugins/*/evals")
        if any(p.glob("*/case.yaml"))
    )


CORPORA = _corpora()
IDS = [p.parent.parent.parent.name for p in CORPORA]


def _plugin_root(corpus: Path) -> Path:
    return corpus.parent


def _runner_cases(corpus: Path) -> list[str]:
    return sorted(c.parent.name for c in corpus.glob("*/case.yaml"))


def _under_test(case_dir: Path) -> str:
    text = (case_dir / "expected.md").read_text()
    m = re.search(r"^## Under test\n(.*?)(?=^## )", text, re.S | re.M)
    assert m, f"{case_dir.name}: expected.md has no '## Under test' section"
    return m.group(1)


def _prose_files(plugin_root: Path) -> list[str]:
    return sorted(
        p.relative_to(plugin_root).as_posix()
        for sub in ("skills", "agents")
        for p in (plugin_root / sub).rglob("*.md")
    )


def _measured_by_expected(corpus: Path) -> dict[str, set[str]]:
    """prose file (plugin-relative) -> the cases whose expected.md names it as under test."""
    root = _plugin_root(corpus)
    files = _prose_files(root)
    by_file: dict[str, set[str]] = {}
    for case in _runner_cases(corpus):
        section = _under_test(corpus / case)
        tokens = {re.sub(r":\d[\d,-]*$", "", t) for t in re.findall(r"`([^`\s]+)`", section)}
        named = {
            f for f in files
            if any(t.endswith(f) or f"skills/{t}" == f for t in tokens)
        }
        named |= {f for f, rx in KEYWORD_PROSE.items() if f in files and rx.search(section)}
        for f in named:
            by_file.setdefault(f, set()).add(case)
    return by_file


@pytest.mark.parametrize("corpus", CORPORA, ids=IDS)
class TestMappingLoads:
    def test_loads_clean_against_the_real_tree_and_maps_every_runner_case(self, corpus):
        mapping_file = corpus / "eval-gate.json"
        assert mapping_file.is_file(), f"{mapping_file} is missing"
        mapping = load_mapping(
            mapping_file.read_text(), str(mapping_file),
            plugin_root=_plugin_root(corpus), evals_dir=corpus,
        )
        assert unmapped_runner_cases(corpus) == []
        assert sorted(mapping.cases) == _runner_cases(corpus)


@pytest.fixture(scope="module", params=CORPORA, ids=IDS)
def throwaway(request, tmp_path_factory):
    """A throwaway git repo holding the real plugin tree at its real relative path."""
    corpus: Path = request.param
    root = _plugin_root(corpus)
    rel = root.relative_to(REPO_ROOT)
    repo = tmp_path_factory.mktemp("repo")
    shutil.copytree(root, repo / rel, ignore=shutil.ignore_patterns("__pycache__", "results"))
    _git(repo, "init", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return repo, rel, corpus


def _selected(repo: Path, capsys) -> tuple[str, list[str]]:
    capsys.readouterr()
    dispatch.main(["eval-gate", "plan", "--repo", str(repo), "--base", "main"])
    out = capsys.readouterr().out
    status = next(l for l in out.splitlines() if l.startswith("status: "))[len("status: "):]
    cases = sorted(l for l in out.splitlines() if re.match(r"^\S+:[a-z0-9][a-z0-9._-]*$", l) and not l.startswith(("state:", "status:", "cases:")))
    return status, cases


def _edit(repo: Path, rel_file: str) -> None:
    _git(repo, "checkout", "-q", "-B", "feature", "main")
    p = repo / rel_file
    p.write_text(p.read_text() + "\nA changed line.\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "edit")


class TestRealEdit:
    def test_editing_a_measured_prose_file_selects_exactly_the_cases_that_name_it(self, throwaway, capsys):
        repo, rel, corpus = throwaway
        measured = _measured_by_expected(corpus)
        assert measured, "no case's expected.md names a prose file under test"
        for prose, cases in sorted(measured.items()):
            _edit(repo, f"{rel.as_posix()}/{prose}")
            status, got = _selected(repo, capsys)
            want = sorted(f"{rel.as_posix()}:{c}" for c in cases)
            assert (status, got) == ("cases-selected", want), prose

    def test_editing_prose_no_case_names_selects_nothing(self, throwaway, capsys):
        repo, rel, corpus = throwaway
        measured = _measured_by_expected(corpus)
        unnamed = next(f for f in _prose_files(_plugin_root(corpus)) if f not in measured)
        _edit(repo, f"{rel.as_posix()}/{unnamed}")
        status, got = _selected(repo, capsys)
        assert (status, got) == ("no-cases-mapped", [])
