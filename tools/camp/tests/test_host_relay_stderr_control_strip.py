"""Tests for control-sequence stripping on relayed remote stderr —
`camp.host.relay.answer_for_host`'s `notices`, the shared seam both the
`--host` path (`relay_all_groups`) and the merged `-a` path
(`camp.host.merge.answer_all_hosts_concurrently` /
`merge_all_hosts_answer`) ride.

Closes `decision/terminal-escape-injection-from-a-remote-answer-is-accepted-not-fixed`:
C0 and C1 control sequences (everything except newline and tab) are removed
from a remote camp's relayed stderr before it reaches this side's terminal,
on both paths.

Task: task/strip-control-sequences-from-relayed-remote-stderr.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _relay_module():
    return importlib.import_module("camp.host.relay")


def _transport_module():
    return importlib.import_module("camp.host.transport")


def _config_module():
    return importlib.import_module("camp.host.config")


def _merge_module():
    return importlib.import_module("camp.host.merge")


def _host(ssh: str = "andromeda"):
    config = _config_module()
    return config.Host(ssh=ssh, camp_bin="camp")


def _answer_via_host_path(monkeypatch, outcome, host_name: str = "andromeda"):
    """Drives `answer_for_host` directly — the value `relay_all_groups`
    (the `--host` surface) prints and exits on immediately."""
    relay = _relay_module()
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kwargs):
        return outcome

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    return relay.answer_for_host(
        "list", _host(host_name), host_name, ["list", "--all-groups", "--json"],
    )


def _notices_via_merged_path(monkeypatch, outcome, host_name: str = "andromeda"):
    """Drives the merged `-a` path end to end: `answer_all_hosts_concurrently`
    (the fan-out `-a` uses) feeding `merge_all_hosts_answer` (the pure merge
    `-a` renders from) — never `answer_for_host` directly."""
    merge = _merge_module()
    transport = _transport_module()

    def fake_runner(argv, timeout, env):
        raise AssertionError("runner should not be called — outcome is injected via run_camp")

    def fake_run_camp(host, remote_argv, **kwargs):
        return outcome

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    local_result, host_answers = merge.answer_all_hosts_concurrently(
        lambda: ([], [], 0),
        [(host_name, _host(host_name))],
        verb="list",
        remote_argv=["list", "--all-groups", "--json"],
        runner=fake_runner,
    )

    _rows, notices, _exit_code = merge.merge_all_hosts_answer(
        *local_result,
        self_name=None,
        host_answers=host_answers,
    )
    return notices


_ESCAPE_PAYLOAD_STDERR = (
    "camp list: \x1b[2J\x1b[H\x1b[31mrefused\x1b[0m: \x9b8mno such group\n"
)
# The control code points (ESC = C0, the 0x9b C1 introducer) are removed; the
# printable parameter/final bytes that followed them ("[2J", "[H", "[31m",
# "[0m", "8m") are ordinary text once their introducer is gone and are left
# in place, per the task's "strip by Unicode code point" instruction — an
# introducer-less "[31m" cannot drive a terminal, so the sequence is
# neutralized without a second, sequence-parsing pass that risks eating real
# content.
_ESCAPE_PAYLOAD_EXPECTED = "camp list: [2J[H[31mrefused[0m: 8mno such group"


# ---------------------------------------------------------------------------
# escape payload — --host path
# ---------------------------------------------------------------------------


def test_host_path_strips_cursor_colour_and_c1_sequences(monkeypatch) -> None:
    transport = _transport_module()
    outcome = transport.RemoteRefusal(stdout="", stderr=_ESCAPE_PAYLOAD_STDERR, exit_code=1)
    answer = _answer_via_host_path(monkeypatch, outcome)
    assert answer.notices == [_ESCAPE_PAYLOAD_EXPECTED]


# ---------------------------------------------------------------------------
# escape payload — merged -a path, asserted separately
# ---------------------------------------------------------------------------


def test_merged_path_strips_cursor_colour_and_c1_sequences(monkeypatch) -> None:
    transport = _transport_module()
    outcome = transport.RemoteRefusal(stdout="", stderr=_ESCAPE_PAYLOAD_STDERR, exit_code=1)
    notices = _notices_via_merged_path(monkeypatch, outcome)
    assert notices == [_ESCAPE_PAYLOAD_EXPECTED]


# ---------------------------------------------------------------------------
# newline and tab survive; multi-line refusals keep their line structure
# ---------------------------------------------------------------------------


def test_newline_and_tab_survive_multiline_refusal(monkeypatch) -> None:
    transport = _transport_module()
    stderr = "camp list: refused\n\tgroup:\tlevr\nsecond line\n"
    outcome = transport.RemoteRefusal(stdout="", stderr=stderr, exit_code=1)
    answer = _answer_via_host_path(monkeypatch, outcome)
    assert answer.notices == ["camp list: refused\n\tgroup:\tlevr\nsecond line"]


# ---------------------------------------------------------------------------
# exact wording preserved — the strip must not become a reword
# ---------------------------------------------------------------------------


def test_remote_refusal_exact_wording_unchanged_apart_from_escapes(monkeypatch) -> None:
    transport = _transport_module()
    stderr = "camp list: \x1b[31mgroup 'levr' does not exist on this host\x1b[0m\n"
    outcome = transport.RemoteRefusal(stdout="", stderr=stderr, exit_code=1)
    answer = _answer_via_host_path(monkeypatch, outcome)
    assert answer.notices == [
        "camp list: [31mgroup 'levr' does not exist on this host[0m"
    ]


# ---------------------------------------------------------------------------
# non-ASCII content passes through unharmed
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# stderr that is nothing but control sequences (or a bare newline) must not
# survive as an empty-string notice.
# ---------------------------------------------------------------------------


def test_stderr_of_only_control_code_points_produces_no_notice(monkeypatch) -> None:
    # Unlike the cursor/colour escapes above (whose printable parameter
    # bytes survive the strip), these control code points have no
    # printable payload at all — the stripped text is genuinely empty.
    transport = _transport_module()
    outcome = transport.RemoteRefusal(stdout="", stderr="\x01\x02\x03\n", exit_code=1)
    answer = _answer_via_host_path(monkeypatch, outcome)
    assert answer.notices == []


def test_bare_newline_stderr_produces_no_notice(monkeypatch) -> None:
    transport = _transport_module()
    outcome = transport.RemoteRefusal(stdout="", stderr="\n", exit_code=1)
    answer = _answer_via_host_path(monkeypatch, outcome)
    assert answer.notices == []


def test_non_ascii_content_not_mangled(monkeypatch) -> None:
    transport = _transport_module()
    stderr = "camp list: café-ソフト group not found: \x1b[31m✗\x1b[0m\n"
    outcome = transport.RemoteRefusal(stdout="", stderr=stderr, exit_code=1)
    answer = _answer_via_host_path(monkeypatch, outcome)
    assert answer.notices == ["camp list: café-ソフト group not found: [31m✗[0m"]
