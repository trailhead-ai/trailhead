"""`outpost-gateway` against a real Outpost: on-demand registration and the reason codes Outpost refuses with.

Opt-in tier (``seam_real``): each test starts a real Outpost (``node --import tsx server/index.ts``) on a
free port with a temp state directory and a temp camp layout, and runs the real gateway command as a child of
a fake harness. The refusal messages are read from the Outpost checkout's shared fixture
(``tests/fixtures/refusal-reasons.json``), so the wording asserted here is the wording Outpost sends.

    .venv/bin/python -m pytest -m seam_real trailhead/tests/test_gateway_command_real_outpost.py
"""

from __future__ import annotations

import json
import sys

import pytest

from ._gateway_e2e_support import (
    W1,
    GROUP,
    make_camp,
    outpost_checkout,
    real_outpost,
    run_fake_harness,
    step_env,
)
from ._gateway_support import gateway_runner_cmd, run_gateway_runner, write_authority, write_credential
from ._harness_process_support import make_fake_harness

pytestmark = pytest.mark.seam_real

WORKSPACE = f"{GROUP}/{W1}"


def refusal_messages() -> dict[str, str]:
    return json.loads((outpost_checkout() / "tests" / "fixtures" / "refusal-reasons.json").read_text())


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("real-outpost")
    camp = make_camp(root)
    with real_outpost(root, camp) as outpost:
        yield root, camp, outpost


def gateway(root, name, cwd, outpost, argv, *, env=None):
    """Run the gateway command with *argv* as a child of a fake harness started in *cwd*; its output and exit code."""
    env = env if env is not None else step_env(root, outpost)
    cmd = gateway_runner_cmd(outpost.port, root / f"{name}-env.json", argv)
    out = run_fake_harness(root, name, cwd, [("gateway", cmd)], env)["gateway"]
    *lines, last = out.splitlines()
    return int(last.removeprefix("rc=")), "\n".join(lines)


def test_a_session_in_a_workspace_with_no_credential_registers_itself_and_the_call_succeeds(world):
    root, camp, outpost = world
    rc, out = gateway(root, "register-on-demand", camp.w1, outpost, ["run", "list"])
    assert (rc, out) == (0, f"No runs in workspace {WORKSPACE}")
    assert outpost.registry_workspaces()[-1][1] == WORKSPACE
    assert len(outpost.credential_files()) >= 1


def test_a_session_outside_any_camp_workspace_is_told_such_sessions_have_no_gateway(world):
    root, camp, outpost = world
    rc, out = gateway(root, "outside", camp.elsewhere, outpost, ["run", "list"])
    assert rc == 2
    assert out == (
        "outpost-gateway: this session is not in a camp workspace; sessions outside any camp workspace have no gateway"
    )


def test_a_registration_outpost_refuses_prints_outposts_reason_and_next_step(world, tmp_path):
    root, camp, outpost = world
    wrong = tmp_path / "wrong-authority-state"
    write_authority(wrong, "not-the-registration-authority")
    rc, out = gateway(
        root,
        "wrong-authority",
        camp.w1,
        outpost,
        ["run", "list"],
        env={**step_env(root, outpost), "OUTPOST_STATE_DIR": str(wrong)},
    )
    assert rc == 2
    assert out == (
        "outpost-gateway: this session is not registered: registration_authority_required: "
        "Registration needs the registration authority; present it as an Authorization: Bearer header."
    )


def test_a_credential_outpost_does_not_know_is_refused_with_the_fixtures_credential_unrecognised_message(
    world, tmp_path
):
    root, camp, outpost = world
    state = tmp_path / "unknown-credential-state"
    state.mkdir()
    result = run_gateway_runner(
        make_fake_harness(root / "unknown-credential"),
        ["run", "list"],
        state_dir=state,
        port=outpost.port,
        seed=lambda key: write_credential(state, key, client_id="not-a-client", client_secret="not-a-secret"),
        scratch=tmp_path,
    )
    assert result.proc.returncode == 2
    assert result.proc.stderr == (
        "outpost-gateway: Outpost refused the credential: credential_unrecognised: "
        f"{refusal_messages()['credential_unrecognised']}\n"
    )


def test_a_token_refused_as_expired_is_exchanged_again_once_then_refused_with_the_fixtures_message(tmp_path_factory):
    root = tmp_path_factory.mktemp("short-lived-tokens")
    camp = make_camp(root)
    with real_outpost(root, camp, extra_env={"BROKER_TOKEN_TTL_MS": "1"}) as outpost:
        rc, out = gateway(root, "expired", camp.w1, outpost, ["run", "list"])
    assert rc == 2
    assert out == f"outpost-gateway: credential_expired: {refusal_messages()['credential_expired']}"


def test_a_credential_whose_session_ended_is_refused_with_the_fixtures_credential_revoked_message(world):
    root, camp, outpost = world
    revoke = (
        f"{sys.executable} -c \"import sqlite3; d = sqlite3.connect('{outpost.db_path}'); "
        "d.execute(\\\"UPDATE broker_clients SET revoked_at = 1 WHERE principal_kind = 'workspace-session'\\\"); "
        "d.commit()\""
    )
    list_cmd = gateway_runner_cmd(outpost.port, root / "revoked-env.json", ["run", "list"])
    sections = run_fake_harness(
        root,
        "revoked",
        camp.w1,
        [("first", list_cmd), ("revoke", revoke), ("second", list_cmd)],
        step_env(root, outpost),
    )
    assert sections["first"].splitlines()[-1] == "rc=0"
    assert sections["revoke"].splitlines()[-1] == "rc=0"
    assert sections["second"].splitlines() == [
        "outpost-gateway: Outpost refused the credential: "
        f"credential_revoked: {refusal_messages()['credential_revoked']}",
        "rc=2",
    ]
