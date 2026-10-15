"""The tidy-followups eval's fixture vault, built and gathered on Linux.

The eval arms run on macOS inside ``scripts/eval-sandbox``; a fixture that does not
gather the way ``expected.md`` says would only surface there. This builds the very
vault the arms use (``make-fixture-vault.py``) with a from-scratch child environment,
then runs the real ``followup_gather.py`` against each spec the builder made and
asserts the exit code and the gathered names.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

CRAFT = Path(__file__).resolve().parents[1] / "plugins" / "craft"
SCRIPT = CRAFT / "scripts" / "followup_gather.py"
BUILDER = (
    CRAFT / "evals" / "tidy-followups-presents-a-finished-spec" / "fixtures" / "make-fixture-vault.py"
)
LORE_CLI = Path(__file__).resolve().parents[2] / "lore" / "plugins" / "lore" / "cli" / "lore"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("tidy-fixture")
    shim = root / "lore-under-test"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{LORE_CLI}" "$@"\n')
    shim.chmod(0o755)
    dest = root / "run"
    scrubbed = {"PATH": "/usr/bin:/bin", "LORE_BIN": str(shim)}
    r = subprocess.run(
        [sys.executable, str(BUILDER), str(dest)], capture_output=True, text=True, env=scrubbed
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def gather(env, spec):
    return subprocess.run(
        [sys.executable, str(SCRIPT), spec], capture_output=True, text=True, env=env
    )


@pytest.mark.parametrize(
    "fixture,entry",
    [
        ("stopped", "stopped-marker"),
        ("completed", "complete-marker"),
        ("complete-unmarked", "complete-status"),
    ],
)
def test_an_ended_spec_gathers_exactly_its_follow_ups(built, fixture, entry):
    r = gather(built["env"], built["specs"][fixture])
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["entry"] == entry
    assert [f["name"] for f in result["followups"]] == sorted(built["followups"][fixture])


def test_a_spec_still_slicing_is_refused(built):
    r = gather(built["env"], built["specs"]["slicing"])
    assert r.returncode == 1
    assert "reason-code: slice-loop-not-ended" in r.stderr


def test_a_spec_with_no_follow_ups_gathers_an_empty_set(built):
    r = gather(built["env"], built["specs"]["empty"])
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["followups"] == []
    assert result["slices"], "the empty-set message names the slices checked"


def test_the_mixed_spec_gathers_open_ready_and_blocked_but_not_finished_work(built):
    r = gather(built["env"], built["specs"]["mixed"])
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    names = [f["name"] for f in result["followups"]]
    assert names == sorted(built["followups"]["mixed"])
    assert {f["status"] for f in result["followups"]} == {"open", "ready", "blocked"}
    for decoy in built["decoys"]:
        assert decoy not in names


def test_the_planted_credential_is_redacted_from_the_gathered_title(built):
    r = gather(built["env"], built["specs"]["mixed"])
    assert built["credential"] not in r.stdout
    titles = [f["title"] for f in json.loads(r.stdout)["followups"]]
    assert any("[REDACTED]" in t for t in titles)


def test_a_failing_lookup_is_a_failure_not_a_partial_set(built):
    r = gather(built["failing_env"], built["specs"]["mixed"])
    assert r.returncode == 2
    assert r.stdout == ""
    assert "reason-code: lore-failed" in r.stderr


def test_the_manifest_env_runs_lore_and_the_script_by_their_shebangs(tmp_path):
    """An arm runs `lore` and the gathering script as executables, so `python3` is
    whatever the manifest's PATH finds first. The fenced PATH must still reach an
    interpreter lore supports, on hosts where /usr/bin/python3 is older (macOS)."""
    dest = tmp_path / "run"
    r = subprocess.run(
        [sys.executable, str(BUILDER), str(dest)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LORE_BIN": str(LORE_CLI)},
    )
    assert r.returncode == 0, r.stderr
    built = json.loads(r.stdout)
    g = subprocess.run(
        [str(SCRIPT), built["specs"]["stopped"]], capture_output=True, text=True, env=built["env"]
    )
    assert g.returncode == 0, g.stderr
    assert [f["name"] for f in json.loads(g.stdout)["followups"]] == sorted(
        built["followups"]["stopped"]
    )
