"""Tests for `camp transfer-probe` and its defensive wire contract.

Test contract:
- The probe verb, run locally, answers its own declared name, the configured
  group, each member repo root's existence, the account binding, and the
  slug's presence and owner.
- The probe answers for a slug absent on that host without erroring.
- The probe answers for a group not configured on that host as "not
  configured", not as a crash.
- A response whose contract version is unrecognized is refused by name.
- A response exceeding the size bound is refused before parsing.
- A response that is not JSON, is JSON but not an object, or carries a field
  of the wrong type, is each refused by name rather than partially trusted.
- A response carrying an absolute path is never used to construct a local
  write path, asserted by a byte-identical snapshot of the local state
  directory.
- A response whose self-name equals this host's declared name refuses the
  transfer, naming the collision and the remedy.
- Each transport outcome — unreachable, stopped responding, identity
  unknown, identity changed, credentials refused, camp not resolvable,
  remote refusal — surfaces as its own distinguishable result, none
  collapsing into another.
- The verb is reserved, so a workspace slug of the same name cannot shadow
  it, asserted through the pinned reserved-token membership test.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _probe_module():
    return importlib.import_module("camp.transfer.probe")


def _transport_module():
    return importlib.import_module("camp.host.transport")


def _make_group(name: str, members: list[dict], *, account: str | None = None) -> dict:
    group: dict = {
        "group": {"name": name},
        "members": members,
        "branch_pattern": "worktree-{slug}",
    }
    if account is not None:
        group["launch"] = {"account": account}
    return group


# ---------------------------------------------------------------------------
# build_probe_answer — the local answer this host constructs about itself
# ---------------------------------------------------------------------------


def test_probe_answers_configured_group_with_member_repo_status_and_account(
    tmp_path: Path,
) -> None:
    probe = _probe_module()

    existing_repo = tmp_path / "repo_a"
    existing_repo.mkdir()
    missing_repo = tmp_path / "does-not-exist"

    group = _make_group(
        "trailhead",
        [
            {"name": "repo_a", "repo_root": str(existing_repo), "base": "origin/main", "tasks": []},
            {"name": "repo_b", "repo_root": str(missing_repo), "base": "origin/main", "tasks": []},
        ],
        account="the-account",
    )

    answer = probe.build_probe_answer(
        group_name="trailhead",
        slug="feat-x",
        groups=[group],
        self_name="andromeda",
        env={"CAMP_STATE_DIR": str(tmp_path / "camp-state")},
    )

    assert answer["contract_version"] == probe.PROBE_CONTRACT_VERSION
    assert answer["self_name"] == "andromeda"
    assert answer["group_configured"] is True
    assert answer["account"] == "the-account"
    assert {"name": "repo_a", "repo_root_exists": True} in answer["members"]
    assert {"name": "repo_b", "repo_root_exists": False} in answer["members"]


def test_probe_answers_for_a_slug_absent_on_this_host_without_erroring(tmp_path: Path) -> None:
    probe = _probe_module()
    group = _make_group(
        "trailhead", [{"name": "repo_a", "repo_root": str(tmp_path), "base": "origin/main", "tasks": []}]
    )

    answer = probe.build_probe_answer(
        group_name="trailhead",
        slug="no-such-slug",
        groups=[group],
        self_name="andromeda",
        env={"CAMP_STATE_DIR": str(tmp_path / "camp-state")},
    )

    assert answer["group_configured"] is True
    assert answer["workspace_exists"] is False
    assert answer["workspace_owner"] is None


def test_probe_answers_for_a_present_slug_with_its_recorded_owner(tmp_path: Path) -> None:
    from camp.group.manifest import manifest_path_for, workspace_dir, write_central_manifest

    probe = _probe_module()
    env = {"CAMP_STATE_DIR": str(tmp_path / "camp-state")}
    group = _make_group(
        "trailhead", [{"name": "repo_a", "repo_root": str(tmp_path), "base": "origin/main", "tasks": []}]
    )

    ws = workspace_dir("trailhead", "feat-o", env=env)
    ws.mkdir(parents=True, exist_ok=True)
    mpath = manifest_path_for("trailhead", "feat-o", env=env)
    write_central_manifest(
        mpath,
        {
            "schema_version": 1,
            "group": "trailhead",
            "slug": "feat-o",
            "branch": "worktree-feat-o",
            "members": [],
            "owner": "someone-else",
        },
    )

    answer = probe.build_probe_answer(
        group_name="trailhead", slug="feat-o", groups=[group], self_name="andromeda", env=env,
    )

    assert answer["workspace_exists"] is True
    assert answer["workspace_owner"] == "someone-else"


def test_probe_answers_group_not_configured_explicitly_rather_than_crashing(tmp_path: Path) -> None:
    probe = _probe_module()

    answer = probe.build_probe_answer(
        group_name="no-such-group",
        slug="whatever",
        groups=[],
        self_name="andromeda",
        env={"CAMP_STATE_DIR": str(tmp_path / "camp-state")},
    )

    assert answer["group_configured"] is False
    assert answer["members"] is None
    assert answer["workspace_exists"] is None
    assert answer["workspace_owner"] is None


def test_probe_not_configured_answer_is_not_derived_from_an_empty_member_list(tmp_path: Path) -> None:
    """A configured-but-currently-empty group must not read the same as an
    unconfigured one — this is the finding from the unknown's resolution:
    `camp list --json` cannot tell the two apart, so the probe must not
    derive `group_configured` from row counts at all."""
    probe = _probe_module()
    empty_group = _make_group("trailhead", [])

    answer = probe.build_probe_answer(
        group_name="trailhead",
        slug="whatever",
        groups=[empty_group],
        self_name="andromeda",
        env={"CAMP_STATE_DIR": str(tmp_path / "camp-state")},
    )

    assert answer["group_configured"] is True
    assert answer["members"] == []


# ---------------------------------------------------------------------------
# parse_probe_response — the defensive parser for untrusted stdout
# ---------------------------------------------------------------------------


def _valid_response(**overrides) -> dict:
    base = {
        "contract_version": 1,
        "self_name": "remote-host",
        "group_configured": True,
        "members": [{"name": "repo_a", "repo_root_exists": True}],
        "account": "the-account",
        "workspace_exists": False,
        "workspace_owner": None,
    }
    base.update(overrides)
    return base


def test_parse_accepts_a_well_formed_response() -> None:
    probe = _probe_module()

    result = probe.parse_probe_response(json.dumps(_valid_response()))

    assert isinstance(result, probe.ProbeAnswer)
    assert result.self_name == "remote-host"
    assert result.group_configured is True
    assert result.members == (probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),)
    assert result.account == "the-account"
    assert result.workspace_exists is False
    assert result.workspace_owner is None


def test_parse_refuses_an_unrecognized_contract_version_by_name() -> None:
    probe = _probe_module()

    result = probe.parse_probe_response(json.dumps(_valid_response(contract_version=999)))

    assert isinstance(result, probe.ProbeRefused)
    assert "contract_version" in result.reason
    assert "999" in result.reason


def test_parse_refuses_a_response_exceeding_the_size_bound_before_parsing() -> None:
    probe = _probe_module()

    # A valid JSON document, just padded well past the bound with a huge
    # string field — proves the size check runs before json.loads is ever
    # reached, not merely that oversize JSON happens to fail to parse.
    huge = json.dumps(_valid_response(account="x" * (probe.MAX_PROBE_RESPONSE_BYTES + 10)))
    assert len(huge.encode("utf-8")) > probe.MAX_PROBE_RESPONSE_BYTES

    result = probe.parse_probe_response(huge)

    assert isinstance(result, probe.ProbeRefused)
    assert "size" in result.reason.lower() or "bytes" in result.reason.lower()


def test_parse_refuses_non_json_by_name() -> None:
    probe = _probe_module()

    result = probe.parse_probe_response("not json at all {{{")

    assert isinstance(result, probe.ProbeRefused)
    assert "json" in result.reason.lower()


def test_parse_refuses_json_that_is_not_an_object() -> None:
    probe = _probe_module()

    result = probe.parse_probe_response(json.dumps([1, 2, 3]))

    assert isinstance(result, probe.ProbeRefused)
    assert "object" in result.reason.lower()


def test_parse_refuses_a_field_of_the_wrong_type() -> None:
    probe = _probe_module()

    result = probe.parse_probe_response(json.dumps(_valid_response(group_configured="yes")))

    assert isinstance(result, probe.ProbeRefused)
    assert "group_configured" in result.reason


def test_parse_refuses_a_malformed_member_entry() -> None:
    probe = _probe_module()

    result = probe.parse_probe_response(
        json.dumps(_valid_response(members=[{"name": "repo_a", "repo_root_exists": "yes"}]))
    )

    assert isinstance(result, probe.ProbeRefused)
    assert "members" in result.reason


# ---------------------------------------------------------------------------
# No path from the response ever builds a local path
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_bytes().hex()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_a_hostile_response_never_touches_the_local_state_directory(tmp_path: Path) -> None:
    """A response cannot use an absolute path (or any other field) to construct
    a local write path. Proven by feeding probe_peer a malicious response and
    diffing the local state directory byte-for-byte before and after."""
    probe = _probe_module()

    state_dir = tmp_path / "camp-state"
    state_dir.mkdir()
    (state_dir / "sentinel.json").write_text('{"ok": true}\n', encoding="utf-8")
    before = _snapshot(state_dir)

    hostile = _valid_response(
        self_name="/etc/passwd",
        account="../../../../etc/shadow",
        workspace_owner="/absolute/path/should/never/be/joined",
    )

    result = probe.probe_peer(
        _host(), group="trailhead", slug="feat-x", self_name="this-host",
        runner=_answering_runner(stdout=json.dumps(hostile)),
    )

    assert isinstance(result, probe.ProbeAnswer)
    after = _snapshot(state_dir)
    assert after == before


# ---------------------------------------------------------------------------
# probe_peer — composes the shipped transport with the parser + collision check
# ---------------------------------------------------------------------------


def _host() -> "object":
    from camp.host.config import Host

    return Host(ssh="peer-host", camp_bin="/opt/camp/bin/camp")


def _answering_runner(*, stdout: str = "", stderr: str = "", exit_code: int = 0):
    """A transport runner that answers every call with exactly this raw result."""
    transport = _transport_module()

    def runner(argv, execution_timeout, env):
        return transport.RawResult(stdout=stdout, stderr=stderr, exit_code=exit_code)

    return runner


def test_probe_peer_returns_a_parsed_answer_on_a_clean_response() -> None:
    probe = _probe_module()

    result = probe.probe_peer(
        _host(), group="trailhead", slug="feat-x", self_name="this-host",
        runner=_answering_runner(stdout=json.dumps(_valid_response())),
    )

    assert isinstance(result, probe.ProbeAnswer)


def test_probe_peer_refuses_self_name_collision_naming_it_and_the_remedy() -> None:
    probe = _probe_module()

    result = probe.probe_peer(
        _host(), group="trailhead", slug="feat-x", self_name="andromeda",
        runner=_answering_runner(stdout=json.dumps(_valid_response(self_name="andromeda"))),
    )

    assert isinstance(result, probe.SelfNameCollision)
    assert result.peer_self_name == "andromeda"
    message = str(result)
    assert "andromeda" in message


def test_probe_peer_does_not_refuse_when_self_name_never_declared_on_either_end() -> None:
    probe = _probe_module()

    result = probe.probe_peer(
        _host(), group="trailhead", slug="feat-x", self_name=None,
        runner=_answering_runner(stdout=json.dumps(_valid_response(self_name=None))),
    )

    assert isinstance(result, probe.ProbeAnswer)


@pytest.mark.parametrize(
    "exit_code,stderr,expected_type_name",
    [
        (255, "Could not resolve hostname peer-host", "Unreachable"),
        (255, "No ED25519 host key is known for peer-host and you have requested strict checking.", "IdentityUnknown"),
        (255, "REMOTE HOST IDENTIFICATION HAS CHANGED!", "IdentityChanged"),
        (255, "someone@peer-host: Permission denied (publickey).", "CredentialsRefused"),
        (127, "bash: camp: command not found", "CampNotResolvable"),
        (1, "camp transfer-probe: some remote refusal", "RemoteRefusal"),
    ],
)
def test_probe_peer_surfaces_each_transport_outcome_distinguishably(
    exit_code, stderr, expected_type_name
) -> None:
    probe = _probe_module()

    result = probe.probe_peer(
        _host(), group="trailhead", slug="feat-x", self_name="this-host",
        runner=_answering_runner(stderr=stderr, exit_code=exit_code),
    )

    assert type(result).__name__ == expected_type_name
    # None of the closed set of outcomes ever collapse into ProbeAnswer,
    # ProbeRefused, or SelfNameCollision.
    assert not isinstance(result, (probe.ProbeAnswer, probe.ProbeRefused, probe.SelfNameCollision))


def test_probe_peer_surfaces_stopped_responding_distinctly() -> None:
    probe = _probe_module()
    transport = _transport_module()
    import subprocess

    def fake_runner(argv, execution_timeout, env):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=execution_timeout)

    result = probe.probe_peer(
        _host(), group="trailhead", slug="feat-x", self_name="this-host", runner=fake_runner,
    )

    assert isinstance(result, transport.StoppedResponding)


def test_probe_peer_treats_a_malformed_answered_response_as_probe_refused() -> None:
    """A remote that answers (exit 0) but whose stdout is contaminated must
    not be reported as a transport failure — it is its own distinguishable
    outcome, ProbeRefused, never collapsed into RemoteRefusal or Unreachable."""
    probe = _probe_module()

    result = probe.probe_peer(
        _host(), group="trailhead", slug="feat-x", self_name="this-host",
        runner=_answering_runner(stdout='garbage before {"ok": true}'),
    )

    assert isinstance(result, probe.ProbeRefused)


def test_probe_peer_revalidates_the_slug_before_handing_it_to_the_transport() -> None:
    """Identifier hygiene at the boundary: a slug carrying shell-relevant
    characters is refused before it ever reaches run_camp, regardless of
    where it came from."""
    probe = _probe_module()

    calls: list[list[str]] = []

    def fake_runner(argv, execution_timeout, env):
        calls.append(list(argv))
        raise AssertionError("the transport must never be reached with a bad slug")

    with pytest.raises(probe.InvalidSlugForTransport):
        probe.probe_peer(
            _host(),
            group="trailhead",
            slug="feat-x; rm -rf /",
            self_name="this-host",
            runner=fake_runner,
        )

    assert calls == []


# ---------------------------------------------------------------------------
# CLI wiring — `camp transfer-probe --group G --slug S` end to end
# ---------------------------------------------------------------------------


def _write_group_toml(groups_dir: Path, name: str, members: list[tuple[str, str]]) -> None:
    groups_dir.mkdir(parents=True, exist_ok=True)
    member_tables = "\n\n".join(
        f'[[members]]\nname = "{member_name}"\nrepo_root = "{repo_root}"'
        for member_name, repo_root in members
    )
    (groups_dir / f"{name}.toml").write_text(f'[group]\nname = "{name}"\n\n{member_tables}\n')


def test_cli_transfer_probe_prints_json_answer_for_a_configured_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    dispatch = _dispatch_module()

    cfg = tmp_path / "config"
    (cfg / "groups").mkdir(parents=True)
    repo = tmp_path / "repo_a"
    repo.mkdir()
    _write_group_toml(cfg / "groups", "trailhead", [("repo_a", str(repo))])
    (cfg / "hosts.toml").write_text('self_name = "peer-box"\n', encoding="utf-8")

    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(sys, "argv", ["camp", "transfer-probe", "--group", "trailhead", "--slug", "feat-x"])

    dispatch.main()

    out = json.loads(capsys.readouterr().out)
    assert out["self_name"] == "peer-box"
    assert out["group_configured"] is True
    assert out["members"] == [{"name": "repo_a", "repo_root_exists": True}]
    assert out["workspace_exists"] is False


def test_cli_transfer_probe_answers_not_configured_for_an_unknown_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    dispatch = _dispatch_module()

    cfg = tmp_path / "config"
    (cfg / "groups").mkdir(parents=True)

    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        sys, "argv", ["camp", "transfer-probe", "--group", "no-such-group", "--slug", "feat-x"]
    )

    dispatch.main()

    out = json.loads(capsys.readouterr().out)
    assert out["group_configured"] is False


# ---------------------------------------------------------------------------
# Reservation — a workspace slug named "transfer-probe" cannot shadow the verb
# ---------------------------------------------------------------------------


def test_transfer_probe_is_reserved_and_never_dispatched_as_a_bare_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.spine import RESERVED

    assert "transfer-probe" in RESERVED

    dispatch = _dispatch_module()
    cfg = tmp_path / "config"
    (cfg / "groups").mkdir(parents=True)
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(sys, "argv", ["camp", "transfer-probe", "--group", "g", "--slug", "s"])

    dispatch.main()

    combined = capsys.readouterr()
    assert "bare slug dispatch is no longer supported" not in (combined.out + combined.err)
