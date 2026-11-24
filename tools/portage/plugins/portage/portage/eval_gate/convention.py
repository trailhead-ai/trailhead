"""The load convention check: one plugin root in, one-line faults out.

``check_plugin(root)`` walks the ``.md`` files of ``<root>/skills`` and ``<root>/agents`` with
``loads.parse_directives`` and returns one fault per breach, ``<file>:<line>: <rule>: <detail>; use: <sentence>``
(an empty list for a clean tree). Values echo through ``mapping.echo``. Command line:
``python -m portage.eval_gate.convention <plugin-root>`` prints one fault per line and exits 1 on any, 0 when
clean, 2 when the root is not a directory.

Rules, by the name each fault carries:

- ``misworded-load``, ``load-names-several-files``: a directive that names an existing plugin file outside the
  three fixed forms (see ``loads``), or a load naming more than one file.
- ``branch-loads-branch``: a branch file (first line opens "Applies when" or "Applies only when") that branch-loads.
- ``rule-file-loads``: a file that is neither ``SKILL.md``, an agent nor a branch file, with a load that is not a
  branch load.
- ``shared-sibling-named``: a file in ``skills/_shared`` naming a sibling ``.md`` file of that directory, by bare
  filename or any path form, outside a load; fenced code and HTML comments are not read.
- ``branch-opening``: a branch load whose target does not open "Applies when"; ``branch-loaded-unconditionally``:
  a file that does, read by a non-branch load.
- ``load-path-unsafe``: an absolute path, a path with a character outside ``[A-Za-z0-9._/-]``, a ``plugins/``
  prefix, or a path that leaves the plugin root. ``load-target-missing``: the path, resolved against the loading
  file's own directory and no other, names no file. ``load-target-not-regular``: it names a symlink, a directory,
  or is reached through a symlinked directory. ``load-target-unreadable``: the file cannot be read.
- ``core-too-long``: a file holding a branch load that has 500 or more lines.
- ``unreadable-file``: a file the walk cannot read as UTF-8 text; the check fails closed.
- ``declared-mentions-malformed``, ``declared-mention-stale``, ``declared-mention-also-loaded``: the
  ``<root>/declared-mentions.json`` list (see ``loads.read_declared_mentions``): a malformed list, an entry
  whose file no longer names the path, an entry naming a path the same file loads.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .loads import (
    BRANCH_LOAD, FULL_LOAD, SECTION_LOAD, LoadError, NotRegularFile, _get, _is_branch, _visible_lines,
    cached, parse_directives, read_declared_mentions, resolve_existing, tree_reader,
)
from .loads import _SAFE_CHARS, _WORD
from .mapping import _q, echo

MAX_CORE_LINES = 500
_USE_ANY = f"{FULL_LOAD} or {SECTION_LOAD} or {BRANCH_LOAD}"


class _Faults:
    def __init__(self) -> None:
        self.found: list[str] = []
        self._files: dict[str, int] = {}

    def add(self, file: str, line: int, rule: str, detail: str, use: str = "") -> None:
        position = self._files.setdefault(file, len(self._files) + 1)
        tail = f"; use: {use}" if use else ""
        self.found.append(f"{echo(file, position)}:{line}: {rule}: {detail}{tail}")


def _walk(root: Path) -> list[str]:
    found: list[str] = []
    for top in ("skills", "agents"):
        for directory, dirs, names in os.walk(root / top):
            dirs.sort()
            for name in sorted(names):
                full = Path(directory) / name
                if name.endswith(".md") and full.is_file() and not full.is_symlink():
                    found.append(full.relative_to(root).as_posix())
    return found


def _sibling_pattern(root: Path, file: str) -> re.Pattern[str] | None:
    here = (root / file).parent
    stems = sorted(p.stem for p in here.glob("*.md") if p.name != Path(file).name and p.is_file())
    if not stems:
        return None
    return re.compile(rf"(?<![\w-])(?:{'|'.join(re.escape(s) for s in stems)})\.md(?![\w-])")


def _unsafe_reason(raw: str, target: str | None) -> str | None:
    if raw.startswith("/"):
        return "is an absolute path"
    if not _SAFE_CHARS.match(raw):
        return "has a character outside [A-Za-z0-9._/-]"
    if raw.startswith("plugins/"):
        return "starts with plugins/; write it from the loading file's directory"
    if target is None:
        return "leaves the plugin root"
    return None


def _check_file(root: Path, read, file: str, faults: _Faults) -> None:
    try:
        text = _get(read, file)
    except LoadError as exc:
        faults.add(file, 1, "unreadable-file", str(exc))
        return
    if text is None:
        return
    directives = parse_directives(file, text, read)
    for item in directives.misworded:
        several = len(item.paths) > 1
        shown = ", ".join(_q(p, n) for n, p in enumerate(item.paths, 1))
        faults.add(file, item.line, "load-names-several-files" if several else "misworded-load",
                   f"the directive naming {shown} {item.reason}", _USE_ANY)
    is_branch = _is_branch(text)
    is_entry = Path(file).name == "SKILL.md" or file.startswith("agents/")
    branch_loads = [load for load in directives.loads if load.kind == "branch"]
    if is_branch:
        for load in branch_loads:
            faults.add(file, load.line, "branch-loads-branch",
                       f"a branch file loads {_q(load.raw, 1)}; move the load to the procedure that tests the "
                       "condition", BRANCH_LOAD)
    if not (is_entry or is_branch):
        for load in directives.loads:
            if load.kind != "branch":
                faults.add(file, load.line, "rule-file-loads",
                           f"a rule file loads {_q(load.raw, 1)} outside a branch load", BRANCH_LOAD)
    if branch_loads and len(text.splitlines()) >= MAX_CORE_LINES:
        faults.add(file, 1, "core-too-long",
                   f"a file holding a branch load has {len(text.splitlines())} lines, "
                   f"the limit is {MAX_CORE_LINES - 1}")
    for number, load in enumerate(directives.loads, 1):
        use = BRANCH_LOAD if load.kind == "branch" else FULL_LOAD
        unsafe = _unsafe_reason(load.raw, load.target)
        if unsafe:
            faults.add(file, load.line, "load-path-unsafe", f"load path {_q(load.raw, number)} {unsafe}", use)
            continue
        try:
            body = read(load.target)
        except NotRegularFile:
            faults.add(file, load.line, "load-target-not-regular",
                       f"load path {_q(load.raw, number)} names a symlink, a directory or a path through a "
                       "symlinked directory", use)
            continue
        except Exception:
            faults.add(file, load.line, "load-target-unreadable", f"load path {_q(load.raw, number)} cannot be read",
                       use)
            continue
        if body is None:
            faults.add(file, load.line, "load-target-missing",
                       f"load path {_q(load.raw, number)} names no file when resolved from this file's own "
                       "directory", use)
        elif load.kind == "branch" and not _is_branch(body):
            faults.add(file, load.line, "branch-opening",
                       f"branch-loaded {_q(load.raw, number)} does not open 'Applies when'; open it with the "
                       "condition under which it applies", BRANCH_LOAD)
        elif load.kind != "branch" and _is_branch(body):
            faults.add(file, load.line, "branch-loaded-unconditionally",
                       f"{_q(load.raw, number)} opens 'Applies when' but is loaded unconditionally", BRANCH_LOAD)
    if str(Path(file).parent) == "skills/_shared":
        pattern = _sibling_pattern(root, file)
        if pattern is not None:
            in_load = {n for load in directives.loads if load.kind == "branch" for n in range(load.line, load.end_line + 1)}
            for number, line in enumerate(_visible_lines(text), 1):
                if number in in_load:
                    continue
                for match in pattern.finditer(line):
                    faults.add(file, number, "shared-sibling-named",
                               f"a shared file names its sibling {_q(match.group(0), 1)}; state the point "
                               f"without a path, or load it as a branch: {BRANCH_LOAD}")


def _check_declared(root: Path, read, faults: _Faults) -> None:
    name = "declared-mentions.json"
    try:
        declared = read_declared_mentions(root)
    except LoadError as exc:
        faults.add(name, 1, "declared-mentions-malformed", str(exc))
        return
    for position, (file, paths) in enumerate(declared.items(), 1):
        try:
            text = _get(read, file)
        except LoadError as exc:
            faults.add(name, 1, "declared-mention-stale", str(exc))
            continue
        loaded = {load.target for load in parse_directives(file, text, read).loads} if text is not None else set()
        named = ({resolve_existing(file, w.rstrip("."), read) for w in _WORD.findall(text) if _SAFE_CHARS.match(w)}
                 if text is not None else set())
        for number, path in enumerate(paths, 1):
            if path not in named:
                faults.add(name, 1, "declared-mention-stale",
                           f"{_q(file, position)} no longer names {_q(path, number)}; remove the entry")
            elif path in loaded:
                faults.add(name, 1, "declared-mention-also-loaded",
                           f"{_q(file, position)} loads {_q(path, number)}, so it is not a plain mention; "
                           "remove the entry")


def check_plugin(root: Path) -> list[str]:
    """The faults in the plugin at ``root``, in file order; ``[]`` for a clean tree."""
    root = Path(root)
    read = cached(tree_reader(root))
    faults = _Faults()
    for file in _walk(root):
        _check_file(root, read, file, faults)
    _check_declared(root, read, faults)
    return faults.found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m portage.eval_gate.convention",
                                     description="Check one plugin root against the load convention.")
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print(f"{args.root} is not a directory", file=sys.stderr)
        return 2
    faults = check_plugin(args.root)
    for fault in faults:
        print(fault)
    return 1 if faults else 0


if __name__ == "__main__":
    raise SystemExit(main())
