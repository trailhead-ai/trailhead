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
them, transitively, by any ``[A-Za-z0-9._/-]+.md`` token in the raw text (a ``#anchor`` ends the token) that
resolves to an existing file, wherever it sits (a link, a code span of any kind, a fence, loose prose) and
whether or not its sentence parsed as a load, so a misread load over-selects and never under-selects. A token
that resolves to nothing or escapes the plugin root is ignored. An undeclared branch is never in the closure.
The parsed loads serve only the refusals (``LoadError``) and ``missing_loads``. An entry that is itself a
branch file is refused: its core is the entry and the branch goes under ``branches``.

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

``parse_directives(path, text, read) -> Directives`` is the one definition of a load the convention check
(``convention.py``) enforces (a fixed sentence wrapped whole in bold, ``**...**``, is that sentence); the gate's own ``derive`` still reads loads with the parser above until it switches.
``read_declared_mentions(root)`` reads ``<root>/declared-mentions.json``, the per-plugin list of paths a file
names only as plain mentions.

Every fault is a ``LoadError`` with a one-line message that names values only through ``mapping.echo``.
A closure holds at most ``MAX_PROCEDURE_FILES`` distinct files, the entry and the branches included. A reader may
raise a ``LoadError`` of its own (a cap, a failed read naming its revision): ``read`` lets it through unchanged.
"""

from __future__ import annotations

import json
import posixpath
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .mapping import MAX_BLOB_BYTES, MAX_PROCEDURE_FILES, _q

_SAFE_CHARS = re.compile(r"^[A-Za-z0-9._/-]+\Z")
_BRANCH_OPENING = re.compile(r"^applies (?:only )?when(?![A-Za-z0-9])", re.IGNORECASE)
_READ_BEFORE_SPAN = re.compile(r"\bRead\s+$")
_ABBREVIATION = re.compile(r"(?:^|[\s(])(?:e\.g|i\.e)$", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z0-9._/-]+")
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


def _sentence_spans(paragraph: str, bold: bool = False) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = 0
    inside = False
    for i, ch in enumerate(paragraph):
        if ch == "`":
            inside = not inside
        elif ch in ".:;" and not inside:
            stop = i + 1
            if bold and paragraph.startswith("**", stop):
                stop += 2
            if not (stop == len(paragraph) or paragraph[stop].isspace()):
                continue
            if ch == "." and _ABBREVIATION.search(paragraph[max(start, i - 4):i]):
                continue
            out.append((start, stop))
            start = stop
    out.append((start, len(paragraph)))
    return out


def _sentences(paragraph: str) -> list[str]:
    return [paragraph[start:end] for start, end in _sentence_spans(paragraph)]


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
    md_tokens: tuple[str, ...]
    branch: bool


def _parse(text: str) -> _Parsed:
    raw_loads: list[str] = []
    for paragraph in _paragraphs(text):
        for sentence in _sentences(paragraph):
            outside, inside = _spans(sentence)
            first = next((i for i, seg in enumerate(outside[:len(inside)])
                          if _READ_BEFORE_SPAN.search(seg) and inside[i].endswith(".md")), None)
            if first is not None:
                raw_loads.extend(s for s in inside[first:] if s.endswith(".md"))
    tokens = (w.rstrip(".") for w in _WORD.findall(text))
    return _Parsed(tuple(raw_loads), tuple(dict.fromkeys(t for t in tokens if t.endswith(".md"))),
                   _is_branch(text))


def _parsed(read, text: str) -> _Parsed:
    memo = getattr(read, "parses", None)
    if memo is None:
        return _parse(text)
    if text not in memo:
        memo[text] = _parse(text)
    return memo[text]


def loads_in(path: str, text: str, read) -> list[str]:
    """The plugin-relative paths ``path`` loads, in order, each once."""
    found: dict[str, None] = {}
    for position, raw in enumerate(_parsed(read, text).raw_loads, 1):
        if not _SAFE_CHARS.match(raw):
            raise LoadError(f"{_q(path, 1)} load #{position} has characters outside [A-Za-z0-9._/-]")
        resolved = _resolve(path, raw, read)
        if resolved is None:
            raise LoadError(f"{_q(path, 1)} loads {_q(raw, position)}, which is outside the plugin root")
        found.setdefault(resolved)
    return list(found)


def _is_branch(text: str) -> bool:
    for line in text.splitlines():
        if line.strip():
            return bool(_BRANCH_OPENING.match(line.lstrip(" \t*_")))
    return False


def _reached(current: str, text: str, read) -> list[str]:
    """Every file ``current`` names: its parsed loads, then every ``.md`` token that resolves."""
    reached = loads_in(current, text, read)
    for raw in _parsed(read, text).md_tokens:
        if _SAFE_CHARS.match(raw) and (resolved := _resolve(current, raw, read)) is not None:
            reached.append(resolved)
    return reached


def reaches_branch(path: str, read) -> bool:
    """Whether the file ``path`` loads or names (a ``.md`` token that resolves) a branch file."""
    text = _get(read, path)
    return text is not None and any(is_branch_file(t, read) for t in _reached(path, text, read))


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
    wrapper.derivations = {}
    return wrapper


def tree_reader(root: Path):
    """The working-tree reader over the plugin root ``root``."""
    root = Path(root)

    def read(path: str):
        full = root / path
        try:
            if any(parent.is_symlink() for parent in full.parents if parent != root and root in parent.parents):
                raise NotRegularFile(path)
            st = full.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(st.st_mode):
            raise NotRegularFile(path)
        if st.st_size > MAX_BLOB_BYTES:
            raise LoadError(f"{_q(path, 1)} is larger than {MAX_BLOB_BYTES} bytes")
        return full.read_text(encoding="utf-8")

    return read


def derive(entry: str, branches, read) -> Derived:
    """The closure of ``entry`` with the declared ``branches``; remembered per reader (see ``cached``), so a
    declaration shared by many cases is walked once."""
    memo = getattr(read, "derivations", None)
    if memo is None:
        return _derive(entry, branches, read)
    key = (entry, tuple(branches))
    if key not in memo:
        try:
            memo[key] = (True, _derive(entry, branches, read))
        except LoadError as exc:
            memo[key] = (False, exc)
    ok, value = memo[key]
    if ok:
        return value
    raise value


def _derive(entry: str, branches, read) -> Derived:
    entry_text = _get(read, entry)
    if entry_text is None:
        raise LoadError(f"entry {_q(entry, 1)} does not exist")
    if is_branch(entry_text, read):
        raise LoadError(f"entry {_q(entry, 1)} is a branch file; declare its core as entry and the branch "
                        "under branches")
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
    split = False

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
        if text is None:
            raise LoadError(f"{_q(current, 1)} does not exist")
        parsed = loads_in(current, text, read)
        if current in declared:
            for target in parsed:
                if is_branch_file(target, read):
                    raise LoadError(f"branch {_q(current, 1)} loads the branch {_q(target, 1)}")
        for target in _reached(current, text, read):
            body = _get(read, target)
            if body is None:
                continue
            if is_branch(body, read):
                split = True
                continue
            if target in seen:
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


DECLARED_MENTIONS = "declared-mentions.json"
FULL_LOAD = "Read `<path>` now and follow it in full."
SECTION_LOAD = "Read `<path>` now." + " Then a sentence naming the section to use."
BRANCH_LOAD = "When <condition>, read `<path>` now and follow it in full."

_FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})")
_LINE_END = re.compile(r"\r\n|\r|\n")
_LOAD_FULL = re.compile(r"Read `([^`]+)` now and follow it in full\.")
_LOAD_SECTION = re.compile(r"Read `([^`]+)` now\.")
_LOAD_BRANCH = re.compile(r"When (.+), read `([^`]+)` now and follow it in full\.")
_LIST_MARKER = re.compile(r"^(?:\s*(?:[-*+]|\d+[.)]|>)\s+)+")
_DIRECTIVE_VERB = re.compile(r"\b(?:read|load|follow|consult|open|see|apply|use|per)\b", re.IGNORECASE)
_LINK_TARGET = re.compile(r"\]\(([^)\s]+)\)")
_PATHISH = re.compile(r"^[A-Za-z0-9._/-]+\Z")


@dataclass(frozen=True)
class Load:
    """A load in the fixed wording. ``kind`` is ``full``, ``section`` or ``branch``; ``raw`` is the path as
    written; ``target`` is the plugin-relative file it names, resolved strictly against the loading file's own
    directory (``None`` when ``raw`` is absolute or leaves the plugin root); ``condition`` is the text between
    ``When`` and the comma of a branch load, else empty. ``line`` and ``end_line`` are the 1-based lines the
    sentence starts and ends on."""
    line: int
    end_line: int
    kind: str
    raw: str
    target: str | None
    condition: str


@dataclass(frozen=True)
class Misworded:
    """A sentence whose directive verb names an existing plugin file outside the fixed wording, or a load that
    names several files, or a section read with no following sentence. ``paths`` are the paths as written."""
    line: int
    paths: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Directives:
    loads: tuple[Load, ...]
    misworded: tuple[Misworded, ...]


def _visible_lines(text: str) -> list[str]:
    """``text`` split on CommonMark line endings, a line inside a fenced code block or an HTML comment blanked
    so the line numbers survive. A fence closes on a same-character marker at least as long as the one that
    opened it; an unterminated fence or comment runs to the end of the text."""
    out: list[str] = []
    fence: tuple[str, int] | None = None
    in_comment = False
    for line in _LINE_END.split(text):
        stripped = line.strip()
        if in_comment:
            in_comment = "-->" not in line
            out.append("")
        elif fence is not None:
            out.append("")
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= fence[1]:
                fence = None
        elif stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
            out.append("")
        elif opened := _FENCE_OPEN.match(stripped):
            fence = (opened.group(1)[0], len(opened.group(1)))
            out.append("")
        else:
            out.append(line)
    return out


def _located_paragraphs(lines: list[str]) -> list[tuple[str, list[tuple[int, int]]]]:
    """Each paragraph as its joined text and ``(offset, line number)`` for each of its lines."""
    blocks: list[list[tuple[int, str]]] = [[]]
    for number, line in enumerate(lines, 1):
        if not line.strip():
            blocks.append([])
            continue
        if _LIST_OR_HEADING.match(line):
            blocks.append([])
        blocks[-1].append((number, line))
    out = []
    for block in blocks:
        if not block:
            continue
        starts, offset = [], 0
        joined = []
        for number, line in block:
            starts.append((offset, number))
            joined.append(line.lstrip() if joined else line)
            offset += len(joined[-1]) + 1
        out.append((" ".join(joined), starts))
    return out


def _line_at(starts: list[tuple[int, int]], offset: int) -> int:
    line = starts[0][1]
    for start, number in starts:
        if start > offset:
            break
        line = number
    return line


def _present(read, path: str) -> bool:
    try:
        return read(path) is not None
    except NotRegularFile:
        return False
    except LoadError:
        raise
    except Exception:
        return False


def resolve_existing(file: str, raw: str, read) -> str | None:
    """The plugin-relative regular file ``raw`` names from ``file``, looking in the loading directory and then each
    ancestor up to the plugin root; ``None`` when none holds one (a directory or a symlink is not one). This is how a mention is told from a
    path to nothing, not how a load resolves (a load resolves strictly: see ``Load.target``)."""
    if raw.startswith("/") or posixpath.basename(raw) in ("", ".", ".."):
        return None
    directory = posixpath.dirname(file)
    while True:
        candidate = posixpath.normpath(posixpath.join(directory, raw))
        if not (candidate == ".." or candidate.startswith("../")) and _present(read, candidate):
            return candidate
        if not directory:
            return None
        directory = posixpath.dirname(directory)


def _strict_target(file: str, raw: str) -> str | None:
    if raw.startswith("/"):
        return None
    candidate = posixpath.normpath(posixpath.join(posixpath.dirname(file), raw))
    return None if candidate == ".." or candidate.startswith("../") else candidate


def _named_files(sentence: str, file: str, read) -> list[tuple[int, str]]:
    """``(offset, path as written)`` for each backticked span or link target in ``sentence`` that names an
    existing plugin file, in order of appearance."""
    found: list[tuple[int, str]] = []
    offset = 0
    for index, part in enumerate(sentence.split("`")):
        if index % 2 == 1 and _PATHISH.match(part):
            found.append((offset, part))
        elif index % 2 == 0:
            found.extend((offset + m.start(1), m.group(1).split("#")[0]) for m in _LINK_TARGET.finditer(part))
        offset += len(part) + 1
    return [(at, raw) for at, raw in found if _PATHISH.match(raw) and resolve_existing(file, raw, read)]


def parse_directives(path: str, text: str, read) -> Directives:
    """Every load in ``text``, the file ``path``, and every sentence that tries to be one and is not.

    A load is one sentence in exactly one of the three fixed forms, naming one file: ``FULL_LOAD``,
    ``SECTION_LOAD`` (the section read, which must be followed by a sentence in its own paragraph) or
    ``BRANCH_LOAD``. A sentence is a mis-worded directive when a directive verb (read, load, follow, consult,
    open, see, apply, use, per) comes before a backticked or linked path to an existing plugin file and the
    sentence is not a load, or when a load sentence names more than one existing file. Fenced code blocks and
    HTML comments are invisible. ``read`` is the reader contract of this module and is used only to tell
    whether a named path is an existing file."""
    loads: list[Load] = []
    misworded: list[Misworded] = []
    for paragraph, starts in _located_paragraphs(_visible_lines(text)):
        spans = _sentence_spans(paragraph, bold=True)
        for index, (begin, end) in enumerate(spans):
            raw_sentence = paragraph[begin:end]
            if not raw_sentence.strip():
                continue
            lead = len(raw_sentence) - len(raw_sentence.lstrip())
            marker = _LIST_MARKER.match(raw_sentence)
            body = raw_sentence[marker.end():].strip() if marker else raw_sentence.strip()
            if body.startswith("**") and body.endswith("**") and body.count("**") == 2:
                body = body[2:-2].strip()
            line = _line_at(starts, begin + lead)
            end_line = _line_at(starts, max(begin + lead, end - 1))
            named = _named_files(body, path, read)
            distinct = tuple(dict.fromkeys(raw for _, raw in named))
            full = _LOAD_FULL.fullmatch(body)
            branch = _LOAD_BRANCH.fullmatch(body)
            section = _LOAD_SECTION.fullmatch(body)
            match = full or branch or section
            if match is None:
                if named and _DIRECTIVE_VERB.search(body[:body.index(named[0][1])]):
                    reason = "names several files" if len(distinct) > 1 else "is not in the fixed wording"
                    misworded.append(Misworded(line, distinct, reason))
                continue
            raw = match.group(match.lastindex)
            if len(distinct) > 1:
                misworded.append(Misworded(line, distinct, "names several files"))
            elif section and not any(spans_text.strip() for spans_text in
                                     (paragraph[b:e] for b, e in spans[index + 1:])):
                misworded.append(Misworded(line, (raw,), "is a section read with no sentence naming the section"))
            else:
                kind = "full" if full else "branch" if branch else "section"
                loads.append(Load(line, end_line, kind, raw, _strict_target(path, raw),
                                  branch.group(1) if branch else ""))
    return Directives(tuple(loads), tuple(misworded))


def _declared_fault(detail: str) -> LoadError:
    return LoadError(f"{DECLARED_MENTIONS} {detail}")


def _is_plugin_path(value: object) -> bool:
    return (isinstance(value, str) and bool(_SAFE_CHARS.match(value)) and posixpath.normpath(value) == value
            and not value.startswith("/") and value != ".." and not value.startswith("../"))


def parse_declared_mentions(text: str) -> dict[str, tuple[str, ...]]:
    """The declared-mentions list: a JSON object mapping a mentioning file (plugin-relative posix path) to the
    plugin-relative paths it names only as plain mentions. Malformed JSON, a non-object, a value that is not a
    list, a path that is not a string or is not a safe plugin-relative path raise a one-line ``LoadError``."""
    try:
        data = json.loads(text)
    except ValueError:
        raise _declared_fault("is not valid JSON") from None
    if not isinstance(data, dict):
        raise _declared_fault("must be a JSON object mapping a file to the paths it only mentions")
    out: dict[str, tuple[str, ...]] = {}
    for position, (file, paths) in enumerate(data.items(), 1):
        if not _is_plugin_path(file):
            raise _declared_fault(f"file {_q(file, position)} is not a safe plugin-relative path")
        if not isinstance(paths, list):
            raise _declared_fault(f"entry for file {_q(file, position)} must map to a list of paths")
        for number, value in enumerate(paths, 1):
            if not _is_plugin_path(value):
                raise _declared_fault(f"entry for file {_q(file, position)} has path {_q(value, number)} "
                                      "that is not a safe plugin-relative path")
        out[file] = tuple(paths)
    return out


def read_declared_mentions(root: Path) -> dict[str, tuple[str, ...]]:
    """``parse_declared_mentions`` of ``<root>/declared-mentions.json``; a missing file is an empty list."""
    text = _get(tree_reader(root), DECLARED_MENTIONS)
    return {} if text is None else parse_declared_mentions(text)
