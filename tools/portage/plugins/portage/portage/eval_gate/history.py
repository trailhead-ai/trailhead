"""The eval gate's timing history: one JSON line per case a runner was started for.

``<cache_dir("portage")>/eval-gate/history.jsonl`` (``history_path``) holds one object per line,
appended by ``run`` after a case's runner started (a ``timeout`` included, with the runs that came
back). A cache hit, and a not-run decided before any runner started (``case-removed-on-branch``,
``corpus-new-on-branch``, ``head-moved``, a refused harness, a failed canary), append nothing::

    {"recorded_at": "<UTC, YYYY-MM-DDTHH:MM:SSZ>", "plugin": str, "case": str,
     "fingerprint": str | null,         # the pass-cache key; null when the case is uncacheable
     "runner_version": str | null,      # the runner JSON's claudeVersion, when it looks like a version
     "pinned_runner": bool,             # PORTAGE_EVAL_RUNNER was set
     "tier": str | null,                # the plan's tier
     "verdict": "pass" | "fail" | "not-run", "reason": str | null,
     "budget_seconds": number,          # the call's timeout
     "elapsed_seconds": number,         # the case's wall time minus a fresh canary's
     "runs": [{"passed": bool, "errored": bool, "duration_seconds": number | null,
               "cost_usd": number | null, "judge_cost_usd": number | null}, ...]}

The file holds case names, hashes, versions and numbers only, and is per host: it lives in the
host's cache directory and nothing syncs it. The directory goes through the pass cache's private
directory check. Each line is one ``os.write`` on a descriptor opened ``O_APPEND | O_NOFOLLOW``
with mode 0600, so gates running in two worktrees do not interleave. A file over ``MAX_BYTES`` is
renamed to ``history.1.jsonl`` (replacing any earlier one) before the next append, which bounds the
history at two generations; readers take both files. A history that cannot be written prints one
``eval-gate: timing history not written: <reason>`` line on stderr and changes nothing else.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

from trailhead import paths

from .cache import APP, SUBDIR, ensure_private_dir

HISTORY_NAME = "history.jsonl"
ROTATED_NAME = "history.1.jsonl"
MAX_BYTES = 5 * 1024 * 1024
_VERSION = re.compile(r"[0-9][0-9A-Za-z.+-]{0,31}")


def history_dir(env: dict[str, str]) -> Path:
    return paths.cache_dir(APP, env=env) / SUBDIR


def history_path(env: dict[str, str]) -> Path:
    return history_dir(env) / HISTORY_NAME


def rotated_path(env: dict[str, str]) -> Path:
    return history_dir(env) / ROTATED_NAME


def runner_version(doc: object) -> str | None:
    """The runner JSON's ``claudeVersion`` when it has the shape of a version, else ``None``."""
    version = doc.get("claudeVersion") if isinstance(doc, dict) else None
    if isinstance(version, str) and _VERSION.fullmatch(version):
        return version
    return None


def append(record: dict, env: dict[str, str]) -> None:
    """Append ``record`` as one line; on any failure print the one stderr line and return."""
    try:
        base = ensure_private_dir(history_dir(env), "eval-gate history")
        _rotate(base)
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode()
        fd = os.open(base / HISTORY_NAME,
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception as e:
        warn(e)


def warn(reason: object) -> None:
    print(f"eval-gate: timing history not written: {reason}", file=sys.stderr)


def _rotate(base: Path) -> None:
    try:
        st = os.lstat(base / HISTORY_NAME)
    except FileNotFoundError:
        return
    if stat.S_ISREG(st.st_mode) and st.st_size > MAX_BYTES:
        os.replace(base / HISTORY_NAME, base / ROTATED_NAME)
