"""The credential scan the gateway end-to-end flow runs, against planted secrets.

The scan looks for a session's client secret and access tokens in every process's command-line
arguments (AC81), in a run's stored log (AC85), and in Outpost's request records: its timeline events
and its log (AC86). The first group of tests runs the scanner against planted secrets, so a scan that
could never find one cannot pass. Its use against a real Outpost is in
``test_gateway_e2e_credential_scan_real_outpost.py``.
"""

from __future__ import annotations

import json
import subprocess
import time
from http.server import BaseHTTPRequestHandler

from trailhead.gateway import outpost_http

from ._gateway_support import short_state_dir, unix_http_server
from ._gateway_e2e_support import (
    ArgumentSampler,
    TokenRecorder,
    stored_run_logs,
    arguments_with_secrets,
    process_arguments,
    secrets_in_text,
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


def test_the_token_recorder_relays_a_request_and_learns_the_tokens_both_ways(monkeypatch):
    state = short_state_dir()
    monkeypatch.setenv("OUTPOST_STATE_DIR", str(state))
    with unix_http_server(state, _Origin):
        with TokenRecorder(state) as recorder:
            reply = outpost_http.request(
                "POST", "/broker/token", headers={"Authorization": "Bearer bearer-PLANTED-5c"}, form={"x": "y"}
            )
            time.sleep(0.1)
        assert reply.body["access_token"] == TOKEN, "the recorder did not relay the origin's answer"
        assert recorder.tokens == {TOKEN, "bearer-PLANTED-5c"}
        assert outpost_http.request("POST", "/broker/token", form={"x": "y"}).status == 200, "the socket was not put back"


def test_the_stored_run_logs_are_read_by_name_and_a_token_in_one_is_found(tmp_path):
    (tmp_path / "copy-a.log").write_text("job output\n")
    (tmp_path / "copy-b.log").write_text(f"job output {TOKEN}\n")
    (tmp_path / "not-a-log.txt").write_text(TOKEN)
    logs = stored_run_logs(tmp_path)
    assert set(logs) == {"run log copy-a", "run log copy-b"}
    problems = secrets_in_text({"access token": TOKEN}, logs)
    assert problems == ["access token is in the run log copy-b"]
