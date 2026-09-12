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
    """`move_env` plus a real config+harness-store for the SENDER (so the
    slice's boundary — the sender keeps its own resumable copy — can be
    checked through the CLI too) and the harness/tmux shim on both sides."""
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


def _live_session_ids(env: dict[str, str]) -> set[str]:
    result = _run_camp(env, "sessions", "--group", "testgroup", "--json")
    assert result.returncode == 0, result.stderr
    return {row["session_id"] for row in json.loads(result.stdout)}


class TestConversationResumesOnARealPeer:
    def test_root_conversation_is_listed_and_its_own_resume_path_accepts_it(
        self, conv_env
    ):
        """AC3/AC5/AC6: a conversation rooted at the workspace root crosses,
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
        """AC3: a conversation started inside the `repo_a` member
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

    def test_arrived_conversation_is_recoverable_but_not_live(self, conv_env):
        """AC4 (through the CLI): the conversation that crossed shows up in
        the DEAD/recoverable listing and is absent from the LIVE listing —
        camp never offers a session that is actually still running as
        something to bring back."""
        from pathlib import PurePosixPath

        c = conv_env
        session_id = "33333333-3333-4333-8333-333333333333"

        _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("."), marker="live-vs-dead-marker"
        )

        assert session_id in {row["session_id"] for row in _recoverable_rows(c["peer_cli_env"])}
        assert session_id not in _live_session_ids(c["peer_cli_env"])

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

    def test_sending_host_keeps_ownership_and_its_own_resumable_copy(self, conv_env):
        """The slice's boundary: the sender is unchanged by the move.
        Ownership has not moved — camp names no `--overwrite`-style
        transport toward the sender at all, this is a same-host check — and
        the sender still holds a conversation that is BOTH listed as
        recoverable AND accepted by its own resume path, exactly as the
        peer's copy is. Asserted positively, not merely "no error"."""
        from pathlib import PurePosixPath

        c = conv_env
        session_id = "55555555-5555-4555-8555-555555555555"

        _cross_one_conversation(
            c, session_id=session_id, subpath=PurePosixPath("."), marker="sender-unchanged-marker"
        )

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

        assert phases == ["begin", "history: repo_a", "worktree: repo_a", "finish"]
        assert result.members == ("repo_a",)

        landed = _git_out(g["peer_repo"], "rev-parse", f"refs/heads/{g['branch']}")
        sender_tip = _git_out(g["wt_path"], "rev-parse", "HEAD")
        assert landed == sender_tip

        peer_wt = _worktree_path("testgroup", g["slug"], "repo_a", env=g["peer_env"])
        assert (peer_wt / "committed.txt").read_text() == "committed on the sender\n"
        assert (peer_wt / "untracked.txt").read_text() == "never committed\n"

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
        from pathlib import PurePosixPath

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
        assert snapshot == ["begin", "history: repo_a", "worktree: repo_a", "finish"]
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
    assert final == ["begin", "history: repo_a", "worktree: repo_a", "conversations", "finish"]


# ---------------------------------------------------------------------------
# The moving CLI path — rendering, exit codes, and flag wiring, with
# `move.move_workspace` faked wholesale (see the section header above for why
# that observes a monkeypatch). The mover's own phase-driving behaviour is
# proven against a real peer in `TestMoveWorkspaceEndToEnd`.
# ---------------------------------------------------------------------------


def test_completion_report_names_peer_workspace_regen_and_no_ownership_move(
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
    monkeypatch.setattr(
        move, "move_workspace", lambda **kw: move.MoveResult(members=("repo_a",))
    )

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_WOULD_TRANSFER
    out = capsys.readouterr().out
    assert "'feat-x' arrived on 'host-b'" in out
    assert "ownership did not move" in out
    assert "'host-a' still owns 'feat-x'" in out
    assert "regeneration" in out and "still running" in out
    assert "camp status --name feat-x --group trailhead" in out
    assert "credential-shaped content" in out
    # Ownership did not move, so work accumulated on the peer since arrival
    # has no way back to the sender — a later --overwrite would destroy it,
    # and the completion report must name that consequence, not just the
    # fact that ownership stayed put.
    assert "--overwrite" in out
    assert "destroy" in out or "discard" in out
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

    assert code == transfer.EXIT_WOULD_TRANSFER
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
    assert "This is a failure, not a refusal" in err


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
