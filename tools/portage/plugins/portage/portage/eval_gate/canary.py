"""The sandbox-write canary: what it asks of the runner, and its per-state-directory cache.

The shipped case lives at ``<portage plugin>/eval-canary/sandbox-write`` — outside every
``evals/`` corpus, so mapping discovery and the corpus checks never see it — and is run
through the runner's ``--eval-dir eval-canary``. One run, on a cheap pinned model, whose
agent creates one file through its sandboxed Bash; ``file_exists`` grades it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

CASE = "sandbox-write"
EVAL_DIR = "eval-canary"
MODEL = "claude-haiku-5-5"
ALLOW_TOOLS = ("Bash(touch *)",)
MAX_COST_USD = 0.25
TIMEOUT_SECONDS = 120
CACHE_NAME = "canary.json"
RUN_JSON = "canary-run.json"
RUN_OUTPUT = "canary-run"

_VERDICTS = {"pass", "fail", "not-run"}


def plugin_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def read_cached(state: Path) -> dict | None:
    try:
        doc = json.loads((state / CACHE_NAME).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("case") != CASE or doc.get("verdict") not in _VERDICTS:
        return None
    return doc


def write_cached(state: Path, result: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=state, prefix=f".{CACHE_NAME}.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    os.replace(tmp, state / CACHE_NAME)
