"""Test contract: `camp attach <slug>` resolves a workspace slug in one
group and nothing else.

Every test drives the real entry point (`camp.cli.dispatch.main`), never an
internal function directly.

Test contract (from
`task/camp-attach-resolves-a-slug-in-one-group-and-nothing-else`):

- A known slug reaches the door handoff; door behavior itself is covered in
  depth by `test_attach_door_dispatch.py` — this file's job is proving group
  resolution and the CLI-level refusals around it.
- A mistyped slug, an 8-hex prefix, and a full UUID each refuse with the
  exact unknown-slug line, exit 1, and touch no tmux seam.
- Group resolution varies: `--group` given, cwd inside a member, cwd in
  unclaimed space, and `--group <unknown>`. No form falls back to a session
  picker.
- `-a` alone, `-a <ref>`, `--resolve --json <ref>`, `--list --json`, and
  `-a --json` each refuse with the retired-flag line, exit 1, and a stubbed
  transport sender records zero calls.
- `--host <name> <slug>` still forwards its argv verbatim through
  `host/handoff.py`.
- No invocation exits 2.

No real tmux, ssh, or exec is ever touched: the tmux seam, the SSH
transport, and the exec handoff seam are all injected.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_TESTS_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_attach_door_dispatch import _DoorTmux, _FakeTTY  # noqa: E402


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _cli_session_module():
    return importlib.import_module("camp.cli.session")


def _lifecycle_module():
    return importlib.import_module("camp.provision.lifecycle")


def _host_transport_module():
    return importlib.import_module("camp.host.transport")


def _host_handoff_module():
    return importlib.import_module("camp.host.handoff")


def _group(name: str) -> dict:
    return {"group": {"name": name}}


class _UntouchedTmux:
    """Fails loudly if the attach flow reaches ANY tmux call at all — used
    for refusal paths that must never touch the door."""

    def __getattr__(self, name):
        raise AssertionError(
            f"tmux.{name} must not be reached — this attach must refuse "
            "before ever touching tmux"
        )


def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        dispatch.main()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _hosts_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *hosts: str,
    connect_timeout_line: str = "",
) -> None:
    """An isolated config dir declaring *hosts*, optionally preceded by the
    caller's own top-level ``connect_timeout`` line."""
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    body = connect_timeout_line + "".join(f"[hosts.{name}]\n" for name in hosts)
    (cfg / "hosts.toml").write_text(body, encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))


def _wire_group_with_workspace(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
    group_name: str = "g",
    slug: str = "camp-cli",
) -> Path:
    """A resolvable group named *group_name* carrying exactly one workspace
    (*slug*), backed by a real directory under `tmp_path/state` — the state
    layout `_resolve_group_for_attach`'s cwd branch reads
    (`<state>/<group>/worktrees/<anything>`)."""
    ws = tmp_path / "state" / group_name / "worktrees" / slug
    ws.mkdir(parents=True, exist_ok=True)

    cli_session = _cli_session_module()
    lifecycle = _lifecycle_module()
    monkeypatch.setattr(cli_session, "_parsable_groups", lambda: [_group(group_name)])

    def fake_cmd_ls_group(group, *, env=None, tmux=None, **kw):
        return lifecycle.GroupListing(
            entries=[
                {
                    "slug": slug,
                    "workspace_path": str(ws),
                    "state": None,
                    "window_count": None,
                }
            ],
            unmanaged=[],
            unmanaged_count=0,
            notice=None,
        )

    monkeypatch.setattr(lifecycle, "cmd_ls_group", fake_cmd_ls_group)
    return ws


def _derived_name(group_name: str, slug: str) -> str:
    from camp.launch.naming import workspace_session_name

    return workspace_session_name(group_name, slug)


# ---------------------------------------------------------------------------
# A known slug reaches the door; group resolution varies
# ---------------------------------------------------------------------------


def test_group_flag_resolves_and_a_known_slug_reaches_the_door(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.delenv("TMUX", raising=False)
    ws = _wire_group_with_workspace(monkeypatch, tmp_path=tmp_path)
    tmux = _DoorTmux(present=False)
    stop_module = importlib.import_module("camp.launch.stop")
    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: tmux)
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr(sys, "stdout", _FakeTTY())

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)

    assert code == 0
    derived = _derived_name("g", "camp-cli")
    assert seen == [["tmux", "attach-session", "-t", f"={derived}"]]
    assert len(tmux.new_session_calls) == 1
    assert ws.exists()


def test_cwd_inside_a_member_resolves_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """No `--group`: cwd under `<state>/g/worktrees/...` resolves group `g`
    on its own — proven here by the unknown-slug refusal naming that
    resolved group, without ever touching tmux."""
    _isolated_env(tmp_path, monkeypatch)
    _wire_group_with_workspace(monkeypatch, tmp_path=tmp_path, group_name="g")
    stop_module = importlib.import_module("camp.launch.stop")
    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: _UntouchedTmux())
    cwd = tmp_path / "state" / "g" / "worktrees" / "some-other-tree"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)

    code = _run(["attach", "not-a-real-slug"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 1
    assert (
        err == "camp attach: no workspace named not-a-real-slug in group g — "
        "'camp list' shows its workspaces\n"
    ), err


def test_no_resolvable_group_prints_the_needs_group_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    _wire_group_with_workspace(monkeypatch, tmp_path=tmp_path, group_name="g")
    unclaimed = tmp_path / "unclaimed"
    unclaimed.mkdir()
    monkeypatch.chdir(unclaimed)

    code = _run(["attach", "camp-cli"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 1
    assert (
        err == "camp attach: no group resolved from cwd — pass --group <name> "
        "or run from inside a group member directory\n"
    ), err


def test_no_resolvable_group_prints_the_needs_group_line_even_with_no_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """No form of `camp attach` degrades to the machine-wide session picker
    when no group resolves — not even the bare, no-slug form."""
    _isolated_env(tmp_path, monkeypatch)
    unclaimed = tmp_path / "unclaimed"
    unclaimed.mkdir()
    monkeypatch.chdir(unclaimed)

    code = _run(["attach"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 1
    assert "no group resolved from cwd" in err, err
    assert "Running sessions" not in err


def test_unknown_group_flag_prints_the_needs_group_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`--group <unknown>` resolves to no group, so `_resolve_group_for_attach`
    degrades it the same way it degrades a cwd that resolves to nothing —
    the exact refusal `camp stop --group <unknown>` prints too."""
    _isolated_env(tmp_path, monkeypatch)
    _wire_group_with_workspace(monkeypatch, tmp_path=tmp_path, group_name="g")

    code = _run(["attach", "camp-cli", "--group", "nosuchgroup"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 1
    assert (
        err == "camp attach: no group resolved from cwd — pass --group <name> "
        "or run from inside a group member directory\n"
    ), err


def test_bare_attach_with_a_resolved_group_offers_the_existing_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Bare `camp attach` inside a resolved group still reaches the door's
    own numbered picker over workspaces (unchanged) — distinguished from the
    needs-group refusal by its own wording."""
    _isolated_env(tmp_path, monkeypatch)
    _wire_group_with_workspace(monkeypatch, tmp_path=tmp_path, group_name="g")
    stop_module = importlib.import_module("camp.launch.stop")
    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: _UntouchedTmux())

    code = _run(["attach", "--group", "g"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 1
    assert err == "camp attach: no workspace named — pass a slug\n", err


# ---------------------------------------------------------------------------
# Unknown-slug variants — a mistyped slug, an 8-hex prefix, a full UUID
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_slug",
    ["camp-clii", "8f2c9a3d", "f47ac10b-58cc-4372-a567-0e02b2c3d479"],
    ids=["mistyped", "eight-hex-prefix", "full-uuid"],
)
def test_unknown_slug_variants_refuse_and_touch_no_tmux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, bad_slug: str
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    _wire_group_with_workspace(monkeypatch, tmp_path=tmp_path, group_name="g")
    stop_module = importlib.import_module("camp.launch.stop")
    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: _UntouchedTmux())

    code = _run(["attach", bad_slug, "--group", "g"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 1
    assert (
        err == f"camp attach: no workspace named {bad_slug} in group g — "
        "'camp list' shows its workspaces\n"
    ), err


# ---------------------------------------------------------------------------
# `-a`, `--resolve`, `--list` are retired
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["attach", "-a"],
        ["attach", "-a", "camp-cli"],
        ["attach", "camp-cli", "--resolve", "--json"],
        ["attach", "--list", "--json"],
        ["attach", "-a", "--json"],
    ],
    ids=[
        "dash_a_alone",
        "dash_a_with_ref",
        "resolve_json_with_ref",
        "list_json",
        "dash_a_json",
    ],
)
def test_retired_forms_refuse_and_contact_no_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, argv: list[str]
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    transport = _host_transport_module()
    calls: list = []
    monkeypatch.setattr(transport, "run_camp", lambda *a, **k: calls.append((a, k)))

    code = _run(argv, monkeypatch)

    err = capsys.readouterr().err
    assert code == 1
    assert (
        err == "camp attach: -a is retired — find the workspace with "
        "'camp list -ag', then 'camp attach <slug> --host <name>'\n"
    ), err
    assert calls == []


# ---------------------------------------------------------------------------
# `--host <name> <slug>` — the untouched pass-through
# ---------------------------------------------------------------------------


def test_host_form_reaches_the_untouched_pass_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    monkeypatch.setattr(
        _cli_session_module(), "_parsable_groups", lambda: [_group("g")]
    )
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", "myslug", "--host", "andromeda", "--group", "g"], monkeypatch)

    assert code == 0
    assert len(seen) == 1
    argv = seen[0]
    assert argv[0] == "ssh"
    assert "attach myslug --group g" in argv[-1]


def test_host_form_threads_the_declared_connect_timeout_to_the_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interactive `--host` handoff is a host-axis verb like every
    other — its ssh invocation must carry the operator's declared
    connect_timeout, not the transport's own module constant, exactly as
    the non-interactive relay verbs already do."""
    _hosts_env(tmp_path, monkeypatch, "andromeda", connect_timeout_line="connect_timeout = 11\n")
    monkeypatch.setattr(
        _cli_session_module(), "_parsable_groups", lambda: [_group("g")]
    )
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", "myslug", "--host", "andromeda", "--group", "g"], monkeypatch)

    assert code == 0
    assert len(seen) == 1
    assert "ConnectTimeout=11" in " ".join(seen[0])


def test_host_form_uses_a_different_declared_connect_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second of the two points that change the answer — proving the
    value threads through rather than any nonzero value passing."""
    _hosts_env(tmp_path, monkeypatch, "andromeda", connect_timeout_line="connect_timeout = 4\n")
    monkeypatch.setattr(
        _cli_session_module(), "_parsable_groups", lambda: [_group("g")]
    )
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", "myslug", "--host", "andromeda", "--group", "g"], monkeypatch)

    assert code == 0
    assert len(seen) == 1
    assert "ConnectTimeout=4" in " ".join(seen[0])


def test_remote_attach_from_inside_a_local_multiplexer_warns_via_the_real_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The key-prefix conflict warning is decided from the environment the
    operator is typing in, so it appears for a REMOTE attach launched from
    inside a LOCAL multiplexer."""
    from camp.attach import prefix_warning

    _hosts_env(tmp_path, monkeypatch, "andromeda")
    monkeypatch.setattr(
        _cli_session_module(), "_parsable_groups", lambda: [_group("g")]
    )
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    handoff = _host_handoff_module()
    monkeypatch.setattr(handoff, "handoff", lambda argv: None)

    code = _run(["attach", "myslug", "--host", "andromeda", "--group", "g"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 0
    assert prefix_warning.MESSAGE in err


# ---------------------------------------------------------------------------
# `--host <name> <slug>` — the group axis: `--group` given, absent with the
# cwd inside a group, and neither.
# ---------------------------------------------------------------------------


def test_host_form_with_explicit_group_forwards_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    monkeypatch.setattr(
        _cli_session_module(), "_parsable_groups", lambda: [_group("g")]
    )
    unclaimed = tmp_path / "unclaimed"
    unclaimed.mkdir()
    monkeypatch.chdir(unclaimed)
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", "myslug", "--host", "andromeda", "--group", "g"], monkeypatch)

    assert code == 0
    assert len(seen) == 1
    assert "attach myslug --group g" in seen[0][-1]


def test_host_form_with_no_group_flag_resolves_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--group`: cwd under `<state>/g/worktrees/...` resolves group `g`
    on its own, exactly as it does for a local attach."""
    _isolated_env(tmp_path, monkeypatch)
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    _wire_group_with_workspace(monkeypatch, tmp_path=tmp_path, group_name="g")
    cwd = tmp_path / "state" / "g" / "worktrees" / "some-other-tree"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", "myslug", "--host", "andromeda"], monkeypatch)

    assert code == 0
    assert len(seen) == 1
    assert "attach myslug --group g" in seen[0][-1]


def test_host_form_with_no_resolvable_group_refuses_locally_and_contacts_no_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Neither `--group` nor a cwd that resolves: refused with the same
    needs-group line a local attach prints, and no machine is contacted —
    `handoff.handoff` is never reached, so `hosts.toml` need not even
    declare the named host for this refusal to fire first."""
    _isolated_env(tmp_path, monkeypatch)
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    monkeypatch.setattr(
        _cli_session_module(), "_parsable_groups", lambda: [_group("g")]
    )
    unclaimed = tmp_path / "unclaimed"
    unclaimed.mkdir()
    monkeypatch.chdir(unclaimed)
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", "myslug", "--host", "andromeda"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 1
    assert (
        err == "camp attach: no group resolved from cwd — pass --group <name> "
        "or run from inside a group member directory\n"
    ), err
    assert seen == []


def test_host_form_unknown_group_flag_names_the_group_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """An explicit `--group` naming a group this machine does not know about
    is a different failure than no group resolving from cwd at all — it
    must say the named group is not configured here, not the generic
    cwd-resolution line the no-`--group` case prints."""
    _isolated_env(tmp_path, monkeypatch)
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    monkeypatch.setattr(
        _cli_session_module(), "_parsable_groups", lambda: [_group("g")]
    )
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(
        ["attach", "myslug", "--host", "andromeda", "--group", "nosuchgroup"],
        monkeypatch,
    )

    err = capsys.readouterr().err
    assert code == 1
    assert "nosuchgroup" in err
    assert "not configured on this machine" in err
    assert "no group resolved from cwd" not in err
    assert seen == []


def test_host_form_group_flag_with_shell_metacharacter_refuses_before_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A `--group` value that fails `validate_group_name` (e.g. a shell
    metacharacter) must refuse locally — never reach the ssh handoff with an
    unvalidated name embedded in the forwarded argv."""
    _isolated_env(tmp_path, monkeypatch)
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    monkeypatch.setattr(
        _cli_session_module(), "_parsable_groups", lambda: [_group("x;id")]
    )
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(
        ["attach", "myslug", "--host", "andromeda", "--group", "x;id"],
        monkeypatch,
    )

    err = capsys.readouterr().err
    assert code == 1
    assert "x;id" in err
    assert seen == []


# ---------------------------------------------------------------------------
# `--host`'s argument-shape refusals name a "workspace slug", matching the
# rest of attach — not the retired "session reference" wording.
# ---------------------------------------------------------------------------


def test_host_form_wrong_ref_count_names_a_workspace_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    monkeypatch.setattr(
        _cli_session_module(), "_parsable_groups", lambda: [_group("g")]
    )

    code = _run(
        ["attach", "one", "two", "--host", "andromeda", "--group", "g"], monkeypatch
    )

    err = capsys.readouterr().err
    assert code == 1
    assert err == "camp attach: --host requires exactly one workspace slug, got 2\n", err
    assert "session reference" not in err


# ---------------------------------------------------------------------------
# The forwarded argv actually resolves on the far side — replayed locally.
# ---------------------------------------------------------------------------


def test_forwarded_argv_reaches_slug_resolution_not_the_needs_group_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Capture the ssh argv `--host` builds, extract the far side's own
    ``camp attach ...`` command from it, then replay THAT argv through the
    real dispatcher from a directory no group claims — standing in for the
    far side's own `$HOME`. With the resolved group carried across, it must
    reach the door's slug resolution (the unknown-slug refusal, naming the
    forwarded group) rather than the needs-group refusal a forward carrying
    no group would hit."""
    import shlex

    _isolated_env(tmp_path, monkeypatch)
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    monkeypatch.setattr(
        _cli_session_module(), "_parsable_groups", lambda: [_group("g")]
    )
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", "myslug", "--host", "andromeda", "--group", "g"], monkeypatch)
    assert code == 0
    capsys.readouterr()

    far_side_argv = shlex.split(seen[0][-1])[1:]  # drop the camp binary itself

    # Simulate the far side: no group claims this cwd, and no --host is
    # forwarded again, but the group config exists locally on "that
    # machine" and has the workspace.
    _wire_group_with_workspace(monkeypatch, tmp_path=tmp_path, group_name="g")
    stop_module = importlib.import_module("camp.launch.stop")
    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: _UntouchedTmux())
    far_side_unclaimed = tmp_path / "far-side-home"
    far_side_unclaimed.mkdir()
    monkeypatch.chdir(far_side_unclaimed)

    far_code = _run(far_side_argv, monkeypatch)

    far_err = capsys.readouterr().err
    assert far_code == 1
    assert (
        far_err == "camp attach: no workspace named myslug in group g — "
        "'camp list' shows its workspaces\n"
    ), far_err
    assert "no group resolved from cwd" not in far_err


# ---------------------------------------------------------------------------
# No invocation exits 2
# ---------------------------------------------------------------------------


def test_no_refusal_form_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    _wire_group_with_workspace(monkeypatch, tmp_path=tmp_path, group_name="g")
    stop_module = importlib.import_module("camp.launch.stop")
    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: _UntouchedTmux())

    codes = []
    codes.append(_run(["attach", "no-such-slug", "--group", "g"], monkeypatch))
    capsys.readouterr()
    unclaimed = tmp_path / "unclaimed"
    unclaimed.mkdir(exist_ok=True)
    monkeypatch.chdir(unclaimed)
    codes.append(_run(["attach", "no-such-slug"], monkeypatch))
    capsys.readouterr()
    codes.append(_run(["attach", "-a"], monkeypatch))
    capsys.readouterr()

    assert 2 not in codes
    assert all(code == 1 for code in codes)


# ---------------------------------------------------------------------------
# `--resolve`/`-a` mistyped-flag negative control — unaffected elsewhere
# ---------------------------------------------------------------------------


def test_a_mistyped_attach_only_flag_is_not_silently_accepted_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Negative control: `--resolve` has meaning only for `camp attach`. A
    verb this dispatcher has never heard of it for must not silently start
    behaving differently because attach now exists."""
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    _run(["status", "--resolve"], monkeypatch)

    err = capsys.readouterr().err
    assert "camp attach:" not in err


def test_dash_a_on_a_mutating_verb_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)

    code = _run(["remove", "-a", "some-ref"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "--all-hosts has no meaning here" in err


def test_list_host_and_all_hosts_are_unaffected_by_attachs_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    transport = _host_transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(stdout="[]", stderr="", exit_code=0),
    )

    code = _run(["list", "--host", "andromeda", "--json"], monkeypatch)
    out = capsys.readouterr().out.strip()

    assert code == 0
    assert out == "[]"
