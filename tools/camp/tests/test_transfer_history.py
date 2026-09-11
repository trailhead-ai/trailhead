"""Tests for `camp transfer-receive history` — one member's committed history,
crossing directly from sender to peer.

Test contract (all must RED before implementation, GREEN after):

- a commit made on the sender's branch and never pushed anywhere is present
  in the peer's repository after the phase, reachable from the slug branch.
- the peer's worktree for that member is checked out at that commit.
- no git remote is contacted: a sender whose `origin` points at an
  unreachable URL still succeeds the phase.
- the bundle is negatived — a transfer whose peer already holds the base
  sends a bundle materially smaller than a full-history one, and both still
  produce the same checked-out commit.
- a peer that reports a basis commit it does not actually hold fails the
  phase by name rather than leaving a half-unbundled repository.
- re-running the phase against a peer that already has the branch
  force-updates it rather than refusing.
- `camp transfer-receive history` is reachable as a real command through the
  actual `camp` dispatcher, not only by calling `history()` directly.
- a basis commit the SENDER does not hold locally is not negatived against —
  `build_bundle_argv` falls back to a full bundle rather than handing git a
  `--not <sha>` it cannot resolve, and `send_history` still succeeds end to
  end when the peer reported such a commit.
- a basis commit that is not a plausible git object-id shape is refused with
  a clear message before it reaches git's own argv parsing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ._helpers import camp_state_env, init_git_repo

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# Setup fixtures here (e.g. `_bundle_bytes`, `receive.history` called directly)
# run before `dispatch.main()` has bootstrapped `trailhead.paths` — see
# test_transfer_cli.py's identical bootstrap for why this is done up front
# rather than relying on test execution order.
import _bootstrap  # noqa: E402

_bootstrap.ensure_trailhead_importable()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _git_out(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def _make_group(name: str, members: list[dict]) -> dict:
    return {"group": {"name": name}, "members": members, "branch_pattern": "worktree-{slug}"}


def _member_group(sender_repo: Path, name: str = "repo_a") -> dict:
    return _make_group(
        "testgroup",
        [{"name": name, "repo_root": str(sender_repo), "tasks": [], "base": "origin/main"}],
    )


# ---------------------------------------------------------------------------
# history() called directly — the peer-side unit
# ---------------------------------------------------------------------------


@pytest.fixture()
def sender_and_peer(tmp_path: Path):
    """A sender repo with a workspace branch carrying one unpushed commit, and
    a bare-bones peer repo (no origin) the phase will land content into."""
    sender_repo = tmp_path / "sender"
    init_git_repo(sender_repo, origin=True)
    branch = "worktree-feat-x"
    _git(sender_repo, "checkout", "-b", branch)
    (sender_repo / "unpushed.txt").write_text("only on the sender\n")
    _git(sender_repo, "add", "unpushed.txt")
    _git(sender_repo, "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "unpushed", "--no-gpg-sign")
    tip = _git_out(sender_repo, "rev-parse", "HEAD")

    # A distinct root commit (own content), never `init_git_repo`'s fixed
    # README/message/identity — with those held identical, a peer built the
    # same way as the sender can coincidentally hash to the SAME sha as the
    # sender's own root commit, which would make TestMissingPrerequisite's
    # "peer lacks the prerequisite" premise false by accident.
    peer_repo = tmp_path / "peer_repo_a"
    peer_repo.mkdir(parents=True)
    _git(peer_repo, "init", "-q", "-b", "main")
    (peer_repo / "peer-only.md").write_text("# peer's own unrelated history\n")
    _git(peer_repo, "add", "peer-only.md")
    _git(
        peer_repo,
        "-c",
        "user.email=peer@test.com",
        "-c",
        "user.name=Peer",
        "commit",
        "-m",
        "peer init",
        "--no-gpg-sign",
    )

    env = camp_state_env(tmp_path)
    group = _member_group(peer_repo)
    return {
        "sender_repo": sender_repo,
        "peer_repo": peer_repo,
        "branch": branch,
        "tip": tip,
        "env": env,
        "group": group,
        "tmp_path": tmp_path,
    }


def _bundle_bytes(repo: Path, ref: str, *, basis_commit: str | None = None) -> bytes:
    from camp.transfer.history import build_bundle_argv

    argv = build_bundle_argv(repo, ref, basis_commit=basis_commit)
    result = subprocess.run(argv, capture_output=True, check=True)
    return result.stdout


class TestHistoryLandsContent:
    def test_unpushed_commit_reachable_from_slug_branch_on_peer(self, sender_and_peer):
        from camp.transfer import receive

        g = sender_and_peer
        bundle = _bundle_bytes(g["sender_repo"], g["branch"])

        receive.history(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            member="repo_a",
            bundle_bytes=bundle,
            env=g["env"],
        )

        landed = _git_out(g["peer_repo"], "rev-parse", f"refs/heads/{g['branch']}")
        assert landed == g["tip"]
        # Reachable, not merely a matching sha printed in isolation.
        contains = _git(g["peer_repo"], "branch", "--contains", g["tip"], g["branch"])
        assert g["branch"] in contains.stdout

    def test_peer_worktree_checked_out_at_that_commit(self, sender_and_peer):
        from camp.transfer import receive

        g = sender_and_peer
        bundle = _bundle_bytes(g["sender_repo"], g["branch"])

        receive.history(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            member="repo_a",
            bundle_bytes=bundle,
            env=g["env"],
        )

        from camp.provision.reconcile import _worktree_path

        wt_path = _worktree_path("testgroup", "feat-x", "repo_a", env=g["env"])
        assert (wt_path / "unpushed.txt").read_text() == "only on the sender\n"
        wt_head = _git_out(wt_path, "rev-parse", "HEAD")
        assert wt_head == g["tip"]


class TestNoRemoteContacted:
    def test_phase_succeeds_with_sender_origin_pointed_at_unreachable_url(self, sender_and_peer):
        """The whole content channel is local-object-store to
        local-object-store. Pointing the SENDER's origin at a URL that
        cannot resolve must not matter — nothing on this path ever reads it."""
        from camp.transfer import receive

        g = sender_and_peer
        _git(g["sender_repo"], "remote", "set-url", "origin", "https://nonexistent.invalid.example/repo.git")

        # If anything on this path tried to contact the remote (fetch/push),
        # this would hang or fail against a URL that cannot resolve. It must
        # not: git bundle create only reads the local object store.
        bundle = _bundle_bytes(g["sender_repo"], g["branch"])

        result = receive.history(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            member="repo_a",
            bundle_bytes=bundle,
            env=g["env"],
        )

        assert result["commit"] == g["tip"]
        landed = _git_out(g["peer_repo"], "rev-parse", f"refs/heads/{g['branch']}")
        assert landed == g["tip"]

    def test_send_history_itself_succeeds_with_sender_origin_unreachable(self, tmp_path: Path):
        """Same property, exercised through the actual `send_history` sender
        path (not just the bundle bytes + peer receive) — the production
        function a future change is most likely to add a fetch/push into."""
        from camp.host.config import Host
        from camp.host.transport import Answered
        from camp.transfer.history import send_history

        sender_repo = tmp_path / "sender"
        init_git_repo(sender_repo, origin=True)
        _git(sender_repo, "remote", "set-url", "origin", "https://nonexistent.invalid.example/repo.git")
        branch = "worktree-feat-noremote"
        _git(sender_repo, "checkout", "-b", branch)
        (sender_repo / "f.txt").write_text("x\n")
        _git(sender_repo, "add", "f.txt")
        _git(sender_repo, "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "x", "--no-gpg-sign")
        tip = _git_out(sender_repo, "rev-parse", "HEAD")

        peer_repo = tmp_path / "peer_noremote_repo_a"
        init_git_repo(peer_repo, origin=False)
        peer_cfg = tmp_path / "peer-noremote-config"
        (peer_cfg / "groups").mkdir(parents=True)
        _write_group_toml(peer_cfg / "groups", "testgroup", [("repo_a", str(peer_repo))])
        peer_state = tmp_path / "peer-noremote-state"

        host = Host(ssh="fake-peer", camp_bin="/opt/camp/bin/camp")

        outcome = send_history(
            host,
            group="testgroup",
            slug="feat-noremote",
            member="repo_a",
            repo_root=sender_repo,
            ref=branch,
            basis_commit=None,
            spawn=_peer_process_spawn(peer_cfg, peer_state),
        )

        assert isinstance(outcome, Answered), outcome
        payload = json.loads(outcome.stdout)
        assert payload["commit"] == tip


class TestBundleNegatived:
    def test_negatived_bundle_is_materially_smaller_and_lands_same_commit(self, sender_and_peer):
        from camp.transfer import receive

        g = sender_and_peer
        base_sha = _git_out(g["sender_repo"], "rev-parse", f"{g['branch']}~1")

        full_bundle = _bundle_bytes(g["sender_repo"], g["branch"])
        neg_bundle = _bundle_bytes(g["sender_repo"], g["branch"], basis_commit=base_sha)

        assert len(neg_bundle) < len(full_bundle) * 0.9

        # Full bundle, landed on one peer.
        peer_full_group = _member_group(g["peer_repo"])
        receive.history(
            groups=[peer_full_group],
            group_name="testgroup",
            slug="feat-x",
            member="repo_a",
            bundle_bytes=full_bundle,
            env=g["env"],
        )
        full_landed = _git_out(g["peer_repo"], "rev-parse", f"refs/heads/{g['branch']}")

        # Negatived bundle, landed on a SECOND peer that already holds
        # base_sha as an object — a clone of the sender's `main`, whose tip
        # IS base_sha (the branch under test forked from it with one extra
        # commit), so the prerequisite is already satisfied without any
        # further checkout.
        # Cloned on `main` explicitly — the sender's checked-out HEAD sits on
        # the workspace branch itself, and cloning that as the peer's own
        # sole checkout would make `git worktree add <that branch>` collide
        # with the branch already checked out in the peer's main worktree.
        peer2_repo = g["tmp_path"] / "peer2_repo_a"
        subprocess.run(
            ["git", "clone", "--quiet", "--branch", "main", str(g["sender_repo"]), str(peer2_repo)],
            check=True,
            capture_output=True,
        )
        assert _git_out(peer2_repo, "cat-file", "-t", base_sha) == "commit"
        env2 = camp_state_env(g["tmp_path"] / "peer2-state")
        peer2_group = _member_group(peer2_repo)

        receive.history(
            groups=[peer2_group],
            group_name="testgroup",
            slug="feat-x",
            member="repo_a",
            bundle_bytes=neg_bundle,
            env=env2,
        )
        neg_landed = _git_out(peer2_repo, "rev-parse", f"refs/heads/{g['branch']}")

        assert full_landed == g["tip"]
        assert neg_landed == g["tip"]


class TestMissingPrerequisite:
    def test_peer_missing_reported_basis_fails_by_name(self, sender_and_peer):
        from camp.transfer import receive

        g = sender_and_peer
        base_sha = _git_out(g["sender_repo"], "rev-parse", f"{g['branch']}~1")
        # Peer does NOT actually hold base_sha (fresh unrelated repo) — the
        # negatived bundle references a commit this peer lacks.
        neg_bundle = _bundle_bytes(g["sender_repo"], g["branch"], basis_commit=base_sha)

        with pytest.raises(receive.BundleUnbundleFailed) as exc_info:
            receive.history(
                groups=[g["group"]],
                group_name="testgroup",
                slug="feat-x",
                member="repo_a",
                bundle_bytes=neg_bundle,
                env=g["env"],
            )
        assert "repo_a" in str(exc_info.value)

        # Half-unbundled repository never happens: no ref was created.
        refs = _git(g["peer_repo"], "for-each-ref", "--format=%(refname)")
        assert f"refs/heads/{g['branch']}" not in refs.stdout


class TestForceUpdateOnRerun:
    def test_rerun_against_existing_branch_force_updates_rather_than_refuses(self, sender_and_peer):
        from camp.transfer import receive

        g = sender_and_peer
        first_bundle = _bundle_bytes(g["sender_repo"], g["branch"])
        receive.history(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            member="repo_a",
            bundle_bytes=first_bundle,
            env=g["env"],
        )
        first_landed = _git_out(g["peer_repo"], "rev-parse", f"refs/heads/{g['branch']}")
        assert first_landed == g["tip"]

        (g["sender_repo"] / "second.txt").write_text("more\n")
        _git(g["sender_repo"], "add", "second.txt")
        _git(
            g["sender_repo"],
            "-c",
            "user.email=t@t.com",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "second",
            "--no-gpg-sign",
        )
        new_tip = _git_out(g["sender_repo"], "rev-parse", "HEAD")
        second_bundle = _bundle_bytes(g["sender_repo"], g["branch"])

        # Must not raise — the branch already existing on this host is
        # exactly the case history() is meant to force-update, not refuse.
        result = receive.history(
            groups=[g["group"]],
            group_name="testgroup",
            slug="feat-x",
            member="repo_a",
            bundle_bytes=second_bundle,
            env=g["env"],
        )

        assert result["commit"] == new_tip
        second_landed = _git_out(g["peer_repo"], "rev-parse", f"refs/heads/{g['branch']}")
        assert second_landed == new_tip
        assert second_landed != first_landed


# ---------------------------------------------------------------------------
# CLI wiring — `camp transfer-receive history` end to end through the real
# dispatcher, exactly like test_transfer_receive.py proves begin/finish.
# ---------------------------------------------------------------------------


def _dispatch_module():
    import importlib

    return importlib.import_module("camp.cli.dispatch")


def _write_group_toml(groups_dir: Path, name: str, members: list[tuple[str, str]]) -> None:
    groups_dir.mkdir(parents=True, exist_ok=True)
    member_tables = "\n\n".join(
        f'[[members]]\nname = "{member_name}"\nrepo_root = "{repo_root}"'
        for member_name, repo_root in members
    )
    (groups_dir / f"{name}.toml").write_text(f'[group]\nname = "{name}"\n\n{member_tables}\n')


class TestHistoryCliDispatch:
    def test_history_requires_member_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["camp", "transfer-receive", "history", "--group", "testgroup", "--slug", "x"],
        )
        dispatch = _dispatch_module()
        with pytest.raises(SystemExit) as exc_info:
            dispatch.main()
        assert exc_info.value.code != 0
        assert "--member" in capsys.readouterr().err

    def test_history_reachable_through_real_dispatcher_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        sender_repo = tmp_path / "sender"
        init_git_repo(sender_repo, origin=True)
        branch = "worktree-feat-cli"
        _git(sender_repo, "checkout", "-b", branch)
        (sender_repo / "via-cli.txt").write_text("cli path\n")
        _git(sender_repo, "add", "via-cli.txt")
        _git(sender_repo, "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "via cli", "--no-gpg-sign")
        tip = _git_out(sender_repo, "rev-parse", "HEAD")
        bundle = _bundle_bytes(sender_repo, branch)

        peer_repo = tmp_path / "peer_cli_repo_a"
        init_git_repo(peer_repo, origin=False)

        cfg = tmp_path / "config"
        (cfg / "groups").mkdir(parents=True)
        _write_group_toml(cfg / "groups", "testgroup", [("repo_a", str(peer_repo))])

        monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
        monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "camp",
                "transfer-receive",
                "history",
                "--group",
                "testgroup",
                "--slug",
                "feat-cli",
                "--member",
                "repo_a",
            ],
        )

        class _FakeStdinBuffer:
            def read(self) -> bytes:
                return bundle

        class _FakeStdin:
            buffer = _FakeStdinBuffer()

        monkeypatch.setattr(sys, "stdin", _FakeStdin())

        _dispatch_module().main()  # success path returns normally, no SystemExit

        payload = json.loads(capsys.readouterr().out)
        assert payload["contract_version"] == 1
        assert payload["commit"] == tip

        landed = _git_out(peer_repo, "rev-parse", f"refs/heads/{branch}")
        assert landed == tip


# ---------------------------------------------------------------------------
# send_history() — sender-side producer + stream_camp wiring, full round trip
# through a spawned real `camp` dispatcher process standing in for the peer.
# ---------------------------------------------------------------------------


def _peer_process_spawn(peer_cfg_dir: Path, peer_state_dir: Path):
    """A `StreamSpawner` that ignores the ssh argv `stream_camp` assembled
    and instead runs the ACTUAL `camp transfer-receive history ...` phase
    (parsed out of that same assembled remote command) as a real subprocess
    invocation of the real dispatcher, isolated to its own env — proving the
    whole sender -> wire -> peer path, not a shortcut around any of it."""
    import shlex

    def _spawn(argv, env):
        remote_command = argv[-1]
        parsed = shlex.split(remote_command)
        camp_args = parsed[1:]  # drop the camp_bin element
        script = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(_PLUGIN_DIR)!r})
            sys.argv = ["camp", *{camp_args!r}]
            from camp.cli import dispatch
            dispatch.main()
            """
        )
        child_env = dict(os.environ)
        child_env["CAMP_CONFIG_DIR"] = str(peer_cfg_dir)
        child_env["CAMP_STATE_DIR"] = str(peer_state_dir)
        return subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
        )

    return _spawn


class TestSendHistoryEndToEnd:
    def test_send_history_lands_commit_on_a_spawned_real_peer_process(self, tmp_path: Path):
        from camp.host.config import Host
        from camp.host.transport import Answered
        from camp.transfer.history import send_history

        sender_repo = tmp_path / "sender"
        init_git_repo(sender_repo, origin=True)
        branch = "worktree-feat-send"
        _git(sender_repo, "checkout", "-b", branch)
        (sender_repo / "sent.txt").write_text("sent via send_history\n")
        _git(sender_repo, "add", "sent.txt")
        _git(sender_repo, "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "sent", "--no-gpg-sign")
        tip = _git_out(sender_repo, "rev-parse", "HEAD")

        peer_repo = tmp_path / "peer_send_repo_a"
        init_git_repo(peer_repo, origin=False)
        peer_cfg = tmp_path / "peer-config"
        (peer_cfg / "groups").mkdir(parents=True)
        _write_group_toml(peer_cfg / "groups", "testgroup", [("repo_a", str(peer_repo))])
        peer_state = tmp_path / "peer-state"

        host = Host(ssh="fake-peer", camp_bin="/opt/camp/bin/camp")

        outcome = send_history(
            host,
            group="testgroup",
            slug="feat-send",
            member="repo_a",
            repo_root=sender_repo,
            ref=branch,
            basis_commit=None,
            spawn=_peer_process_spawn(peer_cfg, peer_state),
        )

        assert isinstance(outcome, Answered), outcome
        payload = json.loads(outcome.stdout)
        assert payload["commit"] == tip

        landed = _git_out(peer_repo, "rev-parse", f"refs/heads/{branch}")
        assert landed == tip


# ---------------------------------------------------------------------------
# A peer-reported basis commit the sender does not hold must not be handed
# to `git bundle create --not` — the sender falls back to a full bundle
# rather than failing permanently against an object it can never resolve.
# ---------------------------------------------------------------------------


class TestBasisCommitNotHeldBySender:
    def test_falls_back_to_full_bundle_when_sender_lacks_the_basis_object(
        self, tmp_path: Path
    ):
        from camp.transfer.history import build_bundle_argv

        sender_repo = tmp_path / "sender"
        init_git_repo(sender_repo, origin=True)
        branch = "worktree-feat-x"
        _git(sender_repo, "checkout", "-b", branch)

        # Plausibly-shaped sha the sender's own object store does not hold —
        # the shape a peer's AHEAD `origin/main` resolves to on ITS host.
        unheld_basis = "a" * 40

        argv = build_bundle_argv(sender_repo, branch, basis_commit=unheld_basis)

        assert "--not" not in argv
        assert unheld_basis not in argv

    def test_still_negatives_when_sender_actually_holds_the_basis(self, tmp_path: Path):
        from camp.transfer.history import build_bundle_argv

        sender_repo = tmp_path / "sender"
        init_git_repo(sender_repo, origin=True)
        branch = "worktree-feat-x"
        _git(sender_repo, "checkout", "-b", branch)
        base_sha = _git_out(sender_repo, "rev-parse", "HEAD")
        (sender_repo / "extra.txt").write_text("extra\n")
        _git(sender_repo, "add", "extra.txt")
        _git(
            sender_repo,
            "-c",
            "user.email=t@t.com",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "extra",
            "--no-gpg-sign",
        )

        argv = build_bundle_argv(sender_repo, branch, basis_commit=base_sha)

        assert "--not" in argv
        assert base_sha in argv

    def test_send_history_still_succeeds_when_peer_basis_is_unheld_by_sender(
        self, tmp_path: Path
    ):
        from camp.host.config import Host
        from camp.host.transport import Answered
        from camp.transfer.history import send_history

        sender_repo = tmp_path / "sender"
        init_git_repo(sender_repo, origin=True)
        branch = "worktree-feat-unheld"
        _git(sender_repo, "checkout", "-b", branch)
        (sender_repo / "sent.txt").write_text("sent despite unheld basis\n")
        _git(sender_repo, "add", "sent.txt")
        _git(
            sender_repo,
            "-c",
            "user.email=t@t.com",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "sent",
            "--no-gpg-sign",
        )
        tip = _git_out(sender_repo, "rev-parse", "HEAD")

        peer_repo = tmp_path / "peer_unheld_repo_a"
        init_git_repo(peer_repo, origin=False)
        peer_cfg = tmp_path / "peer-unheld-config"
        (peer_cfg / "groups").mkdir(parents=True)
        _write_group_toml(peer_cfg / "groups", "testgroup", [("repo_a", str(peer_repo))])
        peer_state = tmp_path / "peer-unheld-state"

        host = Host(ssh="fake-peer", camp_bin="/opt/camp/bin/camp")

        # A basis commit shaped like a real sha (as `_basis_commit` on the
        # peer's own AHEAD `origin/main` would report) but present in
        # neither the sender's nor the peer's object store.
        unheld_basis = "b" * 40

        outcome = send_history(
            host,
            group="testgroup",
            slug="feat-unheld",
            member="repo_a",
            repo_root=sender_repo,
            ref=branch,
            basis_commit=unheld_basis,
            spawn=_peer_process_spawn(peer_cfg, peer_state),
        )

        assert isinstance(outcome, Answered), outcome
        payload = json.loads(outcome.stdout)
        assert payload["commit"] == tip

        landed = _git_out(peer_repo, "rev-parse", f"refs/heads/{branch}")
        assert landed == tip


class TestMalformedBasisCommitShape:
    def test_non_hex_basis_commit_refused_with_a_clear_message(self, tmp_path: Path):
        from camp.transfer.history import InvalidBasisCommit, build_bundle_argv

        sender_repo = tmp_path / "sender"
        init_git_repo(sender_repo, origin=True)
        branch = "worktree-feat-x"
        _git(sender_repo, "checkout", "-b", branch)

        with pytest.raises(InvalidBasisCommit) as exc_info:
            build_bundle_argv(sender_repo, branch, basis_commit="--not-a-sha; rm -rf")

        assert "--not-a-sha; rm -rf" in str(exc_info.value)
