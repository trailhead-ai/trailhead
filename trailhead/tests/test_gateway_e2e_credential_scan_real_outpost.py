"""The credential scan run against a real Outpost and the real pinned ``outpost-gateway`` shim.

Opt-in with the rest of the tests that need an Outpost checkout (``TRAILHEAD_REAL_OUTPOST=1``, see the
root conftest); the scanner's own unit tests live in ``test_gateway_e2e_credential_scan.py`` and run by default.
"""

from __future__ import annotations

import http.client
import subprocess

import pytest

from ._gateway_e2e_support import (
    GROUP,
    W1,
    ArgumentSampler,
    TokenRecorder,
    install_shims,
    known_secrets,
    outpost_request_records,
    make_camp,
    real_outpost,
    run_fake_harness,
    secrets_in_text,
    step_env,
)

pytestmark = pytest.mark.seam_real


def _flow(root, camp, outpost, shim_bin, steps):
    return run_fake_harness(root, "scan-flow", camp.w1, steps, step_env(root, outpost))


def test_a_session_listing_runs_through_the_real_shim_leaves_no_secret_or_token_in_arguments_or_request_records(
    tmp_path_factory,
):  # AC81, AC86
    root = tmp_path_factory.mktemp("scan-flow")
    camp = make_camp(root)
    with real_outpost(root, camp) as outpost, TokenRecorder(outpost.port) as recorder:
        shim_bin = install_shims(root, outpost, port=recorder.port)
        gateway = shim_bin / "outpost-gateway"
        with ArgumentSampler() as sampler:
            sections = _flow(
                root, camp, outpost, shim_bin, [("first", f"{gateway} run list"), ("second", f"{gateway} run list")]
            )
        secrets = known_secrets(outpost, recorder.tokens)
        records = outpost_request_records(outpost)
    expected = f"No runs in workspace {GROUP}/{W1}\nrc=0"
    assert (sections["first"], sections["second"]) == (expected, expected), sections
    assert {"client secret", "registration authority"} <= {k.split(" (")[0] for k in secrets}, list(secrets)
    assert any(k.startswith("access token") for k in secrets), "the proxy saw no token, so the scan looked for none"
    assert set(records) == {"timeline events", "Outpost's log"}
    assert records["timeline events"], "Outpost recorded no request, so there was nothing to scan"
    assert sampler.problems(secrets) == []
    assert secrets_in_text(secrets, records) == []


def test_the_same_scan_reports_the_clients_real_secret_when_a_process_carries_it_in_its_arguments(tmp_path_factory):
    root = tmp_path_factory.mktemp("scan-control")
    camp = make_camp(root)
    with real_outpost(root, camp) as outpost, TokenRecorder(outpost.port) as recorder:
        shim_bin = install_shims(root, outpost, port=recorder.port)
        _flow(root, camp, outpost, shim_bin, [("list", f"{shim_bin / 'outpost-gateway'} run list")])
        secrets = known_secrets(outpost, recorder.tokens)
        leaked = next(v for k, v in secrets.items() if k.startswith("client secret"))
        with ArgumentSampler() as sampler:
            subprocess.run(["sh", "-c", "sleep 0.3", "leaky-tool", leaked], check=True)
        problems = sampler.problems(secrets)
    assert len(problems) == 1 and problems[0].startswith("client secret") and "leaky-tool" not in problems[0], problems
    assert leaked not in problems[0]


def test_a_loopback_page_request_is_not_handed_the_operator_ui_cookie(tmp_path_factory):  # AC84
    root = tmp_path_factory.mktemp("no-cookie")
    camp = make_camp(root)
    with real_outpost(root, camp) as outpost:
        conn = http.client.HTTPConnection("127.0.0.1", outpost.port, timeout=10)
        conn.request("GET", "/", headers={"Accept": "text/html"})
        answer = conn.getresponse()
        answer.read()
        cookies = answer.getheader("Set-Cookie")
        conn.close()
    assert cookies is None
