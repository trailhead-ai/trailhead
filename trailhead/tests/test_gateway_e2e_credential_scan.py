"""The credential scan the gateway end-to-end flow runs, and its use against a real Outpost.

The scan looks for a session's client secret and access tokens in every process's command-line
arguments (AC81), in a run's stored log (AC85), and in Outpost's request records: its timeline events
and its log (AC86). The first group of tests runs the scanner against planted secrets, so a scan that
could never find one cannot pass. The ``seam_real`` tests run a real Outpost with the real pinned
``outpost-gateway`` shim from ``trailhead install``.
"""

from __future__ import annotations

import http.client
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ._gateway_e2e_support import (
    GROUP,
    W1,
    ArgumentSampler,
    TokenRecorder,
    install_shims,
    known_secrets,
    outpost_request_records,
    stored_run_logs,
    arguments_with_secrets,
    make_camp,
    process_arguments,
    real_outpost,
    run_fake_harness,
    secrets_in_text,
    step_env,
)

SECRET = "client-secret-PLANTED-3e91"
TOKEN = "access-token-PLANTED-77ab"


def _spawn_with_argument(value: str, seconds: str = "30") -> subprocess.Popen:
    proc = subprocess.Popen(["sh", "-c", f"sleep {seconds}", "scan-probe", value])
    time.sleep(0.1)
    return proc


def test_a_secret_in_a_live_processs_arguments_is_reported_by_label_and_program_never_by_value():
    proc = _spawn_with_argument(SECRET)
    try:
        problems = arguments_with_secrets({"client secret": SECRET}, process_arguments())
    finally:
        proc.kill()
        proc.wait()
    assert len(problems) == 1, problems
    assert "client secret" in problems[0] and "sh " in problems[0] and str(proc.pid) in problems[0]
    assert SECRET not in problems[0]


def test_the_argument_scan_finds_nothing_when_no_process_carries_the_secret():
    proc = _spawn_with_argument("unrelated-value")
    try:
        assert arguments_with_secrets({"client secret": SECRET}, process_arguments()) == []
    finally:
        proc.kill()
        proc.wait()


def test_the_sampler_keeps_what_a_short_lived_process_showed_after_it_has_exited():
    gone_before = _spawn_with_argument(SECRET, "0.1")
    gone_before.wait()
    with ArgumentSampler(interval=0.01) as sampler:
        time.sleep(0.1)
        short = subprocess.Popen(["sh", "-c", "sleep 0.3", "scan-probe", TOKEN])
        short.wait()
        time.sleep(0.1)
    problems = sampler.problems({"client secret": SECRET, "access token": TOKEN})
    assert len(problems) == 1 and "access token" in problems[0], problems


def test_a_secret_in_a_stored_log_or_a_request_record_is_reported_by_source():
    sources = {"run log": f"step output\nusing {TOKEN}\n", "timeline events": "token | allowed", "outpost log": "clean"}
    problems = secrets_in_text({"access token": TOKEN, "client secret": SECRET}, sources)
    assert len(problems) == 1 and "access token" in problems[0] and "run log" in problems[0], problems
    assert TOKEN not in problems[0]
    assert secrets_in_text({"client secret": SECRET}, sources) == []


class _Origin(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.dumps({"access_token": TOKEN, "token_type": "Bearer"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_the_token_recorder_relays_a_request_and_learns_the_tokens_both_ways():
    origin = ThreadingHTTPServer(("127.0.0.1", 0), _Origin)
    threading.Thread(target=origin.serve_forever, daemon=True).start()
    try:
        with TokenRecorder(origin.server_address[1]) as recorder:
            conn = http.client.HTTPConnection("127.0.0.1", recorder.port, timeout=10)
            conn.request("POST", "/broker/token", body="x", headers={"Authorization": "Bearer bearer-PLANTED-5c"})
            answer = conn.getresponse()
            body = json.loads(answer.read())
            conn.close()
            time.sleep(0.1)
        assert body["access_token"] == TOKEN, "the recorder did not relay the origin's answer"
        assert recorder.tokens == {TOKEN, "bearer-PLANTED-5c"}
    finally:
        origin.shutdown()
        origin.server_close()


def test_the_stored_run_logs_are_read_by_name_and_a_token_in_one_is_found(tmp_path):
    (tmp_path / "copy-a.log").write_text("job output\n")
    (tmp_path / "copy-b.log").write_text(f"job output {TOKEN}\n")
    (tmp_path / "not-a-log.txt").write_text(TOKEN)
    logs = stored_run_logs(tmp_path)
    assert set(logs) == {"run log copy-a", "run log copy-b"}
    problems = secrets_in_text({"access token": TOKEN}, logs)
    assert problems == ["access token is in the run log copy-b"]


def _flow(root, camp, outpost, shim_bin, steps):
    return run_fake_harness(root, "scan-flow", camp.w1, steps, step_env(root, outpost))


@pytest.mark.seam_real
def test_a_registered_session_listing_runs_through_the_real_shim_leaves_no_secret_or_token_in_arguments_or_request_records(
    tmp_path_factory,
):  # AC81, AC86
    root = tmp_path_factory.mktemp("scan-flow")
    camp = make_camp(root)
    with real_outpost(root, camp) as outpost, TokenRecorder(outpost.port) as recorder:
        shim_bin = install_shims(root, outpost, port=recorder.port)
        gateway = shim_bin / "outpost-gateway"
        with ArgumentSampler() as sampler:
            sections = _flow(root, camp, outpost, shim_bin, [("first", f"{gateway} run list"), ("second", f"{gateway} run list")])
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


@pytest.mark.seam_real
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


@pytest.mark.seam_real
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
