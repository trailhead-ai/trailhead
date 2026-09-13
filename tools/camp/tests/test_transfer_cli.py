"""Tests for `camp transfer` — the operator-facing verb: preview with
`--dry-run`, move without it.

Test contract:
- `--dry-run` prints the ordered checks and what would cross, and exits 0 on a
  clean verdict, and never calls `camp.transfer.move.move_workspace`.
- Omitting `--dry-run` runs the identical preflight; on a clean verdict it
  drives `move_workspace` and reports what arrived. A preflight check that is
  not PASSED refuses exactly as it does with `--dry-run` — `move_workspace` is
  never called and nothing crosses.
- `--overwrite` threads through to `move_workspace`; its absence against a
  workspace already on the peer, owned by this host, refuses by its own exit
  code, names the flag, and moves nothing — with the flag it transfers, and a
  re-run after a phase failure succeeds end to end. See
  `TestMoveWorkspaceEndToEnd` below for the real-peer coverage of that phase
  orchestration itself.
- The help output no longer describes `--dry-run` as required and names
  `--overwrite`.
- A refusal exits nonzero, names the owning host and the remedy, and is
  distinguishable by exit code from a failure to reach the peer.
- An indeterminate check is rendered, and its rendering differs from a pass
  and from a failure, asserted on the emitted text.
- An indeterminate check names the transport outcome behind it, in both the
  human and JSON renderings; a changed host key and a connection timeout
  produce visibly different output, asserted on the emitted text and the
  parsed object.
- `--json` emits a parseable object carrying every check, its state, and the
  verdict — asserted by parsing it, not by matching text — and every row
  carries the same success discriminator the existing JSON verbs use.
- Human output carries no ANSI escapes.
- A workspace with no conversations rooted in it reports that explicitly and
  still exits 0.
- An unknown slug, and an unknown peer name, each refuse by name and are
  distinguishable from each other.
- The verb is reserved, so a workspace slug of the same name cannot shadow it.
- Every path — success, refusal, and failure — leaves the whole camp state
  directory byte-identical, asserted by snapshot.
- A failing check with no exit code of its own falls through to the shared
  not-clean code, which is distinct from every code that does name a cause.
- The top-level JSON `ok` discriminator tracks the verdict — true on a clean
  run, false on a refusal — not merely present.
- What would be regenerated rather than copied is reported in both the human
  and JSON renderings, naming the member and its declared paths.
- A slug the transport refuses to send produces a clean `camp transfer:`
  refusal and a nonzero exit, never a traceback.
- Control characters in peer-supplied text are escaped before rendering, so
  the peer cannot rewrite a line camp already printed.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# A test whose setup reads/writes the central manifest (e.g. `_Env.write_manifest`)
# does so before `dispatch.main()` has run and bootstrapped `trailhead.paths` — so
# this file bootstraps it itself, once, up front, rather than depending on test
# execution order to have run `camp` first.
import _bootstrap  # noqa: E402

_bootstrap.ensure_trailhead_importable()

from ._helpers import camp_state_env, init_git_repo  # noqa: E402


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _transfer_module():
    return importlib.import_module("camp.cli.transfer")


def _transport_module():
    return importlib.import_module("camp.host.transport")


def _probe_module():
    return importlib.import_module("camp.transfer.probe")


def _conversations_module():
    return importlib.import_module("camp.transfer.conversations")


def _move_module():
    return importlib.import_module("camp.transfer.move")


def _session_cli_module():
    """Load `test_session_cli.py` by path, to reuse its `FakeHarness`
    registration source (`_SITECUSTOMIZE`) and its tmux stand-in
    (`_TMUX_STUB`) rather than re-authoring the same real-`camp launch`
    convention a second time. Importing it only reads module-level
    definitions (functions, fixtures, classes) — nothing here executes any
    of its tests."""
    source = Path(__file__).resolve().parent / "test_session_cli.py"
    spec = importlib.util.spec_from_file_location("camp_tests_session_cli", source)
    assert spec and spec.loader, source
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_group_toml(
    groups_dir: Path,
    name: str,
    members: list[tuple[str, str]],
    *,
    excluded: dict[str, list[str] | None] | None = None,
    account: str | None = None,
) -> None:
    groups_dir.mkdir(parents=True, exist_ok=True)
    excluded = excluded or {}
    member_tables = []
    for member_name, repo_root in members:
        lines = [f'[[members]]\nname = "{member_name}"\nrepo_root = "{repo_root}"']
        if member_name in excluded and excluded[member_name] is not None:
            entries = ", ".join(f'"{e}"' for e in excluded[member_name])
            lines.append(f"excluded = [{entries}]")
        member_tables.append("\n".join(lines))
    body = f'[group]\nname = "{name}"\n\n' + "\n\n".join(member_tables) + "\n"
    if account is not None:
        body += f'\n[launch]\naccount = "{account}"\n'
    (groups_dir / f"{name}.toml").write_text(body)


def _write_hosts_toml(
    cfg: Path, *, self_name: str | None, peers: dict[str, str] | None = None
) -> None:
    lines = []
    if self_name is not None:
        lines.append(f'self_name = "{self_name}"\n')
    for peer_name, ssh in (peers or {}).items():
        lines.append(f'[hosts.{peer_name}]\nssh = "{ssh}"\n')
    (cfg / "hosts.toml").write_text("\n".join(lines))


class _Env:
    """One hermetic camp config+state environment for a `camp transfer` run."""

    def __init__(self, tmp_path: Path):
        self.cfg = tmp_path / "config"
        self.state = tmp_path / "state"
        (self.cfg / "groups").mkdir(parents=True)
        self.repo = tmp_path / "repo_a"
        self.repo.mkdir()

    def write_group(self, name="trailhead", **kw):
        _write_group_toml(self.cfg / "groups", name, [("repo_a", str(self.repo))], **kw)

    def write_hosts(self, **kw):
        _write_hosts_toml(self.cfg, **kw)

    def manifest_path(self, group="trailhead", slug="feat-x"):
        from camp.group.manifest import manifest_path_for

        return manifest_path_for(group, slug, env=self.env)

    def write_manifest(self, owner: str | None, group="trailhead", slug="feat-x"):
        from camp.group.manifest import write_central_manifest

        write_central_manifest(self.manifest_path(group, slug), {"owner": owner})

    @property
    def env(self) -> dict[str, str]:
        return {"CAMP_CONFIG_DIR": str(self.cfg), "CAMP_STATE_DIR": str(self.state)}

    def apply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CAMP_CONFIG_DIR", str(self.cfg))
        monkeypatch.setenv("CAMP_STATE_DIR", str(self.state))


def _no_conversations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the conversation pool answer "enumerated, empty" without touching
    any real harness — the CLI's own gathering wiring is exercised separately,
    unit-level, below."""
    transfer = _transfer_module()
    monkeypatch.setattr(transfer, "_gather_conversations", lambda **kw: ())


def _clean_probe_answer(**overrides):
    """A peer answer on which every peer-dependent check passes.

    Keyword overrides shift exactly one field, so a test that varies the
    peer's answer shows which field it varied rather than restating all seven.
    """
    probe = _probe_module()
    fields = {
        "self_name": "host-b",
        "group_configured": True,
        "members": (probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),),
        "account": None,
        "workspace_exists": False,
        "workspace_owner": None,
        "contract_version": probe.PROBE_CONTRACT_VERSION,
    }
    fields.update(overrides)
    return probe.ProbeAnswer(**fields)


def _fake_probe(monkeypatch: pytest.MonkeyPatch, result):
    probe = _probe_module()

    def _fake(host, *, group, slug, self_name):
        return result

    # `_cmd_transfer_group_cli` imports `probe_peer` fresh from the module on
    # every call, so patching the module attribute is what it actually sees.
    monkeypatch.setattr(probe, "probe_peer", _fake)
    return _fake


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """Run `camp <argv>` through the real dispatcher; return its exit code."""
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    with pytest.raises(SystemExit) as exc_info:
        dispatch.main()
    return exc_info.value.code


# ---------------------------------------------------------------------------
# --dry-run is optional — it previews; its absence moves. Both paths run the
# identical preflight (see test_transfer_move.py-style coverage below in this
# file for what the moving path actually does).
# ---------------------------------------------------------------------------


def test_dry_run_still_previews_and_never_calls_move_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _boom(**kw):
        raise AssertionError("move_workspace must not be called with --dry-run")

    monkeypatch.setattr(move, "move_workspace", _boom)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    assert code == transfer.EXIT_WOULD_TRANSFER
    assert "verdict — would transfer" in capsys.readouterr().out


def test_dry_run_is_documented_as_a_preview_not_a_requirement() -> None:
    import io
    from contextlib import redirect_stdout

    from camp.spine import cmd_help

    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_help([])
    help_text = buf.getvalue()

    assert "camp transfer" in help_text
    transfer_section = help_text[help_text.index("camp transfer <slug>") :]
    transfer_section = transfer_section[: transfer_section.index("\n\n")]
    assert "REQUIRED" not in transfer_section
    assert "--overwrite" in transfer_section


def test_omitting_dry_run_on_a_clean_preflight_drives_the_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    calls = []

    def _fake_move(**kw):
        calls.append(kw)
        kw["on_phase"]("begin")
        return move.MoveResult(members=("repo_a",))

    monkeypatch.setattr(move, "move_workspace", _fake_move)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_WOULD_TRANSFER
    assert len(calls) == 1
    assert calls[0]["sender_name"] == "host-a"
    assert calls[0]["overwrite"] is False
    out = capsys.readouterr().out
    assert "phase — begin" in out
    assert "arrived on 'host-b'" in out


def test_the_gathered_conversations_are_threaded_into_move_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The pool `_gather_conversations` enumerated for the preflight preview
    is the SAME object handed to `move_workspace` — never re-enumerated —
    so the move can never disagree with what the operator was shown."""
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()
    conversations_mod = _conversations_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    gathered = (
        conversations_mod.WorkspaceConversation(
            session_id="55555555-5555-4555-8555-555555555555",
            subpath=__import__("pathlib").PurePosixPath("."),
            live=False,
            unresolved=False,
        ),
    )
    monkeypatch.setattr(transfer, "_gather_conversations", lambda **kw: gathered)

    move = _move_module()
    calls = []

    def _fake_move(**kw):
        calls.append(kw)
        kw["on_phase"]("begin")
        return move.MoveResult(members=("repo_a",))

    monkeypatch.setattr(move, "move_workspace", _fake_move)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_WOULD_TRANSFER
    assert len(calls) == 1
    assert calls[0]["conversations"] == gathered
    assert callable(calls[0]["locate_transcript"])


# ---------------------------------------------------------------------------
# --to is required
# ---------------------------------------------------------------------------


def test_missing_to_flag_refuses_with_the_generic_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group()
    env.apply(monkeypatch)
    transfer = _transfer_module()

    code = _run(monkeypatch, ["transfer", "feat-x", "--dry-run", "--group", "trailhead"])

    assert code == transfer.EXIT_ERROR
    assert "--to" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Clean verdict
# ---------------------------------------------------------------------------


def test_clean_verdict_prints_ordered_checks_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    assert code == transfer.EXIT_WOULD_TRANSFER
    out = capsys.readouterr().out
    assert re.search(r"\x1b\[", out) is None, "human output must carry no ANSI escapes"
    # Every one of the eleven checks is printed, in order.
    for name in (
        "this host has declared a name",
        "the workspace exists here",
        "this host owns it, or it was never recorded",
        "the named peer is declared",
        "the peer answers",
        "the peer's declared name differs from this host's",
        "the peer has the group configured with existing member repo roots",
        "the peer's harness account binding matches this end's",
        "the slug is free on the peer, or present there and owned by this host",
        "every member declares an excluded set",
        "the conversations rooted here are enumerated",
    ):
        assert name in out
    assert "verdict — would transfer" in out


def test_clean_verdict_json_carries_every_check_and_the_ok_discriminator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(
        monkeypatch,
        [
            "transfer",
            "feat-x",
            "--to",
            "host-b",
            "--group",
            "trailhead",
            "--dry-run",
            "--json",
        ],
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["verdict"] == "would_transfer"
    assert len(payload["checks"]) == 11
    for row in payload["checks"]:
        assert row["ok"] is (row["status"] == "passed")
    assert all(row["ok"] for row in payload["checks"])
    assert payload["conversations"] == []


def test_json_top_level_discriminator_is_false_on_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The top-level `ok` is the field a wrapping script branches on, so it must
    track the verdict rather than merely being present.

    The clean-path test pins it True; this pins it False on an ownership
    refusal, which is what makes it a discriminator instead of a constant.
    """
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-c")  # owned by neither end
    env.apply(monkeypatch)

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(
        monkeypatch,
        [
            "transfer",
            "feat-x",
            "--to",
            "host-b",
            "--group",
            "trailhead",
            "--dry-run",
            "--json",
        ],
    )

    payload = json.loads(capsys.readouterr().out)
    assert code != 0
    assert payload["ok"] is False
    assert payload["verdict"] == "not_clean"
    assert any(row["ok"] is False for row in payload["checks"])


# ---------------------------------------------------------------------------
# No conversations rooted here — still a legitimate, explicit, 0-exit answer
# ---------------------------------------------------------------------------


def test_zero_conversations_is_reported_explicitly_and_still_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    out = capsys.readouterr().out
    assert code == transfer.EXIT_WOULD_TRANSFER
    assert "0 conversation(s) enumerated" in out
    assert "no conversations are rooted in this workspace" in out


# ---------------------------------------------------------------------------
# Ownership refusal vs. peer-unreachable — distinguishable exit codes
# ---------------------------------------------------------------------------


def test_ownership_refusal_names_owner_and_remedy_with_its_own_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-c")  # owned by neither end
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    out = capsys.readouterr().out
    assert code == transfer.EXIT_OWNERSHIP_REFUSED
    assert "host-c" in out
    assert "run this preflight from" in out


@pytest.mark.parametrize(
    "outcome_factory",
    [
        lambda t: t.Unreachable(reason="connection refused"),
        lambda t: t.IdentityChanged(),
    ],
    ids=["unreachable", "identity-changed"],
)
def test_peer_unreachable_has_its_own_exit_code_distinct_from_ownership_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, outcome_factory
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()
    transport = _transport_module()

    _fake_probe(monkeypatch, outcome_factory(transport))
    _no_conversations(monkeypatch)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    assert code == transfer.EXIT_PEER_UNREACHABLE
    assert code != transfer.EXIT_OWNERSHIP_REFUSED


# ---------------------------------------------------------------------------
# Across the whole closed transport-outcome set, pairwise distinct
# ---------------------------------------------------------------------------


def _every_transport_outcome(transport):
    return [
        transport.Unreachable(reason="dns failure"),
        transport.StoppedResponding(execution_timeout=30.0),
        transport.IdentityUnknown(),
        transport.IdentityChanged(),
        transport.CredentialsRefused(),
        transport.CampNotResolvable(),
        transport.RemoteRefusal(stdout="", stderr="boom", exit_code=3),
    ]


def test_every_transport_outcome_renders_distinctly_pairwise_in_json_and_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()
    transport = _transport_module()

    details: list[str] = []
    kinds: list[str] = []
    for outcome in _every_transport_outcome(transport):
        _fake_probe(monkeypatch, outcome)
        _no_conversations(monkeypatch)

        code = _run(
            monkeypatch,
            [
                "transfer",
                "feat-x",
                "--to",
                "host-b",
                "--group",
                "trailhead",
                "--dry-run",
                "--json",
            ],
        )
        assert code == transfer.EXIT_PEER_UNREACHABLE
        payload = json.loads(capsys.readouterr().out)
        row = next(r for r in payload["checks"] if r["name"] == "the peer answers")
        assert row["status"] == "indeterminate"
        assert row["ok"] is False
        assert row["transport_outcome"]["kind"] == type(outcome).__name__
        details.append(row["detail"])
        kinds.append(row["transport_outcome"]["kind"])

    assert len(set(details)) == len(details), f"detail text collided: {details}"
    assert len(set(kinds)) == len(kinds), f"transport_outcome kind collided: {kinds}"

    # And the changed-host-key / timeout pair the contract calls out by name,
    # asserted specifically rather than only as part of the pairwise sweep.
    idx_changed = kinds.index("IdentityChanged")
    idx_timeout = kinds.index("StoppedResponding")
    assert details[idx_changed] != details[idx_timeout]


def test_indeterminate_check_renders_differently_from_pass_and_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transport = _transport_module()

    _fake_probe(monkeypatch, transport.Unreachable(reason="timed out"))
    _no_conversations(monkeypatch)

    _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )
    out = capsys.readouterr().out
    line = next(t for t in out.splitlines() if "the peer answers" in t)
    assert "[INDETERMINATE]" in line
    assert "[PASS]" not in line
    assert "[FAIL]" not in line
    assert "timed out" in line


# ---------------------------------------------------------------------------
# Unknown slug vs. unknown peer — distinguishable exit codes
# ---------------------------------------------------------------------------


def test_unknown_slug_refuses_by_name_with_its_own_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    # No manifest written: the slug is unknown to this host.
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(
        monkeypatch,
        ["transfer", "no-such-slug", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    out = capsys.readouterr().out
    assert code == transfer.EXIT_UNKNOWN_SLUG
    assert "no-such-slug" in out


def test_unknown_peer_refuses_by_name_distinguishably_from_unknown_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a")  # no peers declared at all
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    def _boom(*a, **k):
        raise AssertionError("an undeclared peer must never be probed")

    monkeypatch.setattr(_probe_module(), "probe_peer", _boom)
    _no_conversations(monkeypatch)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "no-such-host", "--group", "trailhead", "--dry-run"],
    )

    out = capsys.readouterr().out
    assert code == transfer.EXIT_UNKNOWN_PEER
    assert "no-such-host" in out
    assert code != transfer.EXIT_UNKNOWN_SLUG


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------


def test_transfer_is_reserved_and_never_dispatched_as_a_bare_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.spine import RESERVED

    assert "transfer" in RESERVED

    cfg = tmp_path / "config"
    (cfg / "groups").mkdir(parents=True)
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))

    _run(monkeypatch, ["transfer"])

    combined = capsys.readouterr()
    assert "bare slug dispatch is no longer supported" not in (combined.out + combined.err)


# ---------------------------------------------------------------------------
# Purity: every path leaves the whole camp state directory byte-identical
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> dict[str, tuple[str, object]]:
    if not root.is_dir():
        return {}
    snapshot: dict[str, tuple[str, object]] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            snapshot[key] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[key] = ("dir", None)
        else:
            snapshot[key] = ("file", path.read_bytes())
    return snapshot


@pytest.mark.parametrize(
    "setup_kind",
    ["clean", "ownership_refused", "peer_unreachable"],
)
def test_every_outcome_leaves_camp_state_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, setup_kind
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    transport = _transport_module()

    # Each kind is the one input that steers the run down its own path: the
    # recorded owner decides clean vs. ownership-refused, the peer's answer
    # decides whether the peer was reached at all.
    env.write_manifest(owner="host-c" if setup_kind == "ownership_refused" else "host-a")
    _fake_probe(
        monkeypatch,
        transport.Unreachable(reason="down")
        if setup_kind == "peer_unreachable"
        else _clean_probe_answer(),
    )

    env.apply(monkeypatch)
    _no_conversations(monkeypatch)

    env.state.mkdir(parents=True, exist_ok=True)
    before = _snapshot(env.state)

    _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )
    capsys.readouterr()

    after = _snapshot(env.state)
    assert before == after


# ---------------------------------------------------------------------------
# _gather_conversations — the CLI's own wiring from the harness pool into
# camp.transfer.conversations.workspace_conversations, tested in isolation
# from any real harness subprocess.
# ---------------------------------------------------------------------------


def test_gather_conversations_returns_none_on_enumeration_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transfer = _transfer_module()
    teardown_guard = importlib.import_module("camp.launch.teardown_guard")

    def _raise(*a, **k):
        raise teardown_guard.EnumerationUnavailable("no harness could answer")

    monkeypatch.setattr(teardown_guard, "gather_pool", _raise)

    result = transfer._gather_conversations(
        group_name="g", slug="s", session_groups=[], resolved_env={}
    )
    assert result is None


def _hermetic_env(tmp_path: Path) -> dict[str, str]:
    return {"HOME": str(tmp_path / "home"), "CAMP_STATE_DIR": str(tmp_path / "state")}


def test_gather_conversations_wires_the_gathered_pool_into_workspace_conversations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transfer = _transfer_module()
    teardown_guard = importlib.import_module("camp.launch.teardown_guard")
    session_mod = importlib.import_module("camp.cli.session")
    conversations_mod = _conversations_module()

    monkeypatch.setattr(session_mod, "_addressable_harnesses", lambda groups, **kw: [])
    monkeypatch.setattr(
        teardown_guard, "gather_pool", lambda harnesses, *, env: (["T"], ["L"])
    )

    captured = {}
    sentinel = (conversations_mod.WorkspaceConversation("sid", None, False, True),)

    def _fake_workspace_conversations(workspace, *, transcripts, live_records, groups, env):
        captured["call"] = (workspace, transcripts, live_records, groups, env)
        return sentinel

    monkeypatch.setattr(conversations_mod, "workspace_conversations", _fake_workspace_conversations)

    resolved_env = _hermetic_env(tmp_path)
    result = transfer._gather_conversations(
        group_name="g", slug="s1", session_groups=["G"], resolved_env=resolved_env
    )

    assert result is sentinel
    _workspace, transcripts, live_records, groups, env = captured["call"]
    assert transcripts == ["T"]
    assert live_records == ["L"]
    assert groups == ["G"]
    assert env == resolved_env


def test_gather_conversations_returns_a_different_answer_for_a_different_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring test above alone would pass even if the pool were ignored;
    this shows the result actually varies with what `gather_pool` returns."""
    transfer = _transfer_module()
    teardown_guard = importlib.import_module("camp.launch.teardown_guard")
    session_mod = importlib.import_module("camp.cli.session")
    conversations_mod = _conversations_module()

    monkeypatch.setattr(session_mod, "_addressable_harnesses", lambda groups, **kw: [])

    def _fake_workspace_conversations(workspace, *, transcripts, live_records, groups, env):
        return tuple(transcripts)

    monkeypatch.setattr(conversations_mod, "workspace_conversations", _fake_workspace_conversations)
    resolved_env = _hermetic_env(tmp_path)

    monkeypatch.setattr(teardown_guard, "gather_pool", lambda harnesses, *, env: (["a"], []))
    first = transfer._gather_conversations(
        group_name="g", slug="s", session_groups=[], resolved_env=resolved_env
    )

    monkeypatch.setattr(teardown_guard, "gather_pool", lambda harnesses, *, env: (["a", "b"], []))
    second = transfer._gather_conversations(
        group_name="g", slug="s", session_groups=[], resolved_env=resolved_env
    )

    assert first != second


# ---------------------------------------------------------------------------
# The shared fallback code every unmapped failing check lands on
# ---------------------------------------------------------------------------


def test_unmapped_failing_check_falls_through_to_the_shared_not_clean_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Only three checks name an exit code of their own. Every other failure -
    here a member that never declared an excluded set - resolves to the shared
    not-clean code, and that code must not collide with any of the three that
    do name a cause, or an operator scripting on the code would read the wrong
    reason.

    The group is written with no `excluded` key for its member, which is Task
    1's "never declared" handoff (`None`) rather than its "declares nothing"
    one (`()`).
    """
    env = _Env(tmp_path)
    env.write_group()  # no excluded key at all -> the None handoff
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    out = capsys.readouterr().out
    assert code == transfer.EXIT_NOT_CLEAN
    assert code not in (
        transfer.EXIT_OWNERSHIP_REFUSED,
        transfer.EXIT_UNKNOWN_SLUG,
        transfer.EXIT_UNKNOWN_PEER,
        transfer.EXIT_PEER_UNREACHABLE,
        transfer.EXIT_WOULD_TRANSFER,
    ), "the shared not-clean code collides with a code that names a cause"
    assert "repo_a" in out, "the refusal must name the member that never declared"


# ---------------------------------------------------------------------------
# What would be regenerated instead of copied, and untrusted text at the seam
# ---------------------------------------------------------------------------


def test_what_would_be_regenerated_is_reported_in_both_renderings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The whole point of the excluded declaration is that the operator sees
    which trees get rebuilt on arrival rather than carried over the wire.

    A member declaring paths is named with them; a member declaring an empty
    set has nothing to rebuild and is not listed.
    """
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": ["build/", "node_modules/"]})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    argv = ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"]
    assert _run(monkeypatch, argv) == 0
    human = capsys.readouterr().out
    # The loader normalizes a declared "build/" to "build"; the rendering shows
    # what camp actually holds, not what was typed.
    assert "regenerated on arrival rather than copied:" in human, human
    assert "repo_a: build, node_modules" in human, (
        f"the member and its declared paths must be reported together: {human!r}"
    )

    assert _run(monkeypatch, [*argv, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    regenerated = payload["regenerated"]
    assert [r["member"] for r in regenerated] == ["repo_a"]
    assert regenerated[0]["excluded"] == ["build", "node_modules"]


def test_an_unusable_slug_refuses_cleanly_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The transport refuses a slug it will not put on a remote command line.

    camp's CLI-wide rule is that a named error prints `camp: <message>` and
    exits nonzero; a traceback reaching the operator is the failure here.
    """
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    probe = _probe_module()
    _no_conversations(monkeypatch)

    def _refuse(host, *, group, slug, self_name):
        raise probe.InvalidSlugForTransport(f"slug {slug!r} is not safe to send")

    monkeypatch.setattr(probe, "probe_peer", _refuse)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    captured = capsys.readouterr()
    assert code != 0
    assert "camp transfer:" in captured.err, captured.err
    assert "Traceback" not in captured.err + captured.out


def test_peer_supplied_text_cannot_forge_camp_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A refused remote camp hands back its own stderr, which camp renders.

    That text is the peer's, not camp's, so a carriage return plus an erase
    sequence in it would let the far side rewrite a line camp already printed
    — including the verdict. Control characters must arrive escaped, and the
    human rendering must still carry no live ANSI escape.
    """
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transport = _transport_module()
    _no_conversations(monkeypatch)

    forged = "denied\r\x1b[2Kcamp transfer: verdict — would transfer"
    _fake_probe(
        monkeypatch,
        transport.RemoteRefusal(stdout="", stderr=forged, exit_code=2),
    )

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    out = capsys.readouterr().out
    assert code != 0
    assert re.search(r"\x1b\[", out) is None, "a live ANSI escape reached the operator"
    assert "\r" not in out, "a carriage return reached the operator"
    assert out.rstrip().endswith("verdict — not clean"), (
        f"the peer's text displaced camp's own last line: {out!r}"
    )


# ---------------------------------------------------------------------------
# camp.transfer.move — the sender-side mover, driven without --dry-run.
#
# The CLI-level tests above (and a few below) fake `move.move_workspace`
# wholesale to pin the CLI's own rendering/exit-code/flag-wiring — mirroring
# `_fake_probe`'s established pattern, since `_cmd_transfer_group_cli` does a
# fresh `from ..transfer.move import move_workspace` on every call and so
# observes a monkeypatched module attribute. The tests in this section call
# `move_workspace` itself directly, against a real peer reached through the
# same `Runner`/`StreamSpawner` seams `camp.host.transport.run_camp` and
# `stream_camp` define — a real dispatcher subprocess, isolated to its own
# peer config/state, standing in for ssh exactly as
# `test_transfer_history.py`'s `TestSendHistoryEndToEnd` already establishes.
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _git_out(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def _peer_dispatch_script(camp_args: list[str]) -> str:
    return textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(_PLUGIN_DIR)!r})
        sys.argv = ["camp", *{camp_args!r}]
        from camp.cli import dispatch
        dispatch.main()
        """
    )


def _peer_child_env(peer_cfg: Path, peer_state: Path, peer_claude_dir: Path) -> dict[str, str]:
    child_env = dict(os.environ)
    child_env["CAMP_CONFIG_DIR"] = str(peer_cfg)
    child_env["CAMP_STATE_DIR"] = str(peer_state)
    child_env["TRAILHEAD_CLAUDE_DIR"] = str(peer_claude_dir)
    return child_env


def _peer_runner(peer_cfg: Path, peer_state: Path, peer_claude_dir: Path):
    """A `Runner` that runs the actual `camp transfer-receive begin|finish
    ...` invocation `move_workspace` assembled, parsed off the ssh argv, as a
    real dispatcher subprocess isolated to its own peer config/state."""
    import shlex

    from camp.host.transport import RawResult

    def _run(argv, execution_timeout, env):
        remote_command = argv[-1]
        camp_args = shlex.split(remote_command)[1:]
        result = subprocess.run(
            [sys.executable, "-c", _peer_dispatch_script(camp_args)],
            capture_output=True,
            text=True,
            env=_peer_child_env(peer_cfg, peer_state, peer_claude_dir),
            timeout=execution_timeout,
        )
        return RawResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)

    return _run


def _peer_stream_spawn(peer_cfg: Path, peer_state: Path, peer_claude_dir: Path):
    """A `StreamSpawner` mirroring `_peer_runner`, for the `history` and
    `worktree` phases `move_workspace` drives over `stream_camp`."""
    import shlex

    def _spawn(argv, env):
        remote_command = argv[-1]
        camp_args = shlex.split(remote_command)[1:]
        return subprocess.Popen(
            [sys.executable, "-c", _peer_dispatch_script(camp_args)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_peer_child_env(peer_cfg, peer_state, peer_claude_dir),
        )

    return _spawn


def _fake_ssh_forwarding_script(peer_cfg: Path, peer_state: Path, peer_claude_dir: Path) -> str:
    """Source for a literal `ssh` executable — installed first on `$PATH` so
    `camp.host.transport`'s real `default_runner`/`default_stream_spawner`
    invoke it exactly as they would invoke real `ssh`. It ignores every ssh
    option and the destination, extracts the remote camp invocation from its
    own last argv element (the same parse `_peer_runner`/`_peer_stream_spawn`
    apply to the argv they receive directly), and runs it as a real
    dispatcher subprocess against the peer's isolated config/state/harness
    store — so `camp transfer`, invoked exactly as the operator invokes it,
    reaches a real peer without a real network hop."""
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import os
        import shlex
        import subprocess
        import sys

        _PEER_ENV = {{
            "CAMP_CONFIG_DIR": {str(peer_cfg)!r},
            "CAMP_STATE_DIR": {str(peer_state)!r},
            "TRAILHEAD_CLAUDE_DIR": {str(peer_claude_dir)!r},
        }}
        _PLUGIN_DIR = {str(_PLUGIN_DIR)!r}

        remote_command = sys.argv[-1]
        camp_args = shlex.split(remote_command)[1:]
        script = (
            "import sys\\n"
            "sys.path.insert(0, " + repr(_PLUGIN_DIR) + ")\\n"
            "sys.argv = ['camp'] + " + repr(camp_args) + "\\n"
            "from camp.cli import dispatch\\n"
            "dispatch.main()\\n"
        )
        env = dict(os.environ)
        env.update(_PEER_ENV)
        stdin_bytes = sys.stdin.buffer.read()
        proc = subprocess.run(
            [sys.executable, "-c", script],
            input=stdin_bytes,
            capture_output=True,
            env=env,
        )
        sys.stdout.buffer.write(proc.stdout)
        sys.stderr.buffer.write(proc.stderr)
        sys.exit(proc.returncode)
        """
    )


def _install_fake_ssh(bin_dir: Path, peer_cfg: Path, peer_state: Path, peer_claude_dir: Path) -> None:
    """Writes a literal `ssh` executable into *bin_dir*, forwarding to the
    peer described by *peer_cfg*/*peer_state*/*peer_claude_dir*. The caller
    is responsible for putting *bin_dir* first on the subprocess `PATH`."""
    import stat

    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "ssh"
    script.write_text(
        _fake_ssh_forwarding_script(peer_cfg, peer_state, peer_claude_dir), encoding="utf-8"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def _move_group(sender_repo: Path) -> dict:
    return {
        "group": {"name": "testgroup"},
        "members": [
            {
                "name": "repo_a",
                "repo_root": str(sender_repo),
                "tasks": [],
                "base": "no-such-base",
                "excluded": [],
            }
        ],
        "branch_pattern": "worktree-{slug}",
    }


@pytest.fixture()
def move_env(tmp_path: Path):
    """A real sender worktree (an unpushed commit plus an untracked file)
    and a real, freshly-initialized peer repo with its own isolated
    config/state — everything `move_workspace` needs to actually move
    content, with the peer reached as a real subprocess rather than a fake
    return value."""
    from camp.host.config import Host
    from camp.provision.reconcile import _worktree_path

    slug = "feat-move"
    branch = f"worktree-{slug}"

    sender_repo = tmp_path / "sender"
    init_git_repo(sender_repo, origin=False)
    sender_env = camp_state_env(tmp_path / "sender")
    wt_path = _worktree_path("testgroup", slug, "repo_a", env=sender_env)
    _git(sender_repo, "worktree", "add", str(wt_path), "-b", branch)
    (wt_path / "committed.txt").write_text("committed on the sender\n")
    _git(wt_path, "add", "committed.txt")
    _git(
        wt_path,
        "-c", "user.email=t@t.com",
        "-c", "user.name=t",
        "commit", "-m", "sender content", "--no-gpg-sign",
    )
    (wt_path / "untracked.txt").write_text("never committed\n")

    peer_repo = tmp_path / "peer_repo_a"
    init_git_repo(peer_repo, origin=False)
    peer_cfg = tmp_path / "peer-config"
    (peer_cfg / "groups").mkdir(parents=True)
    _write_group_toml(peer_cfg / "groups", "testgroup", [("repo_a", str(peer_repo))])
    # `finish` now runs only after `claim`, and `claim` refuses when the peer
    # has no declared self_name — every move_env-based test drives a real
    # `finish`, so the peer needs one.
    _write_hosts_toml(peer_cfg, self_name="host-b")
    peer_state = tmp_path / "peer-state"
    peer_claude_dir = tmp_path / "peer-claude"
    peer_env = {
        "CAMP_CONFIG_DIR": str(peer_cfg),
        "CAMP_STATE_DIR": str(peer_state),
        "TRAILHEAD_CLAUDE_DIR": str(peer_claude_dir),
    }

    return {
        "slug": slug,
        "branch": branch,
        "group": _move_group(sender_repo),
        "host": Host(ssh="fake-peer", camp_bin="/opt/camp/bin/camp"),
        "sender_env": sender_env,
        "sender_repo": sender_repo,
        "wt_path": wt_path,
        "tmp_path": tmp_path,
        "peer_repo": peer_repo,
        "peer_cfg": peer_cfg,
        "peer_state": peer_state,
        "peer_claude_dir": peer_claude_dir,
        "peer_env": peer_env,
        "run": _peer_runner(peer_cfg, peer_state, peer_claude_dir),
        "stream_spawn": _peer_stream_spawn(peer_cfg, peer_state, peer_claude_dir),
    }


# ---------------------------------------------------------------------------
# camp.transfer.move — proving a crossed conversation resumes on a real peer,
# through `camp`'s own CLI rather than at the handler. Reuses `move_env`'s
# real sender worktree + real peer subprocess, plus the `FakeHarness`/tmux
# stand-in convention `test_session_cli.py`'s `cli_env` fixture already
# establishes for running a real `camp launch`/`camp sessions` without a real
# Claude Code install or a real tmux.
# ---------------------------------------------------------------------------


def _harness_shim(tmp_path: Path, tag: str) -> dict:
    """The PATH/PYTHONPATH additions plus the fake session store files a
    `camp` subprocess needs to list/launch/resume sessions without touching a
    real harness or a real tmux — `test_session_cli.py`'s `_SITECUSTOMIZE`
    (registers `FakeHarness` under the `claude_code` registry key) and
    `_TMUX_STUB` (a tiny session table backed by two files), reused rather
    than re-authored."""
    session_cli = _session_cli_module()

    shim_dir = tmp_path / f"{tag}-shim"
    shim_dir.mkdir()
    (shim_dir / "sitecustomize.py").write_text(session_cli._SITECUSTOMIZE, encoding="utf-8")

    bin_dir = tmp_path / f"{tag}-bin"
    bin_dir.mkdir()
    tmux = bin_dir / "tmux"
    tmux.write_text(session_cli._TMUX_STUB, encoding="utf-8")
    tmux.chmod(0o755)

    sessions_file = tmp_path / f"{tag}-sessions.tsv"
    sessions_file.write_text("", encoding="utf-8")
    tmux_argv_file = tmp_path / f"{tag}-tmux-argv.tsv"
    tmux_argv_file.write_text("", encoding="utf-8")
    tmux_table_file = tmp_path / f"{tag}-tmux-table.json"
    tmux_table_file.write_text("{}", encoding="utf-8")

    return {
        "shim_dir": shim_dir,
        "bin_dir": bin_dir,
        "sessions_file": sessions_file,
        "tmux_argv_file": tmux_argv_file,
        "tmux_table_file": tmux_table_file,
    }


def _camp_subprocess_env(*, cfg: Path, state: Path, claude_dir: Path, shim: dict) -> dict[str, str]:
    """The full environment for a real `camp` CLI subprocess against one
    isolated config/state/harness-store triple, with *shim*'s fake tmux and
    fake harness wired in via PATH/PYTHONPATH."""
    env = dict(os.environ)
    env["CAMP_CONFIG_DIR"] = str(cfg)
    env["CAMP_STATE_DIR"] = str(state)
    env["TRAILHEAD_CLAUDE_DIR"] = str(claude_dir)
    env["CAMP_FAKE_SESSIONS_FILE"] = str(shim["sessions_file"])
    env["CAMP_FAKE_TMUX_ARGV_FILE"] = str(shim["tmux_argv_file"])
    env["CAMP_FAKE_TMUX_TABLE_FILE"] = str(shim["tmux_table_file"])
    env["PATH"] = f"{shim['bin_dir']}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(shim["shim_dir"]), str(_REPO_ROOT), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    env.pop("CAMP_SHELL_INTEGRATION", None)
    return env


def _run_camp(env: dict[str, str], *camp_args: str) -> subprocess.CompletedProcess[str]:
    """Run `camp <camp_args>` as a real subprocess under *env* — the same
    dispatcher-by-path mechanism `_peer_runner`/`_peer_stream_spawn` already
    use for the transport phases, reused here to drive the operator-facing
    verbs directly."""
    return subprocess.run(
        [sys.executable, "-c", _peer_dispatch_script(list(camp_args))],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture()
def conv_env(move_env, tmp_path: Path):
    """`move_env` plus a real config+harness-store for the SENDER (so what
    the sender's own recoverable listing and resume path show — before and
    after the handover commits — can be checked through the CLI too) and the
    harness/tmux shim on both sides."""
    g = move_env

    sender_cfg = tmp_path / "sender-config"
    (sender_cfg / "groups").mkdir(parents=True)
    _write_group_toml(sender_cfg / "groups", "testgroup", [("repo_a", str(g["sender_repo"]))])
    sender_claude_dir = tmp_path / "sender-claude"

    sender_shim = _harness_shim(tmp_path, "sender")
    peer_shim = _harness_shim(tmp_path, "peer")

    sender_cli_env = _camp_subprocess_env(
        cfg=sender_cfg,
        state=Path(g["sender_env"]["CAMP_STATE_DIR"]),
        claude_dir=sender_claude_dir,
        shim=sender_shim,
    )
    peer_cli_env = _camp_subprocess_env(
        cfg=g["peer_cfg"], state=g["peer_state"], claude_dir=g["peer_claude_dir"], shim=peer_shim
    )

    return {
        **g,
        "sender_cfg": sender_cfg,
        "sender_claude_dir": sender_claude_dir,
        "sender_cli_env": sender_cli_env,
        "peer_cli_env": peer_cli_env,
        "sender_shim": sender_shim,
        "peer_shim": peer_shim,
    }


@pytest.fixture()
def e2e_env(conv_env, tmp_path: Path):
    """`conv_env` wired so `camp transfer` itself — not `move_workspace`
    imported directly — is the thing under test: the sender's own group
    config declares `excluded` (preflight check 10, otherwise refused
    before anything moves), hosts.toml declares both ends, a real `ssh`
    executable is installed first on the sender's `$PATH` so
    `camp.host.transport`'s real transport reaches the real peer subprocess
    (see `_install_fake_ssh`), and the sender's own manifest starts owned by
    itself — exactly the state an operator's workspace is in before running
    `camp transfer`."""
    c = conv_env

    _write_group_toml(
        c["sender_cfg"] / "groups",
        "testgroup",
        [("repo_a", str(c["sender_repo"]))],
        excluded={"repo_a": []},
    )
    _write_hosts_toml(c["sender_cfg"], self_name="host-a", peers={"host-b": "fake-peer"})

    from camp.group.manifest import manifest_path_for, write_central_manifest

    write_central_manifest(
        manifest_path_for("testgroup", c["slug"], env=c["sender_env"]), {"owner": "host-a"}
    )

    fake_ssh_dir = tmp_path / "fake-ssh-bin"
    _install_fake_ssh(fake_ssh_dir, c["peer_cfg"], c["peer_state"], c["peer_claude_dir"])

    sender_cli_env = dict(c["sender_cli_env"])
    sender_cli_env["PATH"] = f"{fake_ssh_dir}{os.pathsep}{sender_cli_env['PATH']}"

    return {**c, "sender_cli_env": sender_cli_env}


def _seed_conversation(c: dict, *, session_id: str, marker: str) -> None:
    """Writes one real transcript, with identifiable prior content, rooted
    at the sender workspace root — in the sender's own real harness store —
    without driving anything across. `camp transfer` itself (via
    `_gather_conversations`/`_locate_transcript`) is what discovers and
    crosses it in the tests that use this."""
    from camp.group.manifest import workspace_dir
    from trailhead.harness.claude_code import ClaudeCodeHarness

    harness = ClaudeCodeHarness()
    sender_ws_root = workspace_dir("testgroup", c["slug"], env=c["sender_env"]).resolve()
    sender_claude_env = {"TRAILHEAD_CLAUDE_DIR": str(c["sender_claude_dir"])}
    dest = harness.session_transcript_destination(session_id, sender_ws_root, env=sender_claude_env)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps({"type": "summary", "cwd": str(sender_ws_root)})
        + "\n"
        + json.dumps({"type": "user", "message": {"role": "user", "content": marker}})
        + "\n",
        encoding="utf-8",
    )


class TestTransferEndToEndThroughTheRealEntryPath:
    """`camp transfer` itself, invoked as a real subprocess exactly as the
    operator invokes it, against a real peer reached through a real `ssh`
    executable on `$PATH` (see `_install_fake_ssh`) — proving the whole
    slice as one thing rather than through any imported module."""

    def test_the_peers_record_the_senders_record_and_the_senders_offer_agree_in_one_run(
        self, e2e_env
    ):
        """The slice's claim is that three things hold together after one
        `camp transfer` run: the peer's own record names the peer, the
        sender's own record names the peer (not this host's own alias for
        it), and the sender's own recoverable listing no longer offers the
        conversation that just crossed. Asserted from the one run, not
        three separate ones."""
        from camp.group.manifest import manifest_path_for, owner_of, read_central_manifest

        c = e2e_env
        session_id = "e0e00000-0000-4000-8000-0000000000e1"
        _seed_conversation(c, session_id=session_id, marker="e2e-commit-marker-1")

        result = _run_camp(
            c["sender_cli_env"], "transfer", c["slug"], "--to", "host-b", "--group", "testgroup"
        )
        assert result.returncode == 0, result.stderr

        peer_manifest = manifest_path_for("testgroup", c["slug"], env=c["peer_env"])
        assert owner_of(read_central_manifest(peer_manifest)) == "host-b"

        sender_manifest = manifest_path_for("testgroup", c["slug"], env=c["sender_env"])
        assert owner_of(read_central_manifest(sender_manifest)) == "host-b"

        sender_rows = _recoverable_rows(c["sender_cli_env"])
        assert session_id not in {row["session_id"] for row in sender_rows}, sender_rows

    def test_the_same_run_leaves_the_conversation_resumable_on_the_peer_with_its_prior_history(
        self, e2e_env
    ):
        c = e2e_env
        session_id = "e0e00000-0000-4000-8000-0000000000e2"
        marker = "e2e-resumable-history-marker-2"
        _seed_conversation(c, session_id=session_id, marker=marker)

        result = _run_camp(
            c["sender_cli_env"], "transfer", c["slug"], "--to", "host-b", "--group", "testgroup"
        )
        assert result.returncode == 0, result.stderr

        peer_rows = _recoverable_rows(c["peer_cli_env"])
        assert session_id in {row["session_id"] for row in peer_rows}, peer_rows

        resume = _run_camp(c["peer_cli_env"], "launch", "--resume", session_id)
        assert resume.returncode == 0, resume.stderr
        assert resume.stdout.strip() == session_id, resume.stdout

        from camp.group.manifest import workspace_dir
        from trailhead.harness.claude_code import ClaudeCodeHarness

        peer_ws_root = workspace_dir("testgroup", c["slug"], env=c["peer_env"]).resolve()
        peer_transcript = ClaudeCodeHarness().session_transcript_path(
            session_id, peer_ws_root, env=c["peer_env"]
        )
        assert peer_transcript is not None
        assert marker in peer_transcript.read_text()

    def test_completion_output_names_the_new_owner_and_each_conversations_new_home(
        self, e2e_env
    ):
        c = e2e_env
        session_id = "e0e00000-0000-4000-8000-0000000000e3"
        _seed_conversation(c, session_id=session_id, marker="e2e-completion-marker-3")

        result = _run_camp(
            c["sender_cli_env"], "transfer", c["slug"], "--to", "host-b", "--group", "testgroup"
        )
        assert result.returncode == 0, result.stderr

        assert "ownership moved to 'host-b'" in result.stdout
        assert session_id in result.stdout
        assert f"camp launch --resume {session_id}" in result.stdout
        assert "released from this host — archived at" in result.stdout

    def test_a_workspace_with_no_conversations_completes_the_handover_and_says_so(
        self, e2e_env
    ):
        c = e2e_env

        result = _run_camp(
            c["sender_cli_env"], "transfer", c["slug"], "--to", "host-b", "--group", "testgroup"
        )
        assert result.returncode == 0, result.stderr
        assert "no conversations are rooted in this workspace" in result.stdout

        from camp.group.manifest import manifest_path_for, owner_of, read_central_manifest

        peer_manifest = manifest_path_for("testgroup", c["slug"], env=c["peer_env"])
        assert owner_of(read_central_manifest(peer_manifest)) == "host-b"


def _cross_one_conversation(
    c: dict,
    *,
    session_id: str,
    subpath,
    marker: str,
    conversation_producer_spawn=None,
    overwrite: bool = False,
):
    """Seed one conversation with identifiable prior content at *subpath*
    under the sender workspace, in the sender's own real harness store, then
    drive it across with `move_workspace` exactly as `move_env`'s own
    end-to-end tests do. Returns `(result, phases, sender_transcript_path)`."""
    from camp.group.manifest import workspace_dir
    from camp.transfer.conversations import WorkspaceConversation
    from camp.transfer.move import move_workspace
    from trailhead.harness.claude_code import ClaudeCodeHarness

    harness = ClaudeCodeHarness()
    sender_ws_root = workspace_dir("testgroup", c["slug"], env=c["sender_env"]).resolve()
    conv_root = sender_ws_root if str(subpath) == "." else c["wt_path"]

    sender_claude_env = {"TRAILHEAD_CLAUDE_DIR": str(c["sender_claude_dir"])}
    dest = harness.session_transcript_destination(session_id, conv_root, env=sender_claude_env)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps({"type": "summary", "cwd": str(conv_root)})
        + "\n"
        + json.dumps({"type": "user", "message": {"role": "user", "content": marker}})
        + "\n",
        encoding="utf-8",
    )

    conversation = WorkspaceConversation(
        session_id=session_id, subpath=subpath, live=False, unresolved=False
    )

    def _locate(sid, root):
        return harness.session_transcript_path(sid, root, env=sender_claude_env)

    kwargs = {}
    if conversation_producer_spawn is not None:
        kwargs["conversation_producer_spawn"] = conversation_producer_spawn

    phases: list[str] = []
    result = move_workspace(
        host=c["host"],
        group=c["group"],
        group_name="testgroup",
        slug=c["slug"],
        sender_name="host-a",
        overwrite=overwrite,
        on_phase=phases.append,
        env=c["sender_env"],
        run=c["run"],
        stream_spawn=c["stream_spawn"],
        conversations=(conversation,),
        locate_transcript=_locate,
        **kwargs,
    )
    return result, phases, dest


def _recoverable_rows(env: dict[str, str]) -> list[dict]:
    result = _run_camp(env, "sessions", "--group", "testgroup", "--recoverable", "--all", "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestConversationResumesOnARealPeer:
    def test_root_conversation_is_listed_and_its_own_resume_path_accepts_it(
        self, conv_env
    ):
        """A conversation rooted at the workspace root crosses,
        and on the peer it is BOTH listed among the recoverable conversations
        AND accepted by camp's own resume path — asserted as two separate
        observations, since the criterion this pins is precisely that the
        two agree."""
        from pathlib import PurePosixPath

        c = conv_env
        session_id = "11111111-1111-4111-8111-111111111111"
        marker = "root-conversation-marker-4f9c"

        result, phases, _ = _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("."), marker=marker
        )
        assert phases == [
            "begin",
            "history: repo_a",
            "worktree: repo_a",
            "conversations",
            "claim",
            "finish",
        ]
        assert result.conversations[0].session_id == session_id

        # Observation 1: it is listed among the recoverable conversations.
        rows = _recoverable_rows(c["peer_cli_env"])
        assert session_id in {row["session_id"] for row in rows}, rows

        # Observation 2: camp's own resume path accepts it — a separate
        # invocation, never inferred from the listing above.
        resume = _run_camp(c["peer_cli_env"], "launch", "--resume", session_id)
        assert resume.returncode == 0, resume.stderr
        assert resume.stdout.strip() == session_id, resume.stdout

        # The prior history travelled with it: the transcript the peer
        # resumed carries the exact content written before the move.
        from camp.group.manifest import workspace_dir

        peer_ws_root = workspace_dir("testgroup", c["slug"], env=c["peer_env"]).resolve()
        from trailhead.harness.claude_code import ClaudeCodeHarness

        peer_transcript = ClaudeCodeHarness().session_transcript_path(
            session_id, peer_ws_root, env=c["peer_env"]
        )
        assert peer_transcript is not None
        assert marker in peer_transcript.read_text()

    def test_member_subdirectory_conversation_resumes_rooted_there(self, conv_env):
        """A conversation started inside the `repo_a` member
        subdirectory crosses and resumes ROOTED AT the peer's corresponding
        subdirectory — asserted on the directory the resume actually spawned
        into (the tmux pane's own `-c` launch directory), never on the
        `subpath` recorded in the transcript."""
        from pathlib import PurePosixPath

        from camp.provision.reconcile import _worktree_path

        c = conv_env
        session_id = "22222222-2222-4222-8222-222222222222"
        marker = "member-subdir-marker-a170"

        _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("repo_a"), marker=marker
        )

        resume = _run_camp(c["peer_cli_env"], "launch", "--resume", session_id)
        assert resume.returncode == 0, resume.stderr

        session_cli = _session_cli_module()
        spawned = session_cli._tmux_new_session_argv(c["peer_shim"])
        assert len(spawned) == 1, spawned
        launch_dir = session_cli._flag_value(spawned[0], "-c")

        expected_member_dir = _worktree_path(
            "testgroup", c["slug"], "repo_a", env=c["peer_env"]
        ).resolve()
        assert launch_dir == str(expected_member_dir)

    def test_arrived_conversation_is_listed_as_recoverable(self, conv_env):
        """A conversation that crossed is offered as something to bring back:
        it appears in the peer's recoverable listing, keyed by the id it
        arrived under.

        The mirror half — that it is absent from the LIVE listing — is not
        asserted here. Nothing in this fixture can put a session into the live
        listing, so that listing is always empty and such an assertion would
        hold for any id at all, including one that never crossed. Liveness is
        pinned where it can actually vary, in the recovery layer's own tests,
        which drive both a live and a dead record through the same
        subtraction.
        """
        from pathlib import PurePosixPath

        c = conv_env
        session_id = "33333333-3333-4333-8333-333333333333"

        _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("."), marker="live-vs-dead-marker"
        )

        assert session_id in {row["session_id"] for row in _recoverable_rows(c["peer_cli_env"])}

    def test_every_recoverable_conversation_the_peer_lists_is_resumable(self, conv_env):
        """The criterion forbidding a listed-but-unresumable conversation,
        exercised as agreement: the session id fed to `--resume` is read
        BACK OUT of the peer's own recoverable listing, never hand-written
        into the test, so the listing and the resume path cannot drift apart
        here either."""
        from pathlib import PurePosixPath

        c = conv_env
        session_id = "44444444-4444-4444-8444-444444444444"

        _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("."), marker="agreement-marker"
        )

        rows = _recoverable_rows(c["peer_cli_env"])
        assert rows, "expected at least one recoverable row to test resume-agreement against"
        for row in rows:
            resume = _run_camp(c["peer_cli_env"], "launch", "--resume", row["session_id"])
            assert resume.returncode == 0, resume.stderr

    def test_move_workspace_alone_claims_ownership_for_the_peer_but_leaves_release_and_the_flip_to_the_caller(
        self, conv_env
    ):
        """`move_workspace` drives `claim`, so by the time it returns the
        peer's own record already names the peer as owner — asserted
        directly here, never inferred.

        `move_workspace` alone is not the whole verb: it never calls
        `release_conversations` or `flip_sender_ownership`. Those are
        `camp transfer`'s CLI-layer job, run only after `move_workspace`
        returns, so immediately after it returns the sender still holds a
        resumable copy of the conversation. That boundary — ownership
        already moved, release not yet run — is what this test pins. See
        `TestTransferEndToEndThroughTheRealEntryPath` for the full verb's
        end state."""
        from pathlib import PurePosixPath

        from camp.group.manifest import manifest_path_for, owner_of, read_central_manifest

        c = conv_env
        session_id = "55555555-5555-4555-8555-555555555555"

        result, _phases, _dest = _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("."), marker="sender-unchanged-marker"
        )

        assert result.claimed_owner == "host-b"
        peer_manifest = manifest_path_for("testgroup", c["slug"], env=c["peer_env"])
        assert owner_of(read_central_manifest(peer_manifest)) == "host-b"

        sender_rows = _recoverable_rows(c["sender_cli_env"])
        assert session_id in {row["session_id"] for row in sender_rows}, sender_rows

        resume = _run_camp(c["sender_cli_env"], "launch", "--resume", session_id)
        assert resume.returncode == 0, resume.stderr
        assert resume.stdout.strip() == session_id, resume.stdout

    def test_rerun_after_a_conversations_phase_failure_converges(self, conv_env):
        """A re-run after a `conversations`-phase failure lands the
        conversation exactly once and leaves it resumable — never duplicating
        the row, and never refusing forever."""
        from pathlib import PurePosixPath

        from camp.transfer.move import PhaseFailed

        c = conv_env
        session_id = "66666666-6666-4666-8666-666666666666"

        def _corrupt_conversation_producer(argv):
            return subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'not a tarball')"],
                stdout=subprocess.PIPE,
            )

        with pytest.raises(PhaseFailed) as exc_info:
            _cross_one_conversation(
                c,
                session_id=session_id,
                subpath=PurePosixPath("."),
                marker="rerun-converges-marker",
                conversation_producer_spawn=_corrupt_conversation_producer,
            )
        assert exc_info.value.phase.startswith("conversations")

        _cross_one_conversation(
            c,
            session_id=session_id,
            subpath=PurePosixPath("."),
            marker="rerun-converges-marker",
            overwrite=True,
        )

        rows = _recoverable_rows(c["peer_cli_env"])
        matching = [row for row in rows if row["session_id"] == session_id]
        assert len(matching) == 1, rows

        resume = _run_camp(c["peer_cli_env"], "launch", "--resume", session_id)
        assert resume.returncode == 0, resume.stderr


def _sender_locate(c: dict):
    """A `locate_transcript` callable against the SENDER's own real harness
    store, mirroring `_cross_one_conversation`'s own `_locate` closure."""
    from trailhead.harness.claude_code import ClaudeCodeHarness

    harness = ClaudeCodeHarness()
    sender_claude_env = {"TRAILHEAD_CLAUDE_DIR": str(c["sender_claude_dir"])}

    def _locate(session_id: str, root):
        return harness.session_transcript_path(session_id, root, env=sender_claude_env)

    return _locate


class TestReleaseOnARealPeer:
    """`camp.transfer.release` driven directly against `conv_env`'s real
    sender harness store, exactly as `move_workspace` is driven directly in
    `TestConversationResumesOnARealPeer` above rather than through the full
    `camp transfer` CLI — the release module has no CLI entry point of its
    own; `camp transfer`'s wiring to it is covered separately, with
    `move_workspace` faked, in the CLI-wiring section of this file."""

    def test_after_release_the_sender_no_longer_lists_or_resumes_it(self, conv_env):
        from pathlib import PurePosixPath

        from camp.group.manifest import workspace_dir
        from camp.transfer.release import ReleaseOutcome, release_conversations

        c = conv_env
        session_id = "77777777-7777-4777-8777-777777777777"

        result, _phases, _dest = _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("."), marker="release-listing-marker"
        )

        # Before release, the sender still offers and resumes it (pinned
        # directly by
        # `test_move_workspace_alone_claims_ownership_for_the_peer_but_leaves_release_and_the_flip_to_the_caller`
        # above) — the change this test pins is what happens AFTER release.
        sender_ws_root = workspace_dir("testgroup", c["slug"], env=c["sender_env"]).resolve()
        release_results = release_conversations(
            group="testgroup",
            slug=c["slug"],
            workspace_root=sender_ws_root,
            conversations=result.conversations,
            locate_transcript=_sender_locate(c),
            env=c["sender_env"],
        )
        assert release_results[0].outcome is ReleaseOutcome.ARCHIVED

        sender_rows = _recoverable_rows(c["sender_cli_env"])
        assert session_id not in {row["session_id"] for row in sender_rows}, sender_rows

        resume = _run_camp(c["sender_cli_env"], "launch", "--resume", session_id)
        assert resume.returncode != 0, resume.stdout

    def test_archived_bytes_match_and_the_peer_is_unaffected(self, conv_env):
        """The peer's own copy (already pinned resumable by
        `TestConversationResumesOnARealPeer`) is untouched by the sender's
        release — this test drives the release and then checks the peer
        again, rather than assuming a sender-local operation cannot reach it."""
        from pathlib import PurePosixPath

        from camp.group.manifest import workspace_dir
        from camp.transfer.release import ReleaseOutcome, release_conversations

        c = conv_env
        session_id = "77777777-7777-4777-8777-777777777776"
        marker = "archived-bytes-marker-e21c"

        result, _phases, sender_transcript = _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("."), marker=marker
        )
        before_bytes = sender_transcript.read_bytes()

        sender_ws_root = workspace_dir("testgroup", c["slug"], env=c["sender_env"]).resolve()
        release_results = release_conversations(
            group="testgroup",
            slug=c["slug"],
            workspace_root=sender_ws_root,
            conversations=result.conversations,
            locate_transcript=_sender_locate(c),
            env=c["sender_env"],
        )
        assert release_results[0].outcome is ReleaseOutcome.ARCHIVED
        assert release_results[0].archive_path.read_bytes() == before_bytes

        peer_rows = _recoverable_rows(c["peer_cli_env"])
        assert session_id in {row["session_id"] for row in peer_rows}, peer_rows
        resume = _run_camp(c["peer_cli_env"], "launch", "--resume", session_id)
        assert resume.returncode == 0, resume.stderr

    def test_archive_sits_outside_the_workspace_tree_and_the_harness_store(self, conv_env):
        """Proven by running the two real sweeps that could otherwise pick
        the archive back up: the working-tree content walk
        (`camp.transfer.worktree.write_archive`, over the sender's real
        worktree) and conversation enumeration (the sender's real harness
        store scan) — neither reports it."""
        import io
        import tarfile
        from pathlib import PurePosixPath

        from camp.group.manifest import workspace_dir
        from camp.transfer.release import archive_dir, release_conversations
        from camp.transfer.worktree import write_archive
        from trailhead.harness.claude_code import ClaudeCodeHarness

        c = conv_env
        session_id = "77777777-7777-4777-8777-777777777775"

        result, _phases, _dest = _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("."), marker="archive-location-marker"
        )

        sender_ws_root = workspace_dir("testgroup", c["slug"], env=c["sender_env"]).resolve()
        release_conversations(
            group="testgroup",
            slug=c["slug"],
            workspace_root=sender_ws_root,
            conversations=result.conversations,
            locate_transcript=_sender_locate(c),
            env=c["sender_env"],
        )

        archive_root = archive_dir("testgroup", c["slug"], env=c["sender_env"])
        assert archive_root.is_dir()
        assert not archive_root.is_relative_to(sender_ws_root)
        assert not archive_root.is_relative_to(Path(c["sender_claude_dir"]).resolve())

        buf = io.BytesIO()
        write_archive(c["wt_path"], (), buf)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r|") as tf:
            worktree_member_names = [m.name for m in tf]
        assert not any(session_id in name for name in worktree_member_names)

        sender_claude_env = {"TRAILHEAD_CLAUDE_DIR": str(c["sender_claude_dir"])}
        remaining = ClaudeCodeHarness().session_transcripts(env=sender_claude_env)
        assert session_id not in {t.session_id for t in remaining}, remaining

    def test_rerunning_release_after_a_successful_archive_does_not_duplicate(self, conv_env):
        from pathlib import PurePosixPath

        from camp.group.manifest import workspace_dir
        from camp.transfer.release import (
            ReleaseOutcome,
            read_release_marker,
            release_conversations,
        )

        c = conv_env
        session_id = "77777777-7777-4777-8777-777777777774"

        result, _phases, _dest = _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("."), marker="rerun-marker"
        )

        sender_ws_root = workspace_dir("testgroup", c["slug"], env=c["sender_env"]).resolve()
        locate = _sender_locate(c)

        first = release_conversations(
            group="testgroup",
            slug=c["slug"],
            workspace_root=sender_ws_root,
            conversations=result.conversations,
            locate_transcript=locate,
            env=c["sender_env"],
        )
        assert first[0].outcome is ReleaseOutcome.ARCHIVED

        second = release_conversations(
            group="testgroup",
            slug=c["slug"],
            workspace_root=sender_ws_root,
            conversations=result.conversations,
            locate_transcript=locate,
            env=c["sender_env"],
        )
        assert second[0].outcome is ReleaseOutcome.ALREADY_ARCHIVED

        marker = read_release_marker("testgroup", c["slug"], env=c["sender_env"])
        matching = [entry for entry in marker if entry["session_id"] == session_id]
        assert len(matching) == 1, marker

    def test_second_outbound_release_of_a_round_tripped_conversation_leaves_nothing_resumable(
        self, conv_env
    ):
        """A→B→A→B: the conversation is archived once (first outbound leg),
        then a fresh copy lands back on the sender (the round trip's return
        leg — reproduced here the same way `_seed_conversation` seeds any
        arriving conversation, since the defect this pins lives entirely in
        `release_conversations`'s idempotency check and not in the transport
        that lands the returning copy), then the sender sends it outbound a
        second time. The old destination-only check finds the stale archive
        from the first leg, short-circuits to ALREADY_ARCHIVED, and never
        looks at the freshly-returned live copy — leaving it sitting
        resumable while the peer believes it owns the workspace. Proven
        through camp's own listing and resume paths, exactly like
        `test_after_release_the_sender_no_longer_lists_or_resumes_it` above."""
        from pathlib import PurePosixPath

        from camp.group.manifest import workspace_dir
        from camp.transfer.move import ConversationCrossed
        from camp.transfer.release import ReleaseOutcome, release_conversations

        c = conv_env
        session_id = "77777777-7777-4777-8777-777777777773"
        crossed = (ConversationCrossed(session_id=session_id, subpath=PurePosixPath(".")),)
        sender_ws_root = workspace_dir("testgroup", c["slug"], env=c["sender_env"]).resolve()
        locate = _sender_locate(c)

        # Leg 1: outbound A -> B, then archived on A.
        _seed_conversation(c, session_id=session_id, marker="round-trip-leg-1")
        first = release_conversations(
            group="testgroup",
            slug=c["slug"],
            workspace_root=sender_ws_root,
            conversations=crossed,
            locate_transcript=locate,
            env=c["sender_env"],
        )
        assert first[0].outcome is ReleaseOutcome.ARCHIVED

        # Leg 2: the round trip's return, B -> A — a fresh, live, resumable
        # copy lands back on the sender's own harness store.
        _seed_conversation(c, session_id=session_id, marker="round-trip-leg-2-returned")
        assert session_id in {row["session_id"] for row in _recoverable_rows(c["sender_cli_env"])}

        # Leg 3: outbound A -> B again.
        second = release_conversations(
            group="testgroup",
            slug=c["slug"],
            workspace_root=sender_ws_root,
            conversations=crossed,
            locate_transcript=locate,
            env=c["sender_env"],
        )
        assert second[0].outcome is ReleaseOutcome.ARCHIVED

        sender_rows = _recoverable_rows(c["sender_cli_env"])
        assert session_id not in {row["session_id"] for row in sender_rows}, sender_rows

        resume = _run_camp(c["sender_cli_env"], "launch", "--resume", session_id)
        assert resume.returncode != 0, resume.stdout


class TestMoveWorkspaceEndToEnd:
    def test_content_crosses_in_phase_order_against_a_real_peer(self, move_env):
        from camp.provision.reconcile import _worktree_path
        from camp.transfer.move import move_workspace

        g = move_env
        phases: list[str] = []

        result = move_workspace(
            host=g["host"],
            group=g["group"],
            group_name="testgroup",
            slug=g["slug"],
            sender_name="host-a",
            overwrite=False,
            on_phase=phases.append,
            env=g["sender_env"],
            run=g["run"],
            stream_spawn=g["stream_spawn"],
        )

        assert phases == ["begin", "history: repo_a", "worktree: repo_a", "claim", "finish"]
        assert result.members == ("repo_a",)
        assert result.claimed_owner == "host-b"

        landed = _git_out(g["peer_repo"], "rev-parse", f"refs/heads/{g['branch']}")
        sender_tip = _git_out(g["wt_path"], "rev-parse", "HEAD")
        assert landed == sender_tip

        peer_wt = _worktree_path("testgroup", g["slug"], "repo_a", env=g["peer_env"])
        assert (peer_wt / "committed.txt").read_text() == "committed on the sender\n"
        assert (peer_wt / "untracked.txt").read_text() == "never committed\n"

        from camp.group.manifest import manifest_path_for, owner_of, read_central_manifest

        peer_manifest = manifest_path_for("testgroup", g["slug"], env=g["peer_env"])
        assert owner_of(read_central_manifest(peer_manifest)) == "host-b"

    def test_failure_then_overwrite_refusal_then_successful_rerun(
        self, move_env, tmp_path: Path
    ):
        from camp.provision.reconcile import _worktree_path
        from camp.transfer.move import OverwriteNeeded, PhaseFailed, move_workspace

        g = move_env

        def _corrupt_history_producer(argv):
            return subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'not a bundle')"],
                stdout=subprocess.PIPE,
            )

        def _boom_worktree_producer(argv):
            raise AssertionError("worktree must never run after history failed")

        with pytest.raises(PhaseFailed) as exc_info:
            move_workspace(
                host=g["host"],
                group=g["group"],
                group_name="testgroup",
                slug=g["slug"],
                sender_name="host-a",
                overwrite=False,
                env=g["sender_env"],
                run=g["run"],
                stream_spawn=g["stream_spawn"],
                history_producer_spawn=_corrupt_history_producer,
                worktree_producer_spawn=_boom_worktree_producer,
            )
        assert exc_info.value.phase == "history (repo_a)"

        from camp.group.manifest import manifest_path_for

        peer_manifest = manifest_path_for("testgroup", g["slug"], env=g["peer_env"])
        assert peer_manifest.is_file(), "begin must have seeded the manifest before history failed"

        def _snapshot(root: Path) -> dict[str, bytes]:
            return {
                str(p.relative_to(root)): p.read_bytes()
                for p in sorted(root.rglob("*"))
                if p.is_file()
            }

        before_refusal = _snapshot(g["peer_state"])
        with pytest.raises(OverwriteNeeded) as overwrite_exc:
            move_workspace(
                host=g["host"],
                group=g["group"],
                group_name="testgroup",
                slug=g["slug"],
                sender_name="host-a",
                overwrite=False,
                env=g["sender_env"],
                run=g["run"],
                stream_spawn=g["stream_spawn"],
            )
        assert "--overwrite" in overwrite_exc.value.detail
        after_refusal = _snapshot(g["peer_state"])
        assert before_refusal == after_refusal

        result = move_workspace(
            host=g["host"],
            group=g["group"],
            group_name="testgroup",
            slug=g["slug"],
            sender_name="host-a",
            overwrite=True,
            env=g["sender_env"],
            run=g["run"],
            stream_spawn=g["stream_spawn"],
        )
        assert result.members == ("repo_a",)

        landed = _git_out(g["peer_repo"], "rev-parse", f"refs/heads/{g['branch']}")
        sender_tip = _git_out(g["wt_path"], "rev-parse", "HEAD")
        assert landed == sender_tip
        peer_wt = _worktree_path("testgroup", g["slug"], "repo_a", env=g["peer_env"])
        assert (peer_wt / "committed.txt").read_text() == "committed on the sender\n"

    def test_conversation_crosses_after_worktree_and_lands_rewritten_on_the_peer(
        self, move_env
    ):
        """The `conversations` phase runs after the last `worktree` phase and
        before `finish` — pinned by the recorded order of `on_phase` calls,
        not by reading the source — and the conversation that crosses lands
        on the peer with its recorded root rewritten to the peer's own
        worktree, exactly as `camp.transfer.receive.conversations` (already
        built) does for any caller."""
        from pathlib import PurePosixPath

        from camp.group.manifest import workspace_dir
        from camp.transfer.conversations import WorkspaceConversation
        from camp.transfer.move import move_workspace
        from trailhead.harness.claude_code import ClaudeCodeHarness

        g = move_env
        session_id = "11111111-1111-4111-8111-111111111111"
        sender_ws_root = workspace_dir("testgroup", g["slug"], env=g["sender_env"])
        sender_transcript = g["tmp_path"] / "sender-transcript.jsonl"
        sender_transcript.write_text(
            json.dumps({"cwd": str(sender_ws_root), "type": "summary"}) + "\n"
        )

        conversation = WorkspaceConversation(
            session_id=session_id,
            subpath=PurePosixPath("."),
            live=False,
            unresolved=False,
        )

        phases: list[str] = []
        result = move_workspace(
            host=g["host"],
            group=g["group"],
            group_name="testgroup",
            slug=g["slug"],
            sender_name="host-a",
            overwrite=False,
            on_phase=phases.append,
            env=g["sender_env"],
            run=g["run"],
            stream_spawn=g["stream_spawn"],
            conversations=(conversation,),
            locate_transcript=lambda sid, root: sender_transcript,
        )

        assert phases == [
            "begin",
            "history: repo_a",
            "worktree: repo_a",
            "conversations",
            "claim",
            "finish",
        ]
        assert result.conversations == (
            _move_module().ConversationCrossed(
                session_id=session_id, subpath=PurePosixPath(".")
            ),
        )

        peer_ws_root = workspace_dir("testgroup", g["slug"], env=g["peer_env"])
        harness = ClaudeCodeHarness()
        landed = harness.session_transcript_path(session_id, peer_ws_root, env=g["peer_env"])
        assert landed is not None
        record = json.loads(landed.read_text().splitlines()[0])
        assert record["cwd"] == str(peer_ws_root.resolve())
        assert record["type"] == "summary"

    def test_unresolved_conversation_fails_the_named_conversations_phase(self, move_env):
        """A row the enumeration reported UNRESOLVED must never reach the
        completion report — it fails the `conversations` phase by name,
        the same re-runnable shape every other phase failure uses, rather
        than silently completing the move without it."""
        from camp.transfer.conversations import WorkspaceConversation
        from camp.transfer.move import PhaseFailed, move_workspace

        g = move_env
        conversation = WorkspaceConversation(
            session_id="22222222-2222-4222-8222-222222222222",
            subpath=None,
            live=False,
            unresolved=True,
        )

        with pytest.raises(PhaseFailed) as exc_info:
            move_workspace(
                host=g["host"],
                group=g["group"],
                group_name="testgroup",
                slug=g["slug"],
                sender_name="host-a",
                overwrite=False,
                env=g["sender_env"],
                run=g["run"],
                stream_spawn=g["stream_spawn"],
                conversations=(conversation,),
                locate_transcript=lambda sid, root: None,
            )

        assert exc_info.value.phase == "conversations"
        assert "22222222-2222-4222-8222-222222222222" in exc_info.value.detail

    def test_finish_failure_after_a_successful_claim_carries_the_claimed_owner(
        self, move_env
    ):
        """`claim` runs for real against the real peer and answers before
        `finish` is ever called — this test fails `finish` alone, so the
        raised `PhaseFailed` must carry the exact owner name `claim`
        answered with, proving the peer's manifest and this exception agree
        about who owns the workspace now."""
        from camp.host.transport import RawResult
        from camp.transfer.move import PhaseFailed, move_workspace

        g = move_env
        real_run = g["run"]

        def _fail_finish_only(argv, execution_timeout, env):
            if "finish" in argv[-1]:
                return RawResult(
                    stdout="", stderr="simulated bring-up crash", exit_code=1
                )
            return real_run(argv, execution_timeout, env)

        with pytest.raises(PhaseFailed) as exc_info:
            move_workspace(
                host=g["host"],
                group=g["group"],
                group_name="testgroup",
                slug=g["slug"],
                sender_name="host-a",
                overwrite=False,
                env=g["sender_env"],
                run=_fail_finish_only,
                stream_spawn=g["stream_spawn"],
            )

        assert exc_info.value.phase == "finish"
        assert exc_info.value.claimed_owner == "host-b"

        from camp.group.manifest import manifest_path_for, owner_of, read_central_manifest

        peer_manifest = manifest_path_for("testgroup", g["slug"], env=g["peer_env"])
        assert owner_of(read_central_manifest(peer_manifest)) == "host-b"


def test_move_workspace_reports_each_phase_before_a_slow_one_completes():
    """A slow `finish` proves the ordering contract mechanically: `on_phase`
    for every phase, `finish` included, has already fired while the remote
    call for `finish` is still blocked — not merely that the labels end up
    in the right order once everything has returned."""
    import json as json_mod

    from camp.host.config import Host
    from camp.host.transport import RawResult
    from camp.transfer.move import move_workspace

    finish_started = threading.Event()
    release_finish = threading.Event()

    def _run(argv, execution_timeout, env):
        remote_command = argv[-1]
        if "finish" in remote_command:
            finish_started.set()
            assert release_finish.wait(timeout=5), "test deadlocked waiting to release finish"
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "manifest_path": "x"}),
                stderr="",
                exit_code=0,
            )
        if "claim" in remote_command:
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "owner": "host-b"}),
                stderr="",
                exit_code=0,
            )
        return RawResult(
            stdout=json_mod.dumps(
                {"contract_version": 1, "members": [{"name": "repo_a", "basis_commit": None}]}
            ),
            stderr="",
            exit_code=0,
        )

    def _tiny_producer(argv):
        return subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x')"],
            stdout=subprocess.PIPE,
        )

    def _fast_stream_spawn(argv, env):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,json; sys.stdin.buffer.read(); "
                "sys.stdout.write(json.dumps({'contract_version': 1}))",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    phases: list[str] = []
    phases_lock = threading.Lock()

    def _on_phase(phase: str) -> None:
        with phases_lock:
            phases.append(phase)

    group = {
        "group": {"name": "testgroup"},
        "members": [{"name": "repo_a", "repo_root": "/nonexistent", "excluded": []}],
        "branch_pattern": "worktree-{slug}",
    }

    result_box: dict = {}

    def _drive():
        result_box["result"] = move_workspace(
            host=Host(ssh="fake-peer", camp_bin="/opt/camp/bin/camp"),
            group=group,
            group_name="testgroup",
            slug="feat-slow",
            sender_name="host-a",
            overwrite=False,
            on_phase=_on_phase,
            run=_run,
            stream_spawn=_fast_stream_spawn,
            history_producer_spawn=_tiny_producer,
            worktree_producer_spawn=_tiny_producer,
        )

    thread = threading.Thread(target=_drive)
    thread.start()
    try:
        assert finish_started.wait(timeout=5), "finish phase never started"
        with phases_lock:
            snapshot = list(phases)
        assert snapshot == ["begin", "history: repo_a", "worktree: repo_a", "claim", "finish"]
    finally:
        release_finish.set()
        thread.join(timeout=5)

    assert result_box["result"].members == ("repo_a",)


def test_conversations_phase_announced_before_its_own_slow_transport_completes():
    """`on_phase("conversations")` fires between the `worktree` phase and
    `finish` — and, mechanically like the `finish` case above, BEFORE the
    conversations transport call it precedes has returned, so a caller
    rendering each label as it arrives never sits through a silent wait
    while a transcript streams."""
    import json as json_mod
    from pathlib import PurePosixPath

    from camp.host.config import Host
    from camp.host.transport import RawResult
    from camp.transfer.conversations import WorkspaceConversation
    from camp.transfer.move import move_workspace

    conversations_started = threading.Event()
    release_conversations = threading.Event()

    def _run(argv, execution_timeout, env):
        remote_command = argv[-1]
        if "finish" in remote_command:
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "manifest_path": "x"}),
                stderr="",
                exit_code=0,
            )
        if "claim" in remote_command:
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "owner": "host-b"}),
                stderr="",
                exit_code=0,
            )
        return RawResult(
            stdout=json_mod.dumps(
                {"contract_version": 1, "members": [{"name": "repo_a", "basis_commit": None}]}
            ),
            stderr="",
            exit_code=0,
        )

    def _tiny_producer(argv):
        return subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x')"],
            stdout=subprocess.PIPE,
        )

    def _fast_stream_spawn(argv, env):
        remote_command = argv[-1]
        if "conversations" in remote_command:
            conversations_started.set()
            assert release_conversations.wait(timeout=5), (
                "test deadlocked waiting to release conversations"
            )
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,json; sys.stdin.buffer.read(); "
                "sys.stdout.write(json.dumps({'contract_version': 1}))",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    phases: list[str] = []
    phases_lock = threading.Lock()

    def _on_phase(phase: str) -> None:
        with phases_lock:
            phases.append(phase)

    group = {
        "group": {"name": "testgroup"},
        "members": [{"name": "repo_a", "repo_root": "/nonexistent", "excluded": []}],
        "branch_pattern": "worktree-{slug}",
    }

    def _drive(transcript_path):
        return move_workspace(
            host=Host(ssh="fake-peer", camp_bin="/opt/camp/bin/camp"),
            group=group,
            group_name="testgroup",
            slug="feat-slow-conv",
            sender_name="host-a",
            overwrite=False,
            on_phase=_on_phase,
            run=_run,
            stream_spawn=_fast_stream_spawn,
            history_producer_spawn=_tiny_producer,
            worktree_producer_spawn=_tiny_producer,
            conversation_producer_spawn=_tiny_producer,
            conversations=(
                WorkspaceConversation(
                    session_id="44444444-4444-4444-8444-444444444444",
                    subpath=PurePosixPath("."),
                    live=False,
                    unresolved=False,
                ),
            ),
            locate_transcript=lambda sid, root: transcript_path,
        )

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".jsonl") as tf:
        tf.write(b'{"cwd": "/whatever"}\n')
        tf.flush()

        result_box: dict = {}
        thread = threading.Thread(
            target=lambda: result_box.__setitem__("result", _drive(Path(tf.name)))
        )
        thread.start()
        try:
            assert conversations_started.wait(timeout=5), "conversations phase never started"
            with phases_lock:
                snapshot = list(phases)
            assert snapshot == ["begin", "history: repo_a", "worktree: repo_a", "conversations"]
        finally:
            release_conversations.set()
            thread.join(timeout=5)

    with phases_lock:
        final = list(phases)
    assert final == [
        "begin",
        "history: repo_a",
        "worktree: repo_a",
        "conversations",
        "claim",
        "finish",
    ]


def _claim_fixture_group_and_host():
    from camp.host.config import Host

    group = {
        "group": {"name": "testgroup"},
        "members": [{"name": "repo_a", "repo_root": "/nonexistent", "excluded": []}],
        "branch_pattern": "worktree-{slug}",
    }
    host = Host(ssh="fake-peer", camp_bin="/opt/camp/bin/camp")
    return group, host


def test_claim_phase_announced_before_its_own_network_call_completes():
    """`on_phase("claim")` fires — mechanically, like `finish` and
    `conversations` above — BEFORE the remote call it precedes has
    returned, not merely once the whole move has finished."""
    import json as json_mod

    from camp.host.transport import RawResult
    from camp.transfer.move import move_workspace

    claim_started = threading.Event()
    release_claim = threading.Event()

    def _run(argv, execution_timeout, env):
        remote_command = argv[-1]
        if "claim" in remote_command:
            claim_started.set()
            assert release_claim.wait(timeout=5), "test deadlocked waiting to release claim"
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "owner": "host-b"}),
                stderr="",
                exit_code=0,
            )
        if "finish" in remote_command:
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "manifest_path": "x"}),
                stderr="",
                exit_code=0,
            )
        return RawResult(
            stdout=json_mod.dumps(
                {"contract_version": 1, "members": [{"name": "repo_a", "basis_commit": None}]}
            ),
            stderr="",
            exit_code=0,
        )

    def _tiny_producer(argv):
        return subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x')"],
            stdout=subprocess.PIPE,
        )

    def _fast_stream_spawn(argv, env):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,json; sys.stdin.buffer.read(); "
                "sys.stdout.write(json.dumps({'contract_version': 1}))",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    phases: list[str] = []
    phases_lock = threading.Lock()

    def _on_phase(phase: str) -> None:
        with phases_lock:
            phases.append(phase)

    group, host = _claim_fixture_group_and_host()

    result_box: dict = {}

    def _drive():
        result_box["result"] = move_workspace(
            host=host,
            group=group,
            group_name="testgroup",
            slug="feat-slow-x",
            sender_name="host-a",
            overwrite=False,
            on_phase=_on_phase,
            run=_run,
            stream_spawn=_fast_stream_spawn,
            history_producer_spawn=_tiny_producer,
            worktree_producer_spawn=_tiny_producer,
        )

    thread = threading.Thread(target=_drive)
    thread.start()
    try:
        assert claim_started.wait(timeout=5), "claim phase never started"
        with phases_lock:
            snapshot = list(phases)
        assert snapshot == ["begin", "history: repo_a", "worktree: repo_a", "claim"]
    finally:
        release_claim.set()
        thread.join(timeout=5)

    assert result_box["result"].claimed_owner == "host-b"


def test_claim_phase_failure_is_named_and_never_swallowed_into_success():
    """A claim that cannot be completed — the peer's own explicit refusal —
    surfaces as its own named phase failure, and `finish` is never reached."""
    from camp.host.transport import RawResult
    from camp.transfer.move import PhaseFailed, move_workspace

    finish_calls: list[str] = []

    def _run(argv, execution_timeout, env):
        import json as json_mod

        remote_command = argv[-1]
        if "claim" in remote_command:
            return RawResult(
                stdout="",
                stderr="camp transfer-receive: this host has not declared a self_name",
                exit_code=1,
            )
        if "finish" in remote_command:
            finish_calls.append(remote_command)
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "manifest_path": "x"}),
                stderr="",
                exit_code=0,
            )
        return RawResult(
            stdout=json_mod.dumps(
                {"contract_version": 1, "members": [{"name": "repo_a", "basis_commit": None}]}
            ),
            stderr="",
            exit_code=0,
        )

    def _tiny_producer(argv):
        return subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x')"],
            stdout=subprocess.PIPE,
        )

    def _fast_stream_spawn(argv, env):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,json; sys.stdin.buffer.read(); "
                "sys.stdout.write(json.dumps({'contract_version': 1}))",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    group, host = _claim_fixture_group_and_host()

    with pytest.raises(PhaseFailed) as exc_info:
        move_workspace(
            host=host,
            group=group,
            group_name="testgroup",
            slug="feat-fail-x",
            sender_name="host-a",
            overwrite=False,
            run=_run,
            stream_spawn=_fast_stream_spawn,
            history_producer_spawn=_tiny_producer,
            worktree_producer_spawn=_tiny_producer,
        )

    assert exc_info.value.phase == "claim"
    assert "self_name" in exc_info.value.detail
    assert not finish_calls, "finish must never run after claim failed"


def test_claim_phase_fails_closed_against_a_peer_that_does_not_know_the_subcommand():
    """A peer running an older camp build refuses `claim` the same way it
    refuses any other unrecognized phase — the SAME generic mechanism a
    real old dispatcher's `_cmd_transfer_receive_cli` produces — and ownership
    moves on neither host: the move raises before `finish` runs at all."""
    from camp.transfer.move import PhaseFailed, move_workspace

    old_build_phases = "', '".join(("begin", "conversations", "finish", "history", "worktree"))
    old_build_stderr = (
        f"camp transfer-receive: a phase of '{old_build_phases}' is required, got 'claim'"
    )

    finish_calls: list[str] = []

    def _run(argv, execution_timeout, env):
        import json as json_mod
        from camp.host.transport import RawResult

        remote_command = argv[-1]
        if "claim" in remote_command:
            return RawResult(stdout="", stderr=old_build_stderr, exit_code=1)
        if "finish" in remote_command:
            finish_calls.append(remote_command)
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "manifest_path": "x"}),
                stderr="",
                exit_code=0,
            )
        return RawResult(
            stdout=json_mod.dumps(
                {"contract_version": 1, "members": [{"name": "repo_a", "basis_commit": None}]}
            ),
            stderr="",
            exit_code=0,
        )

    def _tiny_producer(argv):
        return subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x')"],
            stdout=subprocess.PIPE,
        )

    def _fast_stream_spawn(argv, env):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,json; sys.stdin.buffer.read(); "
                "sys.stdout.write(json.dumps({'contract_version': 1}))",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    group, host = _claim_fixture_group_and_host()

    with pytest.raises(PhaseFailed) as exc_info:
        move_workspace(
            host=host,
            group=group,
            group_name="testgroup",
            slug="feat-oldpeer-x",
            sender_name="host-a",
            overwrite=False,
            run=_run,
            stream_spawn=_fast_stream_spawn,
            history_producer_spawn=_tiny_producer,
            worktree_producer_spawn=_tiny_producer,
        )

    assert exc_info.value.phase == "claim"
    assert "got 'claim'" in exc_info.value.detail
    assert not finish_calls, "finish must never run when the peer refused claim"


def test_a_finish_refusal_whose_stderr_mentions_overwrite_is_not_promoted_to_overwrite_needed():
    """The `--overwrite` substring match that turns a `begin` refusal into
    `OverwriteNeeded` is meaningful only for `begin`, the phase that flag
    actually governs. `claim` has already answered by the time `finish`
    runs, so a `finish` refusal whose stderr happens to mention the flag —
    for any incidental reason — must still surface as the ordinary
    post-commit `PhaseFailed`, never as `OverwriteNeeded`: that exception
    is caught nowhere past `claim`, so promoting it here would escape
    `move_workspace` entirely and read to the caller as "nothing crossed"
    when ownership had, in fact, already moved."""
    import json as json_mod

    from camp.host.transport import RawResult
    from camp.transfer.move import PhaseFailed, move_workspace

    def _run(argv, execution_timeout, env):
        remote_command = argv[-1]
        if "claim" in remote_command:
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "owner": "host-b"}),
                stderr="",
                exit_code=0,
            )
        if "finish" in remote_command:
            return RawResult(
                stdout="",
                stderr="camp transfer-receive: pass --overwrite to proceed",
                exit_code=1,
            )
        return RawResult(
            stdout=json_mod.dumps(
                {"contract_version": 1, "members": [{"name": "repo_a", "basis_commit": None}]}
            ),
            stderr="",
            exit_code=0,
        )

    def _tiny_producer(argv):
        return subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x')"],
            stdout=subprocess.PIPE,
        )

    def _fast_stream_spawn(argv, env):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,json; sys.stdin.buffer.read(); "
                "sys.stdout.write(json.dumps({'contract_version': 1}))",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    group, host = _claim_fixture_group_and_host()

    with pytest.raises(PhaseFailed) as exc_info:
        move_workspace(
            host=host,
            group=group,
            group_name="testgroup",
            slug="feat-lastphase-overwrite-x",
            sender_name="host-a",
            overwrite=False,
            run=_run,
            stream_spawn=_fast_stream_spawn,
            history_producer_spawn=_tiny_producer,
            worktree_producer_spawn=_tiny_producer,
        )

    assert exc_info.value.phase == "finish"
    assert exc_info.value.claimed_owner == "host-b"


def test_claim_failure_detail_distinguishes_a_timeout_from_an_explicit_refusal():
    """The same `PhaseFailed("claim", ...)` shape carries a materially
    different `.detail` depending on WHY the peer's claim failed — the
    peer's own stderr for an explicit refusal, versus a distinct
    "no response" message for a timeout — so an operator (and any remedy
    text keyed on it) can tell the two apart."""
    import subprocess as subprocess_mod

    from camp.transfer.move import PhaseFailed, move_workspace

    def _make_run(*, timeout: bool, stderr: str = ""):
        def _run(argv, execution_timeout, env):
            import json as json_mod
            from camp.host.transport import RawResult

            remote_command = argv[-1]
            if "claim" in remote_command:
                if timeout:
                    raise subprocess_mod.TimeoutExpired(cmd=argv, timeout=execution_timeout)
                return RawResult(stdout="", stderr=stderr, exit_code=1)
            return RawResult(
                stdout=json_mod.dumps(
                    {"contract_version": 1, "members": [{"name": "repo_a", "basis_commit": None}]}
                ),
                stderr="",
                exit_code=0,
            )

        return _run

    def _tiny_producer(argv):
        return subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x')"],
            stdout=subprocess.PIPE,
        )

    def _fast_stream_spawn(argv, env):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,json; sys.stdin.buffer.read(); "
                "sys.stdout.write(json.dumps({'contract_version': 1}))",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    group, host = _claim_fixture_group_and_host()

    def _drive(run):
        with pytest.raises(PhaseFailed) as exc_info:
            move_workspace(
                host=host,
                group=group,
                group_name="testgroup",
                slug="feat-detail-x",
                sender_name="host-a",
                overwrite=False,
                run=run,
                stream_spawn=_fast_stream_spawn,
                history_producer_spawn=_tiny_producer,
                worktree_producer_spawn=_tiny_producer,
            )
        return exc_info.value.detail

    refusal_detail = _drive(
        _make_run(timeout=False, stderr="camp transfer-receive: refused explicitly")
    )
    timeout_detail = _drive(_make_run(timeout=True))

    assert "refused explicitly" in refusal_detail
    assert "no response within" in timeout_detail
    assert refusal_detail != timeout_detail


def _make_claim_timeout_run(*, probe_answer_kwargs: dict | None):
    """A `Runner` whose `claim` invocation times out (`StoppedResponding` —
    the connection completed, so the peer may have actually run `claim`
    before the response was lost) and whose `transfer-probe` invocation, if
    the fix re-probes, answers with *probe_answer_kwargs* — `None` means the
    probe itself is unreachable, so the re-probe cannot resolve anything
    either."""
    import subprocess as subprocess_mod

    from camp.host.transport import RawResult

    def _run(argv, execution_timeout, env):
        import json as json_mod

        remote_command = argv[-1]
        if "transfer-receive" in remote_command and "claim" in remote_command:
            raise subprocess_mod.TimeoutExpired(cmd=argv, timeout=execution_timeout)
        if "transfer-probe" in remote_command:
            if probe_answer_kwargs is None:
                raise subprocess_mod.TimeoutExpired(cmd=argv, timeout=execution_timeout)
            payload = {
                "self_name": "host-b",
                "group_configured": True,
                "members": [],
                "account": None,
                "workspace_exists": True,
                "workspace_owner": None,
                "contract_version": 1,
            }
            payload.update(probe_answer_kwargs)
            return RawResult(stdout=json_mod.dumps(payload), stderr="", exit_code=0)
        if "finish" in remote_command:
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "manifest_path": "x"}),
                stderr="",
                exit_code=0,
            )
        return RawResult(
            stdout=json_mod.dumps(
                {"contract_version": 1, "members": [{"name": "repo_a", "basis_commit": None}]}
            ),
            stderr="",
            exit_code=0,
        )

    return _run


def _drive_claim_timeout(run):
    from camp.transfer.move import PhaseFailed, move_workspace

    group, host = _claim_fixture_group_and_host()

    def _tiny_producer(argv):
        return subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x')"],
            stdout=subprocess.PIPE,
        )

    def _fast_stream_spawn(argv, env):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,json; sys.stdin.buffer.read(); "
                "sys.stdout.write(json.dumps({'contract_version': 1}))",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    with pytest.raises(PhaseFailed) as exc_info:
        move_workspace(
            host=host,
            group=group,
            group_name="testgroup",
            slug="feat-indoubt-x",
            sender_name="host-a",
            overwrite=False,
            run=run,
            stream_spawn=_fast_stream_spawn,
            history_producer_spawn=_tiny_producer,
            worktree_producer_spawn=_tiny_producer,
        )
    return exc_info.value


def test_a_timed_out_claim_that_reprobes_as_committed_is_treated_as_post_commit():
    """The peer's `transfer-probe` shows its own manifest now names ITSELF
    as owner — the `claim` that timed out actually landed — so this must be
    the post-commit shape: `claimed_owner` populated, exactly like a
    `finish` failure after a successful `claim`."""
    run = _make_claim_timeout_run(probe_answer_kwargs={"workspace_owner": "host-b"})
    failure = _drive_claim_timeout(run)

    assert failure.phase == "claim"
    assert failure.claimed_owner == "host-b"


def test_a_timed_out_claims_negative_reprobe_is_indeterminate_not_pre_commit():
    """Killing the local transport (`StoppedResponding`) does not terminate
    the remote `claim` invocation — it may still be running, blocked on the
    workspace lock its own provisioner holds for minutes. A probe reading
    "not yet owner" right afterward is therefore not proof the claim will
    never land — it can still complete moments later — so this must be
    reported `indeterminate`, never the ordinary safe-to-retry shape a
    conclusively-failed claim gets."""
    run = _make_claim_timeout_run(probe_answer_kwargs={"workspace_owner": "host-a"})
    failure = _drive_claim_timeout(run)

    assert failure.phase == "claim"
    assert failure.claimed_owner is None
    assert failure.indeterminate is True


def _make_claim_refusal_run(*, stderr: str, probe_answer_kwargs: dict | None):
    """A `Runner` whose `claim` invocation exits nonzero with *stderr*
    (`RemoteRefusal` — the ssh session completed, so the remote process
    fully ran to that exit, unlike a `StoppedResponding` timeout) and whose
    `transfer-probe` invocation, if the fix re-probes, answers with
    *probe_answer_kwargs* — `None` means the probe itself cannot be reached
    either."""
    import subprocess as subprocess_mod

    from camp.host.transport import RawResult

    def _run(argv, execution_timeout, env):
        import json as json_mod

        remote_command = argv[-1]
        if "transfer-receive" in remote_command and "claim" in remote_command:
            return RawResult(stdout="", stderr=stderr, exit_code=1)
        if "transfer-probe" in remote_command:
            if probe_answer_kwargs is None:
                raise subprocess_mod.TimeoutExpired(cmd=argv, timeout=execution_timeout)
            payload = {
                "self_name": "host-b",
                "group_configured": True,
                "members": [],
                "account": None,
                "workspace_exists": True,
                "workspace_owner": None,
                "contract_version": 1,
            }
            payload.update(probe_answer_kwargs)
            return RawResult(stdout=json_mod.dumps(payload), stderr="", exit_code=0)
        if "finish" in remote_command:
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "manifest_path": "x"}),
                stderr="",
                exit_code=0,
            )
        return RawResult(
            stdout=json_mod.dumps(
                {"contract_version": 1, "members": [{"name": "repo_a", "basis_commit": None}]}
            ),
            stderr="",
            exit_code=0,
        )

    return _run


def test_a_claim_explicitly_refused_that_reprobes_as_landed_is_treated_as_post_commit():
    """A nonzero `claim` exit (`RemoteRefusal`) is not proof the claim never
    landed — the remote process could have crashed AFTER its manifest write
    already committed. The peer's own probe showing itself as owner settles
    it: this must be treated exactly like a `finish` failure after a
    successful `claim`, never assumed safe-to-retry merely because the exit
    code was nonzero."""
    run = _make_claim_refusal_run(
        stderr="camp transfer-receive: unexpected error after writing owner",
        probe_answer_kwargs={"workspace_owner": "host-b"},
    )
    failure = _drive_claim_timeout(run)

    assert failure.phase == "claim"
    assert failure.claimed_owner == "host-b"


def test_a_claim_explicitly_refused_that_reprobes_as_not_landed_is_treated_as_pre_commit():
    """Unlike a `StoppedResponding` timeout, a `RemoteRefusal` means the
    remote process is confirmed to have fully exited — so a probe reading
    "not owner" right afterward is conclusive, not merely a snapshot of a
    claim still in flight. This is the ordinary, safe-to-retry shape."""
    run = _make_claim_refusal_run(
        stderr="camp transfer-receive: this host has not declared a self_name",
        probe_answer_kwargs={"workspace_owner": "host-a"},
    )
    failure = _drive_claim_timeout(run)

    assert failure.phase == "claim"
    assert failure.claimed_owner is None
    assert failure.indeterminate is False


def test_a_timed_out_claim_whose_reprobe_also_fails_is_reported_indeterminate():
    """Neither the original `claim` call nor the re-probe could establish
    what happened on the peer — guessing either the pre-commit or the
    post-commit remedy would be a coin flip the operator cannot verify, so
    this must be its own distinct outcome rather than silently defaulting to
    one of the other two."""
    run = _make_claim_timeout_run(probe_answer_kwargs=None)
    failure = _drive_claim_timeout(run)

    assert failure.phase == "claim"
    assert failure.claimed_owner is None
    assert failure.indeterminate is True


def test_a_claim_answer_that_is_not_parseable_json_never_raises_unhandled():
    """`claim` answered exit 0 — the remote phase ran to completion and
    already wrote itself as owner (see `camp.transfer.receive.claim`) —
    but stdout is not the expected JSON body (shell-profile noise on the
    remote is the classic cause). This must route through the normal
    `PhaseFailed` shape, re-probing to confirm the commit, never an
    unguarded `KeyError`/`json.JSONDecodeError` escaping past the commit
    point."""
    import json as json_mod

    from camp.host.transport import RawResult

    def _run(argv, execution_timeout, env):
        remote_command = argv[-1]
        if "transfer-receive" in remote_command and "claim" in remote_command:
            return RawResult(stdout="bienvenue sur bash\n", stderr="", exit_code=0)
        if "transfer-probe" in remote_command:
            payload = {
                "self_name": "host-b",
                "group_configured": True,
                "members": [],
                "account": None,
                "workspace_exists": True,
                "workspace_owner": "host-b",
                "contract_version": 1,
            }
            return RawResult(stdout=json_mod.dumps(payload), stderr="", exit_code=0)
        if "finish" in remote_command:
            return RawResult(
                stdout=json_mod.dumps({"contract_version": 1, "manifest_path": "x"}),
                stderr="",
                exit_code=0,
            )
        return RawResult(
            stdout=json_mod.dumps(
                {"contract_version": 1, "members": [{"name": "repo_a", "basis_commit": None}]}
            ),
            stderr="",
            exit_code=0,
        )

    failure = _drive_claim_timeout(_run)

    assert failure.phase == "claim"
    assert failure.claimed_owner == "host-b"


def test_a_malformed_begin_answer_raises_the_ordinary_pre_commit_phase_failure():
    """The same unguarded-parse shape on `begin` is pre-commit and
    survivable — a malformed response there must still surface as an
    ordinary `PhaseFailed("begin", ...)`, never an unhandled parse
    exception, and never with a `claimed_owner` (nothing has committed
    yet)."""
    from camp.host.transport import RawResult
    from camp.transfer.move import PhaseFailed, move_workspace

    group, host = _claim_fixture_group_and_host()

    def _run(argv, execution_timeout, env):
        remote_command = argv[-1]
        if "begin" in remote_command:
            return RawResult(stdout="not json at all", stderr="", exit_code=0)
        raise AssertionError("no phase after begin should ever be reached")

    with pytest.raises(PhaseFailed) as exc_info:
        move_workspace(
            host=host,
            group=group,
            group_name="testgroup",
            slug="feat-badbegin-x",
            sender_name="host-a",
            overwrite=False,
            run=_run,
        )

    assert exc_info.value.phase == "begin"
    assert exc_info.value.claimed_owner is None


# ---------------------------------------------------------------------------
# The moving CLI path — rendering, exit codes, and flag wiring, with
# `move.move_workspace` faked wholesale (see the section header above for why
# that observes a monkeypatch). The mover's own phase-driving behaviour is
# proven against a real peer in `TestMoveWorkspaceEndToEnd`.
# ---------------------------------------------------------------------------


def test_completion_report_names_the_claimed_owner_and_the_provisioning_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Ownership moved as part of this transfer — the completion report
    names the exact owner name the peer's `claim` phase answered, not this
    host's own alias for the peer (`--to host-b` here, `claimed_owner`
    deliberately a different string, so an assertion on the wrong value
    would catch it), and states the peer may still be provisioning, naming
    both the status check and the retry remedy."""
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(members=("repo_a",), claimed_owner="host-b-declared"),
    )

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_WOULD_TRANSFER
    out = capsys.readouterr().out
    assert "'feat-x' arrived on 'host-b'" in out
    assert "ownership moved to 'host-b-declared'" in out
    assert "still owns" not in out
    assert "peer may still be provisioning" in out
    assert "camp status --name feat-x --group trailhead" in out
    assert "camp setup" in out
    assert "credential-shaped content" in out
    assert "no conversations are rooted in this workspace" in out


def test_completion_report_names_arrived_conversations_and_the_literal_resume_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The operator reads this on the machine they just moved to, having
    forgotten where they left the work — an identifier they must turn into a
    command themselves reintroduces exactly the recall `camp launch --resume`
    exists to remove."""
    from pathlib import PurePosixPath

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    session_id = "33333333-3333-4333-8333-333333333333"
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(
            members=("repo_a",),
            conversations=(
                move.ConversationCrossed(session_id=session_id, subpath=PurePosixPath(".")),
            ),
        ),
    )

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    # `release_conversations` is not faked here, so the real one runs against
    # this test's isolated (empty) harness store — it cannot locate a
    # transcript for `session_id` there and reports FAILED, which is exactly
    # the outcome `EXIT_RELEASE_INCOMPLETE` exists to surface distinctly.
    assert code == transfer.EXIT_RELEASE_INCOMPLETE
    out = capsys.readouterr().out
    assert session_id in out
    assert f"camp launch --resume {session_id}" in out
    assert "no conversations are rooted in this workspace" not in out


def test_overwrite_needed_names_the_flag_with_its_own_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _refuse(**kw):
        raise move.OverwriteNeeded("slug 'feat-x' already exists here — pass --overwrite")

    monkeypatch.setattr(move, "move_workspace", _refuse)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_OVERWRITE_REQUIRED
    err = capsys.readouterr().err
    assert "--overwrite" in err
    assert "moves nothing" in err


def test_overwrite_flag_threads_through_to_move_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    calls = []

    def _fake_move(**kw):
        calls.append(kw)
        return move.MoveResult(members=("repo_a",))

    monkeypatch.setattr(move, "move_workspace", _fake_move)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--overwrite"],
    )

    assert code == transfer.EXIT_WOULD_TRANSFER
    assert calls[0]["overwrite"] is True


def test_phase_failure_names_the_phase_and_says_rerun_is_safe_distinctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _fail(**kw):
        raise move.PhaseFailed("worktree (repo_a)", "the peer refused the archive")

    monkeypatch.setattr(move, "move_workspace", _fail)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_PHASE_FAILED
    err = capsys.readouterr().err
    assert "worktree (repo_a)" in err
    assert "re-running the transfer is safe" in err
    # A literal re-run always hits `begin`'s overwrite refusal — the first
    # `begin` already seeded the manifest — so the remedy text must name the
    # flag a re-run actually needs, not just claim a bare re-run is safe.
    assert "--overwrite" in err
    # A phase failure is worded distinguishably from an OverwriteNeeded
    # refusal — it never opens with "refused" or claims to have moved
    # nothing, since some phases already crossed before the failing one.
    assert "camp transfer: refused" not in err
    assert "moves nothing" not in err
    # The success path's completion report states the peer may still be
    # provisioning — a phase failure renders none of that, since a phase
    # failure never reaches `_render_move_completion` at all.
    assert "provisioning" not in err
    assert "ownership moved" not in err
    assert "This is a failure, not a refusal" in err


# ---------------------------------------------------------------------------
# The commit point's two failure shapes: before `claim` answers, ownership
# never moved and the sender's own record must still say so; after `claim`
# answers, ownership already moved and the sender's own record must NOT be
# flipped, since flipping it is `flip_sender_ownership`'s job alone and only
# once release has finished. Each pre-commit phase gets its own test, per the
# task's own enumeration, rather than one parametrized case standing in for
# all five — the property is "at every phase", so every phase is driven.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phase_label",
    ["begin", "history (repo_a)", "worktree (repo_a)", "conversations", "claim"],
)
def test_sender_manifest_still_names_the_sender_after_a_pre_commit_phase_failure(
    phase_label: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """None of these five phases has answered `claim` yet, so ownership never
    moved anywhere — this host's own manifest must still name itself,
    checked by actually reading it back after the CLI run, not by asserting
    a function was or wasn't called."""
    from camp.group.manifest import owner_of, read_central_manifest

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _fail(**kw):
        raise move.PhaseFailed(phase_label, "boom")

    monkeypatch.setattr(move, "move_workspace", _fail)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_PHASE_FAILED
    capsys.readouterr()

    manifest = read_central_manifest(env.manifest_path())
    assert owner_of(manifest) == "host-a"


def test_post_commit_phase_failure_archives_without_flipping_and_names_the_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A `finish` failure that carries a `claimed_owner` is the one genuinely
    new outcome this task adds: the sender's own record must stay exactly as
    it was (never flipped), the conversations that already crossed are still
    archived, the exit code is distinct from every other outcome, and the
    printed remedy never claims a re-run is safe — it names the peer as the
    new owner, says this host's record is stale, and tells the operator to
    continue on the peer rather than retry here."""
    from camp.group.manifest import owner_of, read_central_manifest

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _fail(**kw):
        raise move.PhaseFailed("finish", "peer bring-up crashed", claimed_owner="host-b-declared")

    monkeypatch.setattr(move, "move_workspace", _fail)

    release = _release_module()
    release_calls = []

    def _fake_release(**kw):
        release_calls.append(kw)
        return ()

    monkeypatch.setattr(release, "release_conversations", _fake_release)

    def _boom_flip(**kw):
        raise AssertionError("flip_sender_ownership must not run on a post-commit failure")

    monkeypatch.setattr(release, "flip_sender_ownership", _boom_flip)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_PHASE_FAILED_POST_COMMIT
    assert code != transfer.EXIT_PHASE_FAILED
    assert len(release_calls) == 1

    manifest = read_central_manifest(env.manifest_path())
    assert owner_of(manifest) == "host-a"

    err = capsys.readouterr().err
    assert "host-b-declared" in err
    assert "stale" in err
    assert "continue" in err.lower()
    assert "re-running the transfer is safe" not in err
    assert "moves nothing" not in err


def test_post_commit_remedy_names_the_peer_side_setup_commands_and_where_to_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The post-commit failure is exactly the case where `finish` — the
    phase that performs bring-up — never completed, so the peer's workspace
    has had no setup at all. The remedy must name `camp status` and
    `camp setup`, exactly as the success path's own completion report does
    (see `test_completion_report_names_the_claimed_owner_and_the_provisioning_
    remedy`), and must say the comparison probe command runs ON THE PEER —
    run locally it would only report that this host's own record is stale,
    which the operator already knows."""
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _fail(**kw):
        raise move.PhaseFailed("finish", "peer bring-up crashed", claimed_owner="host-b-declared")

    monkeypatch.setattr(move, "move_workspace", _fail)

    release = _release_module()
    monkeypatch.setattr(release, "release_conversations", lambda **kw: ())
    monkeypatch.setattr(
        release,
        "flip_sender_ownership",
        lambda **kw: (_ for _ in ()).throw(AssertionError("must not flip")),
    )

    _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    err = capsys.readouterr().err
    assert "camp status --name feat-x --group trailhead" in err
    assert "camp setup" in err
    assert "on the peer" in err.lower() or "on host-b-declared" in err.lower()


def test_post_commit_remedy_never_claims_claim_itself_performed_bring_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """When the outcome resolved as post-commit is `claim` itself (a claim
    whose outcome could not be read directly but a re-probe confirmed
    landed — see `camp.transfer.move.PhaseFailed`), `finish` never even ran.
    The remedy text must not assert that the FAILING phase is the one that
    performs bring-up — that phase is `finish`, and it never ran, whether
    the failure is reported against `claim` or `finish`."""
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _fail(**kw):
        raise move.PhaseFailed(
            "claim", "no response within 60.0s", claimed_owner="host-b-declared"
        )

    monkeypatch.setattr(move, "move_workspace", _fail)

    release = _release_module()
    monkeypatch.setattr(release, "release_conversations", lambda **kw: ())
    monkeypatch.setattr(
        release,
        "flip_sender_ownership",
        lambda **kw: (_ for _ in ()).throw(AssertionError("must not flip")),
    )

    _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    err = capsys.readouterr().err
    assert "camp status --name feat-x --group trailhead" in err
    assert "camp setup" in err
    assert "the phase that failed is exactly the one that performs bring-up" not in err.lower()


def test_post_commit_phase_failure_reports_each_conversations_release_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The post-commit failure branch is the one outcome where the operator
    must decide which machine to keep working on — per-conversation
    reporting matters here at least as much as on the success path (see
    `test_completion_report_names_each_conversations_release_outcome`), yet
    it discards `_release_crossed`'s return value entirely. Each conversation
    must be reported by id with its own outcome here too."""
    from pathlib import PurePosixPath

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    archived_id = "eeeeeeee-0000-4000-8000-000000000005"
    failed_id = "ffffffff-0000-4000-8000-000000000006"
    crossed = (
        move.ConversationCrossed(session_id=archived_id, subpath=PurePosixPath(".")),
        move.ConversationCrossed(session_id=failed_id, subpath=PurePosixPath(".")),
    )

    def _fail(**kw):
        raise move.PhaseFailed(
            "finish", "peer bring-up crashed", claimed_owner="host-b-declared", conversations=crossed
        )

    monkeypatch.setattr(move, "move_workspace", _fail)

    release = _release_module()
    archive_path = tmp_path / "archive" / f"{archived_id}.jsonl"

    def _fake_release(**kw):
        return (
            release.ConversationRelease(
                session_id=archived_id,
                outcome=release.ReleaseOutcome.ARCHIVED,
                archive_path=archive_path,
            ),
            release.ConversationRelease(
                session_id=failed_id,
                outcome=release.ReleaseOutcome.FAILED,
                archive_path=None,
                detail="destination unwritable",
            ),
        )

    monkeypatch.setattr(release, "release_conversations", _fake_release)
    monkeypatch.setattr(release, "flip_sender_ownership", lambda **kw: None)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_PHASE_FAILED_POST_COMMIT

    err = capsys.readouterr().err
    assert archived_id in err
    assert str(archive_path) in err
    assert failed_id in err
    assert "destination unwritable" in err


def test_the_three_move_failure_exit_codes_are_pairwise_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`--overwrite` refusal, a pre-commit phase failure, and a post-commit
    phase failure are three different outcomes with three different remedies
    — an operator scripting against the exit code must be able to tell them
    apart, driven here through the real CLI exit path for each."""
    move = _move_module()
    release = _release_module()
    monkeypatch.setattr(release, "release_conversations", lambda **kw: ())

    def _run_with(exc: Exception) -> int:
        env = _Env(tmp_path / str(id(exc)))
        env.write_group(excluded={"repo_a": []})
        env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
        env.write_manifest(owner="host-a")
        env.apply(monkeypatch)
        _fake_probe(monkeypatch, _clean_probe_answer())
        _no_conversations(monkeypatch)
        monkeypatch.setattr(move, "move_workspace", lambda **kw: (_ for _ in ()).throw(exc))
        code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])
        capsys.readouterr()
        return code

    overwrite_code = _run_with(move.OverwriteNeeded("already exists — pass --overwrite"))
    pre_commit_code = _run_with(move.PhaseFailed("worktree (repo_a)", "boom"))
    post_commit_code = _run_with(
        move.PhaseFailed("finish", "boom", claimed_owner="host-b-declared")
    )

    assert len({overwrite_code, pre_commit_code, post_commit_code}) == 3


def test_indeterminate_claim_never_claims_a_re_run_is_safe_and_has_its_own_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`PhaseFailed(indeterminate=True)` must not fall into the pre-commit
    branch, which tells the operator a re-run is safe — that claim would be
    unverified here by construction. It gets its own exit code, distinct
    from every other outcome, and the printed remedy says plainly that
    whether ownership moved could not be established."""
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _fail(**kw):
        raise move.PhaseFailed("claim", "no response within 60.0s", indeterminate=True)

    monkeypatch.setattr(move, "move_workspace", _fail)

    release = _release_module()

    def _boom_release(**kw):
        raise AssertionError("release_conversations must not run on an indeterminate claim")

    monkeypatch.setattr(release, "release_conversations", _boom_release)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code not in (
        transfer.EXIT_WOULD_TRANSFER,
        transfer.EXIT_PHASE_FAILED,
        transfer.EXIT_PHASE_FAILED_POST_COMMIT,
        transfer.EXIT_OVERWRITE_REQUIRED,
    )
    assert code == transfer.EXIT_PHASE_INDETERMINATE

    from camp.group.manifest import owner_of, read_central_manifest

    manifest = read_central_manifest(env.manifest_path())
    assert owner_of(manifest) == "host-a"

    err = capsys.readouterr().err
    assert "re-running the transfer is safe" not in err
    assert "moves nothing" not in err
    assert "could not" in err.lower()


def test_undeclared_excluded_set_refuses_on_the_moving_path_and_never_calls_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Exercised without --dry-run: the same undeclared-excluded-set check
    that governs the preview governs the mover — a member with no declared
    excluded set refuses by name before `move_workspace` is ever reached."""
    env = _Env(tmp_path)
    env.write_group()  # no excluded declared
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _boom(**kw):
        raise AssertionError("move_workspace must not run when a member's excluded set is undeclared")

    monkeypatch.setattr(move, "move_workspace", _boom)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_NOT_CLEAN
    err_or_out = capsys.readouterr()
    assert "repo_a" in err_or_out.out
    assert "never declared an excluded set" in err_or_out.out


def test_not_passed_preflight_on_the_moving_path_never_reaches_the_mover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-c")  # owned elsewhere -> ownership check fails
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _boom(**kw):
        raise AssertionError("move_workspace must not run on a NOT_CLEAN verdict")

    monkeypatch.setattr(move, "move_workspace", _boom)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_OWNERSHIP_REFUSED
    capsys.readouterr()


# ---------------------------------------------------------------------------
# camp.transfer.release wiring — `move.move_workspace` faked wholesale
# exactly like the moving-CLI-path section above; the release module's own
# behaviour (idempotency, collision, cross-filesystem, per-conversation
# failure) is covered hermetically in test_transfer_conversations.py, and the
# sender-forgets-it behaviour is covered against a real peer below in
# `TestReleaseOnARealPeer`.
# ---------------------------------------------------------------------------


def _release_module():
    return importlib.import_module("camp.transfer.release")


def test_release_conversations_is_called_after_a_successful_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Once `move_workspace` returns successfully, the CLI drives the
    release step over exactly the conversations `MoveResult` reports as
    crossed — never re-enumerated — for this same group/slug/workspace."""
    from pathlib import PurePosixPath

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    session_id = "99999999-9999-4999-8999-999999999999"
    crossed = move.ConversationCrossed(session_id=session_id, subpath=PurePosixPath("."))
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(
            members=("repo_a",), conversations=(crossed,), claimed_owner="host-b-declared"
        ),
    )

    release = _release_module()
    calls = []

    def _fake_release(**kw):
        calls.append(kw)
        return ()

    monkeypatch.setattr(release, "release_conversations", _fake_release)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_WOULD_TRANSFER
    assert len(calls) == 1
    assert calls[0]["group"] == "trailhead"
    assert calls[0]["slug"] == "feat-x"
    assert calls[0]["conversations"] == (crossed,)
    assert callable(calls[0]["locate_transcript"])


def test_release_conversations_is_never_called_on_a_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _boom_move(**kw):
        raise AssertionError("move_workspace must not run on a dry run")

    monkeypatch.setattr(move, "move_workspace", _boom_move)

    release = _release_module()

    def _boom_release(**kw):
        raise AssertionError("release_conversations must not run on a dry run")

    monkeypatch.setattr(release, "release_conversations", _boom_release)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    assert code == transfer.EXIT_WOULD_TRANSFER
    capsys.readouterr()


def test_release_conversations_is_never_called_when_a_phase_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A phase failure means `move_workspace` never returns — there is no
    `MoveResult` to release against, and running the release step against an
    unconfirmed handover is exactly the one move this slice must never make."""
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()

    def _fail(**kw):
        # A genuinely pre-commit phase — `move_workspace` never wraps a
        # `worktree` failure with `claimed_owner`, unlike `finish` (which
        # always fails post-commit once `claim` has answered; see
        # `PhaseFailed`'s own docstring) — so this shape cannot be confused
        # with the post-commit branch this test is NOT covering.
        raise move.PhaseFailed("worktree (repo_a)", "peer unreachable mid-worktree")

    monkeypatch.setattr(move, "move_workspace", _fail)

    release = _release_module()

    def _boom_release(**kw):
        raise AssertionError("release_conversations must not run when a phase failed")

    monkeypatch.setattr(release, "release_conversations", _boom_release)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_PHASE_FAILED
    capsys.readouterr()


def test_completion_report_names_each_conversations_release_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The operator reads, per conversation, whether it was archived off this
    host or is still sitting here because the release failed — never a single
    aggregate line that would hide which conversations are now peer-only."""
    from pathlib import PurePosixPath

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    archived_id = "aaaaaaaa-0000-4000-8000-000000000001"
    failed_id = "bbbbbbbb-0000-4000-8000-000000000002"
    crossed = (
        move.ConversationCrossed(session_id=archived_id, subpath=PurePosixPath(".")),
        move.ConversationCrossed(session_id=failed_id, subpath=PurePosixPath(".")),
    )
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(
            members=("repo_a",), conversations=crossed, claimed_owner="host-b-declared"
        ),
    )

    release = _release_module()
    archive_path = tmp_path / "archive" / f"{archived_id}.jsonl"

    def _fake_release(**kw):
        return (
            release.ConversationRelease(
                session_id=archived_id,
                outcome=release.ReleaseOutcome.ARCHIVED,
                archive_path=archive_path,
            ),
            release.ConversationRelease(
                session_id=failed_id,
                outcome=release.ReleaseOutcome.FAILED,
                archive_path=None,
                detail="destination unwritable",
            ),
        )

    monkeypatch.setattr(release, "release_conversations", _fake_release)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    # One of the two releases is FAILED, so this is exactly the partial
    # outcome `EXIT_RELEASE_INCOMPLETE` exists to distinguish from a fully
    # clean transfer — see `test_exit_code_distinguishes_a_clean_transfer_
    # from_one_that_left_resumable_copies` for that property on its own.
    assert code == transfer.EXIT_RELEASE_INCOMPLETE
    out = capsys.readouterr().out
    assert archived_id in out
    assert str(archive_path) in out
    assert failed_id in out
    assert "destination unwritable" in out


def test_a_failed_release_that_already_moved_the_transcript_is_not_reported_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A FAILED release whose `archive_path` is populated (the relocation
    succeeded; only the durable marker append failed — see
    `camp.transfer.release`'s marker-append failure) must never be rendered
    with the same "this host still holds a resumable copy" text a FAILED
    release with `archive_path=None` (nothing moved at all) gets — that text
    is false for the former: the transcript is gone from this host's store."""
    from pathlib import PurePosixPath

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    moved_id = "cccccccc-0000-4000-8000-000000000003"
    unmoved_id = "dddddddd-0000-4000-8000-000000000004"
    crossed = (
        move.ConversationCrossed(session_id=moved_id, subpath=PurePosixPath(".")),
        move.ConversationCrossed(session_id=unmoved_id, subpath=PurePosixPath(".")),
    )
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(
            members=("repo_a",), conversations=crossed, claimed_owner="host-b-declared"
        ),
    )

    release = _release_module()
    moved_archive_path = tmp_path / "archive" / f"{moved_id}.jsonl"

    def _fake_release(**kw):
        return (
            release.ConversationRelease(
                session_id=moved_id,
                outcome=release.ReleaseOutcome.FAILED,
                archive_path=moved_archive_path,
                detail="archived but could not record the durable marker: boom",
            ),
            release.ConversationRelease(
                session_id=unmoved_id,
                outcome=release.ReleaseOutcome.FAILED,
                archive_path=None,
                detail="transcript could not be located on this host",
            ),
        )

    monkeypatch.setattr(release, "release_conversations", _fake_release)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    # Both releases here are FAILED (one moved-but-unmarked, one never
    # moved) — the exit code this test asserts on is a side effect of the
    # fixture, not its subject; see `test_exit_code_distinguishes_a_clean_
    # transfer_from_one_that_left_resumable_copies` for that property.
    assert code == transfer.EXIT_RELEASE_INCOMPLETE
    out = capsys.readouterr().out

    moved_lines = out[out.index(moved_id) :]
    moved_section = moved_lines[: moved_lines.index(unmoved_id)]
    assert "still holds a resumable copy" not in moved_section
    assert str(moved_archive_path) in moved_section

    unmoved_section = out[out.index(unmoved_id) :]
    assert "still holds a resumable copy" in unmoved_section


def test_marker_only_release_failure_summary_never_claims_a_resumable_copy_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The printed summary line for `EXIT_RELEASE_INCOMPLETE` says a
    conversation "still need[s] cleaning up on this host" — true when a
    release's `archive_path` is `None` (the transcript never left), false
    when it is populated (a marker-only failure: the transcript is already
    gone from this host). When every FAILED release is the marker-only
    shape, the summary must not claim anything is still sitting on this
    host to clean up."""
    from pathlib import PurePosixPath

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    moved_id = "eeeeeeee-0000-4000-8000-000000000009"
    crossed = (move.ConversationCrossed(session_id=moved_id, subpath=PurePosixPath(".")),)
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(
            members=("repo_a",), conversations=crossed, claimed_owner="host-b-declared"
        ),
    )

    release = _release_module()
    moved_archive_path = tmp_path / "archive" / f"{moved_id}.jsonl"

    def _fake_release(**kw):
        return (
            release.ConversationRelease(
                session_id=moved_id,
                outcome=release.ReleaseOutcome.FAILED,
                archive_path=moved_archive_path,
                detail="archived but could not record the durable marker: boom",
            ),
        )

    monkeypatch.setattr(release, "release_conversations", _fake_release)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_RELEASE_INCOMPLETE
    err = capsys.readouterr().err
    assert "still need cleaning up on this host" not in err


def test_exit_code_distinguishes_a_clean_transfer_from_one_that_left_resumable_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Ownership committing is not the same outcome as ownership committing
    AND every conversation actually leaving this host — a transfer whose
    release step reports even one FAILED conversation must exit differently
    from a fully clean one, so a script watching the exit code alone can
    tell the two apart. The record flip itself still runs either way (this
    host's own manifest still says so), only the exit code and message
    change."""
    from pathlib import PurePosixPath

    from camp.group.manifest import read_central_manifest

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    failed_id = "11111111-2222-4222-8222-222222222222"
    crossed = (move.ConversationCrossed(session_id=failed_id, subpath=PurePosixPath(".")),)
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(
            members=("repo_a",), conversations=crossed, claimed_owner="host-b-declared"
        ),
    )

    release = _release_module()

    def _fake_release(**kw):
        return (
            release.ConversationRelease(
                session_id=failed_id,
                outcome=release.ReleaseOutcome.FAILED,
                archive_path=None,
                detail="destination unwritable",
            ),
        )

    monkeypatch.setattr(release, "release_conversations", _fake_release)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code != transfer.EXIT_WOULD_TRANSFER
    assert code == transfer.EXIT_RELEASE_INCOMPLETE

    manifest = read_central_manifest(env.manifest_path())
    assert manifest["owner"] == "host-b-declared"

    capsys.readouterr()


def test_clean_release_of_every_conversation_still_exits_the_would_transfer_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The new code is reserved for a partial release, not raised whenever
    conversations crossed at all — a fully ARCHIVED outcome still exits 0,
    exactly as before this fix."""
    from pathlib import PurePosixPath

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    archived_id = "33333333-4444-4444-8444-444444444444"
    crossed = (move.ConversationCrossed(session_id=archived_id, subpath=PurePosixPath(".")),)
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(
            members=("repo_a",), conversations=crossed, claimed_owner="host-b-declared"
        ),
    )

    release = _release_module()

    def _fake_release(**kw):
        return (
            release.ConversationRelease(
                session_id=archived_id,
                outcome=release.ReleaseOutcome.ARCHIVED,
                archive_path=tmp_path / "archive" / f"{archived_id}.jsonl",
            ),
        )

    monkeypatch.setattr(release, "release_conversations", _fake_release)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_WOULD_TRANSFER
    capsys.readouterr()


# ---------------------------------------------------------------------------
# The handover commit — the sender's own record names the peer as owner,
# written only after release_conversations has finished archiving and
# marking. Driven through the real dispatcher exactly like the sections
# above; only `move.move_workspace` and `release.release_conversations` are
# faked, so `camp.transfer.release.flip_sender_ownership` runs for real.
# ---------------------------------------------------------------------------


def test_completed_transfer_names_the_peers_declared_owner_not_the_senders_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The sender's own manifest still exists and now names the owner the
    peer's `claim` phase actually answered with (`claimed_owner`) — never
    this host's own `--to host-b` alias for that peer, which is a different
    string on purpose here."""
    from camp.group.manifest import read_central_manifest

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(members=("repo_a",), claimed_owner="host-b-declared"),
    )

    release = _release_module()
    monkeypatch.setattr(release, "release_conversations", lambda **kw: ())

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_WOULD_TRANSFER
    manifest = read_central_manifest(env.manifest_path())
    assert manifest["owner"] == "host-b-declared"
    assert manifest["owner"] != "host-b"


def test_ownerless_workspace_still_transfers_and_ends_owned_by_the_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A workspace that never recorded an owner is not a refusal — it
    transfers exactly like an owned one, and ends with the sender's record
    naming the peer."""
    from camp.group.manifest import read_central_manifest

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner=None)
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(members=("repo_a",), claimed_owner="host-b-declared"),
    )

    release = _release_module()
    monkeypatch.setattr(release, "release_conversations", lambda **kw: ())

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_WOULD_TRANSFER
    manifest = read_central_manifest(env.manifest_path())
    assert manifest["owner"] == "host-b-declared"


def test_flip_never_lands_when_release_fails_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A failure injected between the archive/marker step and the flip must
    leave the sender's own record exactly as it was before the transfer —
    proving the ordering by the state a mid-sequence failure leaves behind,
    not by recording which function was called first.

    An unexpected error here must never escape as a raw traceback: `claim`
    has already answered by this point (`move_workspace` returned
    normally), so the operator is at the highest-stakes moment in the whole
    verb — this is guarded and routed to an honest, documented exit code,
    exactly like every other post-commit outcome."""
    from camp.group.manifest import read_central_manifest

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(members=("repo_a",), claimed_owner="host-b-declared"),
    )

    release = _release_module()

    def _boom_release(**kw):
        raise RuntimeError("archive step failed mid-flight")

    monkeypatch.setattr(release, "release_conversations", _boom_release)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_RELEASE_INCOMPLETE

    manifest = read_central_manifest(env.manifest_path())
    assert manifest["owner"] == "host-a"

    err = capsys.readouterr().err
    assert "host-b-declared" in err
    assert "archive step failed mid-flight" in err


def test_after_a_completed_transfer_this_hosts_own_preflight_refuses_naming_the_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Once this host's own record has been flipped, its own preflight for a
    further transfer of the same workspace refuses on the ownership check —
    the exact same check that governs a peer-owned workspace today — and
    names the peer as the owner in the refusal."""
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    move = _move_module()
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: move.MoveResult(members=("repo_a",), claimed_owner="host-b-declared"),
    )

    release = _release_module()
    monkeypatch.setattr(release, "release_conversations", lambda **kw: ())

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])
    assert code == transfer.EXIT_WOULD_TRANSFER
    capsys.readouterr()

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    assert code == transfer.EXIT_OWNERSHIP_REFUSED
    out = capsys.readouterr().out
    assert "host-b-declared" in out
    assert "run this preflight from" in out
