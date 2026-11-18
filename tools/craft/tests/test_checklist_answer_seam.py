"""Seam: the answer writer runs as a real process against the real lore CLI.

    checklist_gather.py --json -> checklist_answer.py -> lore record update|amend
    -> lore record criteria -> candidate_set.py

Every `lore` call is a subprocess of lore's real CLI pointed at a temp vault; the scripts run as
subprocesses, with the production runner (no injected `runner`).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from _lore_runner import LoreRunner, seam_env

pytestmark = pytest.mark.seam_real

_SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts"
TODAY = "2026-11-16"

SPEC_BODY = """# Seam spec

Prose.

## Acceptance Criteria

- **AC1.** The first thing works.
- **AC2.** The banner shows the count.

## Slices

- **Parent** — claim (`task/build-it`, closed 2026-11-08, covers AC1, partially covers AC2)

## Waiting

- **AC2.** pending check — task/build-it
  - check: the banner shows 3
  - how: open the app
  - changed behaviour: no
  - 2026-11-08 waiting: set by slice
"""


def _run(env, script, *args, stdin=None):
    return subprocess.run([sys.executable, str(_SCRIPTS / script), *args], input=stdin,
                          capture_output=True, text=True, env=env)


@pytest.fixture
def world(tmp_path):
    lore = LoreRunner(tmp_path / "lore")
    env = seam_env(lore, tmp_path)
    env["CRAFT_WAITING_GATE_TODAY"] = TODAY
    created = lore(["record", "create", "--kind", "spec", "--title", "seam-spec", "--status", "ready",
                    "--vault", "default"], SPEC_BODY)
    assert created.returncode == 0, created.stderr
    return lore, env, created.stdout.splitlines()[0]


# inert-gate: allow wiring smoke: real lore, real scripts
def test_a_gathered_entry_answered_passed_is_saved_closed_and_leaves_the_candidates(world, tmp_path):
    lore, env, spec = world
    gathered = json.loads(_run(env, "checklist_gather.py", "--json").stdout)
    (entry,) = gathered["entries"]
    mktemp_dir = tmp_path / "mktemp"
    mktemp_dir.mkdir()
    env["TMPDIR"] = str(mktemp_dir)
    words = mktemp_dir / "tmp.seam0001"
    words.write_text("banner showed 3\n", encoding="utf-8")

    done = _run(env, "checklist_answer.py", "--vault", entry["vault"], "--spec", entry["spec"],
                "--criterion", entry["criterion"], "--slice", entry["building_slice"], "--answer", "passed",
                "--base", entry["revision"], "--words-file", str(words))

    assert (done.returncode, done.stdout.splitlines()[0]) == (0, "saved: AC2 passed"), done.stderr
    assert not words.exists()
    shown = json.loads(lore(["record", "show", "--json", "--vault", "default", spec]).stdout)
    assert "  - 2026-11-16 passed: banner showed 3\n" in shown["body"]
    criteria = lore(["record", "criteria", spec, "--vault", "default"]).stdout
    assert json.loads(criteria)["closed"] == ["AC2"]
    state = tmp_path / "state.json"
    state.write_text(criteria, encoding="utf-8")
    derived = _run(env, "candidate_set.py", "--criteria-state", str(state), stdin=shown["body"])
    tokens = dict(line.split(": ", 1) for line in derived.stdout.splitlines() if ": " in line)
    assert tokens["closed"] == "AC2"
    assert "AC2" not in tokens["candidates"].split(", ")

    again = _run(env, "checklist_answer.py", "--vault", "default", "--spec", spec, "--criterion", "AC2",
                 "--slice", "task/build-it", "--answer", "failed", "--base", entry["revision"],
                 "--words-file", str(words))
    assert (again.returncode, again.stdout) == (1, "")
    assert "reason-code: stale" in again.stderr
