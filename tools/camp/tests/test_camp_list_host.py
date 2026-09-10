"""Tests for `camp list --host <name>` — the three layers wired end to end.

Drives `camp.cli.dispatch.main()` in-process (sys.argv monkeypatched, the SAME
style `test_cli_dispatch_split.py` already uses for `--host` assertions), with
`camp.host.transport.run_camp` injected so no real SSH connection is ever
made. `hosts.toml` declares one real host ("andromeda") so `--host andromeda`
resolves and reaches the transport seam.

Test contract (docs/design/named-remote-host-answers.md):
- zero / one / many rows relay in the remote's own order, never re-sorted.
- every relayed row gains `host` set to the name the operator typed.
- a plain local `camp list --json` (no `--host`) is unaffected (see the AC6
  pin in test_camp_list.py).
- the far side is always invoked with the all-groups + --json form.
- unreachable / stopped-responding / identity-unknown / identity-changed /
  camp-not-resolvable each get their own stderr line and JSON `reason`, and
  the two identity states never share a message.
- non-ASCII bytes in a relayed row survive the subprocess boundary.
- a JSON failure row carries only `ok`, `host`, `reason`.
- a remote refusal is relayed with no added wrapper text, remote exit status
  carried through.
- a remote answer carrying its own ok:false rows ("collection failure") is
  relayed as rows, distinct from an unreachable host.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _transport_module():
    return importlib.import_module("camp.host.transport")


@pytest.fixture()
def hosts_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAMP_CONFIG_DIR with hosts.toml declaring one host ("andromeda"),
    config/state dirs isolated in tmp_path."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text("[hosts.andromeda]\n", encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))


def _rig(monkeypatch: pytest.MonkeyPatch, outcome, *, capture_argv: list | None = None):
    """Point transport.run_camp at a canned outcome, capturing the argv it
    was called with when *capture_argv* is given."""
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kwargs):
        if capture_argv is not None:
            capture_argv.append(list(remote_argv))
        return outcome

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)
    return transport


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()
    return excinfo.value.code


# ---------------------------------------------------------------------------
# zero / one / many — the answered/relayed rows path
# ---------------------------------------------------------------------------


def test_zero_rows_human_no_stdout_exit_zero(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Answered(stdout="[]", stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""


def test_zero_rows_json_empty_array_exit_zero(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Answered(stdout="[]", stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == []


def test_many_rows_relay_in_remote_order_never_resorted(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """Rows come back in a deliberately non-alphabetical order (zeta before
    alpha) — pinning that the local side never re-sorts them."""
    transport = _transport_module()
    remote_rows = [
        {"ok": True, "slug": "zeta", "branch": "b", "workspace_path": "/z", "group": "g"},
        {"ok": True, "slug": "alpha", "branch": "b", "workspace_path": "/a", "group": "g"},
    ]
    outcome = transport.Answered(
        stdout=json.dumps(remote_rows), stderr="", exit_code=0
    )
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    rows = json.loads(captured.out)
    assert [r["slug"] for r in rows] == ["zeta", "alpha"]


def test_many_rows_human_path_preserves_order(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    remote_rows = [
        {"ok": True, "slug": "zeta", "branch": "b", "workspace_path": "/z", "group": "g"},
        {"ok": True, "slug": "alpha", "branch": "b", "workspace_path": "/a", "group": "g"},
    ]
    outcome = transport.Answered(
        stdout=json.dumps(remote_rows), stderr="", exit_code=0
    )
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda"])

    captured = capsys.readouterr()
    assert code == 0
    lines = [ln for ln in captured.out.splitlines() if ln]
    assert lines == ["zeta /z", "alpha /a"]


def test_every_relayed_row_gains_host_key(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    remote_rows = [
        {"ok": True, "slug": "ws-a", "branch": "b", "workspace_path": "/a", "group": "g"},
    ]
    outcome = transport.Answered(
        stdout=json.dumps(remote_rows), stderr="", exit_code=0
    )
    _rig(monkeypatch, outcome)

    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["host"] == "andromeda"


def _install_fake_ssh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    """Puts a fake `ssh` script FIRST on $PATH that ignores its argv and
    writes *stdout* — used to drive real `default_runner` (a real
    subprocess, a real pipe, real bytes across the subprocess boundary)
    without ever making a real network connection."""
    import os
    import stat

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "ssh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.buffer.write({stdout!r}.encode('utf-8'))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def test_non_ascii_bytes_in_relayed_row_decode_and_render_unmangled(
    hosts_env, monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Drives the REAL `default_runner` (not a stubbed `run_camp`) through a
    fake `ssh` on $PATH — the subprocess boundary non-ASCII bytes must
    actually survive is real, not the stub every other test in this file
    injects at the `run_camp` seam."""
    remote_rows = [
        {
            "ok": True,
            "slug": "café-projet",
            "branch": "b",
            "workspace_path": "/répertoire/café",
            "group": "gröup",
        }
    ]
    _install_fake_ssh(tmp_path, monkeypatch, json.dumps(remote_rows, ensure_ascii=False))

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert code == 0
    assert rows[0]["slug"] == "café-projet"
    assert rows[0]["workspace_path"] == "/répertoire/café"

    code = _run(monkeypatch, ["list", "--host", "andromeda"])
    out = capsys.readouterr().out
    assert "café-projet /répertoire/café" in out


# ---------------------------------------------------------------------------
# The far side is always invoked with the all-groups + --json form.
# ---------------------------------------------------------------------------


def test_far_side_invoked_with_all_groups_and_json_form(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Answered(stdout="[]", stderr="", exit_code=0)
    captured_argv: list = []
    _rig(monkeypatch, outcome, capture_argv=captured_argv)

    _run(monkeypatch, ["list", "--host", "andromeda"])

    assert len(captured_argv) == 1
    remote_argv = captured_argv[0]
    assert remote_argv[0] == "list"
    assert "--all-groups" in remote_argv
    assert "--json" in remote_argv


# ---------------------------------------------------------------------------
# The five non-relayable failure states — each its own stderr line AND its
# own JSON `reason`, each exiting nonzero.
# ---------------------------------------------------------------------------


def test_host_unreachable_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Unreachable(reason="Connection timed out")
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "unreachable" in captured.err
    assert "andromeda" in captured.err
    rows = json.loads(captured.out)
    assert rows == [{"ok": False, "host": "andromeda", "reason": "unreachable — no response within 10s"}]


def test_host_stopped_responding_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.StoppedResponding(execution_timeout=60.0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "did not finish within 60s" in captured.err
    rows = json.loads(captured.out)
    assert rows == [{"ok": False, "host": "andromeda", "reason": "connected but did not finish within 60s"}]


def test_host_identity_unknown_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.IdentityUnknown()
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "no pinned key" in captured.err
    assert "ssh-keyscan" in captured.err
    rows = json.loads(captured.out)
    assert rows == [{"ok": False, "host": "andromeda", "reason": "no pinned host key"}]


def test_host_identity_changed_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.IdentityChanged()
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "different key" in captured.err
    assert "may be intercepted" in captured.err
    rows = json.loads(captured.out)
    assert rows == [{"ok": False, "host": "andromeda", "reason": "host key differs from the pinned key"}]


def test_identity_unknown_and_identity_changed_never_share_a_message(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    _rig(monkeypatch, transport.IdentityUnknown())
    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    unknown_err = capsys.readouterr().err

    _rig(monkeypatch, transport.IdentityChanged())
    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    changed_err = capsys.readouterr().err

    assert unknown_err != changed_err


def test_camp_not_resolvable_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.CampNotResolvable()
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "camp could not be run there" in captured.err
    assert "camp_bin" in captured.err
    rows = json.loads(captured.out)
    assert rows[0]["ok"] is False
    assert rows[0]["host"] == "andromeda"
    assert "reason" in rows[0]


def test_host_credentials_refused_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.CredentialsRefused()
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "refused every credential" in captured.err
    assert "ssh-add" in captured.err
    rows = json.loads(captured.out)
    assert rows == [{"ok": False, "host": "andromeda", "reason": "host refused our credentials"}]


def test_host_credentials_refused_distinct_from_remote_refusal_and_camp_not_resolvable(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    _rig(monkeypatch, transport.CredentialsRefused())
    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    credentials_err = capsys.readouterr().err

    _rig(monkeypatch, transport.CampNotResolvable())
    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    not_resolvable_err = capsys.readouterr().err

    _rig(
        monkeypatch,
        transport.RemoteRefusal(stdout="", stderr="camp list: boom\n", exit_code=1),
    )
    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    remote_refusal_err = capsys.readouterr().err

    assert credentials_err != not_resolvable_err
    assert credentials_err != remote_refusal_err


def test_json_failure_row_carries_only_ok_host_reason(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Unreachable(reason="Connection refused")
    _rig(monkeypatch, outcome)

    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    rows = json.loads(capsys.readouterr().out)
    assert set(rows[0].keys()) == {"ok", "host", "reason"}


# ---------------------------------------------------------------------------
# Remote refusal relayed — no wrapper text, remote exit status carried.
# ---------------------------------------------------------------------------


def test_remote_refusal_relayed_prints_remote_stderr_unchanged(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.RemoteRefusal(
        stdout="",
        stderr="camp list: /home/tom/.config/camp/groups/levr.toml: invalid TOML — skipping\n",
        exit_code=1,
    )
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == (
        "camp list: /home/tom/.config/camp/groups/levr.toml: invalid TOML — skipping\n"
    )
    # No local wrapper text added: nothing about "andromeda" or "unreachable"
    # or any camp-list-host phrasing the local side didn't get from the remote.
    assert "unreachable" not in captured.err


# ---------------------------------------------------------------------------
# Collection failure — the host answered, its answer carries its own
# ok:false rows, distinct from never having reached the machine.
# ---------------------------------------------------------------------------


def test_collection_failure_relays_rows_with_host_and_remote_exit_status(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    remote_rows = [
        {"ok": True, "slug": "ws-a", "branch": "b", "workspace_path": "/a", "group": "g"},
        {"ok": False, "group": None, "reason": "levr.toml: invalid TOML"},
    ]
    outcome = transport.Answered(
        stdout=json.dumps(remote_rows),
        stderr="camp list: levr.toml: invalid TOML — skipping\n",
        exit_code=0,
    )
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 0  # the remote's own exit status, carried through
    rows = json.loads(captured.out)
    assert rows == [
        {"ok": True, "slug": "ws-a", "branch": "b", "workspace_path": "/a", "group": "g", "host": "andromeda"},
        {"ok": False, "group": None, "reason": "levr.toml: invalid TOML", "host": "andromeda"},
    ]


def test_unparsable_remote_answer_emits_a_json_row_not_silence(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """A RemoteRefusal (or any unmatched transport failure classified as one)
    whose stdout does not decode as a JSON array must still answer a --json
    caller with a row — an empty stdout with nothing printed to stdout would
    read as "the host has nothing to report", which is the exact confident
    wrong answer this whole design exists to remove."""
    transport = _transport_module()
    outcome = transport.RemoteRefusal(
        stdout="", stderr="ssh: some unmatched transport failure\n", exit_code=255
    )
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 255
    rows = json.loads(captured.out)
    assert rows == [
        {"ok": False, "host": "andromeda", "reason": "remote answer could not be parsed"}
    ]


def test_malformed_remote_array_of_non_objects_does_not_crash(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """A remote answer that decodes as a JSON array but whose elements are
    not objects (a different-versioned or misbehaving remote camp) must not
    raise — stamping `host` onto a non-dict element is a TypeError, not a
    row to relay."""
    transport = _transport_module()
    outcome = transport.Answered(
        stdout=json.dumps(["oops", "not", "objects"]), stderr="", exit_code=0
    )
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    rows = json.loads(captured.out)
    assert rows == [
        {"ok": False, "host": "andromeda", "reason": "remote answer could not be parsed"}
    ]


def test_collection_failure_is_distinct_from_unreachable(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    remote_rows = [{"ok": False, "group": None, "reason": "levr.toml: invalid TOML"}]
    _rig(
        monkeypatch,
        transport.Answered(stdout=json.dumps(remote_rows), stderr="", exit_code=0),
    )
    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    rows_collection = json.loads(capsys.readouterr().out)

    _rig(monkeypatch, transport.Unreachable(reason="Connection timed out"))
    _run(monkeypatch, ["list", "--host", "andromeda", "--json"])
    rows_unreachable = json.loads(capsys.readouterr().out)

    # Collection failure relays the remote's own row; unreachable never got
    # an answer to relay, so its row shape is different (no "group" key).
    assert "group" in rows_collection[0]
    assert "group" not in rows_unreachable[0]


def test_a_row_missing_a_required_key_does_not_crash_and_other_rows_still_render(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """Version skew across the operator's two machines is the expected
    steady state: a remote camp of a different version can omit a key the
    human renderer indexes directly. It must degrade that ONE row rather
    than crash the whole answer — the other, well-formed row still prints.

    The sibling `sessions` verb already holds this line; this pins the same
    behaviour for `list`, whose renderer indexes slug and workspace_path."""
    transport = _transport_module()
    remote_rows = [
        {"ok": True, "workspace_path": "/ws/feat-x"},  # missing slug
        {"ok": True, "slug": "alpha", "workspace_path": "/ws/alpha"},
    ]
    outcome = transport.Answered(stdout=json.dumps(remote_rows), stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["list", "--host", "andromeda"])

    captured = capsys.readouterr()
    assert code == 0
    assert "alpha" in captured.out
    assert "Traceback" not in captured.err
