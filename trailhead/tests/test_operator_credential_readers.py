"""The operator credential and a session's broker credential are read only when they are what Outpost writes:
a regular file the operator owns with mode 0600, never followed through a link."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trailhead import broker_header, outpost_lifecycle
from trailhead.launch_profile import operator_token_store
from trailhead.outpost_lifecycle import OutpostLifecycleError
from trailhead.paths import state_dir
from trailhead.ranger import outpost_client
from trailhead.ranger.outpost_client import OutpostRefusal

SECRET = "SEEDED-operator-secret-4417"
UNSAFE = ["symlink", "mode-0644", "mode-0660", "foreign-owner"]


def _plant(path: Path, shape: str, monkeypatch, module, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if shape == "symlink":
        real = path.parent / "real-credential"
        real.write_text(body)
        real.chmod(0o600)
        path.symlink_to(real)
        return
    path.write_text(body)
    path.chmod({"mode-0644": 0o644, "mode-0660": 0o660}.get(shape, 0o600))
    if shape == "foreign-owner":
        monkeypatch.setattr(module, "_operator_uid", lambda: os.geteuid() + 1)


def _ranger_env(tmp_path):
    return {"OUTPOST_STATE_DIR": str(tmp_path / "outpost-state"), "HOME": str(tmp_path / "home")}


@pytest.mark.parametrize("shape", UNSAFE)
def test_ranger_refuses_an_operator_credential_that_is_not_private(tmp_path, monkeypatch, shape):
    env = _ranger_env(tmp_path)
    _plant(operator_token_store(env=env) / "credential", shape, monkeypatch, outpost_client, SECRET)
    with pytest.raises(OutpostRefusal) as raised:
        outpost_client.read_operator_credential(env)
    assert "no operator credential is available from Outpost" in str(raised.value)
    assert SECRET not in str(raised.value)


def test_ranger_reads_a_private_operator_credential(tmp_path):
    env = _ranger_env(tmp_path)
    path = operator_token_store(env=env) / "credential"
    path.parent.mkdir(parents=True)
    path.write_text(SECRET + "\n")
    path.chmod(0o600)
    assert outpost_client.read_operator_credential(env) == SECRET


@pytest.mark.parametrize("shape", UNSAFE)
def test_open_refuses_an_operator_credential_that_is_not_private(tmp_path, monkeypatch, shape):
    env = _ranger_env(tmp_path)
    path = state_dir(outpost_lifecycle.APP, env=env) / outpost_lifecycle.OPERATOR_DIR / outpost_lifecycle.CREDENTIAL_NAME
    _plant(path, shape, monkeypatch, outpost_lifecycle, SECRET)
    with pytest.raises(OutpostLifecycleError) as raised:
        outpost_lifecycle._mint_open_code(env, 1.0)
    assert "cannot read the operator credential" in str(raised.value)
    assert SECRET not in str(raised.value)


def test_open_reads_a_private_operator_credential(tmp_path):
    env = _ranger_env(tmp_path)
    path = state_dir(outpost_lifecycle.APP, env=env) / outpost_lifecycle.OPERATOR_DIR / outpost_lifecycle.CREDENTIAL_NAME
    path.parent.mkdir(parents=True)
    path.write_text(SECRET)
    path.chmod(0o600)
    with pytest.raises(OutpostLifecycleError) as raised:
        outpost_lifecycle._mint_open_code(env, 1.0)
    assert "cannot read the operator credential" not in str(raised.value)


@pytest.mark.parametrize("shape", UNSAFE)
def test_broker_header_refuses_a_credential_that_is_not_private(tmp_path, monkeypatch, shape):
    monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "th-state"))
    path = tmp_path / "sessions" / "sess-1" / "broker-client.json"
    body = json.dumps({"client_id": "client-abc", "client_secret": SECRET})
    _plant(path, shape, monkeypatch, broker_header, body)
    rc, out = broker_header.run(str(path))
    assert rc != 0
    assert "credential-unreadable" in out
    assert SECRET not in out
