"""Case selection for ``portage eval-gate plan``.

``plan`` writes ``<state>/plan.json`` (``state`` is a fresh ``mkdtemp`` directory,
mode 0700) and returns ``(state, plan)``. The plan is consumed by the later
``run``/``verdict``/``splice`` subcommands::

    {"schema_version": 1,
     "status": "cases-selected" | "no-cases-mapped" | "base-unresolved" | "base-invalid"
               | "mapping-invalid" | "tree-dirty" | "gate-off",
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
     "needs_merge_model": [{"plugin_root": str, "case": str, "reason": str}, ...],
                          # every case the base mappings mark, touched or not, sorted; [] until the mappings are read
     "mark_changes": [{"plugin_root": str, "case": str, "change": "added" | "altered" | "removed",
                       "reason": str | null}, ...],   # base vs head; reason is head's, null on removal
     "cases": [{"plugin_root": "<repo-relative>", "case": "<name>",
                "runs": int, "pass_at": int,
                "runner": {"model": str, "allow_tools": [str], "max_cost_usd": number},
                                      # model: resolve_runner's choice for the tier
                "needs_merge_model": "<base's reason>" | null,
                "push_model": str | null, "merge_model": str,   # the base block's two ids (HEAD's if new)
                "measures": ["<path relative to plugin_root>", ...],   # base's and head's, merged; a declared
                                      # case's hand-written paths plus the files derived from its entry
                "split_procedure": bool,   # the case's entry loads a branch file, at the base or at head
                "derived": ["<path relative to plugin_root>", ...],   # only a declared case (one with an
                                      # entry): the files derived at the base and at head, each once
                "merge_only": bool,
                "new_runner_grants": bool,
                "case_removed": bool}, ...]}   # true: selected, but its case.yaml is gone at head

The push tier leaves out the cases mapped ``merge_only``; the merge tier selects every touched
case. ``select_touched`` returns every touched case, ``merge_only`` included; ``plan`` filters
by tier. Both mappings and the case files are read from commits (the merge-base and HEAD), never the
working tree. On the push tier the base takes precedence for ``merge_only``; at the merge tier (and in
``backstop_touched``) a case is ``merge_only`` when the base's or HEAD's mapping marks it, so a stale
base cannot hide a flag. A case that HEAD still declares, whose entry is a split procedure core at either
revision and whose HEAD case does not say ``merge_only``, is not ``merge_only`` at any tier, even when the
base marks it. Base takes precedence over the branch for the rest: cases are selected from the base and head
mappings combined, and a corpus whose mapping exists at the base runs under the
base's runner block, runs and pass_at. The base also supplies the mark and both model ids: a mark added on the branch
takes effect once merged. A corpus new on the branch uses its own and
is flagged ``new_runner_grants``; ``run`` records each of its cases as not-run
``corpus-new-on-branch`` without starting a runner (the grants are unreviewed), and they add
nothing to ``total_max_cost_usd``. A selected case whose ``case.yaml`` is gone at head
stays in the plan with ``case_removed`` true; ``run`` records it as not-run
``case-removed-on-branch`` without starting a runner, and it adds nothing to
``total_max_cost_usd``. Every corpus path must pass ``mapping.valid_corpus_path``, else the
plan is ``mapping-invalid``. So is a plan where, with every file read from the commits through one cached
``git cat-file --batch`` reader per revision and plugin root (a dying or short reader is a fault, never an
empty file): a declared case's entry or branch is missing, or the entry never loads a declared branch (a base
fault says so and is ``revision`` ``base``); a mapped corpus's procedures (``skills/**/*.md``, ``agents/*.md``)
at HEAD load a file that does not exist; a hand-written ``measures`` path at HEAD is or holds a split core (a
procedure that loads a branch file) or a branch file; two corpora hold a case of the same name at one revision;
a case moves from one corpus at the base to another at HEAD; or HEAD's case is ``merge_only`` with no
``needs_merge_model`` mark while its entry is a split procedure core at either revision. A tracked file modified in
the working tree (``git status --porcelain --untracked-files=no`` non-empty) makes the status ``tree-dirty``:
the gate grades committed prose, so it selects nothing. Changed files come from ``git diff --no-renames``, so a
renamed file counts as both its old and its new path.

Bounds. A revision (the merge-base, HEAD) holds at most ``MAX_MAPPING_FILES`` mapping files, charged on
the count of ``evals/eval-gate.json`` paths in its tree before any is read; a mapping holds at most
``MAX_CASES_PER_MAPPING`` cases, charged by the loader before any case is validated; and the reader of a revision
fetches at most ``MAX_PROCEDURE_FILES`` distinct files, counted across every plugin root and every case, and
charged when a file is first fetched from git (a path the tree lacks, a symlink and a file read again from the
cache cost nothing). The scans of a mapped corpus's procedures at HEAD read through that same reader, so their
files count. Going over any cap is ``mapping-invalid``, with a fault of one shape: ``<count> <things> at
<HEAD | the base revision> exceed the cap of <cap>`` (``<count> cases in '<mapping>' exceed ...`` for the case
cap, ``case '<name>': <count> procedure files read at ...`` for the file cap, whose count is the file the cap
stopped at); a fault at the base says so and is ``revision`` ``base``. A base that starts with ``-`` is
``base-invalid``, decided before any git process starts; every git call that takes a revision passes it after
``--end-of-options``. ``cat-file --batch`` takes its revisions on stdin, always the merge-base's commit id or
``HEAD``, never the base as given. ``plan`` takes a ``Git`` so a caller can count or fail the processes it starts.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .loads import LoadError, NotRegularFile, cached, derive, is_branch_file, missing_loads, reaches_branch
from .mapping import (MAX_BLOB_BYTES, MAX_MAPPING_FILES, MAX_PROCEDURE_FILES, Mapping, MappingError, _q,
                      is_procedure_path, load_mapping, valid_corpus_path)

MAPPING_NAME = "eval-gate.json"
SCHEMA_VERSION = 1


class GitError(Exception):
    pass


class NotAWorkTree(GitError):
    pass


class UndecodableOutput(GitError):
    """Git answered with bytes that are not UTF-8: a path or a blob."""


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if r.returncode != 0:
        raise GitError(r.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(args)} failed")
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise UndecodableOutput(f"git {args[0]} printed output that is not valid UTF-8 "
                                "(a committed path or file)") from None


class Git:
    """Every git process the selection starts. ``plan`` takes one, so a test can count the processes or
    make one fail; the default runs real git."""

    def run(self, repo: Path, *args: str) -> str:
        return _git(repo, *args)

    def open_cat_file(self, repo: Path) -> subprocess.Popen:
        return _open_cat_file(repo)


GIT = Git()


def _option_like(base: str | None) -> bool:
    return base is not None and base.startswith("-")


def _is_mapping(path: str) -> bool:
    p = PurePosixPath(path)
    return p.name == MAPPING_NAME and p.parent.name == "evals"


def _resolve_base(repo: Path, base: str | None, git: Git = GIT) -> tuple[str, str] | None:
    """Return ``(ref, merge_base_sha)`` or None when the base cannot be resolved. A base that starts with
    ``-`` never resolves: no git process starts for it."""
    if _option_like(base):
        return None
    try:
        if base is None:
            base = git.run(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
        git.run(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{base}^{{commit}}")
        return base, git.run(repo, "merge-base", "--end-of-options", base, "HEAD").strip()
    except GitError:
        return None


def _show(repo: Path, rev: str, path: str, git: Git = GIT) -> str | None:
    try:
        return git.run(repo, "show", "--end-of-options", f"{rev}:{path}")
    except UndecodableOutput:
        raise MappingError(path, "not valid UTF-8") from None
    except GitError:
        return None


_BLOB_MODES = ("100644", "100755")


def _head_tree(repo: Path, rev: str = "HEAD", git: Git = GIT) -> dict[str, str]:
    """``rev``'s committed tree (HEAD's by default) as ``{path: mode}``. The working tree is never consulted."""
    tree: dict[str, str] = {}
    for entry in git.run(repo, "ls-tree", "-r", "-z", "--end-of-options", rev).split("\0"):
        meta, sep, path = entry.partition("\t")
        if sep:
            tree[path] = meta.split(" ", 1)[0]
    return tree


def _tree_dirs(tree: dict[str, str]) -> frozenset[str]:
    """Every directory that has a committed path under it: each tree path contributes its ancestors.
    One pass over the tree, charged once per tree."""
    dirs: set[str] = set()
    for p in tree:
        end = p.rfind("/")
        while end > 0 and p[:end] not in dirs:
            dirs.add(p[:end])
            end = p.rfind("/", 0, end)
    return frozenset(dirs)


def _check_against_tree(path: str, mapping: Mapping, tree: dict[str, str],
                        dirs: frozenset[str] | None = None) -> None:
    """The on-disk checks ``load_mapping`` can make, made against HEAD's committed tree.

    A measure is held by a committed blob at its path or by any path under it; ``dirs`` (built from
    ``tree`` when not given) answers the second in one set lookup."""
    dirs = _tree_dirs(tree) if dirs is None else dirs
    plugin = PurePosixPath(path).parent.parent
    for position, (name, case) in enumerate(mapping.cases.items(), 1):
        ref = _q(name, position)
        for i, m in enumerate(case.measures, 1):
            target = (plugin / m).as_posix()
            if not (tree.get(target) in _BLOB_MODES or target in dirs):
                raise MappingError(path, f"case {ref}: measures path {_q(m, i)} does not exist "
                                         "under the plugin root")
        if tree.get((plugin / "evals" / name / "case.yaml").as_posix()) not in _BLOB_MODES:
            raise MappingError(path, f"case {ref} has no case.yaml")


def _check_mapping_count(mappings: list[str], label: str, revision: str) -> None:
    if len(mappings) > MAX_MAPPING_FILES:
        err = MappingError(mappings[MAX_MAPPING_FILES],
                           f"{len(mappings)} mapping files at {label} exceed the cap of {MAX_MAPPING_FILES}")
        err.revision = revision
        raise err


def _load_head(repo: Path, path: str, tree: dict[str, str], dirs: frozenset[str],
               git: Git = GIT) -> Mapping | None:
    if path not in tree:
        return None
    try:
        text = git.run(repo, "show", "--end-of-options", f"HEAD:{path}")
    except UndecodableOutput:
        raise MappingError(path, "not valid UTF-8") from None
    mapping = load_mapping(text, path)
    _check_against_tree(path, mapping, tree, dirs)
    return mapping


def _load_base(path: str, text: str) -> Mapping:
    try:
        mapping = load_mapping(text, path)
        return mapping
    except MappingError as e:
        err = MappingError(path, f"the base revision's mapping is invalid: {e.fault}")
        err.revision = "base"
        raise err from None


def _open_cat_file(repo: Path) -> subprocess.Popen:
    return subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


class _GitReader:
    """The plugin-relative reader ``loads`` builds on, over ``rev``'s committed tree under ``root``.

    Modes come from ``tree`` (``{repo path: mode}``), so a path the tree lacks is ``None`` and anything but a
    plain blob raises ``NotRegularFile``. Contents come from one lazily started ``git cat-file --batch``;
    a process that dies, answers anything but a whole blob or stops short raises, never an empty text."""

    def __init__(self, repo: Path, rev: str, tree: dict[str, str], root: str, git: Git = GIT,
                 charged: set[str] | None = None):
        self._repo, self._rev, self._tree, self._root, self._git = repo, rev, tree, root, git
        self._charged = set() if charged is None else charged
        self._proc: subprocess.Popen | None = None

    def __call__(self, path: str) -> str | None:
        full = f"{self._root}/{path}" if self._root else path
        mode = self._tree.get(full)
        if mode is None:
            return None
        if mode not in _BLOB_MODES:
            raise NotRegularFile(path)
        label = "HEAD" if self._rev == "HEAD" else "the base revision"
        if full not in self._charged:
            if len(self._charged) >= MAX_PROCEDURE_FILES:
                raise LoadError(f"{MAX_PROCEDURE_FILES + 1} procedure files read at {label} exceed the cap "
                                f"of {MAX_PROCEDURE_FILES}")
            self._charged.add(full)
        try:
            return self._fetch(full)
        except (GitError, OSError, ValueError):
            raise LoadError(f"{_q(path, 1)} could not be read at {label}") from None

    def _fetch(self, full: str) -> str:
        if "\n" in full:
            raise GitError("a path with a newline cannot be read through cat-file --batch")
        if self._proc is None:
            self._proc = self._git.open_cat_file(self._repo)
        proc = self._proc
        proc.stdin.write(f"{self._rev}:{full}\n".encode())
        proc.stdin.flush()
        head = proc.stdout.readline().split()
        if len(head) != 3 or head[1] != b"blob" or not head[2].isdigit():
            raise GitError("git cat-file --batch did not return a blob")
        size = int(head[2])
        if size > MAX_BLOB_BYTES:
            self.close(kill=True)
            raise LoadError(f"{_q(full[len(self._root) + 1:] if self._root else full, 1)} is larger than "
                            f"{MAX_BLOB_BYTES} bytes")
        data = proc.stdout.read(size)
        if len(data) != size or proc.stdout.read(1) != b"\n":
            raise GitError("git cat-file --batch returned a truncated object")
        return data.decode("utf-8")

    def close(self, kill: bool = False) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            if kill:
                proc.kill()
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.stdout.close()
            proc.wait()


class _Readers:
    """One ``cached`` reader per (revision, plugin root), each over its own cat-file process; closed together."""

    def __init__(self, repo: Path, git: Git = GIT):
        self._repo, self._git = repo, git
        self._made: dict[tuple[str, str], tuple[_GitReader, object]] = {}
        self._charged: dict[str, set[str]] = {}

    def get(self, rev: str, tree: dict[str, str], root: str):
        key = (rev, root)
        if key not in self._made:
            raw = _GitReader(self._repo, rev, tree, root, self._git, self._charged.setdefault(rev, set()))
            self._made[key] = (raw, cached(raw))
        return self._made[key][1]

    def close(self) -> None:
        for raw, _ in self._made.values():
            raw.close()


def _fault(path: str, ref: str, err: LoadError, revision: str) -> MappingError:
    if revision == "base":
        fault = MappingError(path, f"the base revision's mapping is invalid: case {ref}: {err}")
        fault.revision = "base"
        return fault
    return MappingError(path, f"case {ref}: {err}")


def _split_cores(procedures: list[str], read) -> list[str]:
    """The procedure files (plugin-relative) that load or name at least one branch file."""
    return [f for f in procedures if reaches_branch(f, read)]


def _check_head_procedures(path: str, plugin: PurePosixPath, head_map: Mapping, markdown: list[str],
                           read) -> None:
    """The scans of a mapped corpus's procedures at HEAD: a load of a missing file, and a hand-written
    ``measures`` path that is or holds a split core or a branch file."""
    prefix = "" if str(plugin) == "." else plugin.as_posix() + "/"
    procedures = sorted(p[len(prefix):] for p in markdown
                        if p.startswith(prefix) and is_procedure_path(p[len(prefix):]))
    try:
        missing = missing_loads(procedures, read)
        cores = _split_cores(procedures, read)
        branch_files = [f for f in procedures if is_branch_file(f, read)]
    except LoadError as e:
        raise MappingError(path, str(e)) from None
    if missing:
        raise MappingError(path, f"{_q(missing[0][0], 1)} loads {_q(missing[0][1], 2)}, which does not exist")
    for position, (name, case) in enumerate(head_map.cases.items(), 1):
        for i, m in enumerate(case.measures, 1):
            target = PurePosixPath(m).as_posix()

            def holds(f: str) -> bool:
                return target == "." or f == target or f.startswith(target.rstrip("/") + "/")

            hit = next((c for c in cores if holds(c)), None)
            if hit is not None:
                raise MappingError(path, f"case {_q(name, position)}: measures path {_q(m, i)} is or holds "
                                         f"the split core {_q(hit, 1)}")
            hit = next((b for b in branch_files if holds(b)), None)
            if hit is not None:
                raise MappingError(path, f"case {_q(name, position)}: measures path {_q(m, i)} is or holds "
                                         f"the branch file {_q(hit, 1)}")


def _derive(path: str, mapping: Mapping | None, name: str, read, revision: str):
    case = mapping.cases.get(name) if mapping else None
    if case is None or case.entry is None:
        return None
    try:
        return derive(case.entry, case.branches, read)
    except LoadError as e:
        raise _fault(path, _q(name, list(mapping.cases).index(name) + 1), e, revision) from None


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


def resolve_runner(runner: dict, tier: str, needs_merge_model: str | None) -> dict:
    """The runner block a case runs under at ``tier``: ``runner`` is the base mapping's block and
    ``needs_merge_model`` the base's mark for the case. The merge tier runs ``model``; the push tier
    runs ``push_model`` unless the case is marked or the block has none, then ``model``. The result
    never carries ``push_model``."""
    model = runner["model"]
    if tier == "push" and needs_merge_model is None:
        model = runner.get("push_model") or model
    return {"model": model, "allow_tools": runner["allow_tools"], "max_cost_usd": runner["max_cost_usd"]}


def _mark_change(base_mark: str | None, head_mark: str | None) -> str | None:
    if base_mark == head_mark:
        return None
    if base_mark is None:
        return "added"
    return "removed" if head_mark is None else "altered"


def _candidates(repo: Path, merge_base: str, tree: dict[str, str], merge_only_either: bool, tier: str,
                git: Git = GIT):
    """Every case in the merge-base's and HEAD's mappings as ``(plugin, measures, entry)``, in sorted
    mapping-path then case-name order, plus the base's marks and the mark changes. The changed paths
    play no part: HEAD's tree, the base's tree and each mapping are read once, and each declared case's
    files are derived at both revisions through one cached ``git cat-file --batch`` reader per
    (revision, plugin root), started only when a file is read. An entry's ``measures`` are the
    hand-written paths and the derived files of the base and of HEAD, each once."""
    base_tree = _head_tree(repo, merge_base, git)
    head_mappings: list[str] = []
    markdown: list[str] = []
    for p, mode in tree.items():
        if _is_mapping(p):
            head_mappings.append(p)
        elif mode in _BLOB_MODES and p.endswith(".md"):
            markdown.append(p)
    base_mappings = sorted(p for p in base_tree if _is_mapping(p))
    _check_mapping_count(sorted(head_mappings), "HEAD", "head")
    _check_mapping_count(base_mappings, "the base revision", "base")
    candidates: list[tuple[PurePosixPath, tuple[str, ...], dict]] = []
    marks: list[dict] = []
    changes: list[dict] = []
    dirs = _tree_dirs(tree)
    readers = _Readers(repo, git)
    owners: dict[str, dict[str, str]] = {"base": {}, "head": {}}
    try:
        for path in sorted(set(head_mappings) | set(base_mappings)):
            plugin = PurePosixPath(path).parent.parent
            root = "" if str(plugin) == "." else plugin.as_posix()
            if not valid_corpus_path(root):
                raise MappingError(path, f"the corpus path {_q(root, 1)} has characters outside A-Za-z0-9._/- or a "
                                         "'..' segment")
            base_text = _show(repo, merge_base, path, git) if path in base_tree else None
            base_map = _load_base(path, base_text) if base_text is not None else None
            head_map = _load_head(repo, path, tree, dirs, git)
            base_read = readers.get(merge_base, base_tree, root)
            head_read = readers.get("HEAD", tree, root)
            names = sorted(set(base_map.cases if base_map else ()) | set(head_map.cases if head_map else ()))
            for name in names:
                for label, mapping in (("base", base_map), ("head", head_map)):
                    if mapping is not None and name in mapping.cases \
                            and owners[label].setdefault(name, path) != path:
                        fault = f"case {_q(name, names.index(name) + 1)} is declared by more than one corpus"
                        if label == "base":
                            err = MappingError(path, f"the base revision's mapping is invalid: {fault}")
                            err.revision = "base"
                            raise err
                        raise MappingError(path, fault)
                base_case = base_map.cases.get(name) if base_map else None
                head_case = head_map.cases.get(name) if head_map else None
                base_derived = _derive(path, base_map, name, base_read, "base")
                head_derived = _derive(path, head_map, name, head_read, "head")
                declared = [d for d in (base_derived, head_derived) if d is not None]
                if head_case and head_case.merge_only and head_case.needs_merge_model is None \
                        and any(d.split for d in declared):
                    raise MappingError(path, f"case {_q(name, list(head_map.cases).index(name) + 1)}: its entry is a "
                                             "split procedure core, which runs on the push model; mark it "
                                             "needs_merge_model with a reason, or drop merge_only")
                measures = tuple(dict.fromkeys(
                    (base_case.measures if base_case else ()) + (base_derived.files if base_derived else ())
                    + (head_case.measures if head_case else ()) + (head_derived.files if head_derived else ())
                ))
                case = base_case or head_case
                marked = case.merge_only or bool(merge_only_either and head_case and head_case.merge_only)
                if head_case and not head_case.merge_only and any(d.split for d in declared):
                    marked = False
                runner = (base_map or head_map).runner
                base_mark = base_case.needs_merge_model if base_case else None
                head_mark = head_case.needs_merge_model if head_case else None
                if base_mark is not None:
                    marks.append({"plugin_root": root, "case": name, "reason": base_mark})
                change = _mark_change(base_mark, head_mark)
                if change:
                    changes.append({"plugin_root": root, "case": name, "change": change, "reason": head_mark})
                entry = {
                    "plugin_root": root,
                    "case": name,
                    "runs": case.runs,
                    "pass_at": case.pass_at,
                    "runner": resolve_runner(runner, tier, base_mark),
                    "needs_merge_model": base_mark,
                    "push_model": runner.get("push_model"),
                    "merge_model": runner["model"],
                    "measures": list(measures),
                    "split_procedure": any(d.split for d in declared),
                    "merge_only": marked,
                    "new_runner_grants": base_map is None,
                    "case_removed": tree.get((plugin / "evals" / name / "case.yaml").as_posix())
                                    not in _BLOB_MODES,
                }
                if declared:
                    entry["derived"] = list(dict.fromkeys(f for d in declared for f in d.files))
                candidates.append((plugin, measures, entry))
            if head_map is not None:
                _check_head_procedures(path, plugin, head_map, markdown, head_read)
        for name, was in owners["base"].items():
            now = owners["head"].get(name)
            if now is not None and now != was:
                roots = [PurePosixPath(q).parent.parent.as_posix() for q in (was, now)]
                roots = ["" if r == "." else r for r in roots]
                raise MappingError(now, f"case {_q(name, 1)} moves from corpus {_q(roots[0], 1)} to corpus "
                                        f"{_q(roots[1], 2)}; a case is identified by its bare name, so rename "
                                        "it instead")
    finally:
        readers.close()
    return candidates, marks, changes


class _Node:
    """One path component in the index: ``targets`` are the candidates whose measure target ends here
    (they match this path and everything under it), ``cases`` those whose case directory ends here
    (they match only what lies strictly under it)."""
    __slots__ = ("children", "targets", "cases")

    def __init__(self):
        self.children: dict[str, _Node] = {}
        self.targets: list[int] = []
        self.cases: list[int] = []


def _build_index(candidates) -> _Node:
    root = _Node()

    def node_for(path: str) -> _Node:
        node = root
        for part in path.split("/"):
            node = node.children.setdefault(part, _Node())
        return node

    for i, (plugin, measures, entry) in enumerate(candidates):
        node_for((plugin / "evals" / entry["case"]).as_posix()).cases.append(i)
        for m in measures:
            node_for((plugin / m).as_posix()).targets.append(i)
    return root


def _touching(root: _Node, path: str, absorbed: tuple[set, set] | None = None) -> set[int]:
    """The candidates ``_touches`` accepts for ``path``, found by walking its components: one child
    lookup per component, each component hashed once, stopping at the first component the index lacks.

    ``absorbed`` is the (target nodes, case nodes) a caller has already folded into its result across
    paths; a node found there contributes nothing again, so the candidate sets added over many paths
    grow with the candidates and the paths, not their product."""
    hit: set[int] = set()
    done_targets, done_cases = absorbed if absorbed is not None else (None, None)
    node = root
    for part in path.split("/"):
        if done_cases is None:
            hit.update(node.cases)
        elif node.cases and node not in done_cases:
            done_cases.add(node)
            hit.update(node.cases)
        node = node.children.get(part)
        if node is None:
            return hit
        if done_targets is None:
            hit.update(node.targets)
        elif node.targets and node not in done_targets:
            done_targets.add(node)
            hit.update(node.targets)
    return hit


def _select(repo: Path, merge_base: str, changed: list[str], merge_only_either: bool,
            tree: dict[str, str] | None, tier: str,
            git: Git = GIT) -> tuple[list[dict], list[dict], list[dict]]:
    tree = _head_tree(repo, "HEAD", git) if tree is None else tree
    candidates, marks, changes = _candidates(repo, merge_base, tree, merge_only_either, tier, git)
    index = _build_index(candidates)
    hit: set[int] = set()
    absorbed: tuple[set, set] = (set(), set())
    for c in changed:
        hit |= _touching(index, c, absorbed)
    return [candidates[i][2] for i in sorted(hit)], marks, changes


def select_touched(repo: Path, merge_base: str, changed: list[str], *,
                   merge_only_either: bool = False, tree: dict[str, str] | None = None,
                   tier: str = "merge") -> list[dict]:
    """Every case the ``changed`` files touch, whatever its tier, as plan entries.

    Everything is read from commits: the merge-base's and HEAD's mappings and HEAD's tree (``tree``,
    when the caller has already read it). An entry's ``merge_only`` is the base's value (the base takes
    precedence); with ``merge_only_either`` it is true when the base's or HEAD's mapping marks the case.
    Either way it is false for a case HEAD still declares, whose entry is a split procedure core at either
    revision and whose HEAD case does not say ``merge_only``.
    An entry's ``runner`` is ``resolve_runner``'s block for ``tier``."""
    return _select(repo, merge_base, changed, merge_only_either, tree, tier)[0]


def select_touched_by_path(repo: Path, merge_base: str, paths: list[str], *,
                           merge_only_either: bool = False,
                           tree: dict[str, str] | None = None,
                           tier: str = "merge") -> dict[str, list[dict]]:
    """For each of ``paths``, the entries ``select_touched`` returns when that path alone changed.

    HEAD's tree, the base listing and each mapping are read once for all the paths, so the git
    commands run grow with the mappings, not with ``len(paths)``. An index from each case directory
    and each measure target to its candidates is built once; a path looks up itself and its ancestor
    directories, so the work grows with the paths and their depth, not with the candidates."""
    tree = _head_tree(repo) if tree is None else tree
    candidates = _candidates(repo, merge_base, tree, merge_only_either, tier)[0]
    index = _build_index(candidates)
    return {path: [candidates[i][2] for i in sorted(_touching(index, path))] for path in paths}


def _runs(case: dict) -> bool:
    return not case["case_removed"] and not case["new_runner_grants"]


TIERS = ("push", "merge")


def _changed_since(repo: Path, merge_base: str, git: Git = GIT) -> list[str]:
    diff_out = git.run(repo, "diff", "--name-only", "--no-renames", "-z", "--end-of-options",
                       f"{merge_base}...HEAD")
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


def backstop_touched(repo: Path, base: str | None = None) -> tuple[str, list[dict]]:
    """The cases a change to ``repo`` touches that the merge backstop covers, as ``(status, entries)``:
    those that are ``merge_only`` or ``split_procedure``.

    The merge backstop asks this to decide whether it applies, using the same selection as
    ``plan``. ``status`` is ``ok`` (``entries`` are exactly the touched covered cases),
    ``base-unresolved`` (the changed files are unknown, so ``entries`` is every covered case
    the repo's mappings hold: a superset, never a miss) or ``mapping-invalid`` (``entries`` is empty
    and the answer is unknown). A case is ``merge_only`` when the merge-base's mapping or HEAD's
    mapping marks it, so a stale base cannot hide a flag, unless HEAD still declares it, its entry is a
    split procedure core at either revision and its HEAD case does not say ``merge_only``;
    ``split_procedure`` is as ``plan`` reports it.
    Only commits are read: the mappings and the case files come from the merge-base and HEAD, never the
    working tree, so an uncommitted edit changes nothing. Raises ``NotAWorkTree`` when ``repo`` is not a
    git work tree and ``GitError`` on any other git failure."""
    repo = Path(repo).resolve()
    _require_work_tree(repo)
    resolved = _resolve_base(repo, base)
    try:
        if resolved is None:
            tree = _head_tree(repo)
            touched = select_touched(repo, "HEAD", list(tree), merge_only_either=True, tree=tree)
        else:
            touched = select_touched(repo, resolved[1], _changed_since(repo, resolved[1]),
                                     merge_only_either=True)
    except MappingError:
        return "mapping-invalid", []
    return ("base-unresolved" if resolved is None else "ok"), [
        c for c in touched if c["merge_only"] or c["split_procedure"]]


def repo_has_mapping(repo: Path, base: str | None = None) -> bool:
    """Whether ``repo`` holds an ``eval-gate.json`` mapping at HEAD or at the merge-base, whatever
    it contains. Raises ``GitError``."""
    repo = Path(repo).resolve()
    if any(_is_mapping(p) for p in _head_tree(repo)):
        return True
    resolved = _resolve_base(repo, base)
    if resolved is None:
        return False
    at_base = _git(repo, "ls-tree", "-r", "-z", "--name-only", "--end-of-options", resolved[1]).split("\0")
    return any(_is_mapping(p) for p in at_base)


def local_head(repo: Path) -> str:
    """The full commit id of ``repo``'s ``HEAD``. Raises ``GitError``."""
    return _git(Path(repo).resolve(), "rev-parse", "--verify", "--end-of-options", "HEAD").strip()


def plan(repo: Path, base: str | None, tier: str = "push", *, git: Git = GIT,
         gate_on: bool = True) -> tuple[Path, dict]:
    """Compute the plan for ``repo`` against ``base`` and write it to a new state dir.

    ``tier`` is ``push`` (leaves out ``merge_only`` cases) or ``merge`` (every touched case). With
    ``gate_on`` false nothing is selected and the status is ``gate-off``.

    A ``base`` that starts with ``-`` is ``base-invalid``. Raises ``GitError`` when ``repo`` is not a git
    work tree."""
    repo = Path(repo).resolve()
    git.run(repo, "rev-parse", "--is-inside-work-tree")
    result: dict = {
        "schema_version": SCHEMA_VERSION, "status": "no-cases-mapped", "repo": str(repo),
        "base": base, "merge_base": None, "tier": tier,
        "head": git.run(repo, "rev-parse", "--verify", "--end-of-options", "HEAD").strip(),
        "merge_only_pending": 0,
        "changed_files": [], "mapping_invalid": None,
        "new_runner_grants": False, "total_max_cost_usd": 0, "cases": [],
        "needs_merge_model": [], "mark_changes": [],
    }
    resolved = _resolve_base(repo, base, git) if gate_on else None
    if not gate_on:
        result["status"] = "gate-off"
    elif _option_like(base):
        result["status"] = "base-invalid"
    elif git.run(repo, "status", "--porcelain", "--untracked-files=no").strip():
        result["status"] = "tree-dirty"
    elif resolved is None:
        result["status"] = "base-unresolved"
    else:
        result["base"], result["merge_base"] = resolved
        changed = _changed_since(repo, resolved[1], git)
        result["changed_files"] = changed
        try:
            touched, marks, changes = _select(repo, resolved[1], changed, tier == "merge", None, tier, git)
        except MappingError as e:
            result["status"] = "mapping-invalid"
            result["mapping_invalid"] = {"file": e.file, "fault": e.fault, "revision": e.revision}
        else:
            result["needs_merge_model"], result["mark_changes"] = marks, changes
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
