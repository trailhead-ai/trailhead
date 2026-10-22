#!/usr/bin/env python3
"""Build a scratch pad: one or more rounds of layout and flow sketches.

Given ``--pad <slug>`` and ``--round <path-to-json>`` (and ``--add`` for a later round of an
existing pad), this checks the round's
outer shape and builds the pad: a copy of the skill's ``kit/`` plus the round's
data. Inside a camp workspace the pad is published as a site of that workspace
and the publisher's link is printed; outside one it is built as plain files
under Outpost's state directory and the ``file://`` link to its top-level
``index.html`` is printed, so it opens straight from disk with nothing served.
Either way the pad's folder path follows.

Contract
--------
* The round is one JSON object: ``title`` (required, non-empty), ``intro``
  (optional string), ``settled`` (optional list of non-empty strings: what
  earlier rounds settled; an empty list is the same as leaving it out) and
  ``questions`` (at least one). Each question has a
  non-empty ``prompt`` and 1-26 ``options``; each option has a non-empty
  ``name`` and ``cost``, exactly one of a ``layout`` key or a ``flow`` string, and an optional boolean
  ``pick``.
  Exactly one option per question carries ``"pick": true``. An unknown key is
  refused with the allowed keys listed. What is inside ``layout`` is never
  inspected here, and a ``flow`` is only checked to be a string (an empty one is left for the pen to
  judge); its text is written into ``round.js`` untouched.
* ``enclosing_workspace(cwd, env)`` names the camp workspace a directory sits
  in: ``cwd`` resolved, taken relative to camp's state root (resolved; that is
  ``$CAMP_STATE_DIR`` verbatim, else ``$XDG_STATE_HOME/camp``, else
  ``~/.local/state/camp``). When its parts are ``<group>/worktrees/<slug>/...``
  at any depth the result is ``<state root>/<group>/worktrees/<slug>``;
  otherwise ``None``. A working directory that cannot be read counts as outside
  any workspace, so the pad builds on disk.
* Inside a camp workspace (``enclosing_workspace`` of the working directory),
  the pad is not built under Outpost's state directory. The round and pad
  name are checked as above; a pad name with anything at
  ``<workspace>/sites/<pad>`` is refused as "already exists"; the pad (same
  layout) is built in a fresh owner-only ``scratch-pad-`` temp folder outside
  the workspace and published by the sibling ``publish-site/publish_site.py``,
  run as ``<python> <publish_site.py> <tmp> <pad> --workspace-path <ws>``
  under a wall-clock timeout. On success stdout is the publisher's stdout
  verbatim, then ``pad folder: <ws>/sites/<pad>``. A non-zero publisher exit is
  refused with each of its stderr lines as ``scratch-pad: publish: <line>``;
  output bytes that are not valid in the locale encoding are shown as
  backslash escapes rather than failing. A publisher that cannot be started
  is refused as ``scratch-pad: publish: could not start the publisher: <why>``. On
  timeout the publisher is killed and the outcome is read from disk: a
  ``<ws>/sites/<pad>/index.html`` gives a ``note:`` that the pad was published
  but not confirmed (exit 0), otherwise a refusal. The temp folder is removed
  on every path, and nothing is created under Outpost's pads folder. With no
  ``publish-site`` skill beside this one, a run inside a workspace is refused.
  The pad's lock and recovery (below) are taken on this path too, keyed on
  ``<ws>/sites/<pad>``, so a new pad and an add on one name never interleave.
* ``--add`` inside a camp workspace runs the same add routine as on disk (see below) against
  ``<ws>/sites/<pad>``, from any depth of the workspace. Only two steps differ: the new pad is built in a
  fresh owner-only ``scratch-pad-`` temp folder outside the workspace, and it is put in place by running
  the publisher with ``--overwrite`` (the publisher replaces the site wholesale, so the temp folder holds
  every earlier round). ``--overwrite`` is passed only for an add. The publisher's stdout is relayed
  verbatim, then ``pad folder:``; a non-zero exit is refused as ``scratch-pad: publish: <stderr line>``
  per line; one that cannot be started as ``scratch-pad: publish: could not start the publisher: <why>``.
  On timeout the publisher is killed and waited for while the pad's lock is still held, then the site is
  read back as for any add: a pad that reads back whole, with top-level ``index.html`` and ``rounds.js``,
  and exactly the new pad's rounds gives the same ``note:`` (exit 0); otherwise a refusal ends
  ``scratch-pad: publish: the publisher did not finish within <n> seconds and`` ``the round was not added``
  (site unchanged), ``the pad is no longer there`` or ``the pad is not as expected``, or the read-back's
  own "can't take another round" refusal. When the pad is no longer there and the publisher's
  ``.<pad>.previous-*`` entry sits beside it in ``sites/`` (it renames the old site there while swapping),
  the refusal adds a line naming the newest such entry (by modification time) as the pad as it was before
  the add, to be renamed back, and, when there are others, one line listing them as older leftovers that
  may be partial; the script never renames any of them itself. The temp folder is removed on every path.
* The pad slug matches ``^[a-z0-9][a-z0-9._-]*$`` and is at most 64 characters.
* Outside a workspace, pads live in ``<state>/pads/<slug>/`` where ``<state>`` is
  ``$OUTPOST_STATE_DIR``, else ``$XDG_STATE_HOME/outpost``, else
  ``~/.local/state/outpost``. Without ``--add`` a pad folder that already exists is refused.
* ``--add`` adds the round to the existing pad, in this order: the round is checked as above; the pad's
  lock is taken; any interrupted earlier run is recovered from; the pad is read back; the whole new pad
  is built in a ``.tmp-`` sibling; the temp copy is checked again; the new pad is put in place. A pad
  that does not exist is refused as "does not exist — drop --add to start a new pad", and a round
  whose title equals an earlier round's as "give each round its own title".
* The pad's lock is an exclusive ``flock`` on
  ``<outpost state>/pad-locks/<sha256 of the pad's resolved path>.lock``. ``pad-locks`` is created
  owner-only and refused if it is a symlink; the lock file is opened ``O_NOFOLLOW | O_CREAT`` with
  mode 0600 and is left in place. Building a new pad, on disk or in a workspace, takes the same lock. A run
  that must wait prints one "waiting for another session's add" line on stderr, and after twice the
  publish timeout is refused with "another session is adding a round to pad ...; try again", changing nothing.
* Recovery, under the lock on every run on a pad name (new pad or add): with no pad and a
  ``.aside-<pad>`` folder, that folder is renamed back; with both, ``.aside-<pad>`` is removed; leftover
  ``.tmp-<pad>-`` folders are removed (a folder of another pad whose name merely starts with this
  one's is left alone).
* Read-back: the pad is a real folder with no symlink or special file anywhere in it, its round folders are
  ``round-1`` .. ``round-<n>`` with no gap, and each holds an ``index.html`` and a ``round.js`` whose first
  line starts with the prefix this script writes. The pad's only other top-level entries are ``index.html``
  and ``rounds.js`` (optional: a pad built before it existed lacks it), plus the operating system's own
  files ``.DS_Store`` and ``._*``, which are ignored and not carried into the rebuilt pad (they are still
  refused when not regular files). Any other entry, such as a file added by hand or a ``round-01`` folder,
  would be lost when the pad is rebuilt, so it is named as not part of a pad this script builds and the
  pad is refused. A folder that cannot be listed, or a file in it that cannot be examined, is named
  ("<path> can't be read: <why>"); the pad folder itself as "the pad folder can't be read: <why>", with
  no round checks. A round's title is the ``json.loads`` of that line's
  string literal, decoded twice; the literal is never evaluated and ``rounds.js`` is never read. Every
  problem is named in one refusal ending "-- start a new pad with a different --pad name"; when the only
  problems are stray top-level entries, which can be moved aside, it ends "-- move it out of the pad
  folder and rerun --add".
* The new pad holds each earlier round copied byte for byte (a symlink is copied as a link, never
  followed; a special file is refused unopened), ``round-<n+1>/`` built from the current kit with
  ``PAD_ROUND_NUMBER = n+1``, a rebuilt ``rounds.js`` and an ``index.html`` that sends the browser to
  ``round-<n+1>/``. The temp copy is checked again for symlinks and special files. It is then put in
  place by renaming the old pad to ``.aside-<pad>``, renaming the new one in, and removing the aside copy;
  any ``BaseException`` between the two renames renames the old pad back.
* The pad is built in a sibling ``.tmp-`` folder and renamed into place, so a
  failure leaves neither a pad nor a temp folder. Layout::

      <pad>/index.html            sends the browser to the latest round's index.html
      <pad>/round-1/<kit files>
      <pad>/round-1/round.js      ``window.PAD_ROUND = JSON.parse(<s>);``
                                  where ``<s>`` is the round's ASCII JSON
                                  encoded once more as a string literal
                                  then ``window.PAD_ROUND_NUMBER = 1;``
                                  then ``window.PAD_ROUND_ID = "<32 hex>";``
                                  a fresh ``secrets.token_hex(16)`` per round
                                  built; an earlier round keeps its own id,
                                  and a round with no such line still reads back
      <pad>/rounds.js             ``window.PAD_ROUNDS = JSON.parse(<s>);`` where
                                  ``<s>`` is the ASCII JSON list of one
                                  ``{"title": ...}`` per round, encoded the same way

* ``read_reference(reference_path, pad, ...)`` reads drawings back for a reference page; it builds and changes
  nothing and has no command-line wiring. The reference is one JSON object: ``site`` (a slug as ``--pad``),
  ``title`` (non-empty) and ``drawings`` (at least one ``{round, question, option}``: whole numbers from 1 and
  a capital letter; an unknown key is refused, the same drawing twice is refused). The pad is found where
  ``--add`` finds it, read under its lock through the add's read-back (refusals worded for a reference, not an
  add), each named round's data decoded from ``round.js`` as data, and the lock released before it returns.
  It returns a ``Reference(site, title, drawings)`` whose drawings are ``ChosenDrawing(round, question, letter,
  round_title, prompt, option)`` in the order named, or raises :class:`Refusal` with every problem found.
  Before it returns, every string bound for the page (title, site, each round title, prompt, name, cost, flow
  and every string inside a layout, dictionary keys included) is matched against the credential-pattern
  classes in ``_CREDENTIAL_PATTERNS`` (mirroring craft's credential-pattern scrub list); a match refuses the
  reference naming the drawing and field, never the matched text.
* ``--pad <slug> --reference <json> --vault-path <p> [--vault <n>]`` publishes a reference page to a lore vault,
  from inside a camp workspace or outside one. ``--reference`` cannot be used with ``--round`` or ``--add``, and
  needs ``--vault-path``; ``--vault-path`` and ``--vault`` are only valid with ``--reference``; without
  ``--reference``, ``--pad`` and ``--round`` stay required (``--pad`` is checked first). The reference is read as
  above (the pad's lock is released before anything is published). A ``<vault>/sites/<site>`` that already holds
  anything, a dangling symlink included, is refused as ``scratch-pad: site "<site>": already exists in the vault
  — choose another site name``, before the publisher runs. The page is built by ``build_reference_page`` in a
  fresh owner-only ``scratch-pad-reference-`` temp folder outside the vault and published by the sibling
  ``publish-site/publish_site.py``, run as ``<python> <publish_site.py> <tmp> <site> --vault-path <p>`` (plus
  ``--vault <n>`` when given), never with ``--overwrite`` and with no timer: the publisher's sync may wait on a
  lock or a signing prompt for as long as it takes. Just before the publisher starts, stdout gets
  ``PUBLISHING_LINE`` (and is flushed); the publisher's stdout (the sync's own lines, then the link
  ``http://127.0.0.1:<port>/<vault>/<site>/``, last) is passed through as it is written, not captured. Its stderr
  is captured: on success it is shown on stderr unchanged; on a non-zero exit each non-blank line is refused as
  ``scratch-pad: publish: <line>`` (escaped as on the workspace path; a bare ``the publisher exited with status
  <n>`` when it said nothing) and nothing more is printed, so a failed sync prints no link and leaves the page in
  ``<vault>/sites/<site>`` as the publisher leaves it. A publisher that cannot be started is refused as
  ``scratch-pad: publish: could not start the publisher: <why>``. The temp folder is removed on every path,
  exceptions included. The pad is never changed.
* Every refusal prints ``scratch-pad: <where>: <what>`` lines on stderr -- every
  problem found in one pass -- and exits non-zero. No pad and no temp folder
  are left behind; a pads folder this run had to create may remain, empty.

Security posture
----------------
Round text is untrusted data. It is written only into ``round.js`` and ``rounds.js``, as a
JSON string literal holding the round's JSON (so a ``__proto__`` key arrives as
data, which an object literal would not give) with every non-ASCII character
escaped, and never into HTML. The top-level ``index.html`` carries no round
text. Round text echoed into an error line is escaped so an escape sequence
cannot drive the terminal. The pads folder and the pad's temp folder
(which becomes the pad folder) are created owner-only (0700); ``round-1/`` and
its files follow the umask, shielded by that 0700 parent. A pads root that is a symlink is refused. A first run that
loses the race to create the pads root to a concurrent run inspects what that
run left with the same checks. Input that cannot be read as a round (not UTF-8,
nested deeper than the interpreter will parse) is a refusal, never a traceback.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

DEFAULT_KIT_DIR = Path(__file__).resolve().parent / "kit"
DEFAULT_REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
PUBLISHER_PATH = Path(__file__).resolve().parent.parent / "publish-site" / "publish_site.py"
PUBLISH_TIMEOUT_SECONDS = 60.0

_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]*")
_MAX_SLUG = 64
_MAX_OPTIONS = 26
_ROUND_KEYS = ("title", "intro", "questions", "settled")
_QUESTION_KEYS = ("prompt", "options")
_OPTION_KEYS = ("name", "cost", "pick", "layout", "flow")
_ROUND_JS_PREFIX = "window.PAD_ROUND = JSON.parse("
_ROUNDS_JS_PREFIX = "window.PAD_ROUNDS = JSON.parse("
_REFERENCE_JS_PREFIX = "window.PAD_REFERENCE = JSON.parse("
_REFERENCE_KIT_FILES = ("pen.js", "flow.js", "pad.css", "ArchitectsDaughter-Regular.ttf", "OFL.txt")
_ROUND_DIR = re.compile(r"round-([1-9][0-9]*)")
_PAD_FILES = ("index.html", "rounds.js")
_LOCK_POLL_SECONDS = 0.05
_WAY_ON = "start a new pad with a different --pad name"
_MOVE_OUT = "move it out of the pad folder and rerun --add"
_OS_METADATA = re.compile(r"\.DS_Store|\._.*", re.DOTALL)
_UNCONFIRMED = "note: the pad was published but the publisher did not confirm it in time"
PUBLISHING_LINE = "publishing to the vault; the sync can wait on a lock or a signing prompt"


def _pad_index(number: int) -> str:
    target = f"round-{number}/index.html"
    return (
        "<!doctype html>\n"
        '<meta charset="utf-8">\n'
        f'<meta http-equiv="refresh" content="0; url={target}">\n'
        "<title>Scratch pad</title>\n"
        f'<a href="{target}">Open the pad</a>\n'
    )


class Refusal(Exception):
    """The run is refused; ``lines`` are the ``scratch-pad:`` lines to print."""

    def __init__(self, lines: Sequence[str]):
        super().__init__("\n".join(lines))
        self.lines = list(lines)


def _line(where: str, what: str) -> str:
    return f"scratch-pad: {where}: {what}"


def _q(text: object) -> str:
    """Quote *text* for an error line with control characters escaped."""
    body = ascii(str(text))
    quote, body = body[0], body[1:-1]
    if quote == "'":
        body = body.replace("\\'", "'").replace('"', '\\"')
    return f'"{body}"'


def _outpost_state_dir(env: Mapping[str, str]) -> Path:
    """Mirror of ``trailhead.paths.state_dir("outpost", env=env)``."""
    override = env.get("OUTPOST_STATE_DIR")
    if override:
        return Path(override)
    base = env.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "outpost"
    home = env.get("HOME") or str(Path.home())
    return Path(home) / ".local" / "state" / "outpost"


def _camp_state_root(env: Mapping[str, str]) -> Path:
    """Mirror of ``trailhead.paths.state_dir("camp", env=env)``."""
    override = env.get("CAMP_STATE_DIR")
    if override:
        return Path(override)
    base = env.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "camp"
    home = env.get("HOME") or str(Path.home())
    return Path(home) / ".local" / "state" / "camp"


def enclosing_workspace(cwd: Path, env: Mapping[str, str]) -> Path | None:
    """The camp workspace *cwd* sits inside, or ``None``."""
    root = _camp_state_root(env).resolve()
    try:
        parts = Path(cwd).resolve().relative_to(root).parts
    except ValueError:
        return None
    if len(parts) >= 3 and parts[1] == "worktrees":
        return root / parts[0] / "worktrees" / parts[2]
    return None


def _pads_root(env: Mapping[str, str]) -> Path:
    return _outpost_state_dir(env).absolute() / "pads"


def _reject_constant(name: str):
    raise ValueError(f"{name} is not valid JSON")


def _read_round(round_path: Path, what: str = "round") -> object:
    where = _q(str(round_path))
    try:
        raw = round_path.read_bytes()
    except OSError as exc:
        raise Refusal([_line(where, f"cannot read the {what}: {exc.strerror or exc}")])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refusal([_line(where, f"not valid UTF-8 (bad byte at offset {exc.start})")])
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise Refusal([_line(where, f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")])
    except ValueError as exc:
        raise Refusal([_line(where, f"invalid JSON: {exc}")])
    except RecursionError:
        raise Refusal([_line(where, "nested too deeply to read")])


def _unknown_keys(obj: dict, allowed: tuple[str, ...], where: str, problems: list[str]) -> None:
    for key in obj:
        if key not in allowed:
            problems.append(_line(where, f"unknown key {_q(key)} — allowed: {', '.join(allowed)}"))


def check_round(data: object) -> list[str]:
    """Return a ``scratch-pad:`` line for every outer-shape problem in *data*."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return [_line("round", "must be a JSON object")]
    _unknown_keys(data, _ROUND_KEYS, "round", problems)
    if "title" not in data:
        problems.append(_line("title", 'no "title" — name the round'))
    elif not isinstance(data["title"], str):
        problems.append(_line("title", "must be a string"))
    elif not data["title"].strip():
        problems.append(_line("title", "empty — name the round"))
    if "intro" in data and not isinstance(data["intro"], str):
        problems.append(_line("intro", "must be a string"))

    _check_settled(data, problems)

    questions = data.get("questions")
    if "questions" not in data:
        problems.append(_line("questions", 'no "questions" — a round asks at least one question'))
    elif not isinstance(questions, list):
        problems.append(_line("questions", "must be a list"))
    elif not questions:
        problems.append(_line("questions", "empty — a round asks at least one question"))
    else:
        for qi, question in enumerate(questions):
            _check_question(question, f"questions[{qi}]", problems)
    return problems


def _check_settled(data: dict, problems: list[str]) -> None:
    if "settled" not in data:
        return
    settled = data["settled"]
    if not isinstance(settled, list):
        problems.append(_line("settled", "must be a list of strings"))
        return
    for i, note in enumerate(settled):
        if not isinstance(note, str):
            problems.append(_line(f"settled[{i}]", "must be a string"))
        elif not note.strip():
            problems.append(_line(f"settled[{i}]", "empty — say what was settled or leave it out"))


def _check_question(question: object, where: str, problems: list[str]) -> None:
    if not isinstance(question, dict):
        problems.append(_line(where, "must be an object"))
        return
    _unknown_keys(question, _QUESTION_KEYS, where, problems)
    if "prompt" not in question:
        problems.append(_line(where, 'no "prompt" — say what is being decided'))
    elif not isinstance(question["prompt"], str):
        problems.append(_line(f"{where}.prompt", "must be a string"))
    elif not question["prompt"].strip():
        problems.append(_line(f"{where}.prompt", "empty — say what is being decided"))

    options = question.get("options")
    if "options" not in question:
        problems.append(_line(where, 'no "options" — a question has at least one option'))
        return
    if not isinstance(options, list):
        problems.append(_line(f"{where}.options", "must be a list"))
        return
    if not options:
        problems.append(_line(f"{where}.options", "empty — a question has at least one option"))
        return
    if len(options) > _MAX_OPTIONS:
        problems.append(_line(f"{where}.options", f"{len(options)} options — at most {_MAX_OPTIONS}, lettered A to Z"))
    picks = 0
    for oi, option in enumerate(options):
        picks += _check_option(option, f"{where}.options[{oi}]", problems)
    if picks == 0:
        problems.append(_line(where, 'no option has "pick": true — mark the session\'s own pick'))
    elif picks > 1:
        problems.append(_line(where, f'{picks} options have "pick": true — exactly one'))


def _check_option(option: object, where: str, problems: list[str]) -> int:
    """Check one option; return 1 if it is picked."""
    if not isinstance(option, dict):
        problems.append(_line(where, "must be an object"))
        return 0
    _unknown_keys(option, _OPTION_KEYS, where, problems)
    for key, why in (("name", "every option has a name"), ("cost", "every option states what it costs")):
        if key not in option:
            problems.append(_line(where, f'no "{key}" — {why}'))
        elif not isinstance(option[key], str):
            problems.append(_line(f"{where}.{key}", "must be a string"))
        elif not option[key].strip():
            problems.append(_line(f"{where}.{key}", f"empty — {why}"))
    if "layout" in option and "flow" in option:
        problems.append(_line(where, 'has both "layout" and "flow" — give it either a layout or a flow'))
    elif "layout" not in option and "flow" not in option:
        problems.append(_line(where, 'no "layout" or "flow" — give it either a layout or a flow'))
    if "flow" in option and not isinstance(option["flow"], str):
        problems.append(_line(f"{where}.flow", "must be a string"))
    pick = option.get("pick", False)
    if not isinstance(pick, bool):
        problems.append(_line(f"{where}.pick", "must be true or false"))
        return 0
    return 1 if pick else 0


def _check_slug(pad: str, where: str = "--pad", noun: str = "pad") -> list[str]:
    if len(pad) > _MAX_SLUG and _SLUG.fullmatch(pad):
        return [_line(where, f"the {noun} name is too long ({len(pad)} characters) — the limit is {_MAX_SLUG}")]
    if _SLUG.fullmatch(pad):
        return []
    return [
        _line(
            where,
            f"{_q(pad)} is not a valid {noun} name — start with a lowercase letter or digit, then use "
            "lowercase letters, digits, '.', '_' or '-'",
        )
    ]


def _ensure_pads_root(pads_root: Path) -> None:
    where = _q(str(pads_root))
    try:
        st = os.lstat(pads_root)
    except FileNotFoundError:
        try:
            pads_root.parent.mkdir(parents=True, exist_ok=True)
            pads_root.mkdir(mode=0o700)
            return
        except FileExistsError:
            pass
        except OSError as exc:
            raise Refusal([_line(where, f"cannot create the pads folder: {exc.strerror or exc}")])
        try:
            st = os.lstat(pads_root)
        except OSError as exc:
            raise Refusal([_line(where, f"cannot inspect the pads folder: {exc.strerror or exc}")])
    except OSError as exc:
        raise Refusal([_line(where, f"cannot inspect the pads folder: {exc.strerror or exc}")])
    if stat.S_ISLNK(st.st_mode):
        raise Refusal([_line(where, "the pads folder is a symlink — refusing to build through it")])
    if not stat.S_ISDIR(st.st_mode):
        raise Refusal([_line(where, "the pads folder is not a directory")])


class Built(NamedTuple):
    folder: Path
    lines: list[str]


def _run_publisher(cmd: Sequence[str], env: Mapping[str, str], timeout: float):
    """Run the publisher; raises ``subprocess.TimeoutExpired`` after killing it."""
    return subprocess.run(
        list(cmd), env=dict(env), capture_output=True, text=True, errors="backslashreplace", timeout=timeout
    )


def _stream_publisher(cmd: Sequence[str], env: Mapping[str, str], timeout: float | None = None):
    """Run the publisher with its stdout passed through as written; only stderr is captured. No timer unless given."""
    return subprocess.run(
        list(cmd), env=dict(env), stderr=subprocess.PIPE, text=True, errors="backslashreplace", timeout=timeout
    )


def _esc(text: str) -> str:
    return _q(text)[1:-1]


def _exists(pad: str) -> Refusal:
    return Refusal([f"scratch-pad: pad {_q(pad)} already exists — choose another --pad name"])


def _js_string_literal(value: object) -> str:
    """*value* as ASCII JSON, encoded once more as a JS string literal."""
    return json.dumps(json.dumps(value, ensure_ascii=True, allow_nan=False))


class Encoded(NamedTuple):
    round_js: str
    title: str


def _check_and_encode(round_path: Path, pad: str, kit_dir: Path) -> Encoded:
    problems = _check_slug(pad)
    data = None
    try:
        data = _read_round(round_path)
    except Refusal as exc:
        problems.extend(exc.lines)
    else:
        problems.extend(check_round(data))
    if problems:
        raise Refusal(problems)

    try:
        round_js = _ROUND_JS_PREFIX + _js_string_literal(data) + ");\n"
    except ValueError:
        raise Refusal([_line(_q(str(round_path)), "a number is too large to write — numbers must be finite")])
    except RecursionError:
        raise Refusal([_line(_q(str(round_path)), "nested too deeply to write")])
    if not kit_dir.is_dir():
        raise Refusal([_line("kit", f"{_q(str(kit_dir))} is not a directory — the pad kit is missing")])
    return Encoded(round_js, data["title"])


def _write_rounds_js(folder: Path, titles: Sequence[str]) -> None:
    listing = [{"title": title} for title in titles]
    (folder / "rounds.js").write_text(_ROUNDS_JS_PREFIX + _js_string_literal(listing) + ");\n", encoding="utf-8")


def _write_pad(folder: Path, kit_dir: Path, encoded: Encoded, earlier: Sequence[str] = ()) -> None:
    """Write the round after the *earlier* rounds' titles, the pad's ``rounds.js`` and its ``index.html``."""
    number = len(earlier) + 1
    shutil.copytree(kit_dir, folder / f"round-{number}")
    round_id = json.dumps(secrets.token_hex(16))
    round_js = encoded.round_js + f"window.PAD_ROUND_NUMBER = {number};\nwindow.PAD_ROUND_ID = {round_id};\n"
    (folder / f"round-{number}" / "round.js").write_text(round_js, encoding="utf-8")
    _write_rounds_js(folder, [*earlier, encoded.title])
    (folder / "index.html").write_text(_pad_index(number), encoding="utf-8")


def _build_failed(final: Path, exc: OSError) -> Refusal:
    return Refusal([_line(_q(str(final)), f"could not build the pad: {exc.strerror or exc}")])


def _locks_dir(env: Mapping[str, str]) -> Path:
    return _outpost_state_dir(env).absolute() / "pad-locks"


def _open_lock(env: Mapping[str, str], pad_path: Path) -> int:
    locks = _locks_dir(env)
    where = _q(str(locks))
    try:
        locks.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(locks, 0o700)
        except FileExistsError:
            pass
        st = os.lstat(locks)
    except OSError as exc:
        raise Refusal([_line(where, f"cannot create the pad-locks folder: {exc.strerror or exc}")])
    if stat.S_ISLNK(st.st_mode):
        raise Refusal([_line(where, "the pad-locks folder is a symlink — refusing to lock through it")])
    if not stat.S_ISDIR(st.st_mode):
        raise Refusal([_line(where, "the pad-locks folder is not a directory")])
    path = locks / (hashlib.sha256(str(pad_path).encode()).hexdigest() + ".lock")
    try:
        return os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise Refusal([_line(_q(str(path)), f"cannot open the pad's lock file: {exc.strerror or exc}")])


@contextlib.contextmanager
def _pad_lock(env: Mapping[str, str], pads_root: Path, pad: str, limit: float):
    """Hold the exclusive lock for one pad; refuse once *limit* seconds of waiting pass."""
    fd = _open_lock(env, pads_root.resolve() / pad)
    try:
        deadline = time.monotonic() + limit
        announced = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                pass
            if not announced:
                print(f"scratch-pad: waiting for another session's add to pad {_q(pad)}…", file=sys.stderr)
                announced = True
            if time.monotonic() >= deadline:
                raise Refusal([f"scratch-pad: another session is adding a round to pad {_q(pad)}; try again"])
            time.sleep(_LOCK_POLL_SECONDS)
        yield
    finally:
        os.close(fd)


def _remove(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path, ignore_errors=True)


def _recover(pads_root: Path, pad: str) -> None:
    """Undo what a run killed part-way left beside the pad; call only under the pad's lock."""
    final, aside = pads_root / pad, pads_root / f".aside-{pad}"
    if os.path.lexists(aside):
        if os.path.lexists(final):
            _remove(aside)
        else:
            os.rename(aside, final)
    stale = re.compile(re.escape(f".tmp-{pad}-") + r"[A-Za-z0-9_]+")
    for entry in os.listdir(pads_root):
        if stale.fullmatch(entry):
            _remove(pads_root / entry)


def _recover_in(pads_root: Path, pad: str) -> None:
    """``_recover`` where the folder may not exist yet (a workspace without sites); I/O failure is a refusal."""
    try:
        if pads_root.is_dir():
            _recover(pads_root, pad)
    except OSError as exc:
        raise _build_failed(pads_root / pad, exc)


def _tree_problems(root: Path) -> list[str]:
    """Every symlink, special file or unlistable folder under *root*, named relative to it."""
    problems: list[str] = []

    def walk(folder: Path) -> None:
        try:
            entries = sorted(os.listdir(folder))
        except OSError as exc:
            problems.append(f"{_esc(str(folder.relative_to(root)))} can't be read: {_esc(exc.strerror or str(exc))}")
            return
        for entry in entries:
            path = folder / entry
            rel = _esc(str(path.relative_to(root)))
            try:
                mode = os.lstat(path).st_mode
            except OSError as exc:
                problems.append(f"{rel} can't be read: {_esc(exc.strerror or str(exc))}")
                continue
            if stat.S_ISLNK(mode):
                problems.append(f"{rel} is a symlink")
            elif stat.S_ISDIR(mode):
                walk(path)
            elif not stat.S_ISREG(mode):
                problems.append(f"{rel} is not a regular file or folder")

    walk(root)
    return problems


def _round_data(round_js: Path) -> object:
    """The round in a ``round.js`` first line, decoded as data; ``ValueError`` when it cannot be."""
    with open(round_js, encoding="utf-8") as handle:
        first = handle.readline().rstrip("\n")
    if not first.startswith(_ROUND_JS_PREFIX):
        raise ValueError("does not start the way this script writes it")
    if not first.endswith(");"):
        raise ValueError("its first line does not end the way this script writes it")
    try:
        return json.loads(json.loads(first[len(_ROUND_JS_PREFIX) : -2]))
    except (ValueError, RecursionError):
        raise ValueError("its first line does not hold a round") from None


def _round_title(round_js: Path) -> str:
    """The title in a ``round.js`` first line, decoded as data; ``ValueError`` when it cannot be."""
    try:
        title = _round_data(round_js)["title"]
    except (KeyError, TypeError):
        raise ValueError("its first line does not hold a round") from None
    if not isinstance(title, str):
        raise ValueError("its round has no title")
    return title


def _cannot_take(pad: str, problems: Sequence[str], strays: Sequence[str] = ()) -> Refusal:
    """Refuse the add naming *problems* then *strays*; only strays, which can be moved out, change the way on."""
    way_on = _WAY_ON if problems else _MOVE_OUT
    return Refusal(
        [f"scratch-pad: pad {_q(pad)} can't take another round: {'; '.join([*problems, *strays])} — {way_on}"]
    )


def _read_pad(final: Path, pad: str, refuse=_cannot_take) -> list[str]:
    """The pad's round titles in order, or the :class:`Refusal` *refuse* builds naming every problem."""
    problems: list[str] = []
    strays: list[str] = []
    titles: list[str] = []
    try:
        mode = os.lstat(final).st_mode
    except OSError as exc:
        raise refuse(pad, [f"the pad folder can't be read: {_esc(exc.strerror or str(exc))}"])
    if stat.S_ISLNK(mode):
        problems.append("the pad folder is a symlink")
    elif not stat.S_ISDIR(mode):
        problems.append("the pad is not a folder")
    else:
        try:
            entries = sorted(os.listdir(final))
        except OSError as exc:
            raise refuse(pad, [f"the pad folder can't be read: {_esc(exc.strerror or str(exc))}"])
        problems.extend(_tree_problems(final))
        strays.extend(
            f"{_esc(e)} is not part of a pad this script builds"
            for e in entries
            if e not in _PAD_FILES and not _ROUND_DIR.fullmatch(e) and not _OS_METADATA.fullmatch(e)
        )
        numbers = sorted(int(m.group(1)) for e in entries if (m := _ROUND_DIR.fullmatch(e)))
        if not numbers:
            problems.append("it has no round-1 folder")
        for expected, found in enumerate(numbers, 1):
            if found != expected:
                problems.append(f"round-{expected} is missing")
                break
        else:
            for k in numbers:
                folder = final / f"round-{k}"
                if not folder.is_dir() or folder.is_symlink():
                    problems.append(f"round-{k} is not a folder")
                    continue
                if not (folder / "index.html").is_file():
                    problems.append(f"round-{k}/index.html is missing")
                round_js = folder / "round.js"
                try:
                    plain = stat.S_ISREG(os.lstat(round_js).st_mode)
                except FileNotFoundError:
                    problems.append(f"round-{k}/round.js is missing")
                    continue
                if not plain:
                    continue  # named by the tree check; a link or special file is never opened
                try:
                    titles.append(_round_title(round_js))
                except (OSError, ValueError) as exc:
                    problems.append(f"round-{k}/round.js: {_esc(str(exc))}")
    if problems or strays:
        raise refuse(pad, problems, strays)
    return titles


def _copy_round(src: Path, dst: Path) -> None:
    """Copy a round folder byte for byte; a symlink is copied as a link, never followed."""
    dst.mkdir()
    for entry in sorted(os.listdir(src)):
        source, target = src / entry, dst / entry
        mode = os.lstat(source).st_mode
        if stat.S_ISLNK(mode):
            os.symlink(os.readlink(source), target)
        elif stat.S_ISDIR(mode):
            _copy_round(source, target)
        elif stat.S_ISREG(mode):
            shutil.copy2(source, target, follow_symlinks=False)
        else:
            raise Refusal([_line(_q(str(source)), "is not a regular file or folder — cannot copy it")])


def _swap_in(tmp: Path, final: Path, aside: Path) -> None:
    """Put *tmp* at *final*, the old pad going aside first and coming back if the swap fails."""
    os.rename(final, aside)
    try:
        os.rename(tmp, final)
    except BaseException:
        if not os.path.lexists(final):
            os.rename(aside, final)
        raise
    shutil.rmtree(aside, ignore_errors=True)


def _add_round(pads_root: Path, pad: str, kit_dir: Path, encoded: Encoded, put_in_place=_swap_in, make_tmp=None):
    """Add the round to the existing pad; return what *put_in_place* returns.

    *put_in_place(tmp, final, aside)* is the step a caller swaps; *make_tmp()* says where the new pad is
    built (default: a ``.tmp-`` sibling of the pad).
    """
    final, aside = pads_root / pad, pads_root / f".aside-{pad}"
    if not os.path.lexists(final):
        raise Refusal([_line(f"pad {_q(pad)}", "does not exist — drop --add to start a new pad")])
    titles = _read_pad(final, pad)
    if encoded.title in titles:
        raise Refusal(
            [
                _line(
                    "title",
                    f"{_q(encoded.title)} is the title of round {titles.index(encoded.title) + 1} — "
                    "give each round its own title",
                )
            ]
        )
    number = len(titles) + 1
    try:
        tmp = Path(make_tmp() if make_tmp else tempfile.mkdtemp(prefix=f".tmp-{pad}-", dir=pads_root))
    except OSError as exc:
        raise _build_failed(final, exc)
    try:
        for k in range(1, number):
            _copy_round(final / f"round-{k}", tmp / f"round-{k}")
        _write_pad(tmp, kit_dir, encoded, titles)
        problems = _tree_problems(tmp)
        if problems:
            raise _cannot_take(pad, problems)
        return put_in_place(tmp, final, aside)
    except OSError as exc:
        raise _build_failed(final, exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _require_plain_sites(sites: Path) -> None:
    """Refuse a ``sites`` that is a symlink or not a folder, before anything is read or recovered through it."""
    try:
        st = os.lstat(sites)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise Refusal([_line(_q(str(sites)), f"cannot inspect the sites folder: {exc.strerror or exc}")])
    if stat.S_ISLNK(st.st_mode):
        raise Refusal([_line(_q(str(sites)), "the sites folder is a symlink — refusing to publish through it")])
    if not stat.S_ISDIR(st.st_mode):
        raise Refusal([_line(_q(str(sites)), "the sites folder is not a directory")])


def _require_publisher() -> None:
    if not PUBLISHER_PATH.is_file():
        raise Refusal(
            [
                _line(
                    "publish",
                    f"the publish-site skill is missing ({_q(str(PUBLISHER_PATH))}) — "
                    "a pad inside a camp workspace is published through it",
                )
            ]
        )


def _publisher_cmd(tmp: Path, pad: str, workspace: Path, *extra: str) -> list[str]:
    return _publisher_argv(tmp, pad, "--workspace-path", str(workspace), *extra)


def _publisher_argv(tmp: Path, site: str, *target: str) -> list[str]:
    return [sys.executable, str(PUBLISHER_PATH), str(tmp), site, *target]


def _cannot_start(exc: OSError) -> Refusal:
    return Refusal([_line("publish", f"could not start the publisher: {exc.strerror or exc}")])


def _publish_refused(result) -> Refusal:
    reasons = [_line("publish", _esc(line)) for line in result.stderr.splitlines() if line.strip()]
    return Refusal(reasons or [_line("publish", f"the publisher exited with status {result.returncode}")])


def _publish(runner, cmd: Sequence[str], env: Mapping[str, str], timeout: float, final: Path, timed_out) -> Built:
    """Run the publisher: its stdout on success, a ``publish:`` refusal on failure, ``timed_out()`` on timeout."""
    try:
        result = runner(cmd, env, timeout)
    except OSError as exc:
        raise _cannot_start(exc)
    except subprocess.TimeoutExpired:
        return timed_out()
    if result.returncode != 0:
        raise _publish_refused(result)
    return Built(final, result.stdout.splitlines())


def _publish_to_workspace(
    workspace: Path, pad: str, kit_dir: Path, encoded: Encoded, env: Mapping[str, str], runner, timeout: float
) -> Built:
    final = workspace / "sites" / pad
    # Two runs of this script on one name are serialised by the pad's lock, so the second one finds the
    # site here. The publisher's own refusal without --overwrite only arises when another tool wrote it.
    if os.path.lexists(final):
        raise _exists(pad)

    def timed_out() -> Built:
        if (final / "index.html").is_file():
            return Built(final, [_UNCONFIRMED])
        raise Refusal(
            [_line("publish", f"the publisher did not finish within {timeout:g} seconds and no pad was published")]
        )

    try:
        tmp = Path(tempfile.mkdtemp(prefix="scratch-pad-"))
    except OSError as exc:
        raise _build_failed(final, exc)
    try:
        _write_pad(tmp, kit_dir, encoded)
        return _publish(runner, _publisher_cmd(tmp, pad, workspace), env, timeout, final, timed_out)
    except OSError as exc:
        raise _build_failed(final, exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mtime(path: Path) -> float:
    try:
        return os.lstat(path).st_mtime
    except OSError:
        return 0.0


def _previous_entries(sites: Path, pad: str) -> list[Path]:
    """The folders beside the pad that have the shape of the publisher's ``.<pad>.previous-`` temp names."""
    shape = re.compile(re.escape(f".{pad}.previous-") + r"[a-z0-9_]{8}")
    found = []
    try:
        entries = os.listdir(sites)
    except OSError:
        return found
    for entry in entries:
        path = sites / entry
        try:
            plain_dir = stat.S_ISDIR(os.lstat(path).st_mode)
        except OSError:
            continue
        if plain_dir and shape.fullmatch(entry):
            found.append(path)
    return found


def _timed_out_add(final: Path, pad: str, tmp: Path, timeout: float) -> Built:
    """Judge an add whose publisher was killed: the site on disk against the pad *tmp* was to become."""
    late = f"the publisher did not finish within {timeout:g} seconds and"
    expected = _read_pad(tmp, pad)
    if not os.path.lexists(final):
        lines = [_line("publish", f"{late} the pad is no longer there")]
        previous = sorted(_previous_entries(final.parent, pad), key=lambda p: (_mtime(p), p.name))
        if previous:
            lines.append(
                _line(
                    "publish",
                    f"{_esc(str(previous[-1]))} holds the pad as it was before this add; "
                    f"rename it back to {_esc(str(final))} to restore it",
                )
            )
        if previous[:-1]:
            older = ", ".join(_esc(str(p)) for p in previous[:-1])
            lines.append(_line("publish", f"older leftovers that may be partial: {older}"))
        raise Refusal(lines)
    titles = _read_pad(final, pad)
    if titles == expected[:-1]:
        raise Refusal([_line("publish", f"{late} the round was not added")])
    if titles != expected or not all((final / name).is_file() for name in ("index.html", "rounds.js")):
        raise Refusal([_line("publish", f"{late} the pad is not as expected")])
    return Built(final, [_UNCONFIRMED])


def _add_to_workspace(
    workspace: Path, pad: str, kit_dir: Path, encoded: Encoded, env: Mapping[str, str], runner, timeout: float
) -> Built:
    """Add the round to the pad published at ``<workspace>/sites/<pad>``; call only under the pad's lock."""

    def publish(tmp: Path, final: Path, aside: Path) -> Built:
        cmd = _publisher_cmd(tmp, pad, workspace, "--overwrite")
        return _publish(runner, cmd, env, timeout, final, lambda: _timed_out_add(final, pad, tmp, timeout))

    def make_tmp() -> str:
        return tempfile.mkdtemp(prefix="scratch-pad-")

    return _add_round(workspace / "sites", pad, kit_dir, encoded, publish, make_tmp)


_REFERENCE_KEYS = ("site", "title", "drawings")
_DRAWING_KEYS = ("round", "question", "option")
_DRAWING_HINTS = {
    "round": "name the round's number",
    "question": "name the question's number in that round",
    "option": "name the option's letter",
}
_LETTER = re.compile(r"[A-Z]")

# Credential-pattern classes, mirroring craft's credential-pattern scrub list (the outpost plugin cannot
# import craft). Each is tried independently, so a string can match more than one class.
_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "vendor-prefix",
        re.compile(
            r"(?i)\b(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}"
            r"|glpat-[A-Za-z0-9_-]{20}|xox[baprs]-[A-Za-z0-9-]+|sk_live_[A-Za-z0-9]+"
            r"|AIza[0-9A-Za-z_-]{35})\b"
        ),
    ),
    ("key-like", re.compile(r"(?i)(secret|token|passwd|password|api[_-]?key)[A-Za-z0-9_-]*\s*[=:]\s*\S+")),
    ("api-key-shape", re.compile(r"(?i)api[_-]?key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{16,}")),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")),
    ("high-entropy-hex", re.compile(r"\b[A-Fa-f0-9]{40,}\b")),
    ("high-entropy-base64", re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b")),
)


class ChosenDrawing(NamedTuple):
    """One drawing a reference names: its place on the pad, its round's title, its question's prompt and the
    option as the pad holds it (``name``, ``cost``, ``pick`` and a ``layout`` or ``flow``)."""

    round: int
    question: int
    letter: str
    round_title: str
    prompt: str
    option: dict


class Reference(NamedTuple):
    site: str
    title: str
    drawings: list[ChosenDrawing]


def credential_patterns(text: str) -> list[str]:
    """The names of the credential-pattern classes *text* matches, in a fixed order; empty when none."""
    return [name for name, pattern in _CREDENTIAL_PATTERNS if pattern.search(text)]


def check_reference(data: object) -> list[str]:
    """Return a ``scratch-pad:`` line for every outer-shape problem in a reference file's *data*."""
    if not isinstance(data, dict):
        return [_line("reference", "must be a JSON object")]
    problems: list[str] = []
    _unknown_keys(data, _REFERENCE_KEYS, "reference", problems)
    if "site" not in data:
        problems.append(_line("site", 'no "site" — name the page'))
    elif not isinstance(data["site"], str):
        problems.append(_line("site", "must be a string"))
    else:
        problems.extend(_check_slug(data["site"], "site", "site"))
    if "title" not in data:
        problems.append(_line("title", 'no "title" — name the page'))
    elif not isinstance(data["title"], str):
        problems.append(_line("title", "must be a string"))
    elif not data["title"].strip():
        problems.append(_line("title", "empty — name the page"))
    drawings = data.get("drawings")
    if drawings is None or drawings == []:
        problems.append(_line("drawings", "name at least one drawing"))
    elif not isinstance(drawings, list):
        problems.append(_line("drawings", "must be a list"))
    else:
        for i, drawing in enumerate(drawings):
            _check_drawing(drawing, f"drawings[{i}]", problems)
    return problems


def _check_drawing(drawing: object, where: str, problems: list[str]) -> None:
    if not isinstance(drawing, dict):
        problems.append(_line(where, "must be an object"))
        return
    _unknown_keys(drawing, _DRAWING_KEYS, where, problems)
    for key in ("round", "question"):
        value = drawing.get(key)
        if key not in drawing:
            problems.append(_line(where, f'no "{key}" — {_DRAWING_HINTS[key]}'))
        elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
            problems.append(_line(f"{where}.{key}", "must be a whole number from 1"))
    if "option" not in drawing:
        problems.append(_line(where, f'no "option" — {_DRAWING_HINTS["option"]}'))
    elif not isinstance(drawing["option"], str) or not _LETTER.fullmatch(drawing["option"]):
        problems.append(_line(f"{where}.option", "must be one capital letter, A to Z"))


def _cannot_reference(pad: str, problems: Sequence[str], strays: Sequence[str] = ()) -> Refusal:
    """The read-back's refusal in a reference's wording: the reasons, with no way on for an add."""
    return Refusal([f"scratch-pad: pad {_q(pad)} can't be referenced: {'; '.join([*problems, *strays])}"])


def _questions_of(data: object) -> list | None:
    """The round's questions when the data has the shape this script writes, else ``None``."""
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list):
        return None
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("prompt"), str):
            return None
        options = question.get("options")
        if not isinstance(options, list) or not all(isinstance(o, dict) for o in options):
            return None
    return questions


def _strings_in(value: object):
    """Every string in *value*, dictionary keys included, as ``(is_key, text)``."""
    stack = [(False, value)]
    while stack:
        is_key, item = stack.pop()
        if isinstance(item, str):
            yield is_key, item
        elif isinstance(item, dict):
            for key, child in item.items():
                stack.append((False, child))
                stack.append((True, key))
        elif isinstance(item, list):
            stack.extend((False, child) for child in item)


def _credential_problems(site: str, title: str, drawings: Sequence[ChosenDrawing]) -> list[str]:
    """A line for every page-bound field that matches a credential pattern; never the matched text."""
    problems: list[str] = []

    def check(where: str, field: str, texts: Sequence[str]) -> None:
        classes = sorted(
            {c for text in texts for c in credential_patterns(text)},
            key=lambda c: [n for n, _ in _CREDENTIAL_PATTERNS].index(c),
        )
        if classes:
            problems.append(
                _line(
                    where, f"{field} matches a credential pattern ({', '.join(classes)}) — this is shared with the team"
                )
            )

    check("title", "title", [title])
    check("site", "site", [site])
    for i, d in enumerate(drawings):
        where = f"drawings[{i}] (round {d.round}, question {d.question}, option {d.letter})"
        check(where, "round title", [d.round_title])
        check(where, "prompt", [d.prompt])
        for field in ("name", "cost", "flow"):
            if field in d.option:
                check(where, field, [t for _, t in _strings_in(d.option[field])])
        if "layout" in d.option:
            strings = list(_strings_in(d.option["layout"]))
            check(where, "layout", [t for is_key, t in strings if not is_key])
            check(where, "layout (a key)", [t for is_key, t in strings if is_key])
    return problems


def _non_finite(value: object) -> str | None:
    """The first NaN, Infinity or -Infinity held anywhere in decoded JSON *value*, as JSON writes it; else ``None``."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, float) and not math.isfinite(item):
            return "NaN" if math.isnan(item) else ("Infinity" if item > 0 else "-Infinity")
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def _resolve_drawings(drawings: Sequence[dict], titles: Sequence[str], rounds: Mapping[int, object]) -> tuple:
    """Match each named drawing to the pad: ``(chosen, problems)``; every problem is named."""
    chosen: list[ChosenDrawing] = []
    problems: list[str] = []
    seen: dict[tuple, int] = {}
    for i, drawing in enumerate(drawings):
        where = f"drawings[{i}]"
        number, qn, letter = drawing["round"], drawing["question"], drawing["option"]
        if number > len(titles):
            problems.append(_line(f"{where}.round", f"{number} — the pad has {_plural(len(titles), 'round')}"))
            continue
        questions = _questions_of(rounds[number])
        if questions is None:
            problems.append(
                _line(f"{where}.round", f"{number} — round {number}'s data does not hold a list of questions")
            )
            continue
        if qn > len(questions):
            problems.append(
                _line(f"{where}.question", f"{qn} — round {number} has {_plural(len(questions), 'question')}")
            )
            continue
        options = questions[qn - 1]["options"]
        index = ord(letter) - ord("A")
        if index >= len(options):
            problems.append(
                _line(
                    f"{where}.option",
                    f"{letter} — question {qn} of round {number} has {_plural(len(options), 'option')} "
                    f"(A to {chr(64 + len(options))})",
                )
            )
            continue
        key = (number, qn, letter)
        if key in seen:
            problems.append(
                _line(
                    where, f"the same drawing as drawings[{seen[key]}] (round {number}, question {qn}, option {letter})"
                )
            )
            continue
        seen[key] = i
        option_problems: list[str] = []
        option_where = f"{where} (round {number}, question {qn}, option {letter})"
        _check_option(options[index], option_where, option_problems)
        constant = _non_finite(options[index])
        if constant is not None:
            message = f"holds {constant}, which is not valid JSON — a number must be finite"
            option_problems.append(_line(option_where, message))
        if option_problems:
            problems.extend(option_problems)
            continue
        chosen.append(
            ChosenDrawing(number, qn, letter, titles[number - 1], questions[qn - 1]["prompt"], options[index])
        )
    return chosen, problems


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def read_reference(
    reference_path: Path | str,
    pad: str,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    publish_timeout: float = PUBLISH_TIMEOUT_SECONDS,
) -> Reference:
    """Check a reference file and read the drawings it names back from *pad*; nothing is built or changed.

    The pad is found where ``--add`` finds it (``<workspace>/sites/<pad>`` inside a camp workspace, otherwise
    under Outpost's pads folder), read under its lock through the add's read-back, and each named round's
    ``round.js`` is decoded as data, never evaluated. The lock is released before this returns. Raises
    :class:`Refusal` (every problem found, as ``scratch-pad:`` lines) when the file, the pad, a named drawing
    or any string bound for the page is wrong. Returns the chosen drawings in the order named.
    """
    env = os.environ if env is None else env
    if cwd is None:
        try:
            cwd = Path.cwd()
        except OSError:
            cwd = None
    problems = _check_slug(pad)
    data = None
    try:
        data = _read_round(Path(reference_path), "reference")
    except Refusal as exc:
        problems.extend(exc.lines)
    else:
        problems.extend(check_reference(data))
    if problems:
        raise Refusal(problems)

    workspace = None if cwd is None else enclosing_workspace(Path(cwd), env)
    if workspace is not None:
        root = workspace / "sites"
        _require_plain_sites(root)
    else:
        root = _pads_root(env)
    final = root / pad
    rounds: dict[int, object] = {}
    with _pad_lock(env, root, pad, 2 * publish_timeout):
        if not os.path.lexists(final):
            raise Refusal([_line(f"pad {_q(pad)}", "does not exist — build it first, or name the pad it is on")])
        titles = _read_pad(final, pad, _cannot_reference)
        for number in sorted({d["round"] for d in data["drawings"] if d["round"] <= len(titles)}):
            rounds[number] = _round_data(final / f"round-{number}" / "round.js")

    chosen, problems = _resolve_drawings(data["drawings"], titles, rounds)
    problems.extend(_credential_problems(data["site"], data["title"], chosen))
    if problems:
        raise Refusal(problems)
    return Reference(data["site"], data["title"], chosen)


def _reference_entry(drawing: ChosenDrawing) -> dict:
    """The fields of one chosen drawing the page needs; the option's other keys are left behind."""
    option = drawing.option
    entry = {
        "question": drawing.question,
        "letter": drawing.letter,
        "roundTitle": drawing.round_title,
        "prompt": drawing.prompt,
        "name": option["name"],
        "cost": option["cost"],
    }
    if "flow" in option:
        entry["flow"] = option["flow"]
    else:
        entry["layout"] = option["layout"]
    return entry


def build_reference_page(
    target: Path | str,
    reference: Reference,
    date: datetime.date | str | None = None,
    *,
    kit_dir: Path | str = DEFAULT_KIT_DIR,
    reference_dir: Path | str = DEFAULT_REFERENCE_DIR,
) -> None:
    """Write the reference page for *reference* into the folder *target*, to be opened from disk.

    *target* is created when absent and is meant to be new: files of the same names are overwritten. It gets the
    reference page's own files, the kit's ``pen.js``, ``flow.js``, ``pad.css``, font and ``OFL.txt``, and
    ``reference-data.js``, whose data is encoded as ``round.js`` is (ASCII JSON as a string literal handed to
    ``JSON.parse``). Only each drawing's place, round title, prompt, name, cost and ``layout`` or ``flow`` are
    written. *date* (a ``datetime.date`` or text; today by default) is the day the drawings are said to have
    been chosen. Raises :class:`Refusal` when a source folder is missing or the target cannot be written.
    """
    target, kit_dir, reference_dir = Path(target), Path(kit_dir), Path(reference_dir)
    for noun, folder in (("kit", kit_dir), ("reference", reference_dir)):
        if not folder.is_dir():
            raise Refusal([_line(noun, f"{_q(str(folder))} is not a directory — the pad {noun} is missing")])
    day = datetime.date.today() if date is None else date
    data = {
        "title": reference.title,
        "date": day.isoformat() if isinstance(day, datetime.date) else str(day),
        "drawings": [_reference_entry(d) for d in reference.drawings],
    }
    try:
        target.mkdir(parents=True, exist_ok=True)
        for source in sorted(reference_dir.iterdir()):
            if source.is_file():
                shutil.copyfile(source, target / source.name)
        for name in _REFERENCE_KIT_FILES:
            shutil.copyfile(kit_dir / name, target / name)
        (target / "reference-data.js").write_text(
            _REFERENCE_JS_PREFIX + _js_string_literal(data) + ");\n", encoding="utf-8"
        )
    except OSError as exc:
        raise _build_failed(target, exc)


def _build(
    round_path: Path | str,
    pad: str,
    kit_dir: Path | str,
    env: Mapping[str, str] | None,
    cwd: Path | str | None,
    runner,
    timeout: float,
    add: bool = False,
) -> Built:
    round_path, kit_dir = Path(round_path), Path(kit_dir)
    env = os.environ if env is None else env
    if cwd is None:
        try:
            cwd = Path.cwd()
        except OSError:
            cwd = None
    else:
        cwd = Path(cwd)
    runner = runner or _run_publisher
    encoded = _check_and_encode(round_path, pad, kit_dir)
    workspace = None if cwd is None else enclosing_workspace(cwd, env)
    if workspace is not None:
        _require_publisher()
        sites = workspace / "sites"
        _require_plain_sites(sites)
        with _pad_lock(env, sites, pad, 2 * timeout):
            _recover_in(sites, pad)
            if add:
                return _add_to_workspace(workspace, pad, kit_dir, encoded, env, runner, timeout)
            return _publish_to_workspace(workspace, pad, kit_dir, encoded, env, runner, timeout)

    pads_root = _pads_root(env)
    final = pads_root / pad
    _ensure_pads_root(pads_root)
    with _pad_lock(env, pads_root, pad, 2 * timeout):
        _recover_in(pads_root, pad)
        if add:
            _add_round(pads_root, pad, kit_dir, encoded)
        else:
            _new_pad(pads_root, pad, kit_dir, encoded)
    return Built(final, [(final / "index.html").as_uri()])


def _new_pad(pads_root: Path, pad: str, kit_dir: Path, encoded: Encoded) -> None:
    final = pads_root / pad
    if os.path.lexists(final):
        raise _exists(pad)

    try:
        tmp = Path(tempfile.mkdtemp(prefix=f".tmp-{pad}-", dir=pads_root))
    except OSError as exc:
        raise _build_failed(final, exc)
    try:
        _write_pad(tmp, kit_dir, encoded)
        try:
            os.rename(tmp, final)
        except OSError:
            if os.path.lexists(final):
                raise _exists(pad)
            raise
    except BaseException as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        if isinstance(exc, OSError):
            raise _build_failed(final, exc)
        raise


def build_pad(
    round_path: Path | str,
    pad: str,
    *,
    kit_dir: Path | str = DEFAULT_KIT_DIR,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    publish_runner=None,
    publish_timeout: float = PUBLISH_TIMEOUT_SECONDS,
    add: bool = False,
) -> tuple[Path, Path]:
    """Check the round and build the pad; return ``(index_html, pad_folder)``.

    Raises :class:`Refusal` (carrying the ``scratch-pad:`` lines) when the pad
    name or round is wrong, the pad exists, or the build fails; in every such
    case nothing is left behind. *cwd* (default the process's) decides whether
    the pad is built on disk or published into the enclosing camp workspace;
    *publish_runner* is ``(cmd, env, timeout) -> CompletedProcess``.
    """
    folder = _build(round_path, pad, kit_dir, env, cwd, publish_runner, publish_timeout, add).folder
    return folder / "index.html", folder


def publish_reference(
    reference_path: Path | str,
    pad: str,
    vault_path: Path | str,
    vault: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    publish_runner=None,
    publish_timeout: float = PUBLISH_TIMEOUT_SECONDS,
) -> None:
    """Publish the reference named by *reference_path* from *pad* into the vault at *vault_path*.

    Raises :class:`Refusal` for every problem; the pad is only read. The publisher is run without
    ``--overwrite`` and without a timer, its stdout passed through as written; *publish_runner* is
    ``(cmd, env, None) -> CompletedProcess`` with the publisher's stderr captured.
    """
    env = os.environ if env is None else env
    reference = read_reference(reference_path, pad, env=env, cwd=cwd, publish_timeout=publish_timeout)
    site = reference.site
    if os.path.lexists(Path(vault_path) / "sites" / site):
        raise Refusal([_line(f"site {_q(site)}", "already exists in the vault — choose another site name")])
    runner = publish_runner or _stream_publisher
    try:
        tmp = Path(tempfile.mkdtemp(prefix="scratch-pad-reference-"))
    except OSError as exc:
        raise Refusal([_line("publish", f"could not make a temp folder: {exc.strerror or exc}")])
    try:
        build_reference_page(tmp, reference)
        target = ["--vault-path", str(vault_path)] + (["--vault", vault] if vault else [])
        print(PUBLISHING_LINE)
        sys.stdout.flush()
        try:
            result = runner(_publisher_argv(tmp, site, *target), env, None)
        except OSError as exc:
            raise _cannot_start(exc)
        if result.returncode != 0:
            raise _publish_refused(result)
        if result.stderr:
            sys.stderr.write(result.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    cwd: Path | str | None = None,
    publish_runner=None,
    publish_timeout: float = PUBLISH_TIMEOUT_SECONDS,
) -> int:
    parser = argparse.ArgumentParser(
        prog="scratch_pad.py", description="Build a scratch pad from a round of questions."
    )
    parser.add_argument("--pad", help="pad name: ^[a-z0-9][a-z0-9._-]*$")
    parser.add_argument("--round", dest="round_path", help="path to the round's JSON file")
    parser.add_argument("--add", action="store_true", help="add the round to the existing pad instead of starting one")
    parser.add_argument("--reference", help="path to a reference's JSON file: publish the chosen drawings to a vault")
    parser.add_argument("--vault-path", help="the vault's folder (with --reference)")
    parser.add_argument("--vault", help="the vault's configured name (with --reference)")
    args = parser.parse_args(argv)
    if args.reference is None:
        missing = [flag for flag, value in (("--pad", args.pad), ("--round", args.round_path)) if value is None]
        if missing:
            parser.error("the following arguments are required: " + ", ".join(missing))
        for flag, value in (("--vault-path", args.vault_path), ("--vault", args.vault)):
            if value is not None:
                parser.error(f"{flag} is only valid with --reference")
    else:
        for flag, used in (("--round", args.round_path is not None), ("--add", args.add)):
            if used:
                parser.error(f"--reference cannot be used with {flag}")
        if args.pad is None:
            parser.error("the following arguments are required: --pad")
        if args.vault_path is None:
            parser.error("--reference needs --vault-path")
        try:
            publish_reference(
                args.reference,
                args.pad,
                args.vault_path,
                args.vault,
                env=env,
                cwd=cwd,
                publish_runner=publish_runner,
                publish_timeout=publish_timeout,
            )
        except Refusal as exc:
            for line in exc.lines:
                print(line, file=sys.stderr)
            return 1
        return 0
    try:
        built = _build(args.round_path, args.pad, DEFAULT_KIT_DIR, env, cwd, publish_runner, publish_timeout, args.add)
    except Refusal as exc:
        for line in exc.lines:
            print(line, file=sys.stderr)
        return 1
    for line in built.lines:
        print(line)
    print(f"pad folder: {built.folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
