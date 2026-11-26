"""The root conftest collects the opt-in gateway e2e file only when its gate is set (collect-only)."""

import os

import pytest

from ._tier_runs import run_pytest

GATED = "test_gateway_e2e_andromeda.py"
OTHER = "test_gateway_e2e_stream.py"


def _collected(*args: str, gate: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "TRAILHEAD_GATEWAY_E2E"}
    if gate is not None:
        env["TRAILHEAD_GATEWAY_E2E"] = gate
    os.environ.clear()
    os.environ.update(env)
    return run_pytest("--collect-only", "-q", "-m", "not benchmark", "trailhead/tests", *args).stdout


@pytest.fixture(autouse=True)
def _restore_environ():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.mark.parametrize(("gate", "collected"), [(None, False), ("0", False), ("true", False), ("1", True)])
def test_the_gated_file_is_collected_only_when_its_gate_is_one(gate, collected):
    out = _collected(gate=gate)
    assert (GATED in out) is collected
    assert OTHER in out


def test_an_ignore_flag_still_drops_a_file_while_the_hook_is_loaded():
    out = _collected(f"--ignore=trailhead/tests/{OTHER}", gate=None)
    assert OTHER not in out


REAL_OUTPOST_GATED = (
    "test_gateway_command_real_outpost.py",
    "test_outpost_open_real_outpost.py",
    "test_gateway_e2e_credential_scan_real_outpost.py",
)
REAL_OUTPOST_UNGATED = "test_gateway_e2e_credential_scan.py"


def _collected_real_outpost(gate: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("TRAILHEAD_REAL_OUTPOST", "TRAILHEAD_GATEWAY_E2E")}
    if gate is not None:
        env["TRAILHEAD_REAL_OUTPOST"] = gate
    return run_pytest("--collect-only", "-q", "-m", "not benchmark", "trailhead/tests", env=env).stdout


@pytest.mark.parametrize(("gate", "collected"), [(None, False), ("0", False), ("true", False), ("1", True)])
def test_the_files_needing_a_real_outpost_are_collected_only_when_their_gate_is_one(gate, collected):
    out = _collected_real_outpost(gate)
    for name in REAL_OUTPOST_GATED:
        assert (name in out) is collected, name
    assert REAL_OUTPOST_UNGATED in out
    assert GATED not in out
