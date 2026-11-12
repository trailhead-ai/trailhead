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
