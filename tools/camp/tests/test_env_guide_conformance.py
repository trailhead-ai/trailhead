"""The environment guide's examples, run through camp's own reader.

The guide (``tools/camp/docs/environments.md``) tags each concrete example with a fence info
string such as ``sh camp-env-example``. Each block is a test *input*: a ``docker`` command is
turned into the reply Docker would print for the container it makes, then handed to
``parse_inventory``; the hosts-file block is loaded by ``load_hosts``. Nothing asserts on the
guide's sentences. Every consumer test requires its own block, so a guide that loses or retags one
fails that test rather than letting the others pass over nothing.

The ``-p`` to ``.Ports`` translation is itself checked against a real capture
(``fixtures/env_inventory_guide_example_docker_desktop_29_9_2.txt``; see ``env_read_README.txt``).
"""

from __future__ import annotations

import json
import re
import shlex
import sys
import tomllib
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.cli.env import _render_block  # noqa: E402
from camp.env.contract import (  # noqa: E402
    INVENTORY_ARGV,
    LABEL_GROUP,
    LABEL_NAME,
    Environment,
    Unreadable,
    container_name,
    parse_inventory,
)
from camp.env.read import DOCKER_NOT_INSTALLED, ENVIRONMENTS, MachineInventory, read_remote  # noqa: E402
from camp.host import transport  # noqa: E402
from camp.host.config import Host, HostConfigError, env_host_names, load_hosts, self_host_name  # noqa: E402

from ._env_reply import record, reply  # noqa: E402
from ._markdown_anchors import CODE_FENCE, github_slugs  # noqa: E402

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "environments.md"
README = GUIDE.parent.parent / "README.md"
CAPTURE = Path(__file__).parent / "fixtures" / "env_inventory_guide_example_docker_desktop_29_9_2.txt"
CAPTURE_RANGE = Path(__file__).parent / "fixtures" / "env_inventory_ports_same_port_range_docker_desktop_29_9_2.txt"
CAPTURE_RANGE_REORDERED = (
    Path(__file__).parent / "fixtures" / "env_inventory_ports_same_port_range_reordered_docker_desktop_29_9_2.txt"
)
CAPTURE_SORT_ORDER = Path(__file__).parent / "fixtures" / "env_inventory_ports_sort_order_docker_desktop_29_9_2.txt"

_FENCE = re.compile(r"^```(\w+) +(camp-env-[\w-]+)\n(.*?)^```$", re.MULTILINE | re.DOTALL)


def _reason_for(text: str) -> str:
    with pytest.raises(Unreadable) as raised:
        parse_inventory(text)
    return re.sub(r"line \d+", "line <N>", str(raised.value))


def _ports_reason() -> str:
    return _reason_for(reply(record("x-env-y", "running", "x", "y", "0.0.0.0:18080->8080/tcp")))


def _blocks(role: str) -> list[str]:
    text = GUIDE.read_text(encoding="utf-8")
    return [body for _lang, found, body in _FENCE.findall(text) if found == role]


def _one(role: str) -> str:
    found = _blocks(role)
    assert len(found) == 1, f"guide must carry exactly one {role!r} block, found {len(found)}"
    return found[0]


def _entry(address: str, first: int, last: int, container_first: int, container_last: int) -> str:
    host = f"{first}" if first == last else f"{first}-{last}"
    container = f"{container_first}" if container_first == container_last else f"{container_first}-{container_last}"
    return f"{address}:{host}->{container}/tcp"


class _Container:
    def __init__(self, command: str) -> None:
        words = shlex.split(command.replace("\\\n", " "))
        assert words[:2] in (["docker", "run"], ["docker", "create"]), words[:2]
        self.running = words[1] == "run"
        self.name = ""
        self.labels: dict[str, str] = {}
        self.publishes: list[str] = []
        it = iter(words[2:])
        for word in it:
            if word == "--name":
                self.name = next(it)
            elif word == "--label":
                key, _, value = next(it).partition("=")
                self.labels[key] = value
            elif word == "-p":
                self.publishes.append(next(it))

    def ports(self) -> str:
        if not self.running:
            return ""
        groups: list[list] = []
        mappings: list[list] = []
        for spec in sorted(self.publishes, key=lambda spec: int(spec.split(":")[-1])):
            *host, container = spec.split(":")
            published = host[-1]
            address = host[0] if len(host) == 2 else "0.0.0.0"
            entry = [address, int(published), int(published), int(container), int(container)]
            if entry[1] != entry[3]:
                mappings.append(entry)
                continue
            last = groups[-1] if groups else None
            if last and last[0] == address and last[2] + 1 == entry[1]:
                last[2] = last[4] = entry[1]
            else:
                groups.append(entry)
        entries = groups + mappings
        return ", ".join(_entry(*e) for e in entries)

    def record(self) -> str:
        state = "running" if self.running else "created"
        return record(
            self.name, state, self.labels.get(LABEL_GROUP, ""), self.labels.get(LABEL_NAME, ""), self.ports()
        )


def test_worked_example_is_one_environment_matching_its_own_flags() -> None:
    c = _Container(_one("camp-env-example"))
    envs = parse_inventory(reply(c.record()))
    expected = sorted(
        (int(p.split(":")[-1]), int(p.split(":")[-2])) for p in c.publishes
    )
    assert envs == [Environment(c.labels[LABEL_GROUP], c.labels[LABEL_NAME], "running", tuple(expected))]
    assert envs[0].ports, "the worked example must publish at least one port"


def test_worked_example_labels_use_the_contract_keys_and_its_name_follows_the_rule() -> None:
    c = _Container(_one("camp-env-example"))
    assert set(c.labels) == {LABEL_GROUP, LABEL_NAME}
    assert c.name == container_name(c.labels[LABEL_GROUP], c.labels[LABEL_NAME])


def test_translation_of_the_example_matches_the_real_docker_capture() -> None:
    c = _Container(_one("camp-env-example"))
    captured = CAPTURE.read_text(encoding="utf-8").splitlines()
    assert captured[0] == reply().splitlines()[0]
    assert captured[1].split("|", 4)[1:2] == ['"running"']
    assert captured[1].split("|", 4)[4] == record(c.ports()), "translated .Ports differs from real Docker"


def test_real_capture_reads_as_the_example_environment() -> None:
    c = _Container(_one("camp-env-example"))
    envs = parse_inventory(CAPTURE.read_text(encoding="utf-8"))
    assert [(e.state, e.ports) for e in envs] == [("running", parse_inventory(reply(c.record()))[0].ports)]


@pytest.mark.parametrize("role", ["camp-env-counter-example", "camp-env-counter-example-range"])
def test_every_counter_example_is_unreadable_for_its_ports(role: str) -> None:
    blocks = _blocks(role)
    assert blocks, f"guide has no {role!r} block"
    for body in blocks:
        assert _reason_for(reply(_Container(body).record())) == _ports_reason()


def test_labelled_helper_under_another_name_is_still_held_to_the_port_rule() -> None:
    helper = _Container(_one("camp-env-rule-helper"))
    assert helper.name != container_name(helper.labels[LABEL_GROUP], helper.labels[LABEL_NAME])
    assert _reason_for(reply(helper.record())) == _ports_reason()


def test_labelled_container_under_another_name_is_skipped() -> None:
    c = _Container(_one("camp-env-wrong-name"))
    assert c.name != container_name(c.labels[LABEL_GROUP], c.labels[LABEL_NAME])
    assert parse_inventory(reply(c.record())) == []


def test_container_port_published_twice_is_unreadable() -> None:
    c = _Container(_one("camp-env-rule-duplicate"))
    assert _reason_for(reply(c.record())) == _ports_reason()


def test_stopped_container_publishes_nothing() -> None:
    c = _Container(_one("camp-env-rule-stopped"))
    assert c.publishes, "the stopped example must declare ports to show they are not published"
    envs = parse_inventory(reply(c.record()))
    assert [(e.state, e.ports) for e in envs] == [("stopped", ())]


def test_documented_inventory_command_is_the_one_camp_runs() -> None:
    assert tuple(shlex.split(_one("camp-env-inventory").replace("\\\n", " "))) == INVENTORY_ARGV


def test_hosts_example_loads_and_env_hosts_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = _one("camp-env-hosts")
    (tmp_path / "hosts.toml").write_text(body, encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path))
    document = tomllib.loads(body)
    documented = document["env_hosts"]
    assert documented, "the example must opt at least one host in"
    assert env_host_names() == documented
    assert self_host_name() == document["self_name"]
    declared = load_hosts()
    assert set(declared) == set(document["hosts"])
    assert all(host.ssh and host.camp_bin for host in declared.values())


def test_hosts_example_naming_an_undeclared_host_is_refused(tmp_path: Path) -> None:
    body = _one("camp-env-hosts")
    broken = re.sub(r"env_hosts = \[", 'env_hosts = ["not-declared", ', body, count=1)
    assert broken != body
    (tmp_path / "hosts.toml").write_text(broken, encoding="utf-8")
    with pytest.raises(HostConfigError, match="not-declared"):
        env_host_names(env={"CAMP_CONFIG_DIR": str(tmp_path)})


def _table_rows() -> list[str]:
    return [ln.replace("\\|", "|") for ln in GUIDE.read_text(encoding="utf-8").splitlines() if ln.startswith("|")]


@pytest.mark.parametrize(
    "bad_reply",
    [
        "not a docker reply\n",
        reply('"only-one-field"'),
        reply(record("x-env-y", "running", "X", "y", "")),
        reply(record("x-env-Y", "running", "x", "Y", "")),
        reply(record("x-env-y", "running", "x", "y", "0.0.0.0:18080->8080/tcp")),
    ],
    ids=["header", "framing", "group", "name", "ports"],
)
def test_if_you_see_table_names_each_unreadable_reason(bad_reply: str) -> None:
    reason = _reason_for(bad_reply)
    assert any(reason in row for row in _table_rows()), f"no 'If you see' row names {reason!r}"


def test_if_you_see_table_names_no_environments_for_a_wrong_name() -> None:
    c = _Container(_one("camp-env-wrong-name"))
    shown = _render_block("h", MachineInventory(ENVIRONMENTS, tuple(parse_inventory(reply(c.record())))))
    line = shown.splitlines()[1].strip()
    assert line == "no environments"
    assert any(line in row for row in _table_rows())


def test_real_docker_range_capture_is_unreadable_for_the_ports_reason() -> None:
    ports = json.loads(CAPTURE_RANGE.read_text(encoding="utf-8"))
    assert "28080-28081" in ports, "the capture must be the range rendering"
    inventory = reply(record(container_name("acme", "main"), "running", "acme", "main", ports))
    assert _reason_for(inventory) == _ports_reason()


def test_translation_of_the_same_port_pair_reproduces_the_real_docker_range_format() -> None:
    c = _Container(_one("camp-env-counter-example-range"))
    assert c.ports() == json.loads(CAPTURE_RANGE.read_text(encoding="utf-8"))


def test_translation_of_the_reordered_same_port_pair_reproduces_the_real_docker_range_format() -> None:
    publishes = "-p 127.0.0.1:28081:28081 -p 127.0.0.1:28090:8090 -p 127.0.0.1:28080:28080"
    c = _Container(f"docker run -d --name a-env-b {publishes} alpine")
    assert c.ports() == json.loads(CAPTURE_RANGE_REORDERED.read_text(encoding="utf-8"))


def test_translation_orders_entries_the_way_real_docker_prints_them() -> None:
    publishes = "-p 127.0.0.1:20000:20000 -p 127.0.0.1:21000:10000 -p 127.0.0.1:22000:5000"
    c = _Container(f"docker run -d --name a-env-b {publishes} alpine")
    assert c.ports() == json.loads(CAPTURE_SORT_ORDER.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "flags",
    ["18080:8080 18081:8081", "28080:28080 28081:8081", "18080:18079 18081:18081"],
    ids=["both-differ", "second-differs", "first-differs"],
)
def test_translation_keeps_consecutive_ports_apart_unless_both_have_the_same_host_and_container_port(
    flags: str,
) -> None:
    publishes = " ".join(f"-p 127.0.0.1:{spec}" for spec in flags.split())
    c = _Container(f"docker run -d --name a-env-b {publishes} alpine")
    expected = sorted(f"127.0.0.1:{h}->{k}/tcp" for h, k in (s.split(":") for s in flags.split()))
    assert sorted(c.ports().split(", ")) == expected


def test_if_you_see_table_names_a_host_key_that_is_not_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport, "run_remote_command", lambda *a, **kw: transport.IdentityUnknown())
    inventory = read_remote("builder", Host(ssh="<dest>", camp_bin="camp"), connect_timeout=5.0)
    assert any(inventory.reason in row for row in _table_rows()), f"no 'If you see' row names {inventory.reason!r}"


def test_if_you_see_table_names_docker_not_installed() -> None:
    line = _render_block("h", MachineInventory(DOCKER_NOT_INSTALLED)).splitlines()[1].strip()
    assert any(line in row for row in _table_rows())


def test_probe_recipe_hosts_file_loads_and_opts_the_probed_host_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "hosts.toml").write_text(_one("camp-env-probe-hosts"), encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path))
    declared = load_hosts()
    assert env_host_names() == list(declared)
    (name,) = declared
    steps = _one("camp-env-probe").replace("\\\n", " ").splitlines()
    run = next(line for line in steps if "docker run" in line)
    assert run.split()[:2] == ["ssh", declared[name].ssh], "the probe must reach the host the hosts file declares"
    listing = next(line for line in steps if "camp env ls" in line)
    assert listing.split()[-1] == name


def test_probe_recipe_container_lists_as_the_recipe_says() -> None:
    steps = _one("camp-env-probe").replace("\\\n", " ").splitlines()
    run = next(line for line in steps if "docker run" in line)
    c = _Container(run[run.index("docker") :])
    envs = parse_inventory(reply(c.record()))
    assert c.name == container_name(c.labels[LABEL_GROUP], c.labels[LABEL_NAME])
    shown = _render_block("builder", MachineInventory(ENVIRONMENTS, tuple(envs)))
    assert shown.split() == _one("camp-env-probe-listing").split()
    removal = next(line for line in steps if "docker rm" in line)
    assert removal.split()[-1] == c.name


_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _unresolved_links(guide: str, readme: str) -> list[str]:
    own, theirs = github_slugs(guide), github_slugs(readme)
    unresolved = []
    for target in _LINK.findall(CODE_FENCE.sub("", guide)):
        path, _, anchor = target.partition("#")
        if target.startswith("#"):
            found = anchor in own
        elif path == "../README.md" and anchor:
            found = anchor in theirs
        else:
            continue
        if not found:
            unresolved.append(target)
    return unresolved


def test_every_guide_link_into_the_readme_or_itself_names_a_heading() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    assert _LINK.search(guide)
    assert _unresolved_links(guide, README.read_text(encoding="utf-8")) == []


def test_the_guide_link_check_catches_a_wrong_anchor_in_either_document() -> None:
    guide = "# G\n\n## Here\n\n[a](#here) [b](../README.md#there) [c](#nowhere) [d](../README.md#gone)\n"
    assert _unresolved_links(guide, "# R\n\n## There\n") == ["#nowhere", "../README.md#gone"]
