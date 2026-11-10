"""`camp remove|stop|status … --host <name>` through the real `main()`.

Each verb resolves its group and checks its arguments on this machine, then
hands the process to the far side's own camp over plain `ssh` — no requested
terminal, so the far side's stdout and stderr stay separate streams (the
shellenv wrapper captures `camp rm`'s stdout, and `status --json` is read by
scripts) and its exit status is the one the operator sees. Hosts are declared
in a tmp `hosts.toml`, the handoff seam is recorded, the listing transport
raises, and no real host is contacted.
"""
from __future__ import annotations

import importlib
import os
import shlex
import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        importlib.import_module("camp.cli.dispatch").main()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cfg = tmp_path / "config"
        self.state = tmp_path / "state"
        (self.cfg / "groups").mkdir(parents=True)
        monkeypatch.setenv("CAMP_CONFIG_DIR", str(self.cfg))
        monkeypatch.setenv("CAMP_STATE_DIR", str(self.state))
        monkeypatch.delenv("CAMP_DRY_RUN", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        self.handoffs: list[list[str]] = []
        monkeypatch.setattr(
            importlib.import_module("camp.host.handoff"),
            "handoff",
            lambda argv: self.handoffs.append(list(argv)),
        )

        def _no_listing(*a, **kw):
            raise AssertionError("the listing transport must not be called")

        monkeypatch.setattr(
            importlib.import_module("camp.host.transport"), "run_camp", _no_listing
        )
        monkeypatch.chdir(tmp_path)

    def hosts(self, body: str) -> None:
        (self.cfg / "hosts.toml").write_text(body, encoding="utf-8")

    def group(self, name: str) -> None:
        (self.cfg / "groups" / f"{name}.toml").write_text(
            f'[group]\nname = "{name}"\n\n'
            '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n',
            encoding="utf-8",
        )

    def cwd_in(self, group: str) -> None:
        member = self.state / group / "worktrees" / "tree"
        member.mkdir(parents=True, exist_ok=True)
        os.chdir(member)

    def remote(self) -> list[str]:
        """The far side's camp argv, split back out of the one handoff."""
        assert len(self.handoffs) == 1, self.handoffs
        return shlex.split(self.handoffs[0][-1])


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    e = Env(tmp_path, monkeypatch)
    e.hosts("[hosts.andromeda]\n")
    e.group("g")
    e.group("g2")
    return e


def _refused(env: Env, capsys, code: int, needle: str, verb: str) -> None:
    err = capsys.readouterr().err
    assert code == 1, err
    assert needle in err, err
    assert f"camp {verb}" in err, err
    assert env.handoffs == []


# The ssh form ----------------------------------------------------------


@pytest.mark.parametrize(
    "argv, far",
    [
        (["rm", "ws1"], "remove ws1 --group g"),
        (["remove", "ws1"], "remove ws1 --group g"),
        (["stop", "ws1"], "stop ws1 --group g"),
        (["status"], "status --group g"),
    ],
    ids=["rm", "remove", "stop", "status"],
)
def test_handoff_is_plain_ssh_to_the_declared_host_running_the_far_verb(
    env: Env, monkeypatch, argv, far
) -> None:
    env.hosts(
        'connect_timeout = 9\n[hosts.andromeda]\nssh = "me@andromeda"\n'
        'camp_bin = "/opt/camp/bin/camp"\n'
    )
    code = _run([*argv, "--host", "andromeda", "--group", "g"], monkeypatch)
    assert code == 0
    assert env.handoffs == [
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=9",
            "me@andromeda",
            f"/opt/camp/bin/camp {far}",
        ]
    ]


@pytest.mark.parametrize("verb", ["rm", "stop"])
def test_a_second_host_changes_destination_and_camp_location(
    env: Env, monkeypatch, verb
) -> None:
    env.hosts(
        '[hosts.andromeda]\nssh = "a-dest"\ncamp_bin = "/a/camp"\n'
        '[hosts.lookout]\nssh = "l-dest"\ncamp_bin = "/l/camp"\n'
    )
    assert _run([verb, "ws1", "--host", "lookout", "--group", "g"], monkeypatch) == 0
    assert env.handoffs[0][-2] == "l-dest"
    assert env.handoffs[0][-1].startswith("/l/camp ")


@pytest.mark.parametrize(
    "argv", [["rm", "ws1"], ["stop", "ws1"], ["status", "--name", "ws1"]]
)
def test_the_group_of_the_cwd_is_forwarded_when_none_is_named(
    env: Env, monkeypatch, argv
) -> None:
    env.cwd_in("g2")
    assert _run([*argv, "--host", "andromeda"], monkeypatch) == 0
    far = env.remote()
    assert far[far.index("--group") + 1] == "g2"


@pytest.mark.parametrize("verb", ["rm", "stop", "status"])
def test_no_group_anywhere_refuses_with_the_needs_group_message(
    env: Env, monkeypatch, capsys, verb
) -> None:
    from camp.workspace.verb_taxonomy import needs_group_message

    argv = [verb] if verb == "status" else [verb, "ws1"]
    code = _run([*argv, "--host", "andromeda"], monkeypatch)
    err = capsys.readouterr().err
    assert code == 1
    canonical = "remove" if verb == "rm" else verb
    assert err.strip() == needs_group_message(canonical)
    assert env.handoffs == []


@pytest.mark.parametrize("verb", ["rm", "stop", "status"])
def test_an_unknown_explicit_group_refuses(env: Env, monkeypatch, capsys, verb) -> None:
    argv = [verb] if verb == "status" else [verb, "ws1"]
    code = _run([*argv, "--host", "andromeda", "--group", "zzz"], monkeypatch)
    canonical = "remove" if verb == "rm" else verb
    _refused(env, capsys, code, "group 'zzz' is not configured on this machine", canonical)


@pytest.mark.parametrize("verb", ["rm", "stop", "status"])
def test_an_undeclared_host_refuses_naming_the_declared_ones(
    env: Env, monkeypatch, capsys, verb
) -> None:
    argv = [verb] if verb == "status" else [verb, "ws1"]
    code = _run([*argv, "--host", "nope", "--group", "g"], monkeypatch)
    canonical = "remove" if verb == "rm" else verb
    _refused(env, capsys, code, "declared hosts are: andromeda", canonical)


@pytest.mark.parametrize("verb", ["rm", "stop"])
def test_nothing_reaches_local_stdout(env: Env, monkeypatch, capsys, verb) -> None:
    """The shellenv wrapper `cd`s to whatever `camp rm` prints on stdout, so the
    local half of a cross-host verb prints nothing there."""
    assert _run([verb, "ws1", "--host", "andromeda", "--group", "g"], monkeypatch) == 0
    assert capsys.readouterr().out == ""


# remove ----------------------------------------------------------------


def test_remove_takes_its_slug_from_name_too(env: Env, monkeypatch) -> None:
    code = _run(
        ["rm", "--name", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch
    )
    assert code == 0
    assert env.remote() == ["camp", "remove", "ws1", "--group", "g"]


@pytest.mark.parametrize(
    "slug_argv, needle",
    [
        ([], "exactly one workspace slug"),
        (["ws1", "--name", "ws2"], "exactly one workspace slug"),
        (["-x"], "-x"),
        (["--name=-x"], "'-x' is not a valid workspace slug"),
        (["   "], "is not a valid workspace slug"),
    ],
    ids=["none", "both", "dash", "dash-name", "blank"],
)
def test_remove_refuses_anything_but_one_valid_slug(
    env: Env, monkeypatch, capsys, slug_argv, needle
) -> None:
    code = _run(["rm", *slug_argv, "--host", "andromeda", "--group", "g"], monkeypatch)
    _refused(env, capsys, code, needle, "remove")


def test_remove_forwards_force(env: Env, monkeypatch) -> None:
    _run(["rm", "ws1", "--host", "andromeda", "--group", "g", "--force"], monkeypatch)
    assert env.remote() == ["camp", "remove", "ws1", "--group", "g", "--force"]


@pytest.mark.parametrize(
    "extra, env_value",
    [(["--dry-run"], None), ([], "1"), (["--dry-run"], "1")],
    ids=["flag", "env", "both"],
)
def test_remove_forwards_dry_run_once_from_flag_or_environment(
    env: Env, monkeypatch, extra, env_value
) -> None:
    if env_value is not None:
        monkeypatch.setenv("CAMP_DRY_RUN", env_value)
    _run(["rm", "ws1", "--host", "andromeda", "--group", "g", *extra], monkeypatch)
    assert env.remote() == ["camp", "remove", "ws1", "--group", "g", "--dry-run"]


def test_remove_refuses_a_flag_local_remove_does_not_accept(
    env: Env, monkeypatch, capsys
) -> None:
    code = _run(["rm", "ws1", "--host", "andromeda", "--group", "g", "--bogus"], monkeypatch)
    err = capsys.readouterr().err
    assert code != 0
    assert "--bogus" in err, err
    assert env.handoffs == []


# stop ------------------------------------------------------------------


def test_stop_forwards_json(env: Env, monkeypatch) -> None:
    _run(["stop", "ws1", "--host", "andromeda", "--group", "g", "--json"], monkeypatch)
    assert env.remote() == ["camp", "stop", "ws1", "--group", "g", "--json"]


@pytest.mark.parametrize(
    "slug_argv, needle",
    [([], "no slug given"), (["-x"], "-x"), (["   "], "is not a valid workspace slug")],
    ids=["none", "dash", "blank"],
)
def test_stop_refuses_anything_but_one_valid_slug(
    env: Env, monkeypatch, capsys, slug_argv, needle
) -> None:
    code = _run(["stop", *slug_argv, "--host", "andromeda", "--group", "g"], monkeypatch)
    _refused(env, capsys, code, needle, "stop")


def test_stop_refuses_dry_run_as_local_stop_does(env: Env, monkeypatch, capsys) -> None:
    code = _run(
        ["stop", "ws1", "--host", "andromeda", "--group", "g", "--dry-run"], monkeypatch
    )
    err = capsys.readouterr().err
    assert code != 0
    assert "--dry-run" in err, err
    assert env.handoffs == []


# status ----------------------------------------------------------------


@pytest.mark.parametrize(
    "extra, far_tail",
    [
        (["--name", "ws1"], ["--name", "ws1"]),
        (["--name", "ws1", "--json"], ["--name", "ws1", "--json"]),
        (["--stale", "--days", "7"], ["--stale", "--days", "7"]),
        (["--json"], ["--json"]),
    ],
    ids=["one-workspace", "one-workspace-json", "stale", "fleet-json"],
)
def test_status_forwards_the_view_it_was_asked_for(
    env: Env, monkeypatch, extra, far_tail
) -> None:
    assert _run(["status", "--host", "andromeda", "--group", "g", *extra], monkeypatch) == 0
    assert env.remote() == ["camp", "status", "--group", "g", *far_tail]


def test_status_refuses_name_with_stale_before_contacting_anything(
    env: Env, monkeypatch, capsys
) -> None:
    code = _run(
        ["status", "--host", "andromeda", "--group", "g", "--name", "ws1", "--stale"],
        monkeypatch,
    )
    _refused(env, capsys, code, "mutually exclusive", "status")


def test_status_refuses_a_dash_leading_name(env: Env, monkeypatch, capsys) -> None:
    code = _run(
        ["status", "--host", "andromeda", "--group", "g", "--name=-x"], monkeypatch
    )
    _refused(env, capsys, code, "'-x' is not a valid workspace slug", "status")
