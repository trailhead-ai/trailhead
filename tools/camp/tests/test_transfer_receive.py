"""Tests for `camp transfer-receive` — the peer side of a workspace move.

Test contract (all must RED before implementation, GREEN after):

- `begin` on a host where the group is not configured refuses by name and
  writes nothing — the state directory is byte-identical across the call.
- `begin` against a slug present here and owned by a third host refuses,
  names both the owner and the sender, and leaves that workspace untouched.
- `begin` against a slug present here and owned by the sender, WITHOUT
  `--overwrite`, refuses and leaves that workspace byte-identical.
- `begin` against that same slug WITH `--overwrite` removes it and re-seeds
  — a file written into the prior attempt's workspace is gone afterwards.
- the seeded manifest's `owner` is the sender's name, not this host's
  declared name, on both the fresh and the overwrite path.
- `begin`'s answer reports a real basis commit for a member whose base ref
  resolves here, and null for one whose base ref does not.
- `finish` spawns the provisioner and returns without waiting.
- a malformed or over-long `--owner` is refused before parsing, like the
  probe's own bound.
- the marker records the phase reached and its outcome, read back through
  `read_transfer_marker`, and a transfer that dies after `begin` leaves a
  marker naming `begin` as the last phase reached.
- a refusal writes no marker into a workspace it refused to touch.
- `seed_pending_workspace`'s new `owner=` is optional and keyword-only: the
  default path (no `owner` passed) keeps self-stamping unchanged.

`conversations` (all must RED before implementation, GREEN after):

- a conversation arriving for the workspace root lands at the destination the
  harness boundary composes for the peer's own workspace directory, and its
  recorded root reads as that directory afterwards.
- a conversation arriving with a member subpath lands rooted at the peer's
  corresponding subdirectory, not at the workspace root.
- a subpath carrying a `..` segment, an absolute subpath, or a subpath whose
  resolution escapes the workspace through a real symlink is refused before
  anything is written, naming the offending subpath.
- a destination-key collision (two distinct workspace directories that munge
  to the same projects key) becomes a named refusal, not an unhandled
  exception and not a silent write — provable only because the first
  conversation's write completes, and is visible on disk, before the second
  conversation's destination is composed.
- re-running the phase for a conversation already placed overwrites it rather
  than failing or duplicating.
- a destination whose parent directory does not exist yet is created by this
  phase itself.
- a nested transcript under the conversation's own subagent/tool-result
  directory carries its own recorded root and is rewritten too, not only the
  top-level transcript.
- a nested transcript recording a root outside the workspace refuses the
  whole placement by name, and nothing of the conversation is left at the
  destination.
- an unresolvable recorded root (the peer cannot determine where the
  conversation ran) refuses by name rather than silently landing an
  un-rewritten transcript, and nothing of the conversation is left behind.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from ._helpers import camp_state_env, init_git_repo

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _receive_module():
    import importlib

    return importlib.import_module("camp.transfer.receive")


def _make_group(name: str, members: list[dict], *, branch_pattern="worktree-{slug}") -> dict:
    return {"group": {"name": name}, "members": members, "branch_pattern": branch_pattern}


def _snapshot(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        str(p.relative_to(root)): p.read_bytes().hex()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture()
def one_member_group(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    init_git_repo(repo_a, origin=True)
    group = _make_group(
        "testgroup",
        [{"name": "repo_a", "repo_root": str(repo_a), "tasks": [], "base": "origin/main"}],
    )
    env = camp_state_env(tmp_path)
    return {"group": group, "repo_a": repo_a, "env": env, "tmp_path": tmp_path}


@pytest.fixture()
def two_member_group(tmp_path: Path):
    """One member whose base ref resolves locally, one whose does not."""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    init_git_repo(repo_a, origin=True)
    init_git_repo(repo_b, origin=False)  # no origin remote — "origin/main" won't resolve
    group = _make_group(
        "testgroup",
        [
            {"name": "repo_a", "repo_root": str(repo_a), "tasks": [], "base": "origin/main"},
            {"name": "repo_b", "repo_root": str(repo_b), "tasks": [], "base": "origin/main"},
        ],
    )
    env = camp_state_env(tmp_path)
    return {"group": group, "repo_a": repo_a, "repo_b": repo_b, "env": env, "tmp_path": tmp_path}


def _workspace_dir(group_name: str, slug: str, env):
    from camp.group.manifest import workspace_dir

    return workspace_dir(group_name, slug, env=env)


def _manifest_path(group_name: str, slug: str, env):
    from camp.group.manifest import manifest_path_for

    return manifest_path_for(group_name, slug, env=env)


# ---------------------------------------------------------------------------
# seed_pending_workspace(owner=) — default path unchanged, new path threads owner
# ---------------------------------------------------------------------------


class TestSeedPendingWorkspaceOwnerParameter:
    def test_default_path_keeps_self_stamping_unchanged(self, one_member_group, tmp_path):
        """Calling seed_pending_workspace with no owner= still self-stamps from
        this host's declared name — the pre-existing call site's behaviour."""
        from camp.provision.provision import seed_pending_workspace
        from camp.group.manifest import owner_of, read_central_manifest

        g = one_member_group
        config_root = Path(g["env"]["CAMP_STATE_DIR"]).parent / "config"
        config_root.mkdir(parents=True, exist_ok=True)
        env = dict(g["env"])
        env["CAMP_CONFIG_DIR"] = str(config_root)
        (config_root / "hosts.toml").write_text('self_name = "andromeda"\n', encoding="utf-8")

        mpath = seed_pending_workspace(g["group"], "feat-default", env=env)
        data = read_central_manifest(mpath)
        assert owner_of(data) == "andromeda"

    def test_explicit_owner_stamps_that_name_not_self_declared(self, one_member_group, tmp_path):
        """Passing owner= explicitly stamps THAT name, even when this host has
        declared a different self_name — proves the sender's name wins."""
        from camp.provision.provision import seed_pending_workspace
        from camp.group.manifest import owner_of, read_central_manifest

        g = one_member_group
        config_root = Path(g["env"]["CAMP_STATE_DIR"]).parent / "config"
        config_root.mkdir(parents=True, exist_ok=True)
        env = dict(g["env"])
        env["CAMP_CONFIG_DIR"] = str(config_root)
        (config_root / "hosts.toml").write_text('self_name = "andromeda"\n', encoding="utf-8")

        mpath = seed_pending_workspace(g["group"], "feat-owner", env=env, owner="sender-host")
        data = read_central_manifest(mpath)
        assert owner_of(data) == "sender-host"


# ---------------------------------------------------------------------------
# begin — refusals
# ---------------------------------------------------------------------------


class TestBeginRefusals:
    def test_refuses_when_group_not_configured_and_writes_nothing(self, tmp_path):
        receive = _receive_module()

        env = camp_state_env(tmp_path)
        state_dir = Path(env["CAMP_STATE_DIR"])
        before = _snapshot(state_dir)

        with pytest.raises(receive.GroupNotConfigured) as exc_info:
            receive.begin(
                groups=[],
                group_name="testgroup",
                slug="feat-x",
                sender="sender-host",
                overwrite=False,
                env=env,
            )
        assert "testgroup" in str(exc_info.value)
        assert _snapshot(state_dir) == before

    def test_refuses_slug_owned_by_third_host_and_leaves_it_untouched(
        self, one_member_group
    ):
        receive = _receive_module()
        g = one_member_group

        # A prior transfer already seeded this slug, owned by a third host.
        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            sender="third-host",
            overwrite=False,
            env=g["env"],
        )
        mpath = _manifest_path("testgroup", "feat-x", g["env"])
        before = mpath.read_bytes()

        with pytest.raises(receive.OwnershipConflict) as exc_info:
            receive.begin(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                sender="sending-host",
                overwrite=True,  # even with --overwrite, a third host's claim wins
                env=g["env"],
            )
        message = str(exc_info.value)
        assert "third-host" in message
        assert "sending-host" in message
        assert mpath.read_bytes() == before

    def test_refuses_sender_owned_slug_without_overwrite(self, one_member_group):
        receive = _receive_module()
        g = one_member_group

        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            sender="sending-host",
            overwrite=False,
            env=g["env"],
        )
        mpath = _manifest_path("testgroup", "feat-x", g["env"])
        before = mpath.read_bytes()

        with pytest.raises(receive.OverwriteRequired) as exc_info:
            receive.begin(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                sender="sending-host",
                overwrite=False,
                env=g["env"],
            )
        assert mpath.read_bytes() == before
        # Ownership deliberately never moves in this stage, so any
        # uncommitted/untracked work that accumulated in the peer's copy
        # since arrival has no way back to the sender — --overwrite
        # destroys it outright, and the refusal an operator reads here must
        # say so before they pass the flag.
        message = str(exc_info.value)
        assert "uncommitted" in message or "untracked" in message
        assert "destroy" in message or "discard" in message

    def test_malformed_owner_refused_before_any_write(self, tmp_path):
        receive = _receive_module()

        env = camp_state_env(tmp_path)
        state_dir = Path(env["CAMP_STATE_DIR"])
        before = _snapshot(state_dir)

        oversized = "a" * (receive.MAX_OWNER_NAME_BYTES + 1)
        with pytest.raises(receive.MalformedOwnerName):
            receive.begin(
                groups=[],
                group_name="testgroup",
                slug="feat-x",
                sender=oversized,
                overwrite=False,
                env=env,
            )
        assert _snapshot(state_dir) == before

    def test_owner_exactly_at_the_byte_bound_is_not_malformed(self, tmp_path):
        """The bound refuses what's OVER it, not what's exactly at it — an
        owner name of precisely MAX_OWNER_NAME_BYTES bytes is accepted (it
        goes on to refuse for the unrelated reason that no group is
        configured, proving _validate_owner let it through)."""
        receive = _receive_module()
        env = camp_state_env(tmp_path)

        at_bound = "a" * receive.MAX_OWNER_NAME_BYTES
        with pytest.raises(receive.GroupNotConfigured):
            receive.begin(
                groups=[],
                group_name="testgroup",
                slug="feat-x",
                sender=at_bound,
                overwrite=False,
                env=env,
            )

    def test_malformed_owner_charset_refused(self, tmp_path):
        receive = _receive_module()

        env = camp_state_env(tmp_path)
        with pytest.raises(receive.MalformedOwnerName):
            receive.begin(
                groups=[],
                group_name="testgroup",
                slug="feat-x",
                sender="Not Valid!",
                overwrite=False,
                env=env,
            )


# ---------------------------------------------------------------------------
# begin — overwrite removes and re-seeds; owner is always the sender's
# ---------------------------------------------------------------------------


class TestBeginOverwrite:
    def test_overwrite_removes_prior_attempt_and_reseeds(self, one_member_group):
        """A retry after a prior attempt materialized real worktree content
        (what the history/worktree phases produce) removes that content —
        `--overwrite` tears down through camp's own reconcile_break, which
        only succeeds against a real git worktree, so this sets one up."""
        receive = _receive_module()
        g = one_member_group

        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            sender="sending-host",
            overwrite=False,
            env=g["env"],
        )
        ws_dir = _workspace_dir("testgroup", "feat-x", g["env"])
        wt_path = ws_dir / "repo_a"
        subprocess.run(
            ["git", "-C", str(g["repo_a"]), "worktree", "add", "-b", "worktree-feat-x", str(wt_path)],
            check=True,
            capture_output=True,
        )
        leftover = wt_path / "leftover-from-prior-attempt.txt"
        leftover.write_text("stale content from a failed attempt\n", encoding="utf-8")
        assert leftover.exists()

        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            sender="sending-host",
            overwrite=True,
            env=g["env"],
        )

        assert not leftover.exists()
        assert not wt_path.exists()

    def test_owner_is_sender_not_this_hosts_declared_name_fresh_path(
        self, one_member_group
    ):
        receive = _receive_module()
        g = one_member_group

        config_root = Path(g["env"]["CAMP_STATE_DIR"]).parent / "config"
        config_root.mkdir(parents=True, exist_ok=True)
        env = dict(g["env"])
        env["CAMP_CONFIG_DIR"] = str(config_root)
        (config_root / "hosts.toml").write_text('self_name = "receiving-host"\n', encoding="utf-8")

        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-fresh",
            sender="sending-host",
            overwrite=False,
            env=env,
        )

        from camp.group.manifest import owner_of, read_central_manifest

        mpath = _manifest_path("testgroup", "feat-fresh", env)
        assert owner_of(read_central_manifest(mpath)) == "sending-host"

    def test_owner_is_sender_not_this_hosts_declared_name_overwrite_path(
        self, one_member_group
    ):
        receive = _receive_module()
        g = one_member_group

        config_root = Path(g["env"]["CAMP_STATE_DIR"]).parent / "config"
        config_root.mkdir(parents=True, exist_ok=True)
        env = dict(g["env"])
        env["CAMP_CONFIG_DIR"] = str(config_root)
        (config_root / "hosts.toml").write_text('self_name = "receiving-host"\n', encoding="utf-8")

        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-ow",
            sender="sending-host",
            overwrite=False,
            env=env,
        )
        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-ow",
            sender="sending-host",
            overwrite=True,
            env=env,
        )

        from camp.group.manifest import owner_of, read_central_manifest

        mpath = _manifest_path("testgroup", "feat-ow", env)
        assert owner_of(read_central_manifest(mpath)) == "sending-host"


# ---------------------------------------------------------------------------
# begin — basis commit answer
# ---------------------------------------------------------------------------


class TestBeginBasisCommitAnswer:
    def test_reports_real_commit_for_resolvable_base_and_null_for_unresolvable(
        self, two_member_group
    ):
        receive = _receive_module()
        g = two_member_group

        from camp.gitutil import _git_out

        expected_commit = _git_out(g["repo_a"], "rev-parse", "origin/main")
        assert expected_commit

        answer = receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-basis",
            sender="sending-host",
            overwrite=False,
            env=g["env"],
        )

        by_name = {m["name"]: m["basis_commit"] for m in answer["members"]}
        assert by_name["repo_a"] == expected_commit
        assert by_name["repo_b"] is None


# ---------------------------------------------------------------------------
# finish — spawns without waiting
# ---------------------------------------------------------------------------


class TestFinish:
    def test_spawns_provisioner_and_returns_without_waiting(self, one_member_group, monkeypatch):
        import camp.provision.provision as provision

        g = one_member_group
        receive = _receive_module()

        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-finish",
            sender="sending-host",
            overwrite=False,
            env=g["env"],
        )

        spawned: list[subprocess.Popen] = []

        def _fake_spawn(*, group_name, slug, logfile_path, _argv=None):
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(0.6)"],
            )
            spawned.append(proc)
            return proc

        monkeypatch.setattr(provision, "spawn_detached_provisioner", _fake_spawn)

        answer = receive.finish(
            groups=[g["group"]], group_name="testgroup", slug="feat-finish", env=g["env"]
        )

        assert len(spawned) == 1
        # finish returned before the (intentionally slow) provisioner exited.
        assert spawned[0].poll() is None
        assert answer["contract_version"] == receive.RECEIVE_CONTRACT_VERSION

        spawned[0].wait(timeout=5)

    def test_refuses_when_group_not_configured(self, tmp_path):
        receive = _receive_module()
        env = camp_state_env(tmp_path)

        with pytest.raises(receive.GroupNotConfigured):
            receive.finish(groups=[], group_name="testgroup", slug="feat-x", env=env)


# ---------------------------------------------------------------------------
# the durable per-transfer marker
# ---------------------------------------------------------------------------


class TestTransferMarker:
    def test_begin_then_no_finish_leaves_begin_as_last_phase_reached(
        self, one_member_group, monkeypatch
    ):
        receive = _receive_module()
        g = one_member_group

        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-marker",
            sender="sending-host",
            overwrite=False,
            env=g["env"],
        )

        ws_dir = _workspace_dir("testgroup", "feat-marker", g["env"])
        entries = receive.read_transfer_marker(ws_dir)

        assert entries[-1].phase == "begin"
        assert entries[-1].outcome == "ok"

    def test_finish_appends_its_own_phase_after_begins(self, one_member_group, monkeypatch):
        import camp.provision.provision as provision

        g = one_member_group
        receive = _receive_module()

        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-marker2",
            sender="sending-host",
            overwrite=False,
            env=g["env"],
        )
        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
        receive.finish(
            groups=[g["group"]], group_name="testgroup", slug="feat-marker2", env=g["env"]
        )

        ws_dir = _workspace_dir("testgroup", "feat-marker2", g["env"])
        entries = receive.read_transfer_marker(ws_dir)
        phases = [e.phase for e in entries]
        assert phases == ["begin", "finish"]

    def test_a_refusal_writes_no_marker_into_the_workspace_it_refused(
        self, one_member_group
    ):
        receive = _receive_module()
        g = one_member_group

        receive.begin(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-refused",
            sender="sending-host",
            overwrite=False,
            env=g["env"],
        )
        ws_dir = _workspace_dir("testgroup", "feat-refused", g["env"])
        before_entries = receive.read_transfer_marker(ws_dir)

        with pytest.raises(receive.OverwriteRequired):
            receive.begin(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-refused",
                sender="sending-host",
                overwrite=False,
                env=g["env"],
            )

        after_entries = receive.read_transfer_marker(ws_dir)
        assert after_entries == before_entries


# ---------------------------------------------------------------------------
# CLI wiring — `camp transfer-receive begin|finish` end to end
# ---------------------------------------------------------------------------


def _dispatch_module():
    import importlib

    return importlib.import_module("camp.cli.dispatch")


def _run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """Run `camp <argv>` through the real dispatcher; return its exit code."""
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    with pytest.raises(SystemExit) as exc_info:
        dispatch.main()
    return exc_info.value.code


def _write_group_toml(groups_dir: Path, name: str, members: list[tuple[str, str]]) -> None:
    groups_dir.mkdir(parents=True, exist_ok=True)
    member_tables = "\n\n".join(
        f'[[members]]\nname = "{member_name}"\nrepo_root = "{repo_root}"'
        for member_name, repo_root in members
    )
    (groups_dir / f"{name}.toml").write_text(f'[group]\nname = "{name}"\n\n{member_tables}\n')


def test_transfer_receive_refuses_host_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))

    code = _run_cli(
        monkeypatch,
        ["transfer-receive", "begin", "--group", "testgroup", "--slug", "x", "--host", "peer"],
    )
    assert code != 0
    assert "--host" in capsys.readouterr().err


def test_transfer_receive_begin_requires_owner_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))

    code = _run_cli(
        monkeypatch, ["transfer-receive", "begin", "--group", "testgroup", "--slug", "x"]
    )
    assert code != 0
    assert "--owner" in capsys.readouterr().err


def test_transfer_receive_begin_cli_end_to_end_prints_json_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    cfg = tmp_path / "config"
    (cfg / "groups").mkdir(parents=True)
    repo = tmp_path / "repo_a"
    init_git_repo(repo, origin=True)
    _write_group_toml(cfg / "groups", "testgroup", [("repo_a", str(repo))])

    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "camp",
            "transfer-receive",
            "begin",
            "--group",
            "testgroup",
            "--slug",
            "feat-cli",
            "--owner",
            "sending-host",
        ],
    )

    _dispatch_module().main()  # success path returns normally, no SystemExit

    payload = json.loads(capsys.readouterr().out)
    assert payload["contract_version"] == 1
    names = {m["name"] for m in payload["members"]}
    assert names == {"repo_a"}


# ---------------------------------------------------------------------------
# Reservation — a workspace slug named "transfer-receive" cannot shadow the verb
# ---------------------------------------------------------------------------


class _ReachedSpineFallback(Exception):
    """Sentinel proving the group-aware router treated its token as RESERVED
    and fell through to spine's fallback branch, rather than dying immediately
    on the removed-bare-slug path."""


def test_transfer_receive_slug_reaches_the_reserved_fallback_unlike_an_ordinary_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_dispatch_group_command` is the ONLY consumer of RESERVED (cli/dispatch.py
    consults it once, to decide between two branches for a token that matched no
    known verb): a reserved token falls through to spine's fallback branch,
    while an ordinary slug dies immediately as a removed bare-slug dispatch.
    `transfer-probe` already takes the reserved branch (test_transfer_probe.py's
    sibling section); `transfer-receive` must take the same branch, dispatched
    from the same pre-group-resolution position (cli/dispatch.py:504, mirroring
    transfer-probe's :497) — so a slug of that name is exactly what RESERVED
    exists to reject. Varying the slug across the reserved token and an ordinary
    one must land in the two different branches."""
    from camp import spine
    from camp.cli import dispatch as dispatch_mod

    def _fake_spine_main() -> None:
        raise _ReachedSpineFallback()

    monkeypatch.setattr(spine, "main", _fake_spine_main)

    group = {"group": {"name": "testgroup"}, "members": []}

    # Reserved: falls through to the spine fallback branch (our sentinel fires).
    with pytest.raises(_ReachedSpineFallback):
        dispatch_mod._dispatch_group_command("transfer-receive", [], group, {}, False)

    # Not reserved: dies immediately on the removed-bare-slug path, never
    # reaching the spine fallback our sentinel would have caught.
    with pytest.raises(SystemExit):
        dispatch_mod._dispatch_group_command("totally-unclaimed-slug-zzz", [], group, {}, False)


def test_transfer_receive_begin_still_dispatches_when_another_workspace_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The reservation must not break ordinary routing: `camp transfer-receive
    begin …` still reaches the real verb handler on a host that already has a
    workspace under some OTHER slug."""
    from camp.provision.provision import seed_pending_workspace

    cfg = tmp_path / "config"
    (cfg / "groups").mkdir(parents=True)
    repo = tmp_path / "repo_a"
    init_git_repo(repo, origin=True)
    _write_group_toml(cfg / "groups", "testgroup", [("repo_a", str(repo))])

    state_dir = tmp_path / "state"
    env = {"CAMP_CONFIG_DIR": str(cfg), "CAMP_STATE_DIR": str(state_dir)}
    group = _make_group("testgroup", [{"name": "repo_a", "repo_root": str(repo)}])
    seed_pending_workspace(group, "some-other-workspace", env=env)

    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(state_dir))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "camp",
            "transfer-receive",
            "begin",
            "--group",
            "testgroup",
            "--slug",
            "feat-cli-2",
            "--owner",
            "sending-host",
        ],
    )

    _dispatch_module().main()  # success path returns normally, no SystemExit

    payload = json.loads(capsys.readouterr().out)
    assert payload["contract_version"] == 1
    names = {m["name"] for m in payload["members"]}
    assert names == {"repo_a"}


# ---------------------------------------------------------------------------
# conversations — peer placement, confinement, collision, re-run, mkdir
# ---------------------------------------------------------------------------


def _conversation_env(g: dict) -> dict[str, str]:
    """*g*'s own `camp_state_env`, plus a fresh, never-yet-created Claude Code
    config dir — so a test can assert on whether `projects/` gets created."""
    env = dict(g["env"])
    env["TRAILHEAD_CLAUDE_DIR"] = str(g["tmp_path"] / "claude-dir")
    return env


def _archive_bytes(transcript: bytes, nested: dict[str, bytes] | None = None) -> bytes:
    """A tar stream shaped exactly like
    `camp.transfer.conversations.write_conversation_archive`'s output: a
    `transcript.jsonl` member, then whatever *nested* names."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("transcript.jsonl")
        info.size = len(transcript)
        tf.addfile(info, io.BytesIO(transcript))
        for name, content in (nested or {}).items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _seed_workspace(g: dict, slug: str) -> Path:
    from camp.group.manifest import workspace_dir
    from camp.provision.provision import seed_pending_workspace

    seed_pending_workspace(g["group"], slug, env=g["env"])
    return workspace_dir("testgroup", slug, env=g["env"]).resolve()


class TestConversationSubpathConfinement:
    def test_traversal_segment_refused_and_nothing_written(self, one_member_group):
        """`a/../a` never actually escapes the workspace once resolved — this
        subpath is refused ONLY because it carries a literal `..` segment, a
        rule pinned unconditionally rather than as an incidental side effect
        of the resolved-escape check below."""
        from camp.transfer import receive

        g = one_member_group
        _seed_workspace(g, "feat-x")
        env = _conversation_env(g)
        before = _snapshot(Path(env["TRAILHEAD_CLAUDE_DIR"]))

        with pytest.raises(receive.ConversationSubpathRefused) as exc_info:
            receive.conversations(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                session_id="11111111-1111-4111-8111-111111111111",
                subpath="a/../a",
                archive_stream=io.BytesIO(_archive_bytes(b'{"cwd": "/whatever"}\n')),
                env=env,
            )

        assert "a/../a" in str(exc_info.value)
        assert _snapshot(Path(env["TRAILHEAD_CLAUDE_DIR"])) == before

    def test_absolute_subpath_refused_and_nothing_written(self, one_member_group):
        """Pins the 'no path from the wire' posture on its own terms: the
        absolute path used here happens to resolve INSIDE the workspace (it is
        literally `ws_root / "nested"`), so a resolve-based confinement check
        alone would let it through. It must still be refused for the sole
        reason that it arrived as an absolute path at all."""
        from camp.transfer import receive

        g = one_member_group
        ws_root = _seed_workspace(g, "feat-x")
        env = _conversation_env(g)
        before = _snapshot(Path(env["TRAILHEAD_CLAUDE_DIR"]))
        coincidentally_safe_absolute_subpath = str(ws_root / "nested")

        with pytest.raises(receive.ConversationSubpathRefused) as exc_info:
            receive.conversations(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                session_id="11111111-1111-4111-8111-111111111111",
                subpath=coincidentally_safe_absolute_subpath,
                archive_stream=io.BytesIO(_archive_bytes(b'{"cwd": "/whatever"}\n')),
                env=env,
            )

        assert coincidentally_safe_absolute_subpath in str(exc_info.value)
        assert _snapshot(Path(env["TRAILHEAD_CLAUDE_DIR"])) == before

    def test_symlink_escape_refused_and_nothing_written(self, one_member_group, tmp_path):
        from camp.transfer import receive

        g = one_member_group
        ws_root = _seed_workspace(g, "feat-x")
        outside = tmp_path / "outside-the-workspace"
        outside.mkdir()
        (ws_root / "escape").symlink_to(outside)

        env = _conversation_env(g)
        before = _snapshot(Path(env["TRAILHEAD_CLAUDE_DIR"]))

        with pytest.raises(receive.ConversationSubpathRefused) as exc_info:
            receive.conversations(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                session_id="11111111-1111-4111-8111-111111111111",
                subpath="escape/conv",
                archive_stream=io.BytesIO(_archive_bytes(b'{"cwd": "/whatever"}\n')),
                env=env,
            )

        assert "escape/conv" in str(exc_info.value)
        assert _snapshot(Path(env["TRAILHEAD_CLAUDE_DIR"])) == before


class TestConversationRootLanding:
    def test_conversation_at_workspace_root_lands_and_rewrites_recorded_root(
        self, one_member_group
    ):
        from camp.transfer import receive
        from trailhead.harness.claude_code import ClaudeCodeHarness

        g = one_member_group
        ws_root = _seed_workspace(g, "feat-x")
        env = _conversation_env(g)
        session_id = "11111111-1111-4111-8111-111111111111"

        # The Claude Code config dir has never been touched — proves this
        # phase creates its own destination parents, not just writes into an
        # already-provisioned tree.
        assert not (Path(env["TRAILHEAD_CLAUDE_DIR"]) / "projects").exists()

        archive = _archive_bytes(
            json.dumps({"cwd": "/home/sender/some-other-workspace", "type": "summary"}).encode()
            + b"\n"
        )

        result = receive.conversations(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            session_id=session_id,
            subpath=".",
            archive_stream=io.BytesIO(archive),
            env=env,
        )

        assert result["session_id"] == session_id

        harness = ClaudeCodeHarness()
        found = harness.session_transcript_path(session_id, ws_root, env=env)
        assert found is not None
        record = json.loads(found.read_text().splitlines()[0])
        assert record["cwd"] == str(ws_root)
        assert record["type"] == "summary"


class TestConversationMemberSubpathLanding:
    def test_conversation_with_member_subpath_lands_at_corresponding_subdirectory(
        self, one_member_group
    ):
        from camp.transfer import receive
        from trailhead.harness.claude_code import ClaudeCodeHarness

        g = one_member_group
        ws_root = _seed_workspace(g, "feat-x")
        member_dir = ws_root / "repo_a"
        member_dir.mkdir(parents=True, exist_ok=True)
        env = _conversation_env(g)
        session_id = "22222222-2222-4222-8222-222222222222"

        archive = _archive_bytes(
            json.dumps({"cwd": "/home/sender/some-workspace/repo_a"}).encode() + b"\n"
        )

        receive.conversations(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            session_id=session_id,
            subpath="repo_a",
            archive_stream=io.BytesIO(archive),
            env=env,
        )

        harness = ClaudeCodeHarness()
        found_at_member = harness.session_transcript_path(session_id, member_dir, env=env)
        found_at_root = harness.session_transcript_path(session_id, ws_root, env=env)
        # The property a same-subpath test cannot show: varying the subpath
        # varies WHERE the conversation is found.
        assert found_at_member is not None
        assert found_at_root is None
        record = json.loads(found_at_member.read_text().splitlines()[0])
        assert record["cwd"] == str(member_dir.resolve())


class TestConversationDestinationCollision:
    def test_collision_refused_after_first_write_second_never_touches_disk(
        self, one_member_group
    ):
        """Two DIFFERENT resolved directories — `repo.a` and `repo/a` — munge
        to the SAME projects key (the lossy `/` and `.` collapse). The first
        conversation's write is what lets the second one's destination
        composition detect the collision at all — pinning the ordering
        contract alongside the refusal itself."""
        from camp.transfer import receive
        from trailhead.harness.claude_code import ClaudeCodeHarness

        g = one_member_group
        ws_root = _seed_workspace(g, "feat-x")
        (ws_root / "repo.a").mkdir(parents=True, exist_ok=True)
        (ws_root / "repo" / "a").mkdir(parents=True, exist_ok=True)
        env = _conversation_env(g)

        session_a = "33333333-3333-4333-8333-333333333333"
        session_b = "44444444-4444-4444-8444-444444444444"

        receive.conversations(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            session_id=session_a,
            subpath="repo.a",
            archive_stream=io.BytesIO(
                _archive_bytes(json.dumps({"cwd": "/sender/a"}).encode() + b"\n")
            ),
            env=env,
        )

        with pytest.raises(receive.ConversationDestinationRefused) as exc_info:
            receive.conversations(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                session_id=session_b,
                subpath="repo/a",
                archive_stream=io.BytesIO(
                    _archive_bytes(json.dumps({"cwd": "/sender/b"}).encode() + b"\n")
                ),
                env=env,
            )

        assert session_b in str(exc_info.value)

        harness = ClaudeCodeHarness()
        found_a = harness.session_transcript_path(
            session_a, (ws_root / "repo.a").resolve(), env=env
        )
        found_b = harness.session_transcript_path(
            session_b, (ws_root / "repo" / "a").resolve(), env=env
        )
        assert found_a is not None
        assert found_b is None


class TestConversationRerun:
    def test_rerunning_overwrites_rather_than_duplicating(self, one_member_group):
        from camp.transfer import receive
        from trailhead.harness.claude_code import ClaudeCodeHarness

        g = one_member_group
        ws_root = _seed_workspace(g, "feat-x")
        env = _conversation_env(g)
        session_id = "55555555-5555-4555-8555-555555555555"

        for n in (1, 2):
            receive.conversations(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                session_id=session_id,
                subpath=".",
                archive_stream=io.BytesIO(
                    _archive_bytes(
                        json.dumps({"cwd": f"/sender/attempt-{n}", "n": n}).encode() + b"\n"
                    )
                ),
                env=env,
            )

        harness = ClaudeCodeHarness()
        found = harness.session_transcript_path(session_id, ws_root, env=env)
        lines = found.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["n"] == 2
        assert record["cwd"] == str(ws_root)


class TestConversationArchiveMemberConfinement:
    def test_nested_member_traversal_refused_by_name(self, one_member_group):
        from camp.transfer import receive

        g = one_member_group
        _seed_workspace(g, "feat-x")
        env = _conversation_env(g)
        session_id = "66666666-6666-4666-8666-666666666666"

        archive = _archive_bytes(
            json.dumps({"cwd": "/sender/root"}).encode() + b"\n",
            nested={"../../etc/passwd": b"pwned"},
        )

        with pytest.raises(receive.ArchiveMemberRefused) as exc_info:
            receive.conversations(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                session_id=session_id,
                subpath=".",
                archive_stream=io.BytesIO(archive),
                env=env,
            )

        assert "../../etc/passwd" in str(exc_info.value)


class TestConversationNestedSubtreeRewrite:
    def test_nested_transcript_recorded_root_rewritten(self, one_member_group):
        from camp.transfer import receive
        from trailhead.harness.claude_code import ClaudeCodeHarness

        g = one_member_group
        ws_root = _seed_workspace(g, "feat-x")
        env = _conversation_env(g)
        session_id = "77777777-7777-4777-8777-777777777777"
        sender_root = "/home/sender/some-other-workspace"

        nested_line = json.dumps({"cwd": sender_root, "type": "agent"}).encode() + b"\n"
        archive = _archive_bytes(
            json.dumps({"cwd": sender_root}).encode() + b"\n",
            nested={"subagents/agent-1.jsonl": nested_line},
        )

        receive.conversations(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            session_id=session_id,
            subpath=".",
            archive_stream=io.BytesIO(archive),
            env=env,
        )

        harness = ClaudeCodeHarness()
        destination = harness.session_transcript_path(session_id, ws_root, env=env)
        assert destination is not None
        nested_path = destination.parent / session_id / "subagents" / "agent-1.jsonl"
        assert nested_path.is_file()
        record = json.loads(nested_path.read_text().splitlines()[0])
        assert record["cwd"] == str(ws_root)
        assert record["type"] == "agent"


class TestConversationNestedRootOutsideWorkspaceRefused:
    def test_nested_transcript_foreign_root_refuses_and_leaves_nothing(self, one_member_group):
        from camp.transfer import receive
        from trailhead.harness.claude_code import ClaudeCodeHarness

        g = one_member_group
        ws_root = _seed_workspace(g, "feat-x")
        env = _conversation_env(g)
        session_id = "88888888-8888-4888-8888-888888888888"
        sender_root = "/home/sender/some-other-workspace"

        foreign_nested = json.dumps({"cwd": "/entirely/unrelated/root"}).encode() + b"\n"
        archive = _archive_bytes(
            json.dumps({"cwd": sender_root}).encode() + b"\n",
            nested={"subagents/agent-1.jsonl": foreign_nested},
        )

        with pytest.raises(receive.ConversationDestinationRefused) as exc_info:
            receive.conversations(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                session_id=session_id,
                subpath=".",
                archive_stream=io.BytesIO(archive),
                env=env,
            )

        assert session_id in str(exc_info.value)

        harness = ClaudeCodeHarness()
        assert harness.session_transcript_path(session_id, ws_root, env=env) is None
        claude_dir = Path(env["TRAILHEAD_CLAUDE_DIR"])
        assert list(claude_dir.rglob(f"{session_id}*")) == []
        # The staged rewrites are dot-prefixed, so the sweep above cannot see
        # them: a leftover would be an un-rewritten transcript surviving a
        # refusal under a name nothing else looks for.
        assert list(claude_dir.rglob("*rewrite-staged*")) == []


class TestConversationUnknownRootRefused:
    def test_unresolvable_recorded_root_refuses_named_and_leaves_nothing(self, one_member_group):
        from camp.transfer import receive
        from trailhead.harness.claude_code import ClaudeCodeHarness

        g = one_member_group
        ws_root = _seed_workspace(g, "feat-x")
        env = _conversation_env(g)
        session_id = "99999999-9999-4999-8999-999999999999"

        archive = _archive_bytes(json.dumps({"type": "summary"}).encode() + b"\n")

        with pytest.raises(receive.ConversationRootUnresolved) as exc_info:
            receive.conversations(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                session_id=session_id,
                subpath=".",
                archive_stream=io.BytesIO(archive),
                env=env,
            )

        assert session_id in str(exc_info.value)

        harness = ClaudeCodeHarness()
        assert harness.session_transcript_path(session_id, ws_root, env=env) is None
        claude_dir = Path(env["TRAILHEAD_CLAUDE_DIR"])
        assert list(claude_dir.rglob(f"{session_id}*")) == []
        # The staged rewrites are dot-prefixed, so the sweep above cannot see
        # them: a leftover would be an un-rewritten transcript surviving a
        # refusal under a name nothing else looks for.
        assert list(claude_dir.rglob("*rewrite-staged*")) == []
