"""Tests for camp.env.resolve — an attach target resolved to one environment or a refusal.

Reads are injected fakes that record which machines were asked; none touches Docker, ssh or a
hosts file.

Test contract:
- A bare name reads this machine once and the opted-in hosts (never self_name remotely, never a
  declared host outside env_hosts); a host form reads that host alone, opted in or not.
- One match resolves; matches on two hosts, two groups on one host, or here and on a host are
  ambiguous, each in its shortest unique form.
- A bare name is refused while any machine it reads is unreadable, but not while Docker is not
  running or not installed.
- Not found, an unreadable or Docker-less named host, an undeclared host, and this machine each
  have their own refusal.
- parse_target accepts NAME, HOST/NAME and HOST/GROUP/NAME and rejects malformed parts.
- Far-side reasons reach a message as printable text.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.env.contract import Environment  # noqa: E402
from camp.env.read import (  # noqa: E402
    DOCKER_NOT_INSTALLED,
    DOCKER_NOT_RUNNING,
    ENVIRONMENTS,
    UNREADABLE,
    MachineInventory,
)
from camp.env.resolve import (  # noqa: E402
    Refused,
    Resolved,
    Target,
    TargetError,
    parse_target,
    resolve,
)
from camp.host.config import Host  # noqa: E402

HOSTS = {
    "andromeda": Host(ssh="tom@andromeda", camp_bin="camp"),
    "perseus": Host(ssh="tom@perseus", camp_bin="camp"),
    "toms-macbook-pro": Host(ssh="tom@mbp", camp_bin="camp"),
}


def envs(*pairs, state="running"):
    return MachineInventory(ENVIRONMENTS, tuple(Environment(g, n, state) for g, n in pairs))


def unreadable(reason):
    return MachineInventory(UNREADABLE, reason=reason)


class Reads:
    def __init__(self, local=None, **remote):
        self.local = local if local is not None else envs()
        self.remote = remote
        self.local_calls = 0
        self.remote_calls: list[str] = []

    def read_local(self):
        self.local_calls += 1
        return self.local

    def read_remote(self, name):
        self.remote_calls.append(name)
        return self.remote[name]


def run(text, reads, *, opted_in=("andromeda", "perseus"), self_name=None, hosts=HOSTS, **extra):
    return resolve(
        parse_target(text),
        hosts=hosts,
        opted_in=list(opted_in),
        self_name=self_name,
        read_local=reads.read_local,
        read_remote=reads.read_remote,
        **extra,
    )


# -- which machines are read ---------------------------------------------------------------------


def test_bare_name_reads_here_and_opted_in_hosts_only():
    reads = Reads(andromeda=envs(), perseus=envs(("levr", "main")))
    run("main", reads)
    assert reads.local_calls == 1
    assert sorted(reads.remote_calls) == ["andromeda", "perseus"]


def test_bare_name_does_not_read_self_name_remotely_and_reads_here_once():
    reads = Reads(perseus=envs(("levr", "main")))
    result = run("main", reads, opted_in=("perseus", "andromeda"), self_name="andromeda")
    assert reads.local_calls == 1
    assert reads.remote_calls == ["perseus"]
    assert result == Resolved("perseus", Environment("levr", "main", "running"))


def test_host_form_reads_that_host_alone_even_when_not_opted_in():
    reads = Reads(**{"toms-macbook-pro": envs(("levr", "main"))})
    result = run("toms-macbook-pro/main", reads)
    assert reads.local_calls == 0
    assert reads.remote_calls == ["toms-macbook-pro"]
    assert result == Resolved("toms-macbook-pro", Environment("levr", "main", "running"))


# -- matches -------------------------------------------------------------------------------------


def test_one_match_resolves_with_its_host():
    reads = Reads(andromeda=envs(("levr", "other")), perseus=envs(("levr", "main")))
    assert run("main", reads) == Resolved("perseus", Environment("levr", "main", "running"))


def test_stopped_environment_still_resolves():
    reads = Reads(andromeda=envs(), perseus=envs(("levr", "main"), state="stopped"))
    assert run("main", reads) == Resolved("perseus", Environment("levr", "main", "stopped"))


def test_host_group_name_takes_that_exact_environment():
    reads = Reads(perseus=envs(("acme", "main"), ("levr", "main")))
    result = run("perseus/levr/main", reads)
    assert result == Resolved("perseus", Environment("levr", "main", "running"))
    assert reads.remote_calls == ["perseus"]


def test_matches_on_two_hosts_are_ambiguous_naming_both_host_forms():
    reads = Reads(andromeda=envs(("levr", "main")), perseus=envs(("acme", "main")))
    result = run("main", reads)
    assert result == Refused(
        "main matches more than one environment: andromeda/main, perseus/main; attach one of those"
    )


def test_same_name_in_two_groups_on_one_host_names_both_group_forms():
    reads = Reads(andromeda=envs(), perseus=envs(("levr", "main"), ("acme", "main")))
    result = run("main", reads)
    assert result == Refused(
        "main matches more than one environment: perseus/levr/main, perseus/acme/main;"
        " attach one of those"
    )


def test_host_form_with_two_groups_is_ambiguous_on_that_host():
    reads = Reads(perseus=envs(("levr", "main"), ("acme", "main")))
    assert run("perseus/main", reads) == Refused(
        "main matches more than one environment on perseus: perseus/levr/main, perseus/acme/main;"
        " attach one of those"
    )


def test_match_here_and_on_a_host_is_ambiguous_naming_this_machine():
    reads = Reads(local=envs(("levr", "main")), andromeda=envs(), perseus=envs(("levr", "main")))
    result = run("main", reads)
    assert result == Refused(
        "main matches more than one environment: local/main (this machine; cannot be attached yet),"
        " perseus/main; attach one of those"
    )


# -- unreadable machines during a bare name ------------------------------------------------------


def test_bare_name_is_refused_while_a_host_is_unreadable_even_if_another_has_it():
    reads = Reads(
        andromeda=envs(("levr", "main")),
        perseus=unreadable("unreachable (Could not resolve hostname)"),
    )
    assert run("main", reads) == Refused(
        "main cannot be resolved while perseus could not be read"
        " (unreachable (Could not resolve hostname)); name the host, as andromeda/main or perseus/main"
    )


def test_bare_name_is_refused_while_this_machine_is_unreadable():
    reads = Reads(
        local=unreadable("line 2: not four JSON strings separated by '|'"),
        andromeda=envs(("levr", "main")),
        perseus=envs(),
    )
    assert run("main", reads) == Refused(
        "main cannot be resolved while this machine could not be read"
        " (line 2: not four JSON strings separated by '|'); name the host, as andromeda/main"
    )


def test_every_unreadable_machine_is_named_with_its_reason():
    reads = Reads(
        andromeda=unreadable("ssh credentials were refused"),
        perseus=unreadable("no answer within 30s"),
    )
    message = run("main", reads).message
    assert "andromeda could not be read (ssh credentials were refused)" in message
    assert "perseus could not be read (no answer within 30s)" in message
    assert message.endswith("name the host, as andromeda/main or perseus/main")


@pytest.mark.parametrize("kind", [DOCKER_NOT_RUNNING, DOCKER_NOT_INSTALLED])
def test_a_host_without_usable_docker_does_not_block_a_bare_name(kind):
    reads = Reads(andromeda=MachineInventory(kind), perseus=envs(("levr", "main")))
    assert run("main", reads) == Resolved("perseus", Environment("levr", "main", "running"))


# -- not found -----------------------------------------------------------------------------------


def test_bare_not_found_names_every_machine_read_and_ends_with_the_ls_pointer():
    reads = Reads(andromeda=envs(), perseus=envs())
    assert run("main", reads, hosts={"andromeda": HOSTS["andromeda"], "perseus": HOSTS["perseus"]}) == Refused(
        "no environment named main on this machine, andromeda or perseus;"
        " run camp env ls to see what is running"
    )


def test_bare_not_found_says_where_docker_is_not_running_or_installed():
    reads = Reads(
        andromeda=MachineInventory(DOCKER_NOT_INSTALLED), perseus=MachineInventory(DOCKER_NOT_RUNNING)
    )
    message = run("main", reads, hosts={"andromeda": HOSTS["andromeda"], "perseus": HOSTS["perseus"]}).message
    assert message == (
        "no environment named main on this machine, andromeda or perseus;"
        " Docker is not installed on andromeda; Docker is not running on perseus;"
        " run camp env ls to see what is running"
    )


def test_bare_not_found_names_declared_hosts_that_were_not_read_with_the_hint():
    reads = Reads(andromeda=envs(), perseus=envs())
    assert run("main", reads).message == (
        "no environment named main on this machine, andromeda or perseus"
        " (not read: toms-macbook-pro; name one as <host>/main);"
        " run camp env ls to see what is running"
    )


def test_a_local_docker_state_is_reported_on_this_machine():
    reads = Reads(local=MachineInventory(DOCKER_NOT_RUNNING), andromeda=envs(), perseus=envs())
    message = run("main", reads, hosts={"andromeda": HOSTS["andromeda"], "perseus": HOSTS["perseus"]}).message
    assert "Docker is not running on this machine" in message


def test_host_form_not_found_names_only_that_host():
    reads = Reads(perseus=envs(("levr", "other")))
    assert run("perseus/main", reads) == Refused(
        "no environment named main on perseus; run camp env ls to see what is running"
    )


def test_host_group_name_not_found_names_the_qualified_name():
    reads = Reads(perseus=envs(("acme", "main")))
    assert run("perseus/levr/main", reads) == Refused(
        "no environment named levr/main on perseus; run camp env ls to see what is running"
    )


# -- a named host that cannot answer -------------------------------------------------------------


def test_named_host_unreadable_carries_the_reads_own_reason():
    reads = Reads(perseus=unreadable("the host key is not pinned; run ssh tduffield@perseus once to pin it"))
    assert run("perseus/main", reads) == Refused(
        "perseus could not be read: the host key is not pinned; run ssh tduffield@perseus once to pin it"
    )


def test_named_host_with_docker_not_running_says_so():
    reads = Reads(perseus=MachineInventory(DOCKER_NOT_RUNNING))
    assert run("perseus/main", reads) == Refused("Docker is not running on perseus")


def test_named_host_without_docker_says_so():
    reads = Reads(perseus=MachineInventory(DOCKER_NOT_INSTALLED))
    assert run("perseus/levr/main", reads) == Refused("Docker is not installed on perseus")


# -- undeclared host -----------------------------------------------------------------------------


def test_undeclared_host_is_refused_naming_the_declared_hosts_and_reads_nothing():
    reads = Reads()
    result = run(
        "orion/main", reads, hosts={"andromeda": HOSTS["andromeda"], "perseus": HOSTS["perseus"]},
        hosts_file="/cfg/hosts.toml",
    )
    assert result == Refused("no host named orion in /cfg/hosts.toml; declared: andromeda, perseus")
    assert (reads.local_calls, reads.remote_calls) == (0, [])


# -- this machine --------------------------------------------------------------------------------

NOT_YET = "attaching an environment on this machine is not supported yet"


@pytest.mark.parametrize("text", ["local/main", "local/levr/main", "box/main", "box/levr/main"])
def test_this_machine_by_either_name_is_refused_without_reading(text):
    reads = Reads()
    assert run(text, reads, self_name="box", hosts=HOSTS) == Refused(NOT_YET)
    assert (reads.local_calls, reads.remote_calls) == (0, [])


def test_bare_name_found_only_here_is_refused_as_not_supported_yet():
    reads = Reads(local=envs(("levr", "main")), andromeda=envs(), perseus=envs())
    assert run("main", reads) == Refused(NOT_YET)


# -- printable -----------------------------------------------------------------------------------


def test_reason_with_escape_and_newline_reaches_the_message_stripped():
    reads = Reads(perseus=unreadable("bad\x1b[31m thing\nsecond"))
    message = run("perseus/main", reads).message
    assert message == "perseus could not be read: bad[31m thingsecond"


def test_reason_is_stripped_in_a_bare_name_refusal_too():
    reads = Reads(andromeda=envs(), perseus=unreadable("x\x1b]0;t\x07y\nz"))
    message = run("main", reads).message
    assert "\x1b" not in message and "\n" not in message and "\x07" not in message
    assert "(x]0;tyz)" in message


# -- parse_target --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("main", Target(None, None, "main")),
        ("perseus/main", Target("perseus", None, "main")),
        ("perseus/levr/main", Target("perseus", "levr", "main")),
        ("perseus/9-a/0b", Target("perseus", "9-a", "0b")),
    ],
)
def test_parse_target_accepts_the_three_forms(text, expected):
    assert parse_target(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "/main", "perseus/", "perseus//main", "a/b/c/d", "Main", "-main", "perseus/Main",
     "perseus/-main", "per seus/main", "perseus/le vr/main", "Perseus/main", "-perseus/main",
     "perseus/levr/Main", "main\n", "perseus/main\n"],
)
def test_parse_target_rejects_malformed_text(text):
    with pytest.raises(TargetError):
        parse_target(text)
