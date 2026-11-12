"""The eval gate's pass cache: a case's pass is reused while nothing it depends on changed.

``fingerprint(plugin_dir, entry)`` is a SHA-256 hex digest over, in this order:

- every regular file in the case directory ``<plugin_dir>/evals/<case>`` (recursive), by
  relative path and bytes;
- every regular file of the plan entry's ``measures`` (a path under ``plugin_dir``; a
  directory contributes every regular file under it), by path and bytes;
- every regular file directly under ``<plugin_dir>/evals`` (the corpus's shared scaffolds)
  except ``eval-gate.json``, by name and bytes;
- the canonical JSON of the entry's ``runner`` block, ``runs``, ``pass_at``, ``plugin_root``
  and ``case``.

Symlinks and other non-regular files are skipped. A plan entry with no ``measures`` key has
none. The key leaves out the ``claude`` CLI version, scripts outside ``evals/`` that a scaffold
calls, and a model alias the runner re-targets: none of them change it. Delete the cache
directory to force a re-run.

A pass is stored as ``<cache_dir("portage")>/eval-gate/<fingerprint>.json`` (``cache_dir``
resolved from the injected ``env``)::

    {"verdict": "pass", "passed": int, "failed": int, "errored": int, "runs": [...]}

``lookup`` returns that record (the result's run counts and per-run grader verdicts) or
``None``; ``store`` writes it atomically. A fail or a not-run is never stored. The cache
directory must be a real directory, owned by the caller and not group- or world-writable
(``ensure_private_dir``, the check the runner temp base also applies): otherwise nothing is
read or written. A refused, unreadable or corrupt cache is a miss and never an error.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from trailhead import paths

from .select import MAPPING_NAME

APP = "portage"
SUBDIR = "eval-gate"


def ensure_private_dir(path: Path, label: str) -> Path:
    """Create ``path`` (mode 0700, parents as needed) and return it; raise ``ValueError``
    naming ``label`` when it is not a real directory, is owned by another user, or is
    group- or world-writable."""
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = os.lstat(path)
    except OSError as e:
        raise ValueError(f"{label} {path} unusable: {e}") from e
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ValueError(f"{label} {path} is a symlink or not a directory")
    if st.st_uid != os.geteuid():
        raise ValueError(f"{label} {path} is owned by another user")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(f"{label} {path} is group- or world-writable")
    return path


def _regular_files(root: Path, recursive: bool) -> list[Path]:
    try:
        st = os.lstat(root)
    except OSError:
        return []
    if stat.S_ISREG(st.st_mode):
        return [root]
    if not stat.S_ISDIR(st.st_mode):
        return []
    found: list[Path] = []
    for child in sorted(root.iterdir()):
        try:
            mode = os.lstat(child).st_mode
        except OSError:
            continue
        if stat.S_ISREG(mode):
            found.append(child)
        elif recursive and stat.S_ISDIR(mode):
            found += _regular_files(child, True)
    return found


def _feed(h, label: str, path: Path, base: Path) -> None:
    data = path.read_bytes()
    h.update(f"{label}\0{path.relative_to(base).as_posix()}\0{len(data)}\0".encode())
    h.update(data)


def fingerprint(plugin_dir: Path, entry: dict) -> str:
    """The cache key of a plan entry's case under ``plugin_dir``. May raise ``OSError``."""
    plugin_dir = Path(plugin_dir)
    evals = plugin_dir / "evals"
    h = hashlib.sha256()
    case_dir = evals / entry["case"]
    for f in _regular_files(case_dir, True):
        _feed(h, "case", f, case_dir)
    for m in entry.get("measures") or []:
        root = plugin_dir / m
        for f in _regular_files(root, True):
            _feed(h, "measure", f, plugin_dir)
    for f in _regular_files(evals, False):
        if f.name != MAPPING_NAME:
            _feed(h, "scaffold", f, evals)
    h.update(json.dumps(
        {k: entry[k] for k in ("runner", "runs", "pass_at", "plugin_root", "case")},
        sort_keys=True, separators=(",", ":")).encode())
    return h.hexdigest()


def _dir(env: dict[str, str]) -> Path:
    return paths.cache_dir(APP, env=env) / SUBDIR


def lookup(fp: str, env: dict[str, str]) -> dict | None:
    """The stored pass for ``fp``, or ``None`` on any miss, refusal or corruption."""
    try:
        base = ensure_private_dir(_dir(env), "eval-gate cache")
        path = base / f"{fp}.json"
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        doc = json.loads(path.read_text())
        counts = [doc[k] for k in ("passed", "failed", "errored")]
        if (doc["verdict"] != "pass" or not isinstance(doc["runs"], list)
                or any(isinstance(c, bool) or not isinstance(c, int) or c < 0 for c in counts)):
            return None
        return {"verdict": "pass", "passed": counts[0], "failed": counts[1],
                "errored": counts[2], "runs": doc["runs"]}
    except (OSError, ValueError, KeyError, TypeError, paths.PathResolutionError):
        return None


def store(fp: str, result: dict, env: dict[str, str]) -> None:
    """Write ``result`` (a pass) under ``fp`` atomically; do nothing for any other verdict
    or when the cache directory is refused or unwritable."""
    if result.get("verdict") != "pass":
        return
    try:
        base = ensure_private_dir(_dir(env), "eval-gate cache")
        doc = {k: result[k] for k in ("verdict", "passed", "failed", "errored", "runs")}
        fd, tmp = tempfile.mkstemp(dir=base, prefix=f".{fp}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(doc, indent=2) + "\n")
            os.replace(tmp, base / f"{fp}.json")
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except (OSError, ValueError, KeyError, paths.PathResolutionError):
        return
