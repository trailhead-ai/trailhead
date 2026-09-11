"""Compatibility for every group config already on disk, and the
operator-facing documentation for the two declarations Slice 2 depends on.

Test contract:
- Every group config in the shipped examples loads unchanged with the new key
  absent.
- A workspace whose manifest predates this slice is read by the preflight
  without error, and the checks that depend on the new declaration report
  their never-declared state rather than failing.
- A preflight run against a group whose members declare no excluded set
  produces the naming refusal, not a crash and not a silent pass.
- The refusal for a missing host declaration names the file to create and the
  fact that the two hosts must declare different names, asserted on the
  emitted text.
- The documented command surface and the verb's actual accepted arguments
  agree, exercised by running the documented invocation rather than by
  reading the doc.
- A workspace whose slug equals a newly reserved verb token stays reachable
  through its named verbs, and the documented remedy is exercised rather than
  described.
- Every exit code the verb can produce appears in the documented table,
  checked by provoking each one and comparing against the table rather than
  by reading it.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_CAMP_ROOT = _REPO_ROOT / "tools" / "camp"
_PLUGIN_DIR = _CAMP_ROOT / "plugins" / "camp"
_README = _CAMP_ROOT / "README.md"
_EXAMPLES_DIR = _CAMP_ROOT / "groups.example"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# A test whose setup reads/writes the central manifest does so before
# dispatch.main() has bootstrapped trailhead.paths, so this file bootstraps it
# itself up front — the same reason test_transfer_cli.py does.
import _bootstrap  # noqa: E402

_bootstrap.ensure_trailhead_importable()


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _transfer_module():
    return importlib.import_module("camp.cli.transfer")


def _probe_module():
    return importlib.import_module("camp.transfer.probe")


def _preflight_module():
    return importlib.import_module("camp.transfer.preflight")


def _config_module():
    return importlib.import_module("camp.group.config")


# ---------------------------------------------------------------------------
# Fixture helpers (mirrors tests/test_transfer_cli.py's, kept local per this
# suite's own convention of self-contained fixtures)
# ---------------------------------------------------------------------------


def _write_group_toml(
    groups_dir: Path,
    name: str,
    members: list[tuple[str, str]],
    *,
    excluded: dict[str, list[str] | None] | None = None,
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
    transfer = _transfer_module()
    monkeypatch.setattr(transfer, "_gather_conversations", lambda **kw: ())


def _clean_probe_answer():
    """A peer answer on which every peer-dependent check passes."""
    probe = _probe_module()
    return probe.ProbeAnswer(
        self_name="host-b",
        group_configured=True,
        members=(probe.MemberRepoStatus(name="repo_a", repo_root_exists=True),),
        account=None,
        workspace_exists=False,
        workspace_owner=None,
        contract_version=probe.PROBE_CONTRACT_VERSION,
    )


def _fake_probe(monkeypatch: pytest.MonkeyPatch, result):
    probe = _probe_module()

    def _fake(host, *, group, slug, self_name):
        return result

    monkeypatch.setattr(probe, "probe_peer", _fake)
    return _fake


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """Run `camp <argv>` through the real dispatcher; return its exit code."""
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    with pytest.raises(SystemExit) as exc_info:
        dispatch.main()
    return exc_info.value.code


def _run_no_exit(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """Run `camp <argv>` for a verb (like transfer-probe) that never exits."""
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    dispatch.main()


def _readme_text() -> str:
    return _README.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Every shipped example loads unchanged, through the real loader
# ---------------------------------------------------------------------------


def test_every_shipped_example_group_config_loads_through_the_real_loader() -> None:
    config = _config_module()

    example_files = sorted(_EXAMPLES_DIR.glob("*.toml"))
    # Closed set: exactly the two example fleets shipped today. A count
    # mismatch means an example was added or removed without this test
    # noticing — the failure IS the finding.
    assert [p.name for p in example_files] == ["levr.toml", "trailhead.toml"]

    loaded = {p.name: config.load_group(p) for p in example_files}

    levr_members = {m["name"]: m["excluded"] for m in loaded["levr.toml"]["members"]}
    assert levr_members == {
        "levr-facilitator-mobile": None,
        "levr-bridge": None,
        "levr-platform": None,
    }

    trailhead_members = {m["name"]: m["excluded"] for m in loaded["trailhead.toml"]["members"]}
    assert trailhead_members == {
        "trailhead": None,
        "trailhead-ai.github.io": ("node_modules", "_site"),
        "outpost": None,
    }


# ---------------------------------------------------------------------------
# 2. A pre-slice manifest's members are read without error; the dependent
#    check reports "never declared" rather than raising
# ---------------------------------------------------------------------------


def test_compose_preflight_reports_never_declared_state_without_raising() -> None:
    preflight = _preflight_module()

    members = (preflight.MemberDeclaration(name="repo_a", excluded=None),)

    result = preflight.compose_preflight(
        self_name="host-a",
        self_account=None,
        workspace_manifest_exists=True,
        owner="host-a",
        peer_name="host-b",
        peer_declared=True,
        probe_result=_clean_probe_answer(),
        members=members,
        slug="feat-x",
        conversations=(),
    )

    check = next(c for c in result.checks if c.name == "every member declares an excluded set")
    assert check.status is preflight.CheckStatus.FAILED
    assert "repo_a" in check.detail
    assert "never declared" in check.detail
    assert result.verdict is preflight.Verdict.NOT_CLEAN


# ---------------------------------------------------------------------------
# 3. The same, exercised end to end through the CLI: names the member, no
#    crash, no silent pass
# ---------------------------------------------------------------------------


def test_undeclared_excluded_set_refuses_by_name_through_the_full_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group()  # no excluded key at all -> the None handoff, on every member
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(
        monkeypatch,
        ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"],
    )

    out = capsys.readouterr().out
    assert code != 0  # not a silent pass
    assert "repo_a" in out
    assert "never declared an excluded set" in out


# ---------------------------------------------------------------------------
# 4. Missing host declaration: refusal names the file and the different-
#    names requirement
# ---------------------------------------------------------------------------


def test_missing_self_name_refusal_names_the_file_and_the_different_names_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name=None, peers={"host-b": "host-b"})  # hosts.toml exists, no self_name
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
    assert "hosts.toml" in out
    assert "self_name" in out
    assert "differs from every peer's own self_name" in out


# ---------------------------------------------------------------------------
# 5. The documented invocation actually runs — a drifted README fails this
# ---------------------------------------------------------------------------


def test_documented_transfer_invocation_matches_the_verbs_accepted_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    readme = _readme_text()
    match = re.search(
        r"^camp transfer <slug> --to <peer> \[--dry-run\] \[--overwrite\] \[--json\]$",
        readme,
        re.MULTILINE,
    )
    assert match is not None, "the documented invocation line has drifted or vanished"
    documented = match.group(0)

    # Concretize the documented invocation for a clean, fully-passing setup:
    # required tokens only (every bracketed token is optional and dropped),
    # then `--dry-run` is added back so this test exercises the preview path
    # — the moving path's own wiring is covered in test_transfer_cli.py.
    tokens = documented.replace("[--overwrite]", "").replace("[--json]", "")
    tokens = tokens.replace("[--dry-run]", "--dry-run").split()
    assert tokens[0] == "camp"
    tokens = tokens[1:]  # drop "camp"; argv starts at the verb
    tokens = ["feat-x" if t == "<slug>" else t for t in tokens]
    tokens = ["host-b" if t == "<peer>" else t for t in tokens]

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(monkeypatch, [*tokens, "--group", "trailhead"])

    assert code == transfer.EXIT_WOULD_TRANSFER


# ---------------------------------------------------------------------------
# 6. A workspace slug equal to a reserved verb token stays reachable through
#    its documented remedy, run rather than described
# ---------------------------------------------------------------------------


def _extract_readme_commands(pattern: str) -> str:
    readme = _readme_text()
    match = re.search(pattern, readme, re.MULTILINE)
    assert match is not None, f"documented remedy command not found for pattern: {pattern!r}"
    return match.group(0)


def test_reserved_slug_transfer_stays_reachable_via_its_documented_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    documented = _extract_readme_commands(
        r"^camp transfer transfer --to <peer> --dry-run --group <name>$"
    )
    tokens = documented.split()[1:]  # drop "camp"
    tokens = ["host-b" if t == "<peer>" else t for t in tokens]
    tokens = ["trailhead" if t == "<name>" else t for t in tokens]

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a", slug="transfer")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(monkeypatch, tokens)

    assert code == transfer.EXIT_WOULD_TRANSFER


def test_reserved_slug_transfer_probe_stays_reachable_via_its_documented_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    documented = _extract_readme_commands(
        r"^camp transfer-probe --group <name> --slug transfer-probe$"
    )
    tokens = documented.split()[1:]  # drop "camp"
    tokens = ["trailhead" if t == "<name>" else t for t in tokens]

    env = _Env(tmp_path)
    env.write_group()
    env.write_hosts(self_name="host-a")
    env.write_manifest(owner="host-a", slug="transfer-probe")
    env.apply(monkeypatch)

    _run_no_exit(monkeypatch, tokens)

    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_exists"] is True
    assert payload["workspace_owner"] == "host-a"


def test_reserved_slug_transfer_receive_stays_reachable_via_its_documented_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    documented = _extract_readme_commands(
        r"^camp transfer transfer-receive --to <peer> --dry-run --group <name>$"
    )
    tokens = documented.split()[1:]  # drop "camp"
    tokens = ["host-b" if t == "<peer>" else t for t in tokens]
    tokens = ["trailhead" if t == "<name>" else t for t in tokens]

    env = _Env(tmp_path)
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a", slug="transfer-receive")
    env.apply(monkeypatch)
    transfer = _transfer_module()

    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)

    code = _run(monkeypatch, tokens)

    assert code == transfer.EXIT_WOULD_TRANSFER


# ---------------------------------------------------------------------------
# 7. Every exit code the verb can produce appears in the documented table
# ---------------------------------------------------------------------------


def _readme_exit_codes() -> set[int]:
    readme = _readme_text()
    section = readme[readme.index("## Transferring a workspace") :]
    table = section[section.index("Exit codes:") : section.index("`transfer` and `transfer-probe`")]
    return {int(n) for n in re.findall(r"^(\d)\s+\S", table, re.MULTILINE)}


def test_every_producible_exit_code_appears_in_the_documented_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    probe = _probe_module()
    transport = importlib.import_module("camp.host.transport")

    produced: set[int] = set()

    # 0 EXIT_WOULD_TRANSFER — everything clean
    env = _Env(tmp_path / "clean")
    env.write_group(excluded={"repo_a": []})
    env.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env.write_manifest(owner="host-a")
    env.apply(monkeypatch)
    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)
    produced.add(
        _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"])
    )

    # 1 EXIT_ERROR — --to omitted
    produced.add(_run(monkeypatch, ["transfer", "feat-x", "--dry-run", "--group", "trailhead"]))

    # 3 EXIT_NOT_CLEAN — a failing check with no exit code of its own
    env3 = _Env(tmp_path / "notclean")
    env3.write_group()  # no excluded declared -> unmapped failure
    env3.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env3.write_manifest(owner="host-a")
    env3.apply(monkeypatch)
    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)
    produced.add(
        _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"])
    )

    # 4 EXIT_OWNERSHIP_REFUSED
    env4 = _Env(tmp_path / "ownership")
    env4.write_group(excluded={"repo_a": []})
    env4.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env4.write_manifest(owner="host-c")
    env4.apply(monkeypatch)
    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)
    produced.add(
        _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"])
    )

    # 5 EXIT_PEER_UNREACHABLE
    env5 = _Env(tmp_path / "unreachable")
    env5.write_group(excluded={"repo_a": []})
    env5.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env5.write_manifest(owner="host-a")
    env5.apply(monkeypatch)
    _fake_probe(monkeypatch, transport.Unreachable(reason="connection refused"))
    _no_conversations(monkeypatch)
    produced.add(
        _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead", "--dry-run"])
    )

    # 6 EXIT_UNKNOWN_SLUG — no manifest written
    env6 = _Env(tmp_path / "unknownslug")
    env6.write_group(excluded={"repo_a": []})
    env6.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env6.apply(monkeypatch)
    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)
    produced.add(
        _run(
            monkeypatch,
            ["transfer", "no-such-slug", "--to", "host-b", "--group", "trailhead", "--dry-run"],
        )
    )

    # 7 EXIT_UNKNOWN_PEER
    env7 = _Env(tmp_path / "unknownpeer")
    env7.write_group(excluded={"repo_a": []})
    env7.write_hosts(self_name="host-a")  # no peers declared
    env7.write_manifest(owner="host-a")
    env7.apply(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("an undeclared peer must never be probed")

    monkeypatch.setattr(probe, "probe_peer", _boom)
    _no_conversations(monkeypatch)
    produced.add(
        _run(
            monkeypatch,
            ["transfer", "feat-x", "--to", "no-such-host", "--group", "trailhead", "--dry-run"],
        )
    )

    # 8 EXIT_OVERWRITE_REQUIRED — the peer's begin refused without --overwrite
    move = importlib.import_module("camp.transfer.move")
    env8 = _Env(tmp_path / "overwrite")
    env8.write_group(excluded={"repo_a": []})
    env8.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env8.write_manifest(owner="host-a")
    env8.apply(monkeypatch)
    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: (_ for _ in ()).throw(
            move.OverwriteNeeded("slug 'feat-x' already exists here — pass --overwrite")
        ),
    )
    produced.add(
        _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])
    )

    # 9 EXIT_PHASE_FAILED — a move phase failed after a clean preflight
    env9 = _Env(tmp_path / "phasefailed")
    env9.write_group(excluded={"repo_a": []})
    env9.write_hosts(self_name="host-a", peers={"host-b": "host-b"})
    env9.write_manifest(owner="host-a")
    env9.apply(monkeypatch)
    _fake_probe(monkeypatch, _clean_probe_answer())
    _no_conversations(monkeypatch)
    monkeypatch.setattr(
        move,
        "move_workspace",
        lambda **kw: (_ for _ in ()).throw(move.PhaseFailed("history (repo_a)", "boom")),
    )
    produced.add(
        _run(monkeypatch, ["transfer", "feat-x", "--to", "host-b", "--group", "trailhead"])
    )

    documented = _readme_exit_codes()
    assert produced == {0, 1, 3, 4, 5, 6, 7, 8, 9}
    assert documented == produced
