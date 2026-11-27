"""The gateway's transport to Outpost: HTTP over the unix socket in Outpost's state folder, and nothing else."""

from __future__ import annotations

import os
import socket

import pytest

from trailhead.gateway import outpost_http
from trailhead.tests._gateway_support import (
    StubOutpost,
    gateway_socket_path,
    short_state_dir,
    stale_socket,
    tcp_squatter,
)


@pytest.fixture
def state(monkeypatch):
    d = short_state_dir()
    monkeypatch.setenv("OUTPOST_STATE_DIR", str(d))
    return d


def test_a_request_reaches_the_server_on_the_socket_with_its_headers_and_body(state):
    with StubOutpost() as stub:
        reply = outpost_http.request(
            "POST",
            "/broker/token",
            headers={"Authorization": outpost_http.basic_auth("client-id-SEEDED-1f3a", "client-secret-SEEDED-9c7e")},
            form={"grant_type": "client_credentials"},
        )
        (seen,) = stub.requests
    assert (reply.status, reply.body["token_type"]) == (200, "Bearer")
    assert (seen.method, seen.path) == ("POST", "/broker/token")
    assert seen.headers["authorization"].startswith("Basic ")
    assert seen.body == "grant_type=client_credentials"


def test_the_socket_is_found_from_the_state_folder_the_environment_names(monkeypatch):
    first, second = short_state_dir(), short_state_dir()
    monkeypatch.setenv("OUTPOST_STATE_DIR", str(first))
    with StubOutpost(first) as stub:
        assert outpost_http.request("GET", "/gateway/runs").status == 401
        assert len(stub.requests) == 1
        monkeypatch.setenv("OUTPOST_STATE_DIR", str(second))
        with pytest.raises(outpost_http.OutpostUnreachable):
            outpost_http.request("GET", "/gateway/runs")
        assert len(stub.requests) == 1


def test_a_tcp_listener_on_the_cockpit_port_with_no_socket_is_sent_nothing_and_outpost_is_not_running(state):
    with tcp_squatter() as squatter:
        with pytest.raises(outpost_http.OutpostUnreachable) as raised:
            outpost_http.request(
                "POST",
                "/broker/token",
                headers={"Authorization": outpost_http.basic_auth("id", "secret")},
                form={"grant_type": "client_credentials"},
            )
        with pytest.raises(socket.timeout):
            squatter.accept()
    assert raised.value.path == gateway_socket_path(state)


def test_a_socket_nothing_listens_on_is_outpost_not_running_naming_the_socket(state):
    path = stale_socket(state)
    with pytest.raises(outpost_http.OutpostUnreachable) as raised:
        outpost_http.request("GET", "/gateway/runs")
    assert raised.value.path == path
    assert not isinstance(raised.value, outpost_http.SocketNotReachable)


@pytest.mark.skipif(os.geteuid() == 0, reason="root passes a folder's mode check, so no folder can deny it")
def test_a_socket_the_operating_system_denies_us_is_not_reachable_naming_the_socket(state):
    folder = gateway_socket_path(state).parent
    folder.mkdir(mode=0o700, parents=True)
    folder.chmod(0o000)
    try:
        with pytest.raises(outpost_http.SocketNotReachable) as raised:
            outpost_http.request("GET", "/gateway/runs")
    finally:
        folder.chmod(0o700)
    assert raised.value.path == gateway_socket_path(state)
    assert not isinstance(raised.value, outpost_http.OutpostUnreachable)
