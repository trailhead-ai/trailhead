"""The shipped eval corpora and their ``eval-gate.json`` mappings.

Every tool corpus that holds a runner case (a ``case.yaml``) must load clean through
the real ``portage.eval_gate`` loader, map every one of its runner cases, and select
exactly the cases that measure a prose file when that file changes. The expected
selection is derived from each case's ``expected.md`` "Under test" section, never from
the mapping under test.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.cli import dispatch
from portage.eval_gate import unmapped_runner_cases
from portage.eval_gate.mapping import load_mapping
from portage.eval_gate.run import MAX_TIMEOUT_SECONDS
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


def _merge_only(corpus: Path) -> set[str]:
    mapping_file = corpus / "eval-gate.json"
    mapping = load_mapping(mapping_file.read_text(), str(mapping_file))
    return {n for n, c in mapping.cases.items() if c.merge_only}


def _selected(repo: Path, capsys, *tier: str) -> tuple[str, list[str]]:
    capsys.readouterr()
    dispatch.main(["eval-gate", "plan", "--repo", str(repo), "--base", "main", *tier])
    out = capsys.readouterr().out
    status = next(ln for ln in out.splitlines() if ln.startswith("status: "))[len("status: "):]
    cases = sorted(
        ln for ln in out.splitlines()
        if re.match(r"^\S+:[a-z0-9][a-z0-9._-]*$", ln) and not ln.startswith(("state:", "status:", "cases:"))
    )
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
        merge_only = _merge_only(corpus)
        for prose, cases in sorted(measured.items()):
            _edit(repo, f"{rel.as_posix()}/{prose}")
            status, got = _selected(repo, capsys)
            want = sorted(f"{rel.as_posix()}:{c}" for c in cases - merge_only)
            assert (status, got) == (("cases-selected" if want else "no-cases-mapped"), want), prose
            status, got = _selected(repo, capsys, "--tier", "merge")
            every = sorted(f"{rel.as_posix()}:{c}" for c in cases)
            assert (status, got) == ("cases-selected", every), prose

    def test_marking_a_named_case_merge_only_leaves_it_out_of_the_push_tier_only(self, throwaway, tmp_path, capsys):
        _, rel, corpus = throwaway
        measured = _measured_by_expected(corpus)
        merge_only = _merge_only(corpus)
        prose, cases = next((f, c) for f, c in sorted(measured.items()) if c - merge_only)
        marked = sorted(cases - merge_only)[0]
        repo = tmp_path / "marked"
        shutil.copytree(_plugin_root(corpus), repo / rel,
                        ignore=shutil.ignore_patterns("__pycache__", "results"))
        mapping_file = repo / rel / "evals" / "eval-gate.json"
        doc = json.loads(mapping_file.read_text())
        doc["cases"][marked]["merge_only"] = True
        mapping_file.write_text(json.dumps(doc))
        _git(repo, "init", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "base")
        _edit(repo, f"{rel.as_posix()}/{prose}")
        want_push = sorted(f"{rel.as_posix()}:{c}" for c in cases - merge_only - {marked})
        status, got = _selected(repo, capsys)
        assert got == want_push
        _, merged = _selected(repo, capsys, "--tier", "merge")
        assert merged == sorted(f"{rel.as_posix()}:{c}" for c in cases)

    def test_editing_prose_no_case_names_selects_nothing(self, throwaway, capsys):
        repo, rel, corpus = throwaway
        measured = _measured_by_expected(corpus)
        unnamed = next(f for f in _prose_files(_plugin_root(corpus)) if f not in measured)
        _edit(repo, f"{rel.as_posix()}/{unnamed}")
        status, got = _selected(repo, capsys)
        assert (status, got) == ("no-cases-mapped", [])


def _mapping(corpus: Path):
    mapping_file = corpus / "eval-gate.json"
    return load_mapping(
        mapping_file.read_text(), str(mapping_file),
        plugin_root=_plugin_root(corpus), evals_dir=corpus,
    )


def _files_with_no_push_case(mapping) -> list[str]:
    """Measured prose files whose every measuring case is merge_only: a push editing one runs nothing."""
    measured: dict[str, list[bool]] = {}
    for case in mapping.cases.values():
        for f in case.measures:
            measured.setdefault(f, []).append(case.merge_only)
    return sorted(f for f, flags in measured.items() if all(flags))


@pytest.mark.parametrize("corpus", CORPORA, ids=IDS)
class TestCoverageInvariant:
    def test_every_measured_prose_file_keeps_a_case_the_push_tier_runs(self, corpus):
        assert _files_with_no_push_case(_mapping(corpus)) == []

    def test_a_file_whose_every_case_is_marked_is_reported(self, corpus, tmp_path):
        mapping = _mapping(corpus)
        prose = sorted({f for c in mapping.cases.values() for f in c.measures})[0]
        evals = tmp_path / "evals"
        shutil.copytree(corpus, evals, ignore=shutil.ignore_patterns("__pycache__", "results"))
        doc = json.loads((evals / "eval-gate.json").read_text())
        for case in doc["cases"].values():
            if prose in case["measures"]:
                case["merge_only"] = True
        (evals / "eval-gate.json").write_text(json.dumps(doc))
        marked = load_mapping((evals / "eval-gate.json").read_text(), "eval-gate.json")
        assert prose in _files_with_no_push_case(marked)


DEFAULT_TIMEOUT_SECONDS = 300


def _declaration(path: Path, text: str, strict: str) -> int | None:
    """The value of the one ``timeout_seconds`` line in ``text`` that matches ``strict``, ``None`` when
    the text mentions none. A line that mentions the key without matching ``strict`` is a fault, never
    "undeclared": the parser would read the case as the 300 s default and pass an over-budget case."""
    found = None
    for line in text.splitlines():
        if "timeout_seconds" not in line:
            continue
        m = re.fullmatch(strict, line)
        if not m:
            raise ValueError(f"{path}: unrecognised timeout_seconds declaration {line!r}")
        found = int(m.group(1)) if found is None else found
    return found


def _declared_timeout(case_dir: Path) -> int:
    """A case's declared ``timeout_seconds``: the ``prompt.md`` frontmatter wins over ``case.yaml``
    ``execution:``, and 300 applies when neither declares one (docs/eval-protocol.md). A declaration in
    any shape this parser does not read (quoted, commented, top level in ``case.yaml``, frontmatter not
    at the first byte) raises ``ValueError`` naming the file."""
    prompt_path, yaml_path = case_dir / "prompt.md", case_dir / "case.yaml"
    prompt = prompt_path.read_text()
    front = re.match(r"---\n(.*?)\n---\n", prompt, re.S)
    if not front:
        stray = re.match(r"[\ufeff\s]*---\n(.*?)\n---", prompt, re.S)
        if stray and "timeout_seconds" in stray.group(1):
            raise ValueError(f"{prompt_path}: frontmatter declaring timeout_seconds must start at the "
                             "first byte")
    declared = _declaration(prompt_path, front.group(1) if front else "", r"timeout_seconds:\s*(\d+)\s*")
    if declared is None:
        declared = _declaration(yaml_path, yaml_path.read_text(), r"\s+timeout_seconds:\s*(\d+)\s*")
    return DEFAULT_TIMEOUT_SECONDS if declared is None else declared


def _over_budget_merge_only(corpus: Path) -> list[str]:
    return sorted(n for n in _merge_only(corpus) if _declared_timeout(corpus / n) > MAX_TIMEOUT_SECONDS)


class TestDeclaredTimeout:
    def _case(self, tmp_path, *, front="", execution=""):
        (tmp_path / "case.yaml").write_text(f'schema_version: "1.1"\nname: x\n{execution}')
        (tmp_path / "prompt.md").write_text(f"---\n{front}---\nbody\n" if front else "body\n")
        return tmp_path

    def test_the_prompt_frontmatter_wins_over_case_yaml(self, tmp_path):
        case = self._case(tmp_path, front="timeout_seconds: 900\n",
                          execution="execution:\n  timeout_seconds: 100\n")
        assert _declared_timeout(case) == 900

    def test_case_yaml_execution_applies_without_frontmatter(self, tmp_path):
        assert _declared_timeout(self._case(tmp_path, execution="execution:\n  timeout_seconds: 450\n")) == 450

    def test_an_undeclared_timeout_is_the_documented_default(self, tmp_path):
        assert _declared_timeout(self._case(tmp_path)) == 300

    @pytest.mark.parametrize("shape,files", [
        ("quoted-in-frontmatter", {"prompt.md": '---\ntimeout_seconds: "900"\n---\nbody\n'}),
        ("commented-in-frontmatter", {"prompt.md": "---\n# timeout_seconds: 900\n---\nbody\n"}),
        ("trailing-comment-in-frontmatter", {"prompt.md": "---\ntimeout_seconds: 900 # slow\n---\nbody\n"}),
        ("frontmatter-after-a-blank-line", {"prompt.md": "\n---\ntimeout_seconds: 900\n---\nbody\n"}),
        ("frontmatter-after-a-bom", {"prompt.md": "\ufeff---\ntimeout_seconds: 900\n---\nbody\n"}),
        ("top-level-in-case-yaml", {"case.yaml": 'schema_version: "1.1"\nname: x\ntimeout_seconds: 900\n'}),
        ("quoted-in-case-yaml",
         {"case.yaml": 'schema_version: "1.1"\nname: x\nexecution:\n  timeout_seconds: "900"\n'}),
        ("commented-in-case-yaml",
         {"case.yaml": 'schema_version: "1.1"\nname: x\nexecution:\n  # timeout_seconds: 900\n'}),
    ])
    def test_a_declaration_the_parser_does_not_recognise_fails_naming_the_file(
        self, tmp_path, shape, files
    ):
        (tmp_path / "case.yaml").write_text('schema_version: "1.1"\nname: x\n')
        (tmp_path / "prompt.md").write_text("body\n")
        for name, text in files.items():
            (tmp_path / name).write_text(text)
        broken = next(iter(files))
        with pytest.raises(ValueError, match=re.escape(str(tmp_path / broken))):
            _declared_timeout(tmp_path)

    def test_a_horizontal_rule_in_a_prompt_body_is_not_a_declaration(self, tmp_path):
        (tmp_path / "case.yaml").write_text('schema_version: "1.1"\nname: x\n')
        (tmp_path / "prompt.md").write_text("\n---\nnot frontmatter\n---\nbody\n")
        assert _declared_timeout(tmp_path) == 300


@pytest.mark.parametrize("corpus", CORPORA, ids=IDS)
class TestMergeOnlyFitsTheGateBudget:
    def test_every_merge_only_case_declares_a_timeout_within_the_gate_budget(self, corpus):
        assert _over_budget_merge_only(corpus) == []

    def test_a_merge_only_case_over_the_budget_is_reported(self, corpus, tmp_path):
        evals = tmp_path / "evals"
        shutil.copytree(corpus, evals, ignore=shutil.ignore_patterns("__pycache__", "results"))
        case = sorted(_runner_cases(evals))[0]
        (evals / case / "prompt.md").write_text(
            f"---\ntimeout_seconds: {MAX_TIMEOUT_SECONDS + 1}\n---\nbody\n")
        doc = json.loads((evals / "eval-gate.json").read_text())
        doc["cases"][case]["merge_only"] = True
        (evals / "eval-gate.json").write_text(json.dumps(doc))
        assert case in _over_budget_merge_only(evals)

    def test_a_timeout_exactly_at_the_budget_fits(self, corpus, tmp_path):
        evals = tmp_path / "evals"
        shutil.copytree(corpus, evals, ignore=shutil.ignore_patterns("__pycache__", "results"))
        case = sorted(_runner_cases(evals))[0]
        (evals / case / "prompt.md").write_text(f"---\ntimeout_seconds: {MAX_TIMEOUT_SECONDS}\n---\nbody\n")
        doc = json.loads((evals / "eval-gate.json").read_text())
        doc["cases"][case]["merge_only"] = True
        (evals / "eval-gate.json").write_text(json.dumps(doc))
        assert case not in _over_budget_merge_only(evals)


class TestRealCoreSelection:
    def test_craft_execute_md_on_push_selects_exactly_its_non_merge_only_cases(self, tmp_path_factory, capsys):
        corpus = next(c for c in CORPORA if c.parent.parent.parent.name == "craft")
        root = _plugin_root(corpus)
        rel = root.relative_to(REPO_ROOT)
        prose = "skills/_shared/execute.md"
        mapping = _mapping(corpus)
        measuring = {n for n, c in mapping.cases.items() if prose in c.measures}
        core = {n for n in measuring if not mapping.cases[n].merge_only}
        assert core and core < measuring, "execute.md must have both core and merge-only cases"
        repo = tmp_path_factory.mktemp("craft-real")
        shutil.copytree(root, repo / rel, ignore=shutil.ignore_patterns("__pycache__", "results"))
        _git(repo, "init", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "base")
        _edit(repo, f"{rel.as_posix()}/{prose}")
        status, got = _selected(repo, capsys)
        assert (status, got) == ("cases-selected", sorted(f"{rel.as_posix()}:{c}" for c in core))
        _, merged = _selected(repo, capsys, "--tier", "merge")
        assert merged == sorted(f"{rel.as_posix()}:{c}" for c in measuring)
