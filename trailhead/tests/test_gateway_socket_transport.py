"""The gateway's transport to Outpost: HTTP over the unix socket in Outpost's state folder, and nothing else."""

from __future__ import annotations

import os
import socket
import stat
from pathlib import Path

import pytest

from trailhead.gateway import outpost_http
from trailhead.tests._gateway_support import (
    StubOutpost,
    denied_socket_folder,
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
    with denied_socket_folder(state) as socket_path:
        with pytest.raises(outpost_http.SocketNotReachable) as raised:
            outpost_http.request("GET", "/gateway/runs")
    assert raised.value.path == socket_path
    assert not isinstance(raised.value, outpost_http.OutpostUnreachable)


def test_an_exhausted_reach_budget_names_the_socket_of_the_env_the_caller_passed(state, tmp_path, monkeypatch):
    other = tmp_path / "other-state"
    monkeypatch.setattr(outpost_http, "REACH_BUDGET_SECONDS", -1.0)
    with outpost_http.reach_budget():
        with pytest.raises(outpost_http.OutpostUnreachable) as raised:
            outpost_http.request("GET", "/health", env={"OUTPOST_STATE_DIR": str(other)})
    assert raised.value.path == other / "gateway" / "gateway.sock"


def _as_socket(real_lstat, socket_path, *, uid=None, folder_uid=None):
    """An lstat that reports *socket_path* as a socket (a bind is impossible in some sandboxes), optionally owned by *uid*."""

    def lstat(path):
        info = real_lstat(path)
        if str(path) == str(socket_path):
            fields = list(info)
            fields[0] = stat.S_IFSOCK | 0o600
            if uid is not None:
                fields[4] = uid
            return os.stat_result(fields)
        if folder_uid is not None and str(path) == str(socket_path.parent):
            fields = list(info)
            fields[4] = folder_uid
            return os.stat_result(fields)
        return info

    return lstat


def _fake_site(tmp_path, monkeypatch, *, folder_mode=0o700, socket_kind="file", uid=None, folder_uid=None):
    state = tmp_path / "state"
    folder = state / "gateway"
    folder.mkdir(mode=0o700, parents=True)
    folder.chmod(folder_mode)
    path = folder / "gateway.sock"
    if socket_kind == "file":
        path.write_text("")
    monkeypatch.setenv("OUTPOST_STATE_DIR", str(state))
    monkeypatch.setattr(outpost_http, "_lstat", _as_socket(os.lstat, path, uid=uid, folder_uid=folder_uid))
    return path


def test_a_private_folder_holding_a_socket_we_own_is_connected_to(tmp_path, monkeypatch):
    path = _fake_site(tmp_path, monkeypatch)
    assert outpost_http._connection(1.0)._path == path


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o705, 0o770, 0o777, 0o701])
def test_a_socket_folder_open_to_group_or_other_is_refused_before_anything_is_sent(tmp_path, monkeypatch, mode):
    _fake_site(tmp_path, monkeypatch, folder_mode=mode)
    with pytest.raises(outpost_http.UnsafeSocket) as raised:
        outpost_http.request("POST", "/broker/token", form={"client_secret": "SEEDED-SECRET-7731"})
    assert "SEEDED-SECRET-7731" not in raised.value.message
    assert raised.value.reason == "folder-mode"
    assert isinstance(raised.value, outpost_http.SocketNotReachable)


def test_a_socket_folder_that_is_a_symlink_is_refused(tmp_path, monkeypatch):
    path = _fake_site(tmp_path, monkeypatch)
    real = path.parent.rename(tmp_path / "elsewhere")
    path.parent.symlink_to(real)
    monkeypatch.setattr(outpost_http, "_lstat", _as_socket(os.lstat, real / "gateway.sock"))
    with pytest.raises(outpost_http.UnsafeSocket) as raised:
        outpost_http._connection(1.0)
    assert raised.value.reason == "folder-not-a-directory"


def test_a_socket_folder_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    _fake_site(tmp_path, monkeypatch, folder_uid=os.geteuid() + 1)
    with pytest.raises(outpost_http.UnsafeSocket) as raised:
        outpost_http._connection(1.0)
    assert raised.value.reason == "folder-owner"


def test_a_gateway_path_that_is_not_a_socket_is_refused(tmp_path, monkeypatch):
    path = _fake_site(tmp_path, monkeypatch)
    monkeypatch.setattr(outpost_http, "_lstat", os.lstat)
    assert path.is_file()
    with pytest.raises(outpost_http.UnsafeSocket) as raised:
        outpost_http._connection(1.0)
    assert raised.value.reason == "not-a-socket"


def test_a_socket_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    _fake_site(tmp_path, monkeypatch, uid=os.geteuid() + 1)
    with pytest.raises(outpost_http.UnsafeSocket) as raised:
        outpost_http._connection(1.0)
    assert raised.value.reason == "socket-owner"


def test_a_missing_socket_in_a_private_folder_is_still_outpost_not_running(tmp_path, monkeypatch):
    path = _fake_site(tmp_path, monkeypatch, socket_kind="none")
    with pytest.raises(outpost_http.OutpostUnreachable) as raised:
        outpost_http._connection(1.0)
    assert raised.value.path == path


def test_host_only_a_real_socket_in_a_folder_open_to_others_is_refused_and_sent_nothing(state):
    # Host-only: binding a unix socket is refused inside the sandbox.
    with StubOutpost() as stub:
        gateway_socket_path(state).parent.chmod(0o755)
        with pytest.raises(outpost_http.UnsafeSocket):
            outpost_http.request("GET", "/gateway/runs")
        assert stub.requests == []


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.asked: list[int | None] = []

    def read(self, amt: int | None = None) -> bytes:
        self.asked.append(amt)
        return self._body if amt is None else self._body[:amt]

    def readline(self) -> bytes:
        return b""

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self._path = Path("/fake/gateway.sock")

    def request(self, *args, **kwargs) -> None:
        pass

    def getresponse(self) -> _FakeResponse:
        return self._response

    def close(self) -> None:
        pass


def _reply_of(monkeypatch, status: int, body: bytes) -> _FakeResponse:
    response = _FakeResponse(status, body)
    monkeypatch.setattr(outpost_http, "_connection", lambda *a, **k: _FakeConnection(response))
    return response


def _json_of_size(size: int) -> bytes:
    filler = "x" * (size - len('{"k": ""}'))
    body = ('{"k": "%s"}' % filler).encode()
    assert len(body) == size
    return body


def test_a_reply_exactly_at_the_cap_is_read_and_parsed(monkeypatch):
    _reply_of(monkeypatch, 200, _json_of_size(outpost_http.MAX_REPLY_BYTES))
    assert outpost_http.request("GET", "/x").body["k"].startswith("xxx")


def test_a_reply_one_byte_over_the_cap_is_refused_as_an_unexpected_reply(monkeypatch):
    _reply_of(monkeypatch, 200, _json_of_size(outpost_http.MAX_REPLY_BYTES + 1))
    with pytest.raises(outpost_http.UnexpectedReply) as raised:
        outpost_http.request("GET", "/x")
    assert isinstance(raised.value, outpost_http.OutpostUnreachable)
    assert "xxxx" not in raised.value.message


def test_an_endless_reply_is_not_read_past_the_cap_plus_one(monkeypatch):
    response = _reply_of(monkeypatch, 200, b"y" * (outpost_http.MAX_REPLY_BYTES * 4))
    with pytest.raises(outpost_http.UnexpectedReply):
        outpost_http.request("GET", "/x")
    assert response.asked == [outpost_http.MAX_REPLY_BYTES + 1]


def test_an_oversized_refusal_body_on_a_stream_is_an_unexpected_reply(monkeypatch):
    _reply_of(monkeypatch, 500, b"z" * (outpost_http.MAX_REPLY_BYTES + 1))
    with pytest.raises(outpost_http.UnexpectedReply):
        outpost_http.open_stream("/x")


def test_a_small_refusal_body_on_a_stream_is_still_a_refusal(monkeypatch):
    _reply_of(monkeypatch, 403, b'{"error": "nope"}')
    stream = outpost_http.open_stream("/x")
    assert (stream.refusal.status, stream.refusal.body) == (403, {"error": "nope"})
