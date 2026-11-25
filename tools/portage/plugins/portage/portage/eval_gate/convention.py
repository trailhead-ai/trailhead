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
- ``shared-sibling-named``: a file in ``skills/_shared``, at any depth, naming a sibling ``.md`` file of its own
  directory, by bare filename or any path form, outside a load written with a path; a load that names its
  sibling by bare filename is the breach (write ``../_shared/<name>``). Fenced code and HTML comments are not
  read.
- ``branch-opening``: a branch load whose target does not open "Applies when"; ``branch-loaded-unconditionally``:
  a file that does, read by a non-branch load.
- ``load-path-unsafe``: an absolute path, a path with a character outside ``[A-Za-z0-9._/-]``, a ``plugins/``
  prefix, or a path that leaves the plugin root. ``load-target-missing``: the path, resolved against the loading
  file's own directory and no other, names no file. ``load-target-not-regular``: it names a symlink, a directory,
  or is reached through a symlinked directory. ``load-target-unreadable``: the file cannot be read.
- ``core-too-long``: a file holding a branch load that has 500 or more lines.
- ``unreadable-file``: a file the walk cannot read as UTF-8 text, a symlinked ``.md`` file or directory under
  ``skills`` or ``agents``, or a directory it cannot list; the check fails closed.
- ``declared-mentions-malformed``, ``declared-mention-stale``, ``declared-mention-also-loaded``: the
  ``<root>/declared-mentions.json`` list (see ``loads.read_declared_mentions``): a malformed list, an entry
  whose file no longer names the path, an entry naming a path the same file loads. A path a file declares there
  is a plain mention, so a sentence naming only declared paths is not a ``misworded-load``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .loads import (
    _SAFE_CHARS, _TOKEN, BRANCH_LOAD, FULL_LOAD, SECTION_LOAD, LoadError, NotRegularFile, _get, _visible_lines,
    cached, is_branch, parse_directives, read_declared_mentions, resolve_existing, tree_reader,
)
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


def _walk(root: Path) -> list[tuple[str, str]]:
    """``(plugin-relative path, kind)`` for each ``.md`` file under ``skills`` and ``agents`` and for each
    entry the walk cannot treat as one; ``kind`` is ``file``, ``symlink`` or ``unreadable``."""
    found: list[tuple[str, str]] = []

    def rel(path: Path) -> str:
        return path.relative_to(root).as_posix()

    def unreadable(error: OSError) -> None:
        if isinstance(error, FileNotFoundError):
            return
        found.append((rel(Path(error.filename)), "unreadable"))

    for top in ("skills", "agents"):
        for directory, dirs, names in os.walk(root / top, onerror=unreadable):
            dirs.sort()
            found.extend((rel(Path(directory) / d), "symlink") for d in dirs if (Path(directory) / d).is_symlink())
            for name in sorted(names):
                full = Path(directory) / name
                if name.endswith(".md"):
                    found.append((rel(full), "symlink" if full.is_symlink() else "file"))
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


_SHARED_BRANCH_FORM = re.compile(r"\.\./_shared/[^/]+\Z")


def _mask_branch_load(lines: list[str], load) -> None:
    """Blank the characters of ``load``'s own sentence in ``lines``, leaving the text around it."""
    first, last = load.line - 1, load.end_line
    joined = "\n".join(lines[first:last])
    condition = r"\s+".join(re.escape(word) for word in load.condition.split())
    sentence = re.compile(rf"When\s+{condition}\s*,\s+read\s+`{re.escape(load.raw)}`\s+now\s+and\s+follow\s+it\s+in"
                          r"\s+full\.")
    found = sentence.search(joined)
    if found is None:
        return
    masked = joined[:found.start()] + re.sub(r"[^\n]", " ", found.group(0)) + joined[found.end():]
    lines[first:last] = masked.split("\n")


def _check_file(root: Path, read, file: str, declared: frozenset[str], faults: _Faults) -> None:
    try:
        text = _get(read, file)
    except LoadError as exc:
        faults.add(file, 1, "unreadable-file", str(exc))
        return
    if text is None:
        return
    directives = parse_directives(file, text, read)
    for item in directives.misworded:
        if all(resolve_existing(file, raw, read) in declared for raw in item.paths):
            continue
        several = len(item.paths) > 1
        shown = ", ".join(_q(p, n) for n, p in enumerate(item.paths, 1))
        faults.add(file, item.line, "load-names-several-files" if several else "misworded-load",
                   f"the directive naming {shown} {item.reason}", _USE_ANY)
    branch_file = is_branch(text)
    is_entry = Path(file).name == "SKILL.md" or file.startswith("agents/")
    branch_loads = [load for load in directives.loads if load.kind == "branch"]
    if branch_file:
        for load in branch_loads:
            faults.add(file, load.line, "branch-loads-branch",
                       f"a branch file loads {_q(load.raw, 1)}; move the load to the procedure that tests the "
                       "condition", BRANCH_LOAD)
    if not (is_entry or branch_file):
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
        elif load.kind == "branch" and not is_branch(body):
            faults.add(file, load.line, "branch-opening",
                       f"branch-loaded {_q(load.raw, number)} does not open 'Applies when'; open it with the "
                       "condition under which it applies", BRANCH_LOAD)
        elif load.kind != "branch" and is_branch(body):
            faults.add(file, load.line, "branch-loaded-unconditionally",
                       f"{_q(load.raw, number)} opens 'Applies when' but is loaded unconditionally", BRANCH_LOAD)
    if file.startswith("skills/_shared/"):
        pattern = _sibling_pattern(root, file)
        if pattern is not None:
            lines = _visible_lines(text)
            for load in directives.loads:
                if load.kind == "branch" and _SHARED_BRANCH_FORM.match(load.raw):
                    _mask_branch_load(lines, load)
            for number, line in enumerate(lines, 1):
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
        try:
            loaded = {load.target for load in parse_directives(file, text, read).loads} if text is not None else set()
            named = ({resolve_existing(file, w.rstrip("."), read) for w in _TOKEN.findall(text)}
                     if text is not None else set())
        except LoadError as exc:
            faults.add(file, 1, "unreadable-file", str(exc))
            continue
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
    try:
        declared = read_declared_mentions(root)
    except LoadError:
        declared = {}
    for file, kind in _walk(root):
        if kind == "file":
            try:
                _check_file(root, read, file, frozenset(declared.get(file, ())), faults)
            except LoadError as exc:
                faults.add(file, 1, "unreadable-file", str(exc))
        else:
            what = "is a symlink or not a regular file" if kind == "symlink" else "could not be read"
            faults.add(file, 1, "unreadable-file", f"{_q(file, 1)} {what}")
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
