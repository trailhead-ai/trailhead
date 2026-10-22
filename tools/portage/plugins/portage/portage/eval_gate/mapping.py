"""Strict loader for a corpus's ``evals/eval-gate.json``.

Shape::

    {"schema_version": 1,
     "runner": {"model": str, "allow_tools": [str, ...], "max_cost_usd": number},
     "cases": {"<case>": {"measures": ["<plugin-relative path>", ...],
                          "runs": int (optional, default 3),
                          "pass_at": int (optional)}}}

``pass_at`` defaults to 2 when ``runs`` is 3; any other ``runs`` must declare its
own ``pass_at``, which must lie in ``1..runs``. ``case.yaml`` is checked for
presence only. ``runs`` lies in 1..10; ``runner.model`` is a conservative model id, each
``runner.allow_tools`` entry a tool name (letters, digits, ``_``) with at most one
single-line parenthesised argument, and ``runner.max_cost_usd`` a finite number in (0, 25]. Every fault raises ``MappingError`` carrying the mapping file and a
one-line fault, never a traceback.
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
MAX_COST_USD = 25
MAX_RUNS = 10

_TOP_KEYS = {"schema_version", "runner", "cases"}
_RUNNER_KEYS = {"model", "allow_tools", "max_cost_usd"}
_CASE_KEYS = {"measures", "runs", "pass_at"}


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


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _keys(obj: dict, allowed: set[str], where: str, file: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise MappingError(file, f"unknown key {unknown[0]!r} in {where}")


def _runner(obj, file: str) -> dict:
    if not isinstance(obj, dict):
        raise MappingError(file, "runner must be an object")
    _keys(obj, _RUNNER_KEYS, "runner", file)
    missing = sorted(_RUNNER_KEYS - set(obj))
    if missing:
        raise MappingError(file, f"runner is missing {missing[0]!r}")
    if not isinstance(obj["model"], str) or not MODEL_ID.match(obj["model"]):
        raise MappingError(file, f"runner.model must match {MODEL_ID.pattern}")
    tools = obj["allow_tools"]
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise MappingError(file, "runner.allow_tools must be a list of strings")
    for t in tools:
        if not TOOL_GRANT.match(t):
            raise MappingError(file, f"runner.allow_tools entry {t!r} must be a tool name, "
                                     "optionally followed by one parenthesised argument on one line")
    cost = obj["max_cost_usd"]
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not 0 < cost <= MAX_COST_USD:
        raise MappingError(file, f"runner.max_cost_usd must be a finite number in (0, {MAX_COST_USD}]")
    return {"model": obj["model"], "allow_tools": list(tools), "max_cost_usd": cost}


def _measure(path, case: str, file: str, plugin_root: Path | None) -> str:
    if not isinstance(path, str) or not path:
        raise MappingError(file, f"case {case!r}: measures entries must be non-empty strings")
    if path.startswith("/") or "\\" in path or "\0" in path or re.match(r"^[A-Za-z]:", path):
        raise MappingError(file, f"case {case!r}: measures path {path!r} must be relative (absolute or unsafe path)")
    if ".." in PurePosixPath(path).parts:
        raise MappingError(file, f"case {case!r}: measures path {path!r} must not contain '..'")
    if plugin_root is not None:
        target = (plugin_root / path).resolve()
        if not target.is_relative_to(plugin_root.resolve()) or not target.exists():
            raise MappingError(file, f"case {case!r}: measures path {path!r} does not exist under the plugin root")
    return PurePosixPath(path).as_posix()


def _case(name: str, obj, file: str, plugin_root: Path | None, evals_dir: Path | None) -> Case:
    if not CASE_NAME.match(name):
        raise MappingError(file, f"case name {name!r} must match {CASE_NAME.pattern}")
    if not isinstance(obj, dict):
        raise MappingError(file, f"case {name!r} must be an object")
    _keys(obj, _CASE_KEYS, f"case {name!r}", file)
    measures = obj.get("measures")
    if not isinstance(measures, list) or not measures:
        raise MappingError(file, f"case {name!r}: measures must be a non-empty list")
    paths = tuple(_measure(m, name, file, plugin_root) for m in measures)
    runs = obj.get("runs", DEFAULT_RUNS)
    if not _is_int(runs) or not 1 <= runs <= MAX_RUNS:
        raise MappingError(file, f"case {name!r}: runs must be an integer in 1..{MAX_RUNS}")
    if "pass_at" in obj:
        pass_at = obj["pass_at"]
        if not _is_int(pass_at) or not 1 <= pass_at <= runs:
            raise MappingError(file, f"case {name!r}: pass_at must be an integer in 1..{runs}")
    elif runs == DEFAULT_RUNS:
        pass_at = DEFAULT_PASS_AT
    else:
        raise MappingError(file, f"case {name!r}: runs={runs} requires its own pass_at")
    if evals_dir is not None and not (evals_dir / name / "case.yaml").is_file():
        raise MappingError(file, f"case {name!r} has no case.yaml")
    return Case(name, paths, runs, pass_at)


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
    cases = {n: _case(n, o, file, plugin_root, evals_dir) for n, o in data["cases"].items()}
    return Mapping(runner, cases)
