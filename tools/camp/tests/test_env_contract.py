"""Tests for `camp.env.contract` — the reading of a Docker inventory reply.

Replies are fed as text and the parsed result (or the `Unreadable` reason) is
asserted. Fixture replies under ``fixtures/env_inventory_*.txt`` are real
captures, never hand-typed: ``*_docker_engine_29_8_0_linux`` is the header from
Docker Engine 29.8.0 on Linux, ``*_docker_desktop_4_97_0`` the header from Docker
Desktop 4.97.0 (server 29.9.2) on macOS.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.env.contract import (  # noqa: E402
    INVENTORY_ARGV,
    INVENTORY_HEADER,
    INVENTORY_REMOTE_COMMAND,
    Environment,
    Unreadable,
    has_inventory_header,
    parse_inventory,
)

FIXTURES = Path(__file__).parent / "fixtures"
HEADER = INVENTORY_HEADER


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _record(*fields: object) -> str:
    return "|".join(json.dumps(f, ensure_ascii=False) for f in fields)


def _reply(*lines: str) -> str:
    return "\n".join([HEADER, *lines]) + "\n"


def _ok(group: str = "levr", name: str = "main", state: str = "running", ports: object = "") -> str:
    return _record(f"{group}-env-{name}", state, group, name, ports)


# --- header ---------------------------------------------------------------


def test_header_only_reply_is_no_environments() -> None:
    assert parse_inventory(_fixture("env_inventory_header_docker_engine_29_8_0_linux.txt")) == []


@pytest.mark.parametrize(
    "fixture",
    [
        "env_inventory_header_docker_engine_29_8_0_linux.txt",
        "env_inventory_header_docker_desktop_4_97_0.txt",
    ],
)
def test_header_from_each_measured_docker_parses(fixture: str) -> None:
    text = _fixture(fixture)
    assert has_inventory_header(text)
    assert parse_inventory(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n",
        "NAMES|STATE|group\n",
        "NAMES|STATE|group|name\n",
        "names|state|group|name|ports\n",
        " NAMES|STATE|group|name|PORTS\n",
        '"levr-env-main"|"running"|"levr"|"main"|""\n',
        "Error response from daemon: boom\n",
    ],
)
def test_missing_or_wrong_header_is_unreadable_naming_the_header(text: str) -> None:
    assert not has_inventory_header(text)
    with pytest.raises(Unreadable, match="header"):
        parse_inventory(text)


def test_header_prefix_of_a_longer_first_line_is_not_the_header() -> None:
    assert not has_inventory_header(HEADER + "x\n")
    with pytest.raises(Unreadable, match="header"):
        parse_inventory(HEADER + "x\n")


def test_header_without_trailing_newline_parses() -> None:
    assert parse_inventory(HEADER) == []


# --- records --------------------------------------------------------------


def test_real_records_parse_with_states_mapped() -> None:
    envs = parse_inventory(_fixture("env_inventory_records.txt"))
    assert envs == [
        Environment("k1probe", "restart", "stopped"),
        Environment("k1probe", "paused", "stopped"),
        Environment("k1probe", "exited", "stopped"),
        Environment("k1probe", "run", "running"),
        Environment("k1probe", "main", "stopped"),
    ]


def test_record_named_for_its_own_labels_yields_one_environment() -> None:
    assert parse_inventory(_reply(_ok("levr", "ben-feature"))) == [
        Environment("levr", "ben-feature", "running")
    ]


@pytest.mark.parametrize(
    "container",
    ["levr-env-other", "other-env-main", "levr-main", "levr-env-main-db", "/levr-env-main", ""],
)
def test_labelled_record_under_another_name_is_skipped_beside_good_ones(container: str) -> None:
    reply = _reply(
        _ok("a", "one"),
        _record(container, "running", "levr", "main", ""),
        _ok("b", "two", "exited"),
    )
    assert parse_inventory(reply) == [
        Environment("a", "one", "running"),
        Environment("b", "two", "stopped"),
    ]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("running", "running"),
        ("exited", "stopped"),
        ("created", "stopped"),
        ("paused", "stopped"),
        ("restarting", "stopped"),
        ("dead", "stopped"),
        ("removing", "stopped"),
    ],
)
def test_state_maps_to_running_or_stopped(state: str, expected: str) -> None:
    [env] = parse_inventory(_reply(_ok(state=state)))
    assert env.state == expected


def test_order_of_the_reply_is_kept() -> None:
    envs = parse_inventory(_reply(_ok("b", "z"), _ok("a", "y")))
    assert [(e.group, e.name) for e in envs] == [("b", "z"), ("a", "y")]


# --- published ports ------------------------------------------------------


def _ports_of(ports: object) -> tuple[tuple[int, int], ...]:
    [env] = parse_inventory(_reply(_ok(ports=ports)))
    return env.ports


def test_one_loopback_tcp_mapping_is_a_standard_published_pair() -> None:
    assert _ports_of("127.0.0.1:28080->8080/tcp") == ((8080, 28080),)


def test_several_mappings_are_ordered_by_standard_port_not_reply_order() -> None:
    reply = "127.0.0.1:28090->9090/tcp, 127.0.0.1:28081->8081/tcp, 127.0.0.1:28080->8080/tcp"
    assert _ports_of(reply) == ((8080, 28080), (8081, 28081), (9090, 28090))


def test_standard_port_is_the_container_side_and_orders_numerically() -> None:
    assert _ports_of("127.0.0.1:1024->65535/tcp, 127.0.0.1:65535->1024/tcp, 127.0.0.1:2000->10000/tcp") == (
        (1024, 65535),
        (10000, 2000),
        (65535, 1024),
    )


def test_stopped_container_with_no_ports_is_still_listed() -> None:
    envs = parse_inventory(_reply(_ok("a", "one", "exited", ""), _ok("b", "two", "running", "127.0.0.1:3000->3000/tcp")))
    assert [(e.name, e.state, e.ports) for e in envs] == [
        ("one", "stopped", ()),
        ("two", "running", ((3000, 3000),)),
    ]


def test_environment_built_without_ports_has_none() -> None:
    assert Environment("levr", "main", "running").ports == ()


@pytest.mark.parametrize(
    "fixture",
    [
        "env_inventory_ports_docker_engine_29_8_0_linux.txt",
        "env_inventory_ports_docker_desktop_4_97_0.txt",
    ],
)
def test_real_captured_tcp_entries_parse(fixture: str) -> None:
    running = json.loads(_fixture(fixture).splitlines()[0])
    tcp_only = ", ".join(e for e in running.split(", ") if e.endswith("/tcp"))
    assert tcp_only
    assert _ports_of(tcp_only) == ((8080, 28080), (8081, 28081), (9090, 28090))


@pytest.mark.parametrize(
    "fixture",
    [
        "env_inventory_ports_docker_engine_29_8_0_linux.txt",
        "env_inventory_ports_docker_desktop_4_97_0.txt",
    ],
)
def test_real_captured_running_line_with_a_udp_entry_is_unreadable(fixture: str) -> None:
    running = json.loads(_fixture(fixture).splitlines()[0])
    with pytest.raises(Unreadable, match="line 2"):
        parse_inventory(_reply(_ok(ports=running)))


@pytest.mark.parametrize(
    "fixture",
    [
        "env_inventory_ports_docker_engine_29_8_0_linux.txt",
        "env_inventory_ports_docker_desktop_4_97_0.txt",
    ],
)
def test_real_captured_stopped_line_is_no_ports(fixture: str) -> None:
    stopped = json.loads(_fixture(fixture).splitlines()[1])
    assert _ports_of(stopped) == ()


@pytest.mark.parametrize(
    "ports",
    [
        "0.0.0.0:28080->8080/tcp",
        "127.0.0.1:28080->8080/tcp, 0.0.0.0:28081->8081/tcp",
        "[::1]:28080->8080/tcp",
        "[::]:28080->8080/tcp",
        "127.0.0.1:28080->8080/udp",
        "127.0.0.1:28080->8080/tcp, 127.0.0.1:28081->8081/udp",
        "127.0.0.1:28080->8080/sctp",
        "127.0.0.2:28080->8080/tcp",
        "127.0.0.1:28080-28081->8080-8081/tcp",
        "8080/tcp",
        "127.0.0.1:28080->8080",
        "127.0.0.1:28080->8080/tcp,127.0.0.1:28081->8081/tcp",
        "127.0.0.1:28080->8080/tcp, ",
        " 127.0.0.1:28080->8080/tcp",
        "127.0.0.1:28080->8080/tcp\n",
        ", ",
    ],
)
def test_a_mapping_that_is_not_loopback_tcp_makes_the_reply_unreadable(ports: str) -> None:
    with pytest.raises(Unreadable, match="line 3.*ports"):
        parse_inventory(_reply(_ok("a", "b"), _ok(ports=ports)))


@pytest.mark.parametrize(
    "ports",
    [
        "127.0.0.1:28080->1023/tcp",
        "127.0.0.1:1023->8080/tcp",
        "127.0.0.1:28080->65536/tcp",
        "127.0.0.1:65536->8080/tcp",
        "127.0.0.1:28080->0/tcp",
        "127.0.0.1:0->8080/tcp",
        "127.0.0.1:28080->80/tcp",
        "127.0.0.1:123456->8080/tcp",
        "127.0.0.1:28080->08080/tcp",
    ],
)
def test_a_port_outside_1024_to_65535_on_either_side_is_unreadable(ports: str) -> None:
    with pytest.raises(Unreadable, match="line 3.*ports"):
        parse_inventory(_reply(_ok("a", "b"), _ok(ports=ports)))


@pytest.mark.parametrize(
    "ports",
    [
        "127.0.0.1:2808\u00b2->8080/tcp",
        "127.0.0.1:28080->808\u00b2/tcp",
        "127.0.0.1:2\u0668\u0660\u0668\u0660->8080/tcp",
        "127.0.0.1:28080->8\uff10\uff18\uff10/tcp",
    ],
)
def test_a_non_ascii_digit_in_a_port_is_unreadable(ports: str) -> None:
    with pytest.raises(Unreadable, match="line 3.*ports"):
        parse_inventory(_reply(_ok("a", "b"), _ok(ports=ports)))


@pytest.mark.parametrize("ports", [None, 28080, ["127.0.0.1:28080->8080/tcp"], {"a": 1}, True])
def test_a_ports_field_that_is_not_a_string_is_unreadable(ports: object) -> None:
    with pytest.raises(Unreadable, match="line 3"):
        parse_inventory(_reply(_ok("a", "b"), _ok(ports=ports)))


def test_ports_reason_names_the_line_and_never_the_raw_value() -> None:
    secret = "0.0.0.0:31337->8080/tcp"
    with pytest.raises(Unreadable) as info:
        parse_inventory(_reply(_ok("a", "b"), _ok(ports=secret)))
    reason = str(info.value)
    assert "line 3" in reason and "31337" not in reason and "0.0.0.0" not in reason
    assert reason.isprintable()


def test_no_partial_list_when_a_later_record_has_bad_ports() -> None:
    with pytest.raises(Unreadable):
        parse_inventory(_reply(_ok("a", "b", ports="127.0.0.1:3000->3000/tcp"), _ok("c", "d", ports="0.0.0.0:1->1/tcp")))


def test_labelled_helper_under_another_name_keeps_its_ports_unjudged() -> None:
    helper = _record("levr-env-main-db", "running", "levr", "main", "0.0.0.0:5432->5432/tcp")
    assert parse_inventory(_reply(_ok("a", "b"), helper)) == [Environment("a", "b", "running")]


# --- unreadable records ---------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        '"levr-env-main"|"running"|"levr"',
        '"levr-env-main"|"running"|"levr"|"main"',
        '"levr-env-main"|"running"|"levr"|"main"|""|""',
        "",
        "|",
        '"levr-env-main"|"running"|"levr"|"main"|""|',
        '"levr-env-main"|"running"|"levr"|"main"|"" ',
        '"levr-env-main" |"running"|"levr"|"main"|""',
    ],
)
def test_wrong_field_layout_is_unreadable(line: str) -> None:
    with pytest.raises(Unreadable, match="line 3"):
        parse_inventory(_reply(_ok("a", "b"), line))


@pytest.mark.parametrize(
    "bad",
    [
        "levr-env-main",
        "{",
        "'levr-env-main'",
        "\"unterminated",
        '"bad \\q escape"',
        '"raw\ttab"',
    ],
)
def test_field_that_is_not_valid_json_is_unreadable(bad: str) -> None:
    line = f'{bad}|"running"|"levr"|"main"|""'
    with pytest.raises(Unreadable, match="line 3"):
        parse_inventory(_reply(_ok("a", "b"), line))


@pytest.mark.parametrize(
    "bad",
    ["1", "-0.5", "null", "true", "{}", '{"a":1}', "[]", '["levr"]', "[" * 100000 + "]" * 100000, "NaN"],
)
@pytest.mark.parametrize("position", [0, 1, 2, 3, 4])
def test_field_that_is_json_but_not_a_string_is_unreadable(bad: str, position: int) -> None:
    fields = ['"levr-env-main"', '"running"', '"levr"', '"main"', '""']
    fields[position] = bad
    with pytest.raises(Unreadable):
        parse_inventory(_reply(_ok("a", "b"), "|".join(fields)))


@pytest.mark.parametrize(
    "value",
    ["Levr", "-levr", "", "levr\n", "le vr", "le_vr", "lévr", "levr\x7f", "levr ", "a" + "\ud800"],
)
@pytest.mark.parametrize("which", ["group", "name"])
def test_label_outside_the_charset_is_unreadable(value: str, which: str) -> None:
    group, name = ("levr", "main")
    if which == "group":
        group = value
    else:
        name = value
    line = _record(f"{group}-env-{name}", "running", group, name, "")
    with pytest.raises(Unreadable, match=which):
        parse_inventory(_reply(_ok("a", "b"), line))


def test_charset_failure_is_unreadable_even_under_a_non_matching_name() -> None:
    line = _record("helper", "running", "Levr", "main", "")
    with pytest.raises(Unreadable):
        parse_inventory(_reply(line))


def test_no_partial_list_when_a_later_record_is_bad() -> None:
    with pytest.raises(Unreadable):
        parse_inventory(_reply(_ok("a", "b"), _ok("c", "d"), "garbage"))


@pytest.mark.parametrize("line_no", range(4))
def test_real_label_escaping_captures_are_unreadable(line_no: int) -> None:
    line = _fixture("env_inventory_label_escaping.txt").splitlines()[line_no]
    with pytest.raises(Unreadable):
        parse_inventory(_reply(line))


def test_pipe_inside_a_json_string_does_not_shift_fields() -> None:
    line = _record("levr-env-main", "running", "levr|main", "x", "")
    with pytest.raises(Unreadable, match="group"):
        parse_inventory(_reply(line))


def test_pipe_in_container_name_field_does_not_shift_fields() -> None:
    line = _record("levr-env-main|running|levr|main", "running", "levr", "main", "")
    assert parse_inventory(_reply(line)) == []


def test_newline_in_label_cannot_forge_a_second_record() -> None:
    forged = _record("x", "running", "levr\n" + _ok("a", "b"), "main", "")
    assert "\n" not in forged
    with pytest.raises(Unreadable):
        parse_inventory(_reply(forged))


def test_newline_and_quotes_in_label_that_would_split_into_two_valid_records_are_unreadable() -> None:
    name = 'main"\n"levr-env-evil"|"running"|"levr"|"evil'
    forged = _record("levr-env-main", "running", "levr", name, "")
    assert "\n" not in forged
    with pytest.raises(Unreadable):
        parse_inventory(_reply(forged))


def test_raw_newline_splitting_a_record_is_unreadable() -> None:
    with pytest.raises(Unreadable):
        parse_inventory(HEADER + '\n"levr-env-main"|"running"|"levr"|\n"main"|""\n')


def test_unreadable_reason_is_one_line() -> None:
    with pytest.raises(Unreadable) as info:
        parse_inventory(_reply(_record("x", "running", "Le\nvr\x1b[31m", "main", "")))
    reason = str(info.value)
    assert reason and reason.isprintable() and "\n" not in reason


# --- the command ----------------------------------------------------------


def test_remote_command_has_no_backslash_or_control_character() -> None:
    assert "\\" not in INVENTORY_REMOTE_COMMAND
    assert all(ord(c) >= 0x20 and ord(c) != 0x7F for c in INVENTORY_REMOTE_COMMAND)


def _stub_docker(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    out = tmp_path / "argv.out"
    stub = bindir / "docker"
    stub.write_text('#!/bin/sh\nfor a in "$@"; do printf \'%s\\0\' "$a"; done > "$STUB_OUT"\n')
    stub.chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "STUB_OUT": str(out), "HOME": str(tmp_path)}
    return env, out


def _received(out: Path) -> tuple[str, ...]:
    return tuple(out.read_bytes().decode().split("\0")[:-1])


def test_remote_command_under_sh_hands_docker_the_local_argv(tmp_path: Path) -> None:
    env, out = _stub_docker(tmp_path)
    subprocess.run(["/bin/sh", "-c", INVENTORY_REMOTE_COMMAND], env=env, check=True)
    assert _received(out) == INVENTORY_ARGV[1:]


def test_remote_command_under_fish_hands_docker_the_local_argv(tmp_path: Path) -> None:
    fish = shutil.which("fish")
    if fish is None:
        pytest.skip("fish not installed")
    env, out = _stub_docker(tmp_path)
    subprocess.run([fish, "-c", INVENTORY_REMOTE_COMMAND], env=env, check=True)
    assert _received(out) == INVENTORY_ARGV[1:]


def test_remote_command_matches_the_measured_command() -> None:  # inert-gate: allow seam smoke, captured command
    assert INVENTORY_REMOTE_COMMAND + "\n" == _fixture("env_inventory_command.txt")
    assert INVENTORY_ARGV[0] == "docker"
