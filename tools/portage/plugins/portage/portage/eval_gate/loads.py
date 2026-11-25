"""Which files a declared eval case depends on, derived from the loads in its procedure files.

A load is one sentence in a fixed wording (``parse_directives``, below): ``FULL_LOAD``, ``SECTION_LOAD`` or
``BRANCH_LOAD``. Its path resolves strictly against the loading file's own directory, with no search of any
ancestor, and may name a file of any type. A *branch file* opens (first non-blank line, after optional ``*``/``_``)
with "Applies when" or "Applies only when". A *split core* reaches at least one.

``derive``'s closure is the entry, the declared branches and every file reached from them, transitively, by a
parsed load or by a *mention*: a raw token of the text, ``[A-Za-z0-9._/-]+`` ending in an extension (a ``#anchor``
ends it), that names an existing plugin file (searched from the mentioning file's directory up through each
ancestor, wherever it sits: a link, a code span, a fence, loose prose) and that the plugin has not declared a
plain mention. The declared mentions are ``<plugin-root>/declared-mentions.json`` (``read_declared_mentions``),
a file mapping a mentioning file to the paths it names only as plain mentions; a declared mention is not
followed. A mention is followed whether or not its sentence parsed as a load, so a load phrased outside the
fixed wording over-selects and never under-selects. A mention token must end in an extension, so an
extensionless file is reachable only through a fixed-wording load. A reached file that exists but is not UTF-8
(an image, say) is a leaf: it is in the closure, so an edit to it selects the case, and it is not parsed for
further names; a symlink or a file that cannot be read is still a fault. A load that resolves to nothing is
ignored by ``derive``; ``missing_loads`` reports it. An undeclared branch is never in the closure.

A declared branch must be a parsed load of the entry or of a non-branch file in its closure, so the head
revision refuses a case whose closure only mentions it. The merge-base revision is derived with
``base_tolerant=True``, which accepts a mention too: a base still in an older wording, or with
ancestor-resolved paths, predates the strict declaration and must not fault.
An entry that is itself a branch file is refused: its core is the entry and the branch goes under ``branches``.

The reader contract, which every caller builds against: ``read(path) -> str | None`` takes a plugin-relative
posix path. ``None`` means the file does not exist. A symlink or any other non-regular file is signalled by
raising ``NotRegularFile``. Any other exception is a read failure. Both become a ``LoadError``; a failed read
is never an empty file. ``tree_reader(root)`` is the working-tree reader; a git reader raises
``NotRegularFile`` for a tree entry that is not a plain blob (mode 100644 or 100755).

The cache is held per revision by the caller: wrap that revision's reader once with ``cached(read)`` and pass
the wrapper to every ``derive`` and ``missing_loads`` call for that revision, so a file is read at most once
across every case. ``None`` and raised exceptions are remembered too. The wrapper also carries ``parses`` (the
parse of each file, keyed by its path and text) and the revision's declared-mentions list, so each revision's
files are parsed once and each revision reads its own list.

``parse_directives(path, text, read) -> Directives`` is the one definition of a load, shared with the
convention check (``convention.py``); a fixed sentence wrapped whole in bold, ``**...**``, is that sentence.

Every fault is a ``LoadError`` with a one-line message that names values only through ``mapping.echo``.
A closure holds at most ``MAX_PROCEDURE_FILES`` distinct files, the entry and the branches included. A reader may
raise a ``LoadError`` of its own (a cap, a failed read naming its revision): ``read`` lets it through unchanged.
"""

from __future__ import annotations

import errno
import json
import posixpath
import re
import stat
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .mapping import MAX_BLOB_BYTES, MAX_PROCEDURE_FILES, _q

DECLARED_MENTIONS = "declared-mentions.json"
FULL_LOAD = "Read `<path>` now and follow it in full."
SECTION_LOAD = "Read `<path>` now." + " Then a sentence naming the section to use."
BRANCH_LOAD = "When <condition>, read `<path>` now and follow it in full."

_SAFE_CHARS = re.compile(r"^[A-Za-z0-9._/-]+\Z")
_BRANCH_OPENING = re.compile(r"^applies (?:only )?when(?![A-Za-z0-9])", re.IGNORECASE)
_EXTENSION = re.compile(r"\.[A-Za-z0-9]+\Z")
_ABBREVIATION = re.compile(r"(?:^|[\s(])(?:e\.g|i\.e)$", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z0-9._/-]+")
_PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"
_TOKEN = re.compile(rf"(?:{re.escape(_PLUGIN_ROOT_VAR)})?[A-Za-z0-9._/-]+")
_LIST_OR_HEADING = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|#|\|)")
_FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})")
_LINE_END = re.compile(r"\r\n|\r|\n")
_LOAD_FULL = re.compile(r"Read `([^`]+)` now and follow it in full\.")
_LOAD_SECTION = re.compile(r"Read `([^`]+)` now\.")
_LOAD_BRANCH = re.compile(r"When (.+), read `([^`]+)` now and follow it in full\.")
_LIST_MARKER = re.compile(r"^(?:\s*(?:[-*+]|\d+[.)]|>)\s+)+")
_DIRECTIVE_VERB = re.compile(r"\b(?:read|load|follow|consult|open|see|apply|use|per)\b", re.IGNORECASE)
_LINK_END = re.compile(r"[)\s]")


class LoadError(Exception):
    """A one-line fault in a case's procedure files."""


class BinaryFile(LoadError):
    """A regular file whose bytes are not UTF-8: reached, never parsed."""


class NotRegularFile(Exception):
    """Raised by a reader for a symlink or any other non-regular file."""


@dataclass(frozen=True)
class Derived:
    files: tuple[str, ...]
    split: bool


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


def is_branch(text: str) -> bool:
    """Whether ``text`` opens a branch file."""
    for line in text.splitlines():
        if line.strip():
            return bool(_BRANCH_OPENING.match(line.lstrip(" \t*_")))
    return False


@dataclass(frozen=True)
class _Analysis:
    loads: tuple[Load, ...]
    tokens: tuple[str, ...]


def _analysis(path: str, text: str, read) -> _Analysis:
    """The parsed loads of ``text``, the file ``path``, and every distinct token of it that could name a file
    (``[A-Za-z0-9._/-]+`` ending in an extension, a trailing ``.`` dropped); remembered per revision."""
    memo = getattr(read, "parses", None)
    key = (path, text)
    if memo is not None and key in memo:
        return memo[key]
    tokens = (w.rstrip(".") for w in _TOKEN.findall(text))
    found = _Analysis(parse_directives(path, text, read).loads,
                      tuple(dict.fromkeys(t for t in tokens if _EXTENSION.search(t))))
    if memo is not None:
        memo[key] = found
    return found


def _load_targets(path: str, text: str, read) -> list[str]:
    """The plugin-relative files ``path`` loads, in order, each once; a load whose path has a character outside
    the allowlist or leaves the plugin root is a fault."""
    found: dict[str, None] = {}
    for position, load in enumerate(_analysis(path, text, read).loads, 1):
        if not _SAFE_CHARS.match(load.raw):
            raise LoadError(f"{_q(path, 1)} load #{position} has characters outside [A-Za-z0-9._/-]")
        if load.target is None:
            raise LoadError(f"{_q(path, 1)} loads {_q(load.raw, position)}, which is outside the plugin root")
        found.setdefault(load.target)
    return list(found)


def _declared(read) -> dict[str, tuple[str, ...]]:
    """The declared-mentions list of the revision ``read`` serves; a missing file is an empty list."""
    memo = getattr(read, "declared", None)
    if memo is not None and "list" in memo:
        return memo["list"]
    text = _get(read, DECLARED_MENTIONS)
    value = {} if text is None else parse_declared_mentions(text)
    if memo is not None:
        memo["list"] = value
    return value


def _leaves_root(path: str) -> bool:
    """Whether the normalised plugin-relative ``path`` climbs above the plugin root."""
    return path == ".." or path.startswith("../")


def _search_up(file: str, raw: str, exists) -> str | None:
    """The first plugin-relative path ``raw`` names from ``file`` for which ``exists(path)`` holds, trying the
    file's directory and then each ancestor up to the plugin root; ``None`` for an absolute ``raw``, one whose
    last part is empty, ``.`` or ``..``, or when no directory holds one. A ``raw`` written under
    ``${CLAUDE_PLUGIN_ROOT}`` names that path from the plugin root alone."""
    if raw.startswith(_PLUGIN_ROOT_VAR):
        rest = raw[len(_PLUGIN_ROOT_VAR):]
        if not rest.startswith("/") or posixpath.basename(rest) in ("", ".", ".."):
            return None
        candidate = posixpath.normpath(rest.lstrip("/"))
        return candidate if not _leaves_root(candidate) and exists(candidate) else None
    if raw.startswith("/") or posixpath.basename(raw) in ("", ".", ".."):
        return None
    directory = posixpath.dirname(file)
    while True:
        candidate = posixpath.normpath(posixpath.join(directory, raw))
        if not _leaves_root(candidate) and exists(candidate):
            return candidate
        parent = posixpath.dirname(directory)
        if not directory or parent == directory:
            return None
        directory = parent


def _find_mentioned(file: str, raw: str, read) -> str | None:
    """The plugin-relative file, binary included, ``raw`` names from ``file`` (see ``_search_up``). A read
    failure is a fault."""
    return _search_up(file, raw, lambda path: _exists(read, path))


def _reached(current: str, text: str, read) -> tuple[list[str], list[str]]:
    """``(loads, reached)``: the files ``current`` loads, and those plus every existing file it names in a
    token the plugin has not declared a plain mention, each once, loads first."""
    loaded = _load_targets(current, text, read)
    declared = set(_declared(read).get(current, ()))
    reached = dict.fromkeys(loaded)
    for raw in _analysis(current, text, read).tokens:
        found = _find_mentioned(current, raw, read)
        if found is not None and found != current and found not in declared:
            reached.setdefault(found)
    return loaded, list(reached)


def reaches_branch(path: str, read) -> bool:
    """Whether the file ``path`` loads or names (as ``derive`` reads names) a branch file."""
    text = _get(read, path)
    return text is not None and any(is_branch_file(t, read) for t in _reached(path, text, read)[1])


def is_branch_file(path: str, read) -> bool:
    """Whether ``path`` exists and is a branch file."""
    try:
        text = _get(read, path)
    except BinaryFile:
        return False
    return text is not None and is_branch(text)


def _exists(read, path: str) -> bool:
    """Whether ``path`` is a regular file, binary included."""
    try:
        return _get(read, path) is not None
    except BinaryFile:
        return True


def _get(read, path: str) -> str | None:
    try:
        return read(path)
    except NotRegularFile:
        raise LoadError(f"{_q(path, 1)} is a symlink or not a regular file") from None
    except LoadError:
        raise
    except UnicodeDecodeError:
        raise BinaryFile(f"{_q(path, 1)} could not be read") from None
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
    wrapper.declared = {}
    wrapper.derivations = {}
    return wrapper


def tree_reader(root: Path):
    """The working-tree reader over the plugin root ``root``."""
    root = Path(root)

    def read(path: str):
        full = root / path
        try:
            walked = root
            for part in PurePosixPath(path).parts[:-1]:
                walked = walked / part
                if walked.is_symlink():
                    raise NotRegularFile(path)
            st = full.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:
            if exc.errno == errno.ENAMETOOLONG:
                return None
            raise
        if not stat.S_ISREG(st.st_mode):
            raise NotRegularFile(path)
        if st.st_size > MAX_BLOB_BYTES:
            raise LoadError(f"{_q(path, 1)} is larger than {MAX_BLOB_BYTES} bytes")
        return full.read_text(encoding="utf-8")

    return read


def derive(entry: str, branches, read, *, base_tolerant: bool = False) -> Derived:
    """The closure of ``entry`` with the declared ``branches``; remembered per reader (see ``cached``), so a
    declaration shared by many cases is walked once. A declared branch must be a parsed load of the entry or of a
    non-branch file in its closure; ``base_tolerant`` (the merge-base revision only) also accepts one the entry
    merely mentions."""
    memo = getattr(read, "derivations", None)
    if memo is None:
        return _derive(entry, branches, read, base_tolerant)
    key = (entry, tuple(branches), base_tolerant)
    if key not in memo:
        try:
            memo[key] = (True, _derive(entry, branches, read, base_tolerant))
        except LoadError as exc:
            memo[key] = (False, exc)
    ok, value = memo[key]
    if ok:
        return value
    raise value


def _loads_in_closure(entry: str, read) -> set[str]:
    """The files loaded by ``entry`` or by any non-branch file reached from it, transitively."""
    loaded: set[str] = set()
    queue = [entry]
    queued = {entry}
    for current in queue:
        text = _get(read, current)
        if text is None:
            continue
        found, reached = _reached(current, text, read)
        loaded.update(found)
        for target in reached:
            try:
                body = _get(read, target)
            except BinaryFile:
                continue
            if body is not None and target not in queued and not is_branch(body):
                queued.add(target)
                queue.append(target)
    return loaded


def _derive(entry: str, branches, read, base_tolerant: bool) -> Derived:
    entry_text = _get(read, entry)
    if entry_text is None:
        raise LoadError(f"entry {_q(entry, 1)} does not exist")
    if is_branch(entry_text):
        raise LoadError(f"entry {_q(entry, 1)} is a branch file; declare its core as entry and the branch "
                        "under branches")
    entry_loaded, entry_reached = _reached(entry, entry_text, read)
    accepted = entry_reached if base_tolerant else entry_loaded
    declared = []
    below: set[str] | None = None
    for position, branch in enumerate(branches, 1):
        norm = posixpath.normpath(branch)
        text = _get(read, norm)
        if text is None:
            raise LoadError(f"declared branch {_q(branch, position)} does not exist")
        if norm not in accepted:
            if below is None:
                below = _loads_in_closure(entry, read)
            if norm not in below:
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
        loaded, reached = _reached(current, text, read)
        if current in declared:
            for target in loaded:
                if is_branch_file(target, read):
                    raise LoadError(f"branch {_q(current, 1)} loads the branch {_q(target, 1)}")
        for target in reached:
            try:
                body = _get(read, target)
            except BinaryFile:
                add(target)
                continue
            if body is None:
                continue
            if is_branch(body):
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
        try:
            text = _get(read, path)
        except BinaryFile:
            continue
        if text is None:
            continue
        for target in _load_targets(path, text, read):
            if not _exists(read, target):
                missing.append((path, target))
    return missing


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


def _line_at(offsets: list[int], numbers: list[int], offset: int) -> int:
    return numbers[max(bisect_right(offsets, offset) - 1, 0)]


def _link_targets(text: str):
    """``(offset, target)`` for each ``](target)`` in ``text``, a target being one or more characters that are
    neither ``)`` nor whitespace. One pass: a failed opener cannot be rescued by a later one before the character
    that stopped it."""
    at = text.find("](")
    while at != -1:
        stop = _LINK_END.search(text, at + 2)
        end = stop.start() if stop else len(text)
        if stop and stop.group() == ")" and end > at + 2:
            yield at + 2, text[at + 2:end]
            at = text.find("](", end + 1)
        else:
            at = text.find("](", max(end, at + 1))


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
    ancestor up to the plugin root; ``None`` when none holds one (a directory or a symlink is not one). This is how a
    mention is told from a path to nothing, not how a load resolves (a load resolves strictly: see ``Load.target``)."""
    return _search_up(file, raw, lambda path: _present(read, path))


def _strict_target(file: str, raw: str) -> str | None:
    if raw.startswith("/"):
        return None
    candidate = posixpath.normpath(posixpath.join(posixpath.dirname(file), raw))
    return None if _leaves_root(candidate) else candidate


def _named_files(sentence: str, file: str, read) -> list[tuple[int, str]]:
    """``(offset, path as written)`` for each backticked span or link target in ``sentence`` that names an
    existing plugin file, in order of appearance."""
    found: list[tuple[int, str]] = []
    offset = 0
    for index, part in enumerate(sentence.split("`")):
        if index % 2 == 1 and _SAFE_CHARS.match(part):
            found.append((offset, part))
        elif index % 2 == 0:
            found.extend((offset + at, target.split("#")[0]) for at, target in _link_targets(part))
        offset += len(part) + 1
    return [(at, raw) for at, raw in found if _SAFE_CHARS.match(raw) and resolve_existing(file, raw, read)]


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
        offsets = [start for start, _ in starts]
        numbers = [number for _, number in starts]
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
            line = _line_at(offsets, numbers, begin + lead)
            end_line = _line_at(offsets, numbers, max(begin + lead, end - 1))
            named = _named_files(body, path, read)
            distinct = tuple(dict.fromkeys(raw for _, raw in named))
            full = _LOAD_FULL.fullmatch(body)
            branch = _LOAD_BRANCH.fullmatch(body)
            section = _LOAD_SECTION.fullmatch(body)
            match = full or branch or section
            if match is None:
                if named and _DIRECTIVE_VERB.search(body[:named[0][0]]):
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
            and not value.startswith("/") and not _leaves_root(value))


def parse_declared_mentions(text: str) -> dict[str, tuple[str, ...]]:
    """The declared-mentions list: a JSON object mapping a mentioning file (plugin-relative posix path) to the
    plugin-relative paths it names only as plain mentions. Malformed JSON, a non-object, a value that is not a
    list, a path that is not a string or is not a safe plugin-relative path raise a one-line ``LoadError``."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
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
    return _declared(tree_reader(root))
