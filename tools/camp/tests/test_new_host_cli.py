"""`camp new <slug> --host <name> [--group <g>]` through the real `main()`.

The router gates `--host`; the cross-host creation handler refuses everything
answerable locally and hands the terminal to the far side's own `camp new`
over `ssh -t`. Hosts are declared in a tmp `hosts.toml`, the handoff seam is
recorded, the listing transport raises, and no real host is contacted.
"""
from __future__ import annotations

import importlib
import shlex
import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _dispatch():
    return importlib.import_module("camp.cli.dispatch")


def _handoff_module():
    return importlib.import_module("camp.host.handoff")


def _transport_module():
    return importlib.import_module("camp.host.transport")


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        _dispatch().main()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.cfg = tmp_path / "config"
        self.state = tmp_path / "state"
        (self.cfg / "groups").mkdir(parents=True)
        monkeypatch.setenv("CAMP_CONFIG_DIR", str(self.cfg))
        monkeypatch.setenv("CAMP_STATE_DIR", str(self.state))
        monkeypatch.delenv("CAMP_DRY_RUN", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        self.handoffs: list[list[str]] = []
        monkeypatch.setattr(
            _handoff_module(), "handoff", lambda argv: self.handoffs.append(list(argv))
        )

        def _no_listing(*a, **kw):
            raise AssertionError("the listing transport must not be called")

        monkeypatch.setattr(_transport_module(), "run_camp", _no_listing)
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
        import os

        os.chdir(member)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    e = Env(tmp_path, monkeypatch)
    e.hosts("[hosts.andromeda]\n")
    e.group("g")
    e.group("g2")
    return e


def _refused(
    env: Env, capsys, code: int, message: str | None = None, prefix: str = "camp new: "
) -> str:
    err = capsys.readouterr().err
    assert code == 1, err
    assert err.startswith(prefix), err
    if message is not None:
        assert message in err, err
    assert env.handoffs == []
    return err


# AC1 -------------------------------------------------------------------


def test_handoff_argv_is_the_pinned_ssh_form_for_the_declared_host(
    env: Env, monkeypatch
) -> None:
    env.hosts(
        'connect_timeout = 9\n[hosts.andromeda]\nssh = "me@andromeda"\n'
        'camp_bin = "/opt/camp/bin/camp"\n'
    )
    code = _run(["new", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch)
    assert code == 0
    assert env.handoffs == [
        [
            "ssh", "-t",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=9",
            "me@andromeda",
            "/opt/camp/bin/camp new ws1 --group g",
        ]
    ]


def test_a_second_host_changes_destination_and_camp_location(
    env: Env, monkeypatch
) -> None:
    env.hosts(
        '[hosts.andromeda]\nssh = "a-dest"\ncamp_bin = "/a/camp"\n'
        '[hosts.lookout]\nssh = "l-dest"\ncamp_bin = "/l/camp"\n'
    )
    assert _run(["new", "ws1", "--host", "lookout", "--group", "g"], monkeypatch) == 0
    argv = env.handoffs[0]
    assert argv[-2:] == ["l-dest", "/l/camp new ws1 --group g"]


# AC2 -------------------------------------------------------------------


@pytest.mark.parametrize("spelling", [["--group", "g2"], ["--group=g2"]])
def test_explicit_group_forwards_in_both_spellings(
    env: Env, monkeypatch, spelling
) -> None:
    assert _run(["new", "ws1", "--host", "andromeda", *spelling], monkeypatch) == 0
    assert env.handoffs[0][-1].endswith("new ws1 --group g2")


def test_no_group_forwards_the_group_of_the_cwd(env: Env, monkeypatch) -> None:
    env.cwd_in("g")
    assert _run(["new", "ws1", "--host", "andromeda"], monkeypatch) == 0
    assert env.handoffs[0][-1].endswith("new ws1 --group g")


# AC17 ------------------------------------------------------------------


def _remote_tail(env: Env) -> str:
    assert len(env.handoffs) == 1
    return env.handoffs[0][-1]


def test_dry_run_unset_forwards_no_dry_run(env: Env, monkeypatch) -> None:
    _run(["new", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch)
    assert _remote_tail(env) == "camp new ws1 --group g"


def test_env_dry_run_appends_the_flag_after_slug_and_group(
    env: Env, monkeypatch
) -> None:
    monkeypatch.setenv("CAMP_DRY_RUN", "1")
    _run(["new", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch)
    assert _remote_tail(env) == "camp new ws1 --group g --dry-run"


def test_explicit_dry_run_flag_forwards_it_once(env: Env, monkeypatch) -> None:
    _run(["new", "ws1", "--host", "andromeda", "--group", "g", "--dry-run"], monkeypatch)
    assert _remote_tail(env) == "camp new ws1 --group g --dry-run"


def test_env_and_flag_together_still_forward_it_once(env: Env, monkeypatch) -> None:
    monkeypatch.setenv("CAMP_DRY_RUN", "1")
    _run(["new", "ws1", "--host", "andromeda", "--group", "g", "--dry-run"], monkeypatch)
    assert _remote_tail(env).count("--dry-run") == 1


def test_empty_env_dry_run_is_absent(env: Env, monkeypatch) -> None:
    monkeypatch.setenv("CAMP_DRY_RUN", "")
    _run(["new", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch)
    assert _remote_tail(env) == "camp new ws1 --group g"


def test_env_dry_run_zero_is_present_like_local_camp(env: Env, monkeypatch) -> None:
    monkeypatch.setenv("CAMP_DRY_RUN", "0")
    _run(["new", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch)
    assert _remote_tail(env) == "camp new ws1 --group g --dry-run"


# AC6 -------------------------------------------------------------------


def test_nested_multiplexer_notice_is_printed_and_handoff_still_happens(
    env: Env, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    code = _run(["new", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err
    assert code == 0
    assert "prefix" in err.lower()
    assert len(env.handoffs) == 1


def test_no_notice_outside_a_multiplexer(env: Env, monkeypatch, capsys) -> None:
    code = _run(["new", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch)
    assert code == 0
    assert capsys.readouterr().err == ""


# Refusals --------------------------------------------------------------


def test_undeclared_host_lists_the_declared_hosts(env: Env, monkeypatch, capsys) -> None:
    code = _run(["new", "ws1", "--host", "nope", "--group", "g"], monkeypatch)
    _refused(env, capsys, code, "declared hosts are: andromeda")


def test_unknown_explicit_group_refuses(env: Env, monkeypatch, capsys) -> None:
    code = _run(["new", "ws1", "--host", "andromeda", "--group", "zzz"], monkeypatch)
    _refused(env, capsys, code, "group 'zzz' is not configured on this machine")


def test_unclaimed_cwd_without_group_prints_the_needs_group_message(
    env: Env, monkeypatch, capsys
) -> None:
    from camp.workspace.verb_taxonomy import needs_group_message

    code = _run(["new", "ws1", "--host", "andromeda"], monkeypatch)
    err = _refused(env, capsys, code)
    assert err.strip() == needs_group_message("new")


def test_group_failing_confinement_refuses(env: Env, monkeypatch, capsys) -> None:
    from camp.group.resolve import GroupConfinementError, validate_group_name

    with pytest.raises(GroupConfinementError) as exc:
        validate_group_name("x;id")
    env.group("x;id")
    code = _run(["new", "ws1", "--host", "andromeda", "--group", "x;id"], monkeypatch)
    err = _refused(env, capsys, code, prefix="camp: ")
    assert err.strip() == str(exc.value)


def test_dash_leading_group_via_flag_refuses_as_flag_shaped(
    env: Env, monkeypatch, capsys
) -> None:
    env.group("-x")
    code = _run(["new", "ws1", "--host", "andromeda", "--group=-x"], monkeypatch)
    _refused(env, capsys, code, "camp new: group '-x' begins with '-'")


def test_dash_leading_group_from_cwd_refuses_as_flag_shaped(
    env: Env, monkeypatch, capsys
) -> None:
    env.group("-x")
    env.cwd_in("-x")
    code = _run(["new", "ws1", "--host", "andromeda"], monkeypatch)
    _refused(env, capsys, code, "camp new: group '-x' begins with '-'")


@pytest.mark.parametrize(
    "slug_argv, needle",
    [
        ([], "exactly one workspace slug"),
        ([""], "is not a valid workspace slug"),
        (["   "], "is not a valid workspace slug"),
    ],
    ids=["missing", "empty", "blank"],
)
def test_missing_empty_or_blank_slug_refuses(
    env: Env, monkeypatch, capsys, slug_argv, needle
) -> None:
    code = _run(
        ["new", *slug_argv, "--host", "andromeda", "--group", "g"], monkeypatch
    )
    _refused(env, capsys, code, needle)


def test_dash_leading_slug_refuses(env: Env, monkeypatch, capsys) -> None:
    code = _run(["new", "-x", "--host", "andromeda", "--group", "g"], monkeypatch)
    _refused(env, capsys, code, "-x")


def test_dash_leading_slug_after_double_dash_refuses(
    env: Env, monkeypatch, capsys
) -> None:
    code = _run(
        ["new", "--host", "andromeda", "--group", "g", "--", "-x"], monkeypatch
    )
    _refused(env, capsys, code, "'-x' is not a valid workspace slug")


def test_space_dash_slug_is_forwarded_raw_as_one_argument(
    env: Env, monkeypatch
) -> None:
    code = _run(["new", " -x", "--host", "andromeda", "--group", "g"], monkeypatch)
    assert code == 0
    assert shlex.split(_remote_tail(env)) == ["camp", "new", " -x", "--group", "g"]


def test_double_dash_before_host_never_reaches_handoff_or_creates_anything(
    env: Env, monkeypatch, capsys
) -> None:
    env.cwd_in("g")
    code = _run(["new", "--", "-x", "--host", "andromeda"], monkeypatch)
    assert code == 1, capsys.readouterr().err
    assert env.handoffs == []
    assert {str(p.relative_to(env.state)) for p in env.state.rglob("*")} == {
        "g",
        "g/worktrees",
        "g/worktrees/tree",
    }


@pytest.mark.parametrize(
    "flag", ["--no-attach", "--no-session", "--activate", "--json"]
)
def test_creation_flags_are_refused(env: Env, monkeypatch, capsys, flag) -> None:
    code = _run(
        ["new", "ws1", "--host", "andromeda", "--group", "g", flag], monkeypatch
    )
    _refused(env, capsys, code, flag)


def test_dash_leading_ssh_destination_is_refused_by_the_hosts_loader(
    env: Env, monkeypatch, capsys
) -> None:
    env.hosts('[hosts.andromeda]\nssh = "-oProxyCommand=evil"\n')
    code = _run(["new", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch)
    _refused(env, capsys, code, "must not begin with '-'")


# AC12 ------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["-a", "--all-hosts"])
def test_all_hosts_is_refused_and_hosts_file_never_read(
    env: Env, monkeypatch, capsys, flag
) -> None:
    def _no_read(*a, **kw):
        raise AssertionError("hosts file must not be read")

    monkeypatch.setattr(importlib.import_module("camp.host.config"), "load_hosts", _no_read)
    code = _run(["new", "ws1", flag], monkeypatch)
    _refused(env, capsys, code, "--all-hosts has no meaning here")


# AC13 ------------------------------------------------------------------


def test_list_with_host_and_group_still_refuses_without_the_transport(
    env: Env, monkeypatch, capsys
) -> None:
    code = _run(["list", "--host", "andromeda", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("camp list: ")
    assert "--host and --group" in err
    assert env.handoffs == []


def test_attach_and_new_with_host_and_group_both_reach_their_handoff(
    env: Env, monkeypatch
) -> None:
    assert _run(["attach", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch) == 0
    assert _run(["new", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch) == 0
    assert [a[-1].split()[1] for a in env.handoffs] == ["attach", "new"]


# help ------------------------------------------------------------------


def test_the_form_documented_in_camp_help_dispatches_to_the_handoff(
    env: Env, monkeypatch, capsys
) -> None:
    assert _run(["help"], monkeypatch) == 0
    out = capsys.readouterr().out
    line = next(
        ln for ln in out.splitlines() if ln.strip().startswith("camp new <slug> --host")
    )
    tokens = line.split()
    argv: list[str] = []
    for tok in tokens[1:]:
        if tok.startswith("["):
            break
        argv.append(
            {"<slug>": "ws1", "<name>": "andromeda"}.get(tok, tok)
        )
    argv += ["--group", "g"]
    assert _run(argv, monkeypatch) == 0
    assert env.handoffs[0][-1] == "camp new ws1 --group g"


# far side --------------------------------------------------------------


def test_far_side_main_accepts_exactly_what_is_forwarded(
    env: Env, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("CAMP_DRY_RUN", "1")
    _run(["new", "ws1", "--host", "andromeda", "--group", "g"], monkeypatch)
    forwarded = shlex.split(env.handoffs[0][-1])
    monkeypatch.delenv("CAMP_DRY_RUN")
    assert forwarded[1:] == ["new", "ws1", "--group", "g", "--dry-run"]

    code = _run(forwarded[1:], monkeypatch)
    err = capsys.readouterr().err
    assert code == 0, err
    assert "[dry-run] would seed" in err
    assert not (env.state / "g" / "ws1").exists()


@pytest.mark.parametrize(
    "slug", ["\\'\\' ; echo INJECTED #", "a\\b"], ids=["fish-exploit", "backslash"]
)
def test_slug_the_far_rule_would_refuse_never_reaches_the_handoff(
    env: Env, monkeypatch, capsys, slug
) -> None:
    code = _run(["new", slug, "--host", "andromeda", "--group", "g"], monkeypatch)
    _refused(env, capsys, code, repr(slug))


def test_ordinary_slug_still_reaches_the_handoff(env: Env, monkeypatch) -> None:
    code = _run(["new", "good-slug", "--host", "andromeda", "--group", "g"], monkeypatch)
    assert code == 0
    assert shlex.split(_remote_tail(env)) == ["camp", "new", "good-slug", "--group", "g"]


def test_attach_ref_the_far_rule_would_refuse_never_reaches_the_handoff(
    env: Env, monkeypatch, capsys
) -> None:
    code = _run(["attach", "a;b", "--host", "andromeda", "--group", "g"], monkeypatch)
    _refused(env, capsys, code, "'a;b'", prefix="camp attach: ")
