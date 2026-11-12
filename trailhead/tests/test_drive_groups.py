"""Tests for ``trailhead drive-groups``: the camp groups a drive may start a workspace in.

Real camp runs under a scratch CAMP_CONFIG_DIR / CAMP_STATE_DIR through a ``camp`` shim on the
fenced PATH; HOME, XDG roots and every TRAILHEAD_* directory are fenced into tmp_path, so
nothing reads the live camp groups, vaults or hub configuration.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from trailhead.launch_profile import DRIVE_GROUPS_OUTPUT_KEYS
from trailhead.ranger.config import HubConfig, write_hub_config
from trailhead.tests.test_session_start import _fence, _run_cli

CAMP_CLI = Path(__file__).resolve().parents[2] / "tools" / "camp" / "plugins" / "camp" / "cli" / "camp"


def _shim(tmp_path: Path, body: str) -> dict[str, str]:
    bin_dir = tmp_path / "shim-bin"
    bin_dir.mkdir(exist_ok=True)
    camp = bin_dir / "camp"
    camp.write_text(f"#!/bin/sh\n{body}\n")
    camp.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}


def _real_camp(tmp_path: Path) -> dict[str, str]:
    env = _fence(tmp_path)
    env.update(_shim(tmp_path, f'exec "{sys.executable}" "{CAMP_CLI}" "$@"'))
    return env


def _group(env: dict[str, str], name: str, *, account: str | None = None, vaults: tuple[str, ...] = ()) -> None:
    groups = Path(env["CAMP_CONFIG_DIR"]) / "groups"
    groups.mkdir(parents=True, exist_ok=True)
    text = f'[group]\nname = "{name}"\n\n[[members]]\nname = "m-{name}"\nrepo_root = "/tmp/fake-{name}"\n'
    if account is not None:
        text += f'\n[launch]\naccount = "{account}"\n'
    for vault in vaults:
        text += f'\n[[shared_vaults]]\nname = "{vault}"\nroot = "/tmp/vault-{vault}"\n'
    (groups / f"{name}.toml").write_text(text)


def _hub(env: dict[str, str], launch: str = "default", account: str = "a") -> None:
    write_hub_config(HubConfig(name="Hub", account=account, launch_account=launch), env=env, platform="linux")


def _drive(monkeypatch, capsys, env, *, account="a", vault="v"):
    code = _run_cli(monkeypatch, env, ["drive-groups", "--account", account, f"--vault={vault}", "--json"])
    out = capsys.readouterr()
    return code, out.out, out.err


def _names(out: str) -> list[str]:
    return [g["name"] for g in json.loads(out)["groups"]]


def test_a_group_on_the_hubs_account_binding_the_vault_is_returned(tmp_path, monkeypatch, capsys):
    env = _real_camp(tmp_path)
    _hub(env, launch="/acct/work")
    _group(env, "one", account="/acct/work", vaults=("v",))
    code, out, err = _drive(monkeypatch, capsys, env)
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert tuple(data) == DRIVE_GROUPS_OUTPUT_KEYS
    assert data["groups"] == [
        {"name": "one", "members": ["m-one"], "account": "/acct/work", "vaults": ["v"]}
    ]


def test_two_groups_on_the_account_binding_the_vault_are_both_returned(tmp_path, monkeypatch, capsys):
    env = _real_camp(tmp_path)
    _hub(env, launch="/acct/work")
    _group(env, "zeta", account="/acct/work", vaults=("v",))
    _group(env, "alpha", account="/acct/work", vaults=("v", "other"))
    code, out, _ = _drive(monkeypatch, capsys, env)
    assert code == 0
    assert _names(out) == ["alpha", "zeta"]


def test_a_group_binding_the_vault_on_another_account_is_excluded(tmp_path, monkeypatch, capsys):
    env = _real_camp(tmp_path)
    _hub(env, launch="/acct/work")
    _group(env, "mine", account="/acct/work", vaults=("v",))
    _group(env, "theirs", account="/acct/other", vaults=("v",))
    _group(env, "unaccounted", vaults=("v",))
    code, out, _ = _drive(monkeypatch, capsys, env)
    assert code == 0
    assert _names(out) == ["mine"]


def test_a_group_on_the_account_not_binding_the_vault_is_excluded(tmp_path, monkeypatch, capsys):
    env = _real_camp(tmp_path)
    _hub(env, launch="/acct/work")
    _group(env, "mine", account="/acct/work", vaults=("v",))
    _group(env, "elsewhere", account="/acct/work", vaults=("w",))
    code, out, _ = _drive(monkeypatch, capsys, env)
    assert code == 0
    assert _names(out) == ["mine"]


def test_no_matching_group_prints_an_empty_list(tmp_path, monkeypatch, capsys):
    env = _real_camp(tmp_path)
    _hub(env, launch="/acct/work")
    _group(env, "theirs", account="/acct/other", vaults=("v",))
    code, out, err = _drive(monkeypatch, capsys, env)
    assert (code, json.loads(out), err) == (0, {"groups": []}, "")


def test_a_default_launch_account_matches_only_groups_with_no_launch_account(tmp_path, monkeypatch, capsys):
    env = _real_camp(tmp_path)
    _hub(env, launch="default")
    _group(env, "bare", vaults=("v",))
    _group(env, "named", account="/acct/work", vaults=("v",))
    code, out, _ = _drive(monkeypatch, capsys, env)
    assert code == 0
    assert _names(out) == ["bare"]


def test_a_named_launch_account_does_not_match_groups_with_no_launch_account(tmp_path, monkeypatch, capsys):
    env = _real_camp(tmp_path)
    _hub(env, launch="/acct/work")
    _group(env, "bare", vaults=("v",))
    code, out, _ = _drive(monkeypatch, capsys, env)
    assert (code, _names(out)) == (0, [])


def test_a_home_relative_launch_account_matches_its_expanded_form(tmp_path, monkeypatch, capsys):
    env = _real_camp(tmp_path)
    _hub(env, launch="~/.claude-work")
    _group(env, "tilde", account="~/.claude-work", vaults=("v",))
    _group(env, "absolute", account=f"{env['HOME']}/.claude-work", vaults=("v",))
    _group(env, "other", account="~/.claude-play", vaults=("v",))
    code, out, _ = _drive(monkeypatch, capsys, env)
    assert code == 0
    assert _names(out) == ["absolute", "tilde"]


def test_an_account_with_no_hub_configuration_is_refused(tmp_path, monkeypatch, capsys):
    env = _real_camp(tmp_path)
    _group(env, "one", vaults=("v",))
    code, out, err = _drive(monkeypatch, capsys, env, account="nohub")
    assert (code, out) == (1, "")
    assert err.startswith("trailhead: no hub is configured for account 'nohub'")


def test_a_camp_failure_is_refused_with_its_reason(tmp_path, monkeypatch, capsys):
    env = _fence(tmp_path)
    env.update(_shim(tmp_path, 'echo "camp: exploded" >&2; exit 3'))
    _hub(env)
    code, out, err = _drive(monkeypatch, capsys, env)
    assert (code, out) == (1, "")
    assert err == "trailhead: camp groups --json failed (exit 3): camp: exploded\n"


def test_an_unparseable_camp_answer_is_refused(tmp_path, monkeypatch, capsys):
    env = _fence(tmp_path)
    env.update(_shim(tmp_path, "echo 'not json'"))
    _hub(env)
    code, out, err = _drive(monkeypatch, capsys, env)
    assert (code, out) == (1, "")
    assert err == "trailhead: camp groups --json answered something unparseable\n"


def test_a_camp_answer_of_the_wrong_shape_is_refused(tmp_path, monkeypatch, capsys):
    env = _fence(tmp_path)
    env.update(_shim(tmp_path, 'echo \'[{"name": "x"}]\''))
    _hub(env)
    code, out, err = _drive(monkeypatch, capsys, env)
    assert (code, out) == (1, "")
    assert err == "trailhead: camp groups --json answered something unparseable\n"


def test_a_missing_camp_is_refused(tmp_path, monkeypatch, capsys):
    env = _fence(tmp_path)
    env["PATH"] = str(tmp_path / "empty-bin")
    _hub(env)
    code, out, err = _drive(monkeypatch, capsys, env)
    assert (code, out) == (1, "")
    assert err == "trailhead: camp is not on PATH; cannot list groups\n"


@pytest.mark.parametrize("vault", ["Has Space", "UPPER", "a/b", "-lead", "", "semi;colon"])
def test_a_vault_name_failing_the_vault_name_shape_is_refused(tmp_path, monkeypatch, capsys, vault):
    env = _real_camp(tmp_path)
    _hub(env)
    code, out, err = _drive(monkeypatch, capsys, env, vault=vault)
    assert (code, out) == (1, "")
    assert err.startswith(f"trailhead: vault name {vault!r} must match ")


def test_drive_groups_without_json_is_refused(tmp_path, monkeypatch, capsys):
    env = _real_camp(tmp_path)
    _hub(env)
    code = _run_cli(monkeypatch, env, ["drive-groups", "--account", "a", "--vault", "v"])
    assert (code, capsys.readouterr().err) == (1, "trailhead: drive-groups prints JSON; pass --json\n")
