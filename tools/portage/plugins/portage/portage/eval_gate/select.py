"""Case selection for ``portage eval-gate plan``.

``plan`` writes ``<state>/plan.json`` (``state`` is a fresh ``mkdtemp`` directory,
mode 0700) and returns ``(state, plan)``. The plan is consumed by the later
``run``/``verdict``/``splice`` subcommands::

    {"schema_version": 1,
     "status": "cases-selected" | "no-cases-mapped" | "base-unresolved" | "mapping-invalid",
     "repo": "<absolute repo path>",
     "base": "<ref as given or resolved>" | null,
     "merge_base": "<sha>" | null,
     "changed_files": ["<repo-relative path>", ...],
     "mapping_invalid": {"file": "<repo-relative mapping path>", "fault": "<one line>"} | null,
     "new_runner_grants": bool,          # any selected case runs under a branch-authored runner block
     "total_max_cost_usd": number,       # sum of the selected cases' runner.max_cost_usd
     "cases": [{"plugin_root": "<repo-relative>", "case": "<name>",
                "runs": int, "pass_at": int,
                "runner": {"model": str, "allow_tools": [str], "max_cost_usd": number},
                "new_runner_grants": bool}, ...]}

Base takes precedence over the branch: cases are selected from the base and head
mappings combined, and a corpus whose mapping exists at the base runs under the
base's runner block, runs and pass_at. A corpus new on the branch uses its own and
is flagged ``new_runner_grants``.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .mapping import Case, Mapping, MappingError, load_mapping

MAPPING_NAME = "eval-gate.json"
SCHEMA_VERSION = 1


class GitError(Exception):
    pass


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise GitError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def _is_mapping(path: str) -> bool:
    p = PurePosixPath(path)
    return p.name == MAPPING_NAME and p.parent.name == "evals"


def _resolve_base(repo: Path, base: str | None) -> tuple[str, str] | None:
    """Return ``(ref, merge_base_sha)`` or None when the base cannot be resolved."""
    try:
        if base is None:
            base = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
        _git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
        return base, _git(repo, "merge-base", base, "HEAD").strip()
    except GitError:
        return None


def _show(repo: Path, rev: str, path: str) -> str | None:
    try:
        return _git(repo, "show", f"{rev}:{path}")
    except GitError:
        return None


def _load_head(repo: Path, path: str, tracked: set[str]) -> Mapping | None:
    if path not in tracked:
        return None
    evals = (repo / path).parent
    return load_mapping((repo / path).read_text(), path, plugin_root=evals.parent, evals_dir=evals)


def _touches(case: Case, plugin: PurePosixPath, changed: list[str]) -> bool:
    case_dir = (plugin / "evals" / case.name).as_posix() + "/"
    for c in changed:
        if c.startswith(case_dir):
            return True
        for m in case.measures:
            target = (plugin / m).as_posix()
            if c == target or c.startswith(target + "/"):
                return True
    return False


def _select(repo: Path, merge_base: str, changed: list[str]) -> list[dict]:
    tracked = set(_git(repo, "ls-files", "-z").split("\0")) - {""}
    at_base = set(_git(repo, "ls-tree", "-r", "-z", "--name-only", merge_base).split("\0")) - {""}
    selected: list[dict] = []
    for path in sorted(p for p in tracked | at_base if _is_mapping(p)):
        base_text = _show(repo, merge_base, path) if path in at_base else None
        base_map = load_mapping(base_text, path) if base_text is not None else None
        head_map = _load_head(repo, path, tracked)
        plugin = PurePosixPath(path).parent.parent
        evals = repo / path
        evals = evals.parent
        names = sorted(set(base_map.cases if base_map else ()) | set(head_map.cases if head_map else ()))
        for name in names:
            if not (evals / name / "case.yaml").is_file():
                continue
            base_case = base_map.cases.get(name) if base_map else None
            head_case = head_map.cases.get(name) if head_map else None
            measures = tuple(dict.fromkeys((base_case.measures if base_case else ()) + (head_case.measures if head_case else ())))
            merged = Case(name, measures, 0, 0)
            if not _touches(merged, plugin, changed):
                continue
            case = base_case or head_case
            runner = (base_map or head_map).runner
            selected.append({
                "plugin_root": plugin.as_posix() if str(plugin) != "." else "",
                "case": name,
                "runs": case.runs,
                "pass_at": case.pass_at,
                "runner": runner,
                "new_runner_grants": base_map is None,
            })
    return selected


def plan(repo: Path, base: str | None) -> tuple[Path, dict]:
    """Compute the plan for ``repo`` against ``base`` and write it to a new state dir.

    Raises ``GitError`` when ``repo`` is not a git work tree."""
    repo = Path(repo).resolve()
    _git(repo, "rev-parse", "--is-inside-work-tree")
    result: dict = {
        "schema_version": SCHEMA_VERSION, "status": "no-cases-mapped", "repo": str(repo),
        "base": base, "merge_base": None, "changed_files": [], "mapping_invalid": None,
        "new_runner_grants": False, "total_max_cost_usd": 0, "cases": [],
    }
    resolved = _resolve_base(repo, base)
    if resolved is None:
        result["status"] = "base-unresolved"
    else:
        result["base"], result["merge_base"] = resolved
        changed = [c for c in _git(repo, "diff", "--name-only", "-z", f"{resolved[1]}...HEAD").split("\0") if c]
        result["changed_files"] = changed
        try:
            cases = _select(repo, resolved[1], changed)
        except MappingError as e:
            result["status"] = "mapping-invalid"
            result["mapping_invalid"] = {"file": e.file, "fault": e.fault}
        else:
            if cases:
                result["status"] = "cases-selected"
                result["cases"] = cases
                result["new_runner_grants"] = any(c["new_runner_grants"] for c in cases)
                result["total_max_cost_usd"] = sum(c["runner"]["max_cost_usd"] for c in cases)
    state = Path(tempfile.mkdtemp(prefix="portage-eval-gate-"))
    (state / "plan.json").write_text(json.dumps(result, indent=2) + "\n")
    return state, result


def unmapped_runner_cases(corpus: Path) -> list[str]:
    """Case directories under ``corpus`` (an ``evals`` dir) holding a ``case.yaml``
    with no entry in its ``eval-gate.json``. Raises ``MappingError`` on a bad mapping."""
    corpus = Path(corpus)
    mapping_file = corpus / MAPPING_NAME
    mapped: set[str] = set()
    if mapping_file.is_file():
        mapped = set(load_mapping(mapping_file.read_text(), str(mapping_file)).cases)
    return sorted(
        d.name for d in corpus.iterdir()
        if d.is_dir() and (d / "case.yaml").is_file() and d.name not in mapped
    )
