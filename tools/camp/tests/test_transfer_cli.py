"""Tests for `camp transfer` — the operator-facing dry-run verb.

Test contract:
- `--dry-run` prints the ordered checks and what would cross, and exits 0 on a
  clean verdict.
- Omitting `--dry-run` refuses with its own exit code and a message naming the
  reason; nothing is read from the peer on that path.
- The requirement appears in the verb's help output.
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
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
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


def _fake_probe(monkeypatch: pytest.MonkeyPatch, result):
    probe = _probe_module()
    transfer = _transfer_module()

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
# --dry-run is required
# ---------------------------------------------------------------------------


def test_omitting_dry_run_refuses_before_reading_the_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group()
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.apply(monkeypatch)

    transfer = _transfer_module()

    def _boom(*a, **k):
        raise AssertionError("probe_peer must not be called without --dry-run")

    monkeypatch.setattr(_probe_module(), "probe_peer", _boom)

    code = _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])

    assert code == transfer.EXIT_DRY_RUN_REQUIRED
    err = capsys.readouterr().err
    assert "--dry-run" in err
    assert "not built yet" in err


def test_dry_run_requirement_is_documented_in_help() -> None:
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
    assert "--dry-run" in transfer_section
    assert "REQUIRED" in transfer_section


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
    probe = _probe_module()

    _fake_probe(
        monkeypatch,
        probe.ProbeAnswer(
            self_name="host-b",
            group_configured=True,
            members=(probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),),
            account=None,
            workspace_exists=False,
            workspace_owner=None,
            contract_version=probe.PROBE_CONTRACT_VERSION,
        ),
    )
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
    probe = _probe_module()

    _fake_probe(
        monkeypatch,
        probe.ProbeAnswer(
            self_name="host-b",
            group_configured=True,
            members=(probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),),
            account=None,
            workspace_exists=False,
            workspace_owner=None,
            contract_version=probe.PROBE_CONTRACT_VERSION,
        ),
    )
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
    assert payload["verdict"] == "would_transfer"
    assert len(payload["checks"]) == 11
    for row in payload["checks"]:
        assert row["ok"] is (row["status"] == "passed")
    assert all(row["ok"] for row in payload["checks"])
    assert payload["conversations"] == []


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
    probe = _probe_module()
    transfer = _transfer_module()

    _fake_probe(
        monkeypatch,
        probe.ProbeAnswer(
            self_name="host-b",
            group_configured=True,
            members=(probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),),
            account=None,
            workspace_exists=False,
            workspace_owner=None,
            contract_version=probe.PROBE_CONTRACT_VERSION,
        ),
    )
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
    probe = _probe_module()
    transfer = _transfer_module()

    _fake_probe(
        monkeypatch,
        probe.ProbeAnswer(
            self_name="host-b",
            group_configured=True,
            members=(probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),),
            account=None,
            workspace_exists=False,
            workspace_owner=None,
            contract_version=probe.PROBE_CONTRACT_VERSION,
        ),
    )
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
    line = next(l for l in out.splitlines() if "the peer answers" in l)
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
    probe = _probe_module()
    transfer = _transfer_module()

    _fake_probe(
        monkeypatch,
        probe.ProbeAnswer(
            self_name="host-b",
            group_configured=True,
            members=(probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),),
            account=None,
            workspace_exists=False,
            workspace_owner=None,
            contract_version=probe.PROBE_CONTRACT_VERSION,
        ),
    )
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
    probe = _probe_module()
    transport = _transport_module()

    if setup_kind == "clean":
        env.write_manifest(owner="host-a")
        _fake_probe(
            monkeypatch,
            probe.ProbeAnswer(
                self_name="host-b",
                group_configured=True,
                members=(probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),),
                account=None,
                workspace_exists=False,
                workspace_owner=None,
                contract_version=probe.PROBE_CONTRACT_VERSION,
            ),
        )
    elif setup_kind == "ownership_refused":
        env.write_manifest(owner="host-c")
        _fake_probe(
            monkeypatch,
            probe.ProbeAnswer(
                self_name="host-b",
                group_configured=True,
                members=(probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),),
                account=None,
                workspace_exists=False,
                workspace_owner=None,
                contract_version=probe.PROBE_CONTRACT_VERSION,
            ),
        )
    else:
        env.write_manifest(owner="host-a")
        _fake_probe(monkeypatch, transport.Unreachable(reason="down"))

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
    probe = _probe_module()
    transfer = _transfer_module()

    _fake_probe(
        monkeypatch,
        probe.ProbeAnswer(
            self_name="host-b",
            group_configured=True,
            members=(probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),),
            account=None,
            workspace_exists=False,
            workspace_owner=None,
            contract_version=probe.PROBE_CONTRACT_VERSION,
        ),
    )
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
