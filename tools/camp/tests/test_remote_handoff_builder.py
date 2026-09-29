"""Tests for camp.host.handoff.remote_camp_argv — the general remote handoff.

Test contract:
- A creation argument list produces the pinned interactive ssh argv with the
  host's destination and camp location; a trailing `--dry-run` stays last; a
  different slug, group, or connect timeout changes exactly the matching
  elements.
- Adversarial values in the slug, the group, and the camp location each reach
  a local `/bin/sh` standing in for the far side as one literal argument, and
  nothing they contain executes.
- The remote command is composed through the transport's own quote-and-join,
  and never through the listing transport's `run_camp`.

No real remote host is contacted anywhere in this file; every side-effect probe
resolves relative to `tmp_path`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.host import handoff  # noqa: E402
from camp.host import transport  # noqa: E402
from camp.host.config import Host  # noqa: E402

_HOST = Host(ssh="andromeda", camp_bin="/opt/camp/bin/camp")


def _creation(slug: str, group: str, *, dry_run: bool = False) -> list[str]:
    args = ["new", slug, "--group", group]
    return args + ["--dry-run"] if dry_run else args


def test_creation_args_produce_the_pinned_interactive_ssh_argv():
    argv = handoff.remote_camp_argv(_HOST, _creation("feat-x", "trailhead"), connect_timeout=7.0)
    assert argv == [
        "ssh",
        "-t",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=7",
        "andromeda",
        "/opt/camp/bin/camp new feat-x --group trailhead",
    ]


def test_default_connect_timeout_is_the_transports():
    argv = handoff.remote_camp_argv(_HOST, _creation("s", "g"))
    assert f"ConnectTimeout={transport.DEFAULT_CONNECT_TIMEOUT_SECONDS:g}" in argv


def test_dry_run_is_appended_last():
    argv = handoff.remote_camp_argv(_HOST, _creation("s", "g", dry_run=True))
    assert argv[-1] == "/opt/camp/bin/camp new s --group g --dry-run"


def test_changing_slug_group_and_timeout_changes_exactly_those_elements():
    base = handoff.remote_camp_argv(_HOST, _creation("s1", "g1"), connect_timeout=5.0)
    other = handoff.remote_camp_argv(_HOST, _creation("s2", "g2"), connect_timeout=9.0)
    assert [i for i, (a, b) in enumerate(zip(base, other)) if a != b] == [7, 9]
    assert other[7] == "ConnectTimeout=9"
    assert other[9] == "/opt/camp/bin/camp new s2 --group g2"


def test_destination_and_camp_location_come_from_the_host():
    argv = handoff.remote_camp_argv(Host(ssh="orion", camp_bin="camp"), ["new", "s"])
    assert argv[-2:] == ["orion", "camp new s"]


_HOSTILE = [
    "$(touch pwned)",
    "`touch pwned`",
    "a;touch pwned",
    "a&&touch pwned",
    "a|touch pwned",
    "it's",
    'say "hi"',
    "a b",
    "a\ntouch pwned",
]

_RECORDER = "#!/bin/sh\nfor a in \"$@\"; do printf '%s\\0' \"$a\"; done > recorded_argv\n"


def _run_far_side(tmp_path, argv):
    result = subprocess.run(
        ["sh", "-c", argv[-1]], capture_output=True, text=True, timeout=10, cwd=str(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "pwned").exists()
    return (tmp_path / "recorded_argv").read_bytes().split(b"\x00")[:-1]


def _fake_camp(tmp_path) -> str:
    fake = tmp_path / "fake_camp"
    fake.write_text(_RECORDER)
    fake.chmod(0o755)
    return str(fake)


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_hostile_slug_arrives_as_one_literal_argument(hostile, tmp_path):
    host = Host(ssh="andromeda", camp_bin=_fake_camp(tmp_path))
    argv = handoff.remote_camp_argv(host, _creation(hostile, "g", dry_run=True))
    assert _run_far_side(tmp_path, argv) == [
        b"new", hostile.encode(), b"--group", b"g", b"--dry-run"
    ]


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_hostile_group_arrives_as_one_literal_argument(hostile, tmp_path):
    host = Host(ssh="andromeda", camp_bin=_fake_camp(tmp_path))
    argv = handoff.remote_camp_argv(host, _creation("s", hostile))
    assert _run_far_side(tmp_path, argv) == [b"new", b"s", b"--group", hostile.encode()]


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_hostile_camp_location_is_one_literal_command_word(hostile, tmp_path):
    hostile_dir = tmp_path / hostile
    hostile_dir.mkdir()
    fake = hostile_dir / "camp"
    fake.write_text(_RECORDER)
    fake.chmod(0o755)
    host = Host(ssh="andromeda", camp_bin=str(fake))
    argv = handoff.remote_camp_argv(host, _creation("s", "g"))
    result = subprocess.run(
        ["sh", "-c", argv[-1]], capture_output=True, text=True, timeout=10, cwd=str(hostile_dir)
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "pwned").exists()
    assert not (hostile_dir / "pwned").exists()
    received = (hostile_dir / "recorded_argv").read_bytes().split(b"\x00")[:-1]
    assert received == [b"new", b"s", b"--group", b"g"]


def test_remote_command_is_composed_by_the_transports_quote_and_join(monkeypatch):
    calls = []
    real = transport.quote_and_join

    def spy(camp_bin, remote_argv):
        calls.append((camp_bin, list(remote_argv)))
        return real(camp_bin, remote_argv)

    monkeypatch.setattr(handoff, "quote_and_join", spy)
    handoff.remote_camp_argv(_HOST, _creation("s", "g"))
    assert calls == [(_HOST.camp_bin, ["new", "s", "--group", "g"])]


def test_builder_never_reaches_the_listing_transport(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("listing transport reached")

    monkeypatch.setattr(transport, "run_camp", boom)
    monkeypatch.setattr(transport, "stream_camp", boom)
    handoff.remote_camp_argv(_HOST, _creation("s", "g"))
