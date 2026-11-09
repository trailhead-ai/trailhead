"""The environment guide's examples, run through camp's own reader.

The guide (``tools/camp/docs/environments.md``) tags each concrete example with a fence info
string such as ``sh camp-env-example``. Each block is a test *input*: a ``docker`` command is
turned into the reply Docker would print for the container it makes, then handed to
``parse_inventory``; the hosts-file block is loaded by ``load_hosts``. Nothing asserts on the
guide's sentences. A guide that loses or retags a block fails ``test_every_example_role_is_present``
rather than letting the other tests pass over nothing.

The ``-p`` to ``.Ports`` translation is itself checked against a real capture
(``fixtures/env_inventory_guide_example_docker_desktop_29_9_2.txt``; see ``env_read_README.txt``).
"""

from __future__ import annotations

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
from camp.env.read import ENVIRONMENTS, MachineInventory  # noqa: E402
from camp.host.config import HostConfigError, env_host_names, load_hosts, self_host_name  # noqa: E402

from ._env_reply import record, reply  # noqa: E402

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "environments.md"
CAPTURE = Path(__file__).parent / "fixtures" / "env_inventory_guide_example_docker_desktop_29_9_2.txt"

_FENCE = re.compile(r"^```(\w+) +(camp-env-[\w-]+)\n(.*?)^```$", re.MULTILINE | re.DOTALL)
ROLES = (
    "camp-env-example",
    "camp-env-counter-example",
    "camp-env-wrong-name",
    "camp-env-rule-helper",
    "camp-env-rule-duplicate",
    "camp-env-rule-stopped",
    "camp-env-inventory",
    "camp-env-hosts",
)


def _blocks(role: str) -> list[str]:
    text = GUIDE.read_text(encoding="utf-8")
    return [body for _lang, found, body in _FENCE.findall(text) if found == role]


def _one(role: str) -> str:
    found = _blocks(role)
    assert len(found) == 1, f"guide must carry exactly one {role!r} block, found {len(found)}"
    return found[0]


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
        entries = []
        for spec in self.publishes:
            *host, container = spec.split(":")
            published = host[-1]
            address = host[0] if len(host) == 2 else "0.0.0.0"
            entries.append(f"{address}:{published}->{container}/tcp")
        return ", ".join(entries)

    def record(self) -> str:
        state = "running" if self.running else "created"
        return record(
            self.name, state, self.labels.get(LABEL_GROUP, ""), self.labels.get(LABEL_NAME, ""), self.ports()
        )


def test_every_example_role_is_present() -> None:
    for role in ROLES:
        assert _blocks(role), f"guide has no {role!r} block"


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


def test_every_counter_example_is_unreadable() -> None:
    blocks = _blocks("camp-env-counter-example")
    assert blocks, "guide has no counter-example"
    for body in blocks:
        with pytest.raises(Unreadable):
            parse_inventory(reply(_Container(body).record()))


def test_labelled_helper_under_another_name_is_still_held_to_the_port_rule() -> None:
    helper = _Container(_one("camp-env-rule-helper"))
    assert helper.name != container_name(helper.labels[LABEL_GROUP], helper.labels[LABEL_NAME])
    with pytest.raises(Unreadable):
        parse_inventory(reply(helper.record()))


def test_labelled_container_under_another_name_is_skipped() -> None:
    c = _Container(_one("camp-env-wrong-name"))
    assert c.name != container_name(c.labels[LABEL_GROUP], c.labels[LABEL_NAME])
    assert parse_inventory(reply(c.record())) == []


def test_container_port_published_twice_is_unreadable() -> None:
    c = _Container(_one("camp-env-rule-duplicate"))
    with pytest.raises(Unreadable):
        parse_inventory(reply(c.record()))


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


def _reason_for(text: str) -> str:
    with pytest.raises(Unreadable) as raised:
        parse_inventory(text)
    return re.sub(r"line \d+", "line <N>", str(raised.value))


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
