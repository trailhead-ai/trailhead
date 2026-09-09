"""The agent cost-tier gate, run against the real tree and against seeded trees.

`model:` and `effort:` are declarations in an agent's frontmatter, and nothing in
this repo executes them — the harness reads them at dispatch time. That makes the
convention a lint over source text rather than a behaviour, so the check itself
lives in `scripts/agent-tier-gate`, wired as a pre-commit hook. This suite runs
that gate rather than reimplementing its parsing, so the verdict a commit gets and
the verdict the suite reports can never drift apart.

The seeded cases below are what make the clean-tree case meaningful: each plants
one specific violation in a tmp tree and asserts the same gate reports it. A gate
that scanned nothing would pass the real tree forever and fail every seeded case.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE = _REPO_ROOT / "scripts" / "agent-tier-gate"


def _run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _agent(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


_GOOD = """---
name: probe
description: A probe agent.
model: sonnet
effort: medium
---

Body.
"""


def test_every_shipped_agent_declares_its_tier():
    """The real tree, scanned by the gate with no arguments."""
    result = _run()
    assert result.returncode == 0, result.stderr


def test_the_gate_scans_a_non_empty_set():
    """Anti-vacuity: a gate whose glob found nothing would pass the tree above
    forever. `--list` reports the set it would scan, so the enumeration is
    checked through the gate rather than reimplemented here.
    """
    result = subprocess.run(
        [sys.executable, str(_GATE), "--list"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    listed = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(listed) >= 20, (
        f"the gate enumerates only {len(listed)} agents; expected the full shipped set"
    )


def test_a_conforming_agent_passes(tmp_path):
    result = _run(_agent(tmp_path / "agents" / "probe.md", _GOOD))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda b: b.replace("model: sonnet\n", ""), "declares no `model:`"),
        (lambda b: b.replace("effort: medium\n", ""), "declares no `effort:`"),
        (lambda b: b.replace("model: sonnet", "model: gpt"), "declares model `gpt`"),
        (lambda b: b.replace("effort: medium", "effort: colossal"), "declares effort `colossal`"),
        (lambda b: "No frontmatter at all.\n", "no closed YAML frontmatter"),
    ],
    ids=["no-model", "no-effort", "bad-model", "bad-effort", "no-frontmatter"],
)
def test_the_gate_reports_each_violation(tmp_path, mutation, expected):
    seeded = _agent(tmp_path / "agents" / "probe.md", mutation(_GOOD))
    result = _run(seeded)
    assert result.returncode == 1, (
        f"the gate passed an agent it should have refused:\n{result.stdout}{result.stderr}"
    )
    assert expected in result.stderr, result.stderr
