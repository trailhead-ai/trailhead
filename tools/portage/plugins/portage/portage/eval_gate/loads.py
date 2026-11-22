"""Which files a declared eval case depends on, derived from the loads in its procedure prose.

A load is the word ``Read`` followed by a backticked path ending ``.md``, plus every further backticked ``.md``
path in the same sentence. A sentence spans the lines of its paragraph and ends at ``.``, ``:`` or ``;``
followed by whitespace or the end of the paragraph, outside backticks (``e.g.`` and ``i.e.`` do not end one).
Fenced code blocks are skipped. Each path resolves against the loading file's directory first, then each ancestor
directory up to the plugin root, taking the first that holds a file (a path found nowhere resolves to its
loading-directory form, which ``missing_loads`` reports); it must match ``[A-Za-z0-9._/-]`` and must stay inside
the plugin root. A *branch file* opens (first non-blank line, after optional ``*``/``_``) with "Applies when" or
"Applies only when". A *split core* loads at least one.

``derive`` fails safe: its closure is the entry, the declared branches and every non-branch file reached from
them, transitively, by any backticked ``.md`` path that resolves to an existing file, whether or not its
sentence parsed as a load, so a misread load over-selects and never under-selects. An undeclared branch is
never in it. The parsed loads serve only the refusals (``LoadError``) and ``missing_loads``.

The reader contract, which every caller builds against: ``read(path) -> str | None`` takes a plugin-relative
posix path. ``None`` means the file does not exist. A symlink or any other non-regular file is signalled by
raising ``NotRegularFile``. Any other exception is a read failure. Both become a ``LoadError``; a failed read
is never an empty file. ``tree_reader(root)`` is the working-tree reader; a git reader raises
``NotRegularFile`` for a tree entry that is not a plain blob (mode 100644 or 100755).

The cache is held per revision by the caller: wrap that revision's reader once with ``cached(read)`` and pass
the wrapper to every ``derive`` and ``missing_loads`` call for that revision, so a file is read at most once
across every case. ``None`` and raised exceptions are remembered too. The wrapper also carries ``parses``, the
parse of each file text (its loads, its backticked ``.md`` mentions and whether it is a branch), so a text is
parsed once per revision; keyed by text, two revisions never share a parse.

Every fault is a ``LoadError`` with a one-line message that names values only through ``mapping.echo``.
A closure holds at most ``MAX_PROCEDURE_FILES`` distinct files, the entry and the branches included. A reader may
raise a ``LoadError`` of its own (a cap, a failed read naming its revision): ``read`` lets it through unchanged.
"""

from __future__ import annotations

import posixpath
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .mapping import MAX_PROCEDURE_FILES, _q

_SAFE_CHARS = re.compile(r"^[A-Za-z0-9._/-]+\Z")
_BRANCH_OPENING = re.compile(r"^applies (?:only )?when(?![A-Za-z0-9])", re.IGNORECASE)
_READ_BEFORE_SPAN = re.compile(r"\bRead\s+$")
_ABBREVIATION = re.compile(r"(?:^|[\s(])(?:e\.g|i\.e)$", re.IGNORECASE)
_LIST_OR_HEADING = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|#|\|)")


class LoadError(Exception):
    """A one-line fault in a case's procedure files."""


class NotRegularFile(Exception):
    """Raised by a reader for a symlink or any other non-regular file."""


@dataclass(frozen=True)
class Derived:
    files: tuple[str, ...]
    split: bool


def _paragraphs(text: str) -> list[str]:
    paragraphs: list[list[str]] = [[]]
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            paragraphs.append([])
        elif fenced:
            continue
        elif not line.strip():
            paragraphs.append([])
        else:
            if _LIST_OR_HEADING.match(line):
                paragraphs.append([])
            paragraphs[-1].append(line)
    return [" ".join(p) for p in paragraphs if p]


def _sentences(paragraph: str) -> list[str]:
    out: list[str] = []
    start = 0
    inside = False
    for i, ch in enumerate(paragraph):
        if ch == "`":
            inside = not inside
        elif ch in ".:;" and not inside and (i + 1 == len(paragraph) or paragraph[i + 1].isspace()):
            if ch == "." and _ABBREVIATION.search(paragraph[max(start, i - 4):i]):
                continue
            out.append(paragraph[start:i + 1])
            start = i + 1
    out.append(paragraph[start:])
    return out


def _spans(sentence: str) -> tuple[list[str], list[str]]:
    parts = sentence.split("`")
    return parts[0::2], parts[1::2]


def _resolve(file: str, raw: str, read) -> str | None:
    """The plugin-relative file ``raw`` names from ``file``: the loading directory's form when it exists, else
    the first ancestor directory (up to the plugin root) holding it, else the loading directory's form.
    ``None`` when the loading directory's form leaves the plugin root."""
    if raw.startswith("/"):
        return None
    directory = posixpath.dirname(file)
    first = posixpath.normpath(posixpath.join(directory, raw))
    if first == ".." or first.startswith("../"):
        return None
    while True:
        candidate = posixpath.normpath(posixpath.join(directory, raw))
        if not (candidate == ".." or candidate.startswith("../")) and _get(read, candidate) is not None:
            return candidate
        if not directory:
            return first
        directory = posixpath.dirname(directory)


@dataclass(frozen=True)
class _Parsed:
    raw_loads: tuple[str, ...]
    md_spans: tuple[str, ...]
    branch: bool


def _parse(text: str) -> _Parsed:
    raw_loads: list[str] = []
    md_spans: list[str] = []
    for paragraph in _paragraphs(text):
        for sentence in _sentences(paragraph):
            outside, inside = _spans(sentence)
            mds = [s for s in inside if s.endswith(".md")]
            md_spans.extend(mds)
            first = next((i for i, seg in enumerate(outside[:len(inside)])
                          if _READ_BEFORE_SPAN.search(seg) and inside[i].endswith(".md")), None)
            if first is not None:
                raw_loads.extend(s for s in inside[first:] if s.endswith(".md"))
    return _Parsed(tuple(raw_loads), tuple(md_spans), _is_branch(text))


def _parsed(read, text: str) -> _Parsed:
    memo = getattr(read, "parses", None)
    if memo is None:
        return _parse(text)
    if text not in memo:
        memo[text] = _parse(text)
    return memo[text]


def loads_in(path: str, text: str, read) -> list[str]:
    """The plugin-relative paths ``path`` loads, in order, each once."""
    found: list[str] = []
    for position, raw in enumerate(_parsed(read, text).raw_loads, 1):
        if not _SAFE_CHARS.match(raw):
            raise LoadError(f"{_q(path, 1)} load #{position} has characters outside [A-Za-z0-9._/-]")
        resolved = _resolve(path, raw, read)
        if resolved is None:
            raise LoadError(f"{_q(path, 1)} loads {_q(raw, position)}, which is outside the plugin root")
        if resolved not in found:
            found.append(resolved)
    return found


def _is_branch(text: str) -> bool:
    for line in text.splitlines():
        if line.strip():
            return bool(_BRANCH_OPENING.match(line.lstrip(" \t*_")))
    return False


def is_branch(text: str, read=None) -> bool:
    return _is_branch(text) if read is None else _parsed(read, text).branch


def is_branch_file(path: str, read) -> bool:
    """Whether ``path`` exists and is a branch file."""
    text = _get(read, path)
    return text is not None and is_branch(text, read)


def _get(read, path: str) -> str | None:
    try:
        return read(path)
    except NotRegularFile:
        raise LoadError(f"{_q(path, 1)} is a symlink or not a regular file") from None
    except LoadError:
        raise
    except Exception:
        raise LoadError(f"{_q(path, 1)} could not be read") from None


def cached(read):
    """``read`` remembered per path, absent files and raised exceptions included."""
    memo: dict[str, tuple[bool, object]] = {}

    def wrapper(path: str):
        if path not in memo:
            try:
                memo[path] = (True, read(path))
            except Exception as exc:
                memo[path] = (False, exc)
        ok, value = memo[path]
        if ok:
            return value
        raise value

    wrapper.parses = {}
    return wrapper


def tree_reader(root: Path):
    """The working-tree reader over the plugin root ``root``."""
    root = Path(root)

    def read(path: str):
        full = root / path
        try:
            st = full.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(st.st_mode):
            raise NotRegularFile(path)
        return full.read_text(encoding="utf-8")

    return read


def derive(entry: str, branches, read) -> Derived:
    entry_text = _get(read, entry)
    if entry_text is None:
        raise LoadError(f"entry {_q(entry, 1)} does not exist")
    entry_loads = loads_in(entry, entry_text, read)
    declared = []
    for position, branch in enumerate(branches, 1):
        norm = posixpath.normpath(branch)
        text = _get(read, norm)
        if text is None:
            raise LoadError(f"declared branch {_q(branch, position)} does not exist")
        if norm not in entry_loads:
            raise LoadError(f"entry {_q(entry, 1)} does not load the declared branch {_q(branch, position)}")
        if not is_branch(text):
            raise LoadError(f"declared branch {_q(branch, position)} does not open with 'Applies when'")
        declared.append(norm)
    split = any(is_branch_file(p, read) for p in entry_loads)

    files: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> bool:
        if path in seen:
            return False
        if len(seen) >= MAX_PROCEDURE_FILES:
            raise LoadError(f"the closure of {_q(entry, 1)} exceeds {MAX_PROCEDURE_FILES} files")
        seen.add(path)
        files.append(path)
        return True

    queue = [entry]
    add(entry)
    for branch in declared:
        add(branch)
        queue.append(branch)
    index = 0
    while index < len(queue):
        current = queue[index]
        index += 1
        text = _get(read, current)
        parsed = loads_in(current, text, read)
        if current in declared:
            for target in parsed:
                if is_branch_file(target, read):
                    raise LoadError(f"branch {_q(current, 1)} loads the branch {_q(target, 1)}")
        reached = list(parsed)
        for raw in _parsed(read, text).md_spans:
            if _SAFE_CHARS.match(raw) and (resolved := _resolve(current, raw, read)) is not None:
                reached.append(resolved)
        for target in reached:
            if target in seen:
                continue
            body = _get(read, target)
            if body is None or is_branch(body, read):
                continue
            add(target)
            queue.append(target)
    return Derived(tuple(files), split)


def missing_loads(files, read) -> list[tuple[str, str]]:
    """``(loading file, missing file)`` for every parsed load that names a file that does not exist."""
    missing = []
    for path in files:
        text = _get(read, path)
        if text is None:
            continue
        for target in loads_in(path, text, read):
            if _get(read, target) is None:
                missing.append((path, target))
    return missing
