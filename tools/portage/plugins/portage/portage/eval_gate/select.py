"""Case selection for ``portage eval-gate plan``.

``plan`` writes ``<state>/plan.json`` (``state`` is a fresh ``mkdtemp`` directory,
mode 0700) and returns ``(state, plan)``. The plan is consumed by the later
``run``/``verdict``/``splice`` subcommands::

    {"schema_version": 1,
     "status": "cases-selected" | "no-cases-mapped" | "base-unresolved" | "mapping-invalid"
               | "tree-dirty",
     "repo": "<absolute repo path>",
     "base": "<ref as given or resolved>" | null,
     "merge_base": "<sha>" | null,
     "changed_files": ["<repo-relative path>", ...],
     "mapping_invalid": {"file": "<repo-relative mapping path>", "fault": "<one line>",
                         "revision": "base" | "head"} | null,
                          # revision: which side's mapping is faulty; "base" faults also say so in the text
     "new_runner_grants": bool,          # any selected case runs under a branch-authored runner block
     "tier": "push" | "merge",
     "head": "<sha of HEAD at plan time>",
     "merge_only_pending": int,          # touched merge_only cases this tier leaves out (push) or 0 (merge)
     "total_max_cost_usd": number,       # sum of the runner.max_cost_usd of the selected cases that will run
     "cases": [{"plugin_root": "<repo-relative>", "case": "<name>",
                "runs": int, "pass_at": int,
                "runner": {"model": str, "allow_tools": [str], "max_cost_usd": number},
                "measures": ["<path relative to plugin_root>", ...],   # base's and head's, merged
                "merge_only": bool,
                "new_runner_grants": bool,
                "case_removed": bool}, ...]}   # true: selected, but its case.yaml is gone at head

The push tier leaves out the cases mapped ``merge_only``; the merge tier selects every touched
case. ``select_touched`` returns every touched case, ``merge_only`` included; ``plan`` filters
by tier. Both mappings and the case files are read from commits (the merge-base and HEAD), never the
working tree. On the push tier the base takes precedence for ``merge_only``; at the merge tier (and in
``merge_only_touched``) a case is ``merge_only`` when the base's or HEAD's mapping marks it, so a stale
base cannot hide a flag. Base takes precedence over the branch for the rest: cases are selected from the base and head
mappings combined, and a corpus whose mapping exists at the base runs under the
base's runner block, runs and pass_at. A corpus new on the branch uses its own and
is flagged ``new_runner_grants``; ``run`` records each of its cases as not-run
``corpus-new-on-branch`` without starting a runner (the grants are unreviewed), and they add
nothing to ``total_max_cost_usd``. A selected case whose ``case.yaml`` is gone at head
stays in the plan with ``case_removed`` true; ``run`` records it as not-run
``case-removed-on-branch`` without starting a runner, and it adds nothing to
``total_max_cost_usd``. Every corpus path must pass ``mapping.valid_corpus_path``, else the
plan is ``mapping-invalid``. A tracked file modified in the working tree
(``git status --porcelain --untracked-files=no`` non-empty) makes the status ``tree-dirty``: the
gate grades committed prose, so it selects nothing. Changed files come from ``git diff --no-renames``, so a renamed
file counts as both its old and its new path.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .mapping import Mapping, MappingError, load_mapping, valid_corpus_path

MAPPING_NAME = "eval-gate.json"
SCHEMA_VERSION = 1


class GitError(Exception):
    pass


class NotAWorkTree(GitError):
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
    except UnicodeDecodeError:
        raise MappingError(path, "not valid UTF-8") from None


_BLOB_MODES = ("100644", "100755")


def _head_tree(repo: Path) -> dict[str, str]:
    """HEAD's committed tree as ``{path: mode}``. The working tree is never consulted."""
    tree: dict[str, str] = {}
    for entry in _git(repo, "ls-tree", "-r", "-z", "HEAD").split("\0"):
        meta, sep, path = entry.partition("\t")
        if sep:
            tree[path] = meta.split(" ", 1)[0]
    return tree


def _check_against_tree(path: str, mapping: Mapping, tree: dict[str, str]) -> None:
    """The on-disk checks ``load_mapping`` can make, made against HEAD's committed tree."""
    plugin = PurePosixPath(path).parent.parent
    for name, case in mapping.cases.items():
        for m in case.measures:
            target = (plugin / m).as_posix()
            prefix = target + "/"
            held = tree.get(target) in _BLOB_MODES or any(p.startswith(prefix) for p in tree)
            if not held:
                raise MappingError(path, f"case {name!r}: measures path {m!r} does not exist "
                                         "under the plugin root")
        if tree.get((plugin / "evals" / name / "case.yaml").as_posix()) not in _BLOB_MODES:
            raise MappingError(path, f"case {name!r} has no case.yaml")


def _load_head(repo: Path, path: str, tree: dict[str, str]) -> Mapping | None:
    if path not in tree:
        return None
    try:
        text = _git(repo, "show", f"HEAD:{path}")
    except UnicodeDecodeError:
        raise MappingError(path, "not valid UTF-8") from None
    mapping = load_mapping(text, path)
    _check_against_tree(path, mapping, tree)
    return mapping


def _load_base(path: str, text: str) -> Mapping:
    try:
        return load_mapping(text, path)
    except MappingError as e:
        err = MappingError(path, f"the base revision's mapping is invalid: {e.fault}")
        err.revision = "base"
        raise err from None


def _touches(name: str, measures: tuple[str, ...], plugin: PurePosixPath, changed: list[str]) -> bool:
    case_dir = (plugin / "evals" / name).as_posix() + "/"
    for c in changed:
        if c.startswith(case_dir):
            return True
        for m in measures:
            target = (plugin / m).as_posix()
            if c == target or c.startswith(target + "/"):
                return True
    return False


def select_touched(repo: Path, merge_base: str, changed: list[str], *,
                   merge_only_either: bool = False) -> list[dict]:
    """Every case the ``changed`` files touch, whatever its tier, as plan entries.

    Everything is read from commits: the merge-base's and HEAD's mappings and HEAD's tree. An entry's
    ``merge_only`` is the base's value (the base takes precedence); with ``merge_only_either`` it is
    true when the base's or HEAD's mapping marks the case."""
    tree = _head_tree(repo)
    tracked = set(tree)
    at_base = set(_git(repo, "ls-tree", "-r", "-z", "--name-only", merge_base).split("\0")) - {""}
    selected: list[dict] = []
    for path in sorted(p for p in tracked | at_base if _is_mapping(p)):
        plugin = PurePosixPath(path).parent.parent
        root = "" if str(plugin) == "." else plugin.as_posix()
        if not valid_corpus_path(root):
            raise MappingError(path, "the corpus path has characters outside A-Za-z0-9._/- or a '..' segment")
        base_text = _show(repo, merge_base, path) if path in at_base else None
        base_map = _load_base(path, base_text) if base_text is not None else None
        head_map = _load_head(repo, path, tree)
        names = sorted(set(base_map.cases if base_map else ()) | set(head_map.cases if head_map else ()))
        for name in names:
            base_case = base_map.cases.get(name) if base_map else None
            head_case = head_map.cases.get(name) if head_map else None
            measures = tuple(dict.fromkeys(
                (base_case.measures if base_case else ()) + (head_case.measures if head_case else ())
            ))
            if not _touches(name, measures, plugin, changed):
                continue
            case = base_case or head_case
            marked = case.merge_only or bool(merge_only_either and head_case and head_case.merge_only)
            runner = (base_map or head_map).runner
            selected.append({
                "plugin_root": root,
                "case": name,
                "runs": case.runs,
                "pass_at": case.pass_at,
                "runner": runner,
                "measures": list(measures),
                "merge_only": marked,
                "new_runner_grants": base_map is None,
                "case_removed": tree.get((plugin / "evals" / name / "case.yaml").as_posix())
                                not in _BLOB_MODES,
            })
    return selected


def _runs(case: dict) -> bool:
    return not case["case_removed"] and not case["new_runner_grants"]


TIERS = ("push", "merge")


def _changed_since(repo: Path, merge_base: str) -> list[str]:
    diff_out = _git(repo, "diff", "--name-only", "--no-renames", "-z", f"{merge_base}...HEAD")
    return [c for c in diff_out.split("\0") if c]


def _require_work_tree(repo: Path) -> None:
    """Raise ``NotAWorkTree`` only on git's explicit "not a git repository"; any other failure
    to answer is a ``GitError``."""
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
                       capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"})
    err = r.stderr.strip()
    if r.returncode != 0:
        if "not a git repository" in err:
            raise NotAWorkTree(err)
        raise GitError(err or "git rev-parse --is-inside-work-tree failed")
    if r.stdout.strip() != "true":
        raise GitError("git does not report an enclosing work tree")


def merge_only_touched(repo: Path, base: str | None = None) -> tuple[str, list[dict]]:
    """The ``merge_only`` cases a change to ``repo`` touches, as ``(status, entries)``.

    The merge backstop asks this to decide whether it applies, using the same selection as
    ``plan``. ``status`` is ``ok`` (``entries`` are exactly the touched ``merge_only`` cases),
    ``base-unresolved`` (the changed files are unknown, so ``entries`` is every ``merge_only`` case
    the repo's mappings hold: a superset, never a miss) or ``mapping-invalid`` (``entries`` is empty
    and the answer is unknown). A case is ``merge_only`` when the merge-base's mapping or HEAD's
    mapping marks it, so a stale base cannot hide a flag. Only commits are read: the mappings and
    the case files come from the merge-base and HEAD, never the working tree, so an uncommitted
    edit changes nothing. Raises ``NotAWorkTree`` when ``repo`` is not a git work tree
    and ``GitError`` on any other git failure."""
    repo = Path(repo).resolve()
    _require_work_tree(repo)
    resolved = _resolve_base(repo, base)
    try:
        if resolved is None:
            touched = select_touched(repo, "HEAD", list(_head_tree(repo)), merge_only_either=True)
        else:
            touched = select_touched(repo, resolved[1], _changed_since(repo, resolved[1]),
                                     merge_only_either=True)
    except MappingError:
        return "mapping-invalid", []
    return ("base-unresolved" if resolved is None else "ok"), [c for c in touched if c["merge_only"]]


def repo_has_mapping(repo: Path, base: str | None = None) -> bool:
    """Whether ``repo`` holds an ``eval-gate.json`` mapping at HEAD or at the merge-base, whatever
    it contains. Raises ``GitError``."""
    repo = Path(repo).resolve()
    if any(_is_mapping(p) for p in _head_tree(repo)):
        return True
    resolved = _resolve_base(repo, base)
    if resolved is None:
        return False
    at_base = _git(repo, "ls-tree", "-r", "-z", "--name-only", resolved[1]).split("\0")
    return any(_is_mapping(p) for p in at_base)


def local_head(repo: Path) -> str:
    """The full commit id of ``repo``'s ``HEAD``. Raises ``GitError``."""
    return _git(Path(repo).resolve(), "rev-parse", "HEAD").strip()


def plan(repo: Path, base: str | None, tier: str = "push") -> tuple[Path, dict]:
    """Compute the plan for ``repo`` against ``base`` and write it to a new state dir.

    ``tier`` is ``push`` (leaves out ``merge_only`` cases) or ``merge`` (every touched case).

    Raises ``GitError`` when ``repo`` is not a git work tree."""
    repo = Path(repo).resolve()
    _git(repo, "rev-parse", "--is-inside-work-tree")
    result: dict = {
        "schema_version": SCHEMA_VERSION, "status": "no-cases-mapped", "repo": str(repo),
        "base": base, "merge_base": None, "tier": tier,
        "head": _git(repo, "rev-parse", "HEAD").strip(), "merge_only_pending": 0,
        "changed_files": [], "mapping_invalid": None,
        "new_runner_grants": False, "total_max_cost_usd": 0, "cases": [],
    }
    resolved = _resolve_base(repo, base)
    if _git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        result["status"] = "tree-dirty"
    elif resolved is None:
        result["status"] = "base-unresolved"
    else:
        result["base"], result["merge_base"] = resolved
        changed = _changed_since(repo, resolved[1])
        result["changed_files"] = changed
        try:
            touched = select_touched(repo, resolved[1], changed, merge_only_either=tier == "merge")
        except MappingError as e:
            result["status"] = "mapping-invalid"
            result["mapping_invalid"] = {"file": e.file, "fault": e.fault, "revision": e.revision}
        else:
            cases = [c for c in touched if tier == "merge" or not c["merge_only"]]
            if tier == "push":
                result["merge_only_pending"] = len(touched) - len(cases)
            if cases:
                result["status"] = "cases-selected"
                result["cases"] = cases
                result["new_runner_grants"] = any(c["new_runner_grants"] for c in cases)
                result["total_max_cost_usd"] = sum(c["runner"]["max_cost_usd"] for c in cases if _runs(c))
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
