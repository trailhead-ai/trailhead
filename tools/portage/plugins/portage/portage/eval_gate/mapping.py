"""Strict loader for a corpus's ``evals/eval-gate.json``.

Shape::

    {"schema_version": 1,
     "runner": {"model": str, "allow_tools": [str, ...], "max_cost_usd": number,
                "push_model": str (optional)},
     "cases": {"<case>": {"measures": ["<plugin-relative path>", ...],
                          "entry": "<plugin-relative .md path>" (optional),
                          "branches": ["<plugin-relative .md path>", ...] (optional, needs entry),
                          "runs": int (optional, default 3),
                          "pass_at": int (optional),
                          "merge_only": bool (optional, default false),
                          "needs_merge_model": str (optional)}}}

A case names what a change can affect by ``measures`` (every file it reads), by a declaration, or both. The
declaration is ``entry``, the skill or agent the case starts from, plus ``branches``, the procedure files it may
descend into; a pure declaration has an empty ``measures``. A case with neither ``measures`` nor ``entry`` is a
fault, as is ``branches`` without ``entry``. ``entry`` and every branch are plugin-relative ``.md`` paths with no
``..`` segment, present under the plugin root when it is given. A mapping that declares ``entry`` is read only by a
gate that understands it, so land the gate before any mapping that uses the key.
``pass_at`` defaults to 2 when ``runs`` is 3; any other ``runs`` must declare its
own ``pass_at``, which must lie in ``1..runs``. ``case.yaml`` is checked for
presence only. ``runs`` lies in 1..10; ``runner.model`` is a conservative model id, each
``runner.allow_tools`` entry a tool name (letters, digits, ``_``) with at most one
single-line parenthesised argument, and ``runner.max_cost_usd`` a finite number in (0, 25].
``merge_only`` marks a case the push tier leaves out; it must be a boolean.
``runner.push_model`` is the model the push tier runs every case on (same shape as ``runner.model``,
which the merge tier uses); it is carried in the runner dict only when configured.
``needs_merge_model`` marks a case the push tier cannot trust to the cheaper model and states why: a
reason of 1-200 characters, one line, starting with a letter or digit and otherwise letters, digits,
space and ``.,;'()_-``, and never containing ``www.`` in any case. A GitHub autolink needs a scheme (``:``),
a path (``/``), an address (``@``) or a ``www.`` prefix, and the charset or the ``www.`` rule excludes each, so a reason
can never form such a link in a PR body. It does not stop GitHub's same-repo references: text such as ``GH-123`` or a
commit SHA still links.
A fault names a case by its key and echoes any other mapping value only when ``echo`` finds it safe (it matches
``ECHO_SAFE``); otherwise it names the value by its position (``#2``), so mapping text never reaches a report.
``MAX_MAPPING_FILES``, ``MAX_CASES_PER_MAPPING`` and ``MAX_PROCEDURE_FILES`` are the bounds later stages enforce.
Every fault raises ``MappingError`` carrying the mapping file and a one-line fault, never a traceback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
DEFAULT_RUNS = 3
DEFAULT_PASS_AT = 2
CASE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*\Z")
CORPUS_PATH = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,199}\Z")
TOOL_GRANT = re.compile(r"^[A-Za-z0-9_]+(?:\([^\n\r]*\))?\Z")
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
NEEDS_MERGE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,;'()_-]{0,199}\Z")
_AUTOLINK = "www."
MAX_COST_USD = 25
MAX_RUNS = 10
MAX_MAPPING_FILES = 32
MAX_CASES_PER_MAPPING = 256
MAX_PROCEDURE_FILES = 2000
ECHO_SAFE = re.compile(r"^[A-Za-z0-9._/-]{1,120}\Z")

_TOP_KEYS = {"schema_version", "runner", "cases"}
_RUNNER_REQUIRED = {"model", "allow_tools", "max_cost_usd"}
_RUNNER_KEYS = _RUNNER_REQUIRED | {"push_model"}
_CASE_KEYS = {"measures", "entry", "branches", "runs", "pass_at", "merge_only", "needs_merge_model"}


class MappingError(Exception):
    def __init__(self, file: str, fault: str):
        super().__init__(f"{file}: {fault}")
        self.file = file
        self.fault = fault
        self.revision = "head"


@dataclass(frozen=True)
class Case:
    name: str
    measures: tuple[str, ...]
    runs: int
    pass_at: int
    merge_only: bool = False
    needs_merge_model: str | None = None
    entry: str | None = None
    branches: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mapping:
    runner: dict
    cases: dict[str, Case]


def valid_corpus_path(root: object) -> bool:
    """A repo-relative corpus (plugin root) path safe to print: the empty root, or
    a conservative-character path with no ``..`` segment."""
    if root == "":
        return True
    return isinstance(root, str) and bool(CORPUS_PATH.match(root)) and ".." not in PurePosixPath(root).parts


def echo(value: object, position: int) -> str:
    """``value`` itself when it is safe to print, otherwise ``#<position>``."""
    if isinstance(value, str) and ECHO_SAFE.match(value):
        return value
    return f"#{position}"


def _q(value: object, position: int) -> str:
    shown = echo(value, position)
    return shown if shown.startswith("#") else f"'{shown}'"


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _keys(obj: dict, allowed: set[str], where: str, file: str) -> None:
    for position, key in enumerate(obj, 1):
        if key not in allowed:
            raise MappingError(file, f"unknown key {_q(key, position)} in {where}")


def _model_id(obj: dict, key: str, file: str) -> str:
    if not isinstance(obj[key], str) or not MODEL_ID.match(obj[key]):
        raise MappingError(file, f"runner.{key} must match {MODEL_ID.pattern}")
    return obj[key]


def _runner(obj, file: str) -> dict:
    if not isinstance(obj, dict):
        raise MappingError(file, "runner must be an object")
    _keys(obj, _RUNNER_KEYS, "runner", file)
    missing = sorted(_RUNNER_REQUIRED - set(obj))
    if missing:
        raise MappingError(file, f"runner is missing {missing[0]!r}")
    _model_id(obj, "model", file)
    tools = obj["allow_tools"]
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise MappingError(file, "runner.allow_tools must be a list of strings")
    for position, t in enumerate(tools, 1):
        if not TOOL_GRANT.match(t):
            raise MappingError(file, f"runner.allow_tools entry {_q(t, position)} must be a tool name, "
                                     "optionally followed by one parenthesised argument on one line")
    cost = obj["max_cost_usd"]
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not 0 < cost <= MAX_COST_USD:
        raise MappingError(file, f"runner.max_cost_usd must be a finite number in (0, {MAX_COST_USD}]")
    runner = {"model": obj["model"], "allow_tools": list(tools), "max_cost_usd": cost}
    if "push_model" in obj:
        runner["push_model"] = _model_id(obj, "push_model", file)
    return runner


def _path(path, what: str, case: str, position: int, file: str, plugin_root: Path | None,
          *, markdown: bool = False) -> str:
    if not isinstance(path, str) or not path:
        raise MappingError(file, f"case {case}: {what} entries must be non-empty strings")
    shown = _q(path, position)
    if path.startswith("/") or "\\" in path or "\0" in path or re.match(r"^[A-Za-z]:", path):
        raise MappingError(file, f"case {case}: {what} path {shown} must be relative (absolute or unsafe path)")
    if ".." in PurePosixPath(path).parts:
        raise MappingError(file, f"case {case}: {what} path {shown} must not contain '..'")
    if markdown and not path.endswith(".md"):
        raise MappingError(file, f"case {case}: {what} path {shown} must end in .md")
    if plugin_root is not None:
        target = (plugin_root / path).resolve()
        if not target.is_relative_to(plugin_root.resolve()) or not target.exists():
            raise MappingError(file, f"case {case}: {what} path {shown} does not exist under the plugin root")
    return PurePosixPath(path).as_posix()


def _case(position: int, name: str, obj, file: str, plugin_root: Path | None, evals_dir: Path | None) -> Case:
    ref = _q(name, position)
    if not CASE_NAME.match(name):
        raise MappingError(file, f"case name {ref} must match {CASE_NAME.pattern}")
    if not isinstance(obj, dict):
        raise MappingError(file, f"case {ref} must be an object")
    _keys(obj, _CASE_KEYS, f"case {ref}", file)
    entry = obj.get("entry")
    if "entry" in obj:
        if not isinstance(entry, str):
            raise MappingError(file, f"case {ref}: entry must be a string")
        entry = _path(entry, "entry", ref, 1, file, plugin_root, markdown=True)
    branches = obj.get("branches", [])
    if "branches" in obj and entry is None:
        raise MappingError(file, f"case {ref}: branches requires entry")
    if not isinstance(branches, list) or not all(isinstance(b, str) for b in branches):
        raise MappingError(file, f"case {ref}: branches must be a list of strings")
    branch_paths = tuple(_path(b, "branches", ref, i, file, plugin_root, markdown=True)
                         for i, b in enumerate(branches, 1))
    measures = obj.get("measures", [])
    if not isinstance(measures, list):
        raise MappingError(file, f"case {ref}: measures must be a list")
    if not measures and entry is None:
        raise MappingError(file, f"case {ref} needs measures or entry")
    paths = tuple(_path(m, "measures", ref, i, file, plugin_root) for i, m in enumerate(measures, 1))
    runs = obj.get("runs", DEFAULT_RUNS)
    if not _is_int(runs) or not 1 <= runs <= MAX_RUNS:
        raise MappingError(file, f"case {ref}: runs must be an integer in 1..{MAX_RUNS}")
    if "pass_at" in obj:
        pass_at = obj["pass_at"]
        if not _is_int(pass_at) or not 1 <= pass_at <= runs:
            raise MappingError(file, f"case {ref}: pass_at must be an integer in 1..{runs}")
    elif runs == DEFAULT_RUNS:
        pass_at = DEFAULT_PASS_AT
    else:
        raise MappingError(file, f"case {ref}: runs={runs} requires its own pass_at")
    if evals_dir is not None and not (evals_dir / name / "case.yaml").is_file():
        raise MappingError(file, f"case {ref} has no case.yaml")
    merge_only = obj.get("merge_only", False)
    if not isinstance(merge_only, bool):
        raise MappingError(file, f"case {ref}: merge_only must be a boolean")
    needs_merge_model = obj.get("needs_merge_model")
    if "needs_merge_model" in obj and not (
        isinstance(needs_merge_model, str) and NEEDS_MERGE_MODEL.match(needs_merge_model)
        and _AUTOLINK not in needs_merge_model.lower()
    ):
        raise MappingError(file, f"case {ref}: needs_merge_model must be a reason of 1-200 characters on one line, "
                                 "starting with a letter or digit, using only letters, digits, space and .,;'()_- "
                                 "and not containing 'www.'")
    return Case(name, paths, runs, pass_at, merge_only, needs_merge_model, entry, branch_paths)


def load_mapping(
    text: str, file: str, *, plugin_root: Path | None = None, evals_dir: Path | None = None
) -> Mapping:
    """Parse and validate ``text``. With ``plugin_root``/``evals_dir`` the measured
    paths and ``case.yaml`` files are also checked on disk."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise MappingError(file, f"not valid JSON ({e})") from None
    if not isinstance(data, dict):
        raise MappingError(file, "top level must be an object")
    _keys(data, _TOP_KEYS, "the mapping", file)
    if data.get("schema_version") != SCHEMA_VERSION or isinstance(data.get("schema_version"), bool):
        raise MappingError(file, f"schema_version must be {SCHEMA_VERSION}")
    if "runner" not in data or "cases" not in data:
        raise MappingError(file, "runner and cases are both required")
    runner = _runner(data["runner"], file)
    if not isinstance(data["cases"], dict):
        raise MappingError(file, "cases must be an object")
    if len(data["cases"]) > MAX_CASES_PER_MAPPING:
        raise MappingError(file, f"{len(data['cases'])} cases in {_q(file, 1)} exceed the cap of "
                                 f"{MAX_CASES_PER_MAPPING}")
    cases = {n: _case(i, n, o, file, plugin_root, evals_dir)
             for i, (n, o) in enumerate(data["cases"].items(), 1)}
    return Mapping(runner, cases)
