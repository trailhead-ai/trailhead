#!/usr/bin/env python3
"""Build a scratch pad: one round of layout sketches, as plain files on disk.

Given ``--pad <slug>`` and ``--round <path-to-json>``, this checks the round's
outer shape, builds the pad under Outpost's state directory, and prints the
``file://`` link to the pad's top-level ``index.html`` followed by the pad's
folder path. Nothing is served and nothing needs installing: the pad is a copy
of the skill's ``kit/`` plus the round's data, so it opens straight from disk.

Contract
--------
* The round is one JSON object: ``title`` (required, non-empty), ``intro``
  (optional string) and ``questions`` (at least one). Each question has a
  non-empty ``prompt`` and 1-26 ``options``; each option has a non-empty
  ``name`` and ``cost``, a ``layout`` key, and an optional boolean ``pick``.
  Exactly one option per question carries ``"pick": true``. An unknown key is
  refused with the allowed keys listed. What is inside ``layout`` is never
  inspected here.
* ``enclosing_workspace(cwd, env)`` names the camp workspace a directory sits
  in: ``cwd`` resolved, taken relative to camp's state root (resolved; that is
  ``$CAMP_STATE_DIR`` verbatim, else ``$XDG_STATE_HOME/camp``, else
  ``~/.local/state/camp``). When its parts are ``<group>/worktrees/<slug>/...``
  at any depth the result is ``<state root>/<group>/worktrees/<slug>``;
  otherwise ``None``.
* Inside a camp workspace (``enclosing_workspace`` of the working directory),
  the pad is not built under Outpost's state directory. The round and pad
  name are checked as above; a pad name with anything at
  ``<workspace>/sites/<pad>`` is refused as "already exists"; the pad (same
  layout) is built in a fresh owner-only ``scratch-pad-`` temp folder outside
  the workspace and published by the sibling ``publish-site/publish_site.py``,
  run as ``<python> <publish_site.py> <tmp> <pad> --workspace-path <ws>``
  under a wall-clock timeout. On success stdout is the publisher's stdout
  verbatim, then ``pad folder: <ws>/sites/<pad>``. A non-zero publisher exit is
  refused with each of its stderr lines as ``scratch-pad: publish: <line>``. On
  timeout the publisher is killed and the outcome is read from disk: a
  ``<ws>/sites/<pad>/index.html`` gives a ``note:`` that the pad was published
  but not confirmed (exit 0), otherwise a refusal. The temp folder is removed
  on every path, and nothing is created under Outpost's pads folder. With no
  ``publish-site`` skill beside this one, a run inside a workspace is refused.
* The pad slug matches ``^[a-z0-9][a-z0-9._-]*$`` and is at most 64 characters.
* Outside a workspace, pads live in ``<state>/pads/<slug>/`` where ``<state>`` is
  ``$OUTPOST_STATE_DIR``, else ``$XDG_STATE_HOME/outpost``, else
  ``~/.local/state/outpost``. A pad folder that already exists is refused.
* The pad is built in a sibling ``.tmp-`` folder and renamed into place, so a
  failure leaves neither a pad nor a temp folder. Layout::

      <pad>/index.html            sends the browser to round-1/index.html
      <pad>/round-1/<kit files>
      <pad>/round-1/round.js      ``window.PAD_ROUND = JSON.parse(<s>);``
                                  where ``<s>`` is the round's ASCII JSON
                                  encoded once more as a string literal

* Every refusal prints ``scratch-pad: <where>: <what>`` lines on stderr -- every
  problem found in one pass -- and exits non-zero. No pad and no temp folder
  are left behind; a pads folder this run had to create may remain, empty.

Security posture
----------------
Round text is untrusted data. It is written only into ``round.js``, as a
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
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

DEFAULT_KIT_DIR = Path(__file__).resolve().parent / "kit"
PUBLISHER_PATH = Path(__file__).resolve().parent.parent / "publish-site" / "publish_site.py"
PUBLISH_TIMEOUT_SECONDS = 60.0

_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]*")
_MAX_SLUG = 64
_MAX_OPTIONS = 26
_ROUND_KEYS = ("title", "intro", "questions")
_QUESTION_KEYS = ("prompt", "options")
_OPTION_KEYS = ("name", "cost", "pick", "layout")
_ROUND_JS_PREFIX = "window.PAD_ROUND = JSON.parse("
_PAD_INDEX = (
    "<!doctype html>\n"
    '<meta charset="utf-8">\n'
    '<meta http-equiv="refresh" content="0; url=round-1/index.html">\n'
    "<title>Scratch pad</title>\n"
    '<a href="round-1/index.html">Open the pad</a>\n'
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


def _read_round(round_path: Path) -> object:
    where = _q(str(round_path))
    try:
        raw = round_path.read_bytes()
    except OSError as exc:
        raise Refusal([_line(where, f"cannot read the round: {exc.strerror or exc}")])
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
    if "layout" not in option:
        problems.append(_line(where, 'no "layout" — every option is drawn'))
    pick = option.get("pick", False)
    if not isinstance(pick, bool):
        problems.append(_line(f"{where}.pick", "must be true or false"))
        return 0
    return 1 if pick else 0


def _check_slug(pad: str) -> list[str]:
    if len(pad) > _MAX_SLUG and _SLUG.fullmatch(pad):
        return [_line("--pad", f"the pad name is too long ({len(pad)} characters) — the limit is {_MAX_SLUG}")]
    if _SLUG.fullmatch(pad):
        return []
    return [
        _line(
            "--pad",
            f"{_q(pad)} is not a valid pad name — start with a lowercase letter or digit, then use "
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
    index: Path
    folder: Path
    lines: list[str]


def _run_publisher(cmd: Sequence[str], env: Mapping[str, str], timeout: float):
    """Run the publisher; raises ``subprocess.TimeoutExpired`` after killing it."""
    return subprocess.run(list(cmd), env=dict(env), capture_output=True, text=True, timeout=timeout)


def _esc(text: str) -> str:
    return _q(text)[1:-1]


def _check_and_encode(round_path: Path, pad: str, kit_dir: Path) -> str:
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
        round_js = _ROUND_JS_PREFIX + json.dumps(json.dumps(data, ensure_ascii=True, allow_nan=False)) + ");\n"
    except ValueError:
        raise Refusal([_line(_q(str(round_path)), "a number is too large to write — numbers must be finite")])
    except RecursionError:
        raise Refusal([_line(_q(str(round_path)), "nested too deeply to write")])
    if not kit_dir.is_dir():
        raise Refusal([_line("kit", f"{_q(str(kit_dir))} is not a directory — the pad kit is missing")])
    return round_js


def _write_pad(folder: Path, kit_dir: Path, round_js: str) -> None:
    shutil.copytree(kit_dir, folder / "round-1")
    (folder / "round-1" / "round.js").write_text(round_js, encoding="utf-8")
    (folder / "index.html").write_text(_PAD_INDEX, encoding="utf-8")


def _publish_to_workspace(
    workspace: Path, pad: str, kit_dir: Path, round_js: str, env: Mapping[str, str], runner, timeout: float
) -> Built:
    exists = Refusal([f"scratch-pad: pad {_q(pad)} already exists — choose another --pad name"])
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
    final = workspace / "sites" / pad
    # Only an early refusal: the publisher's own refusal without --overwrite, taken under its
    # workspace lock, is what makes two racing runs on one name yield one site.
    if os.path.lexists(final):
        raise exists

    tmp = Path(tempfile.mkdtemp(prefix="scratch-pad-"))
    try:
        _write_pad(tmp, kit_dir, round_js)
        cmd = [sys.executable, str(PUBLISHER_PATH), str(tmp), pad, "--workspace-path", str(workspace)]
        try:
            result = runner(cmd, env, timeout)
        except subprocess.TimeoutExpired:
            if (final / "index.html").is_file():
                note = "note: the pad was published but the publisher did not confirm it in time"
                return Built(final / "index.html", final, [note])
            raise Refusal(
                [_line("publish", f"the publisher did not finish within {timeout:g} seconds and no pad was published")]
            )
        if result.returncode != 0:
            reasons = [_line("publish", _esc(line)) for line in result.stderr.splitlines() if line.strip()]
            raise Refusal(reasons or [_line("publish", f"the publisher exited with status {result.returncode}")])
        return Built(final / "index.html", final, result.stdout.splitlines())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _build(
    round_path: Path,
    pad: str,
    kit_dir: Path,
    env: Mapping[str, str],
    cwd: Path,
    runner,
    timeout: float,
) -> Built:
    round_js = _check_and_encode(round_path, pad, kit_dir)
    workspace = enclosing_workspace(cwd, env)
    if workspace is not None:
        return _publish_to_workspace(workspace, pad, kit_dir, round_js, env, runner, timeout)

    pads_root = _pads_root(env)
    final = pads_root / pad
    _ensure_pads_root(pads_root)
    exists = Refusal([f"scratch-pad: pad {_q(pad)} already exists — choose another --pad name"])
    if os.path.lexists(final):
        raise exists

    tmp = Path(tempfile.mkdtemp(prefix=f".tmp-{pad}-", dir=pads_root))
    try:
        _write_pad(tmp, kit_dir, round_js)
        try:
            os.rename(tmp, final)
        except OSError:
            if os.path.lexists(final):
                raise exists
            raise
    except BaseException as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        if isinstance(exc, OSError):
            raise Refusal([_line(_q(str(final)), f"could not build the pad: {exc.strerror or exc}")])
        raise
    return Built(final / "index.html", final, [(final / "index.html").as_uri()])


def build_pad(
    round_path: Path | str,
    pad: str,
    *,
    kit_dir: Path | str = DEFAULT_KIT_DIR,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    publish_runner=None,
    publish_timeout: float = PUBLISH_TIMEOUT_SECONDS,
) -> tuple[Path, Path]:
    """Check the round and build the pad; return ``(index_html, pad_folder)``.

    Raises :class:`Refusal` (carrying the ``scratch-pad:`` lines) when the pad
    name or round is wrong, the pad exists, or the build fails; in every such
    case nothing is left behind. *cwd* (default the process's) decides whether
    the pad is built on disk or published into the enclosing camp workspace;
    *publish_runner* is ``(cmd, env, timeout) -> CompletedProcess``.
    """
    built = _build(
        Path(round_path),
        pad,
        Path(kit_dir),
        os.environ if env is None else env,
        Path.cwd() if cwd is None else Path(cwd),
        publish_runner or _run_publisher,
        publish_timeout,
    )
    return built.index, built.folder


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
    parser.add_argument("--pad", required=True, help="pad name: ^[a-z0-9][a-z0-9._-]*$")
    parser.add_argument("--round", required=True, dest="round_path", help="path to the round's JSON file")
    args = parser.parse_args(argv)
    try:
        built = _build(
            Path(args.round_path),
            args.pad,
            DEFAULT_KIT_DIR,
            os.environ if env is None else env,
            Path.cwd() if cwd is None else Path(cwd),
            publish_runner or _run_publisher,
            publish_timeout,
        )
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
