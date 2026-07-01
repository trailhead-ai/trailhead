"""Behavioral tests for three confirmed correctness bugs (code review).

BUG 1 — `camp setup --status` was a phantom flag: it fell through to slug
  resolution, normalized to the slug "status", and ran the provisioning mutator
  against a phantom `status` workspace on every SessionStart. Fixed to be a
  real, read-only status handler that prints a summary and exits 0 always.

BUG 2 — `camp rm` left the workspace dir behind, so the next `camp ai <slug>`
  saw `ws_dir.exists()` True and wrongly took the resume path. Fixed:
  reconcile_break removes the now-camp-owned workspace dir after removing the
  member worktrees + manifest (under the same confinement guard).

BUG 4 — a fast `git fetch` failure was swallowed (`check=False`) and the member
  silently branched off HEAD, then got flipped to `ready` on the wrong base.
  Fixed: when the base is a remote ref AND the fetch fails AND the ref does not
  resolve locally, raise ReconcileError → the member is flipped to `failed`.

Patterns: fake-git + tmp_path + CAMP_STATE_DIR (no real ~/.claude, no real
claude exec).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_SCRIPTS_DIR = _PLUGIN_DIR / "scripts"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "T"], check=True, capture_output=True
    )
    (path / "README.md").write_text("# t\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "i", "--no-gpg-sign"],
        check=True,
        capture_output=True,
    )
    # Self-origin so the configured base `origin/main` resolves locally (a real
    # member always has a fetchable/resolvable base).
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "fetch", "origin", "--quiet"], check=True, capture_output=True
    )


def _make_group_config(name, members, *, branch_pattern="worktree-{slug}"):
    return {"group": {"name": name}, "members": members, "branch_pattern": branch_pattern}


def _camp_state_env(tmp_path: Path) -> dict[str, str]:
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root)}


def _workspace_dir(group_name, slug, env):
    from camp.group.resolve import central_state_dir

    return central_state_dir(group_name, env=env) / "worktrees" / slug


def _load_cli_module():
    spec = importlib.util.spec_from_loader(
        "camp_cli", importlib.machinery.SourceFileLoader("camp_cli", str(_CLI_CAMP))
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def camp_cli():
    return _load_cli_module()


@pytest.fixture()
def two_member_group(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)
    group = _make_group_config(
        "bugfixgroup",
        [
            {"name": "repo_a", "repo_root": str(repo_a), "bootstrap": [], "base": "origin/main"},
            {"name": "repo_b", "repo_root": str(repo_b), "bootstrap": [], "base": "origin/main"},
        ],
    )
    env = _camp_state_env(tmp_path)
    return {"group": group, "repo_a": repo_a, "repo_b": repo_b, "env": env, "tmp_path": tmp_path}


@pytest.fixture(autouse=True)
def _stub_spawn(monkeypatch):
    """Never spawn a real detached provisioner in these tests."""
    import provision

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)


# ===========================================================================
# BUG 1 — `camp setup --status` is a real, read-only, always-exit-0 handler
# ===========================================================================


class TestBug1SetupStatus:
    def _provision(self, camp_cli, g, slug):
        """Seed + provision a workspace so members are ready."""
        from provision import bring_up_workspace
        from lifecycle_cmds import cmd_setup_group

        bring_up_workspace(g["group"], slug, env=g["env"])
        cmd_setup_group(g["group"], slug, env=g["env"])

    def _run_status(self, camp_cli, g, slug, monkeypatch, capsys):
        """Run `camp setup --status` from inside the workspace dir. Returns
        (exit_code, stdout, stderr)."""
        ws = _workspace_dir("bugfixgroup", slug, g["env"])
        # The real SessionStart hook runs in the workspace dir with CAMP_STATE_DIR
        # in the actual environment; resolve_from_cwd reads os.environ.
        monkeypatch.setenv("CAMP_STATE_DIR", g["env"]["CAMP_STATE_DIR"])
        monkeypatch.chdir(ws)
        code = 0
        try:
            camp_cli._cmd_setup_group_cli(["--status"], g["group"], g["env"], dry_run=False)
        except SystemExit as e:
            code = e.code or 0
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    def test_status_prints_summary_to_stdout(self, camp_cli, two_member_group, monkeypatch, capsys):
        g = two_member_group
        self._provision(camp_cli, g, "feat-st")
        code, out, _err = self._run_status(camp_cli, g, "feat-st", monkeypatch, capsys)
        assert out.strip(), "camp setup --status should print a status summary to stdout"
        assert "feat-st" in out

    def test_status_exits_zero_when_ready(self, camp_cli, two_member_group, monkeypatch, capsys):
        g = two_member_group
        self._provision(camp_cli, g, "feat-st0")
        code, _out, _err = self._run_status(camp_cli, g, "feat-st0", monkeypatch, capsys)
        assert code == 0

    def test_status_exits_zero_when_pending_or_failed(
        self, camp_cli, two_member_group, monkeypatch, capsys
    ):
        from provision import seed_pending_workspace
        from camp.group.manifest import flip_member_state_unlocked, reconcile_lock

        g = two_member_group
        seed_pending_workspace(g["group"], "feat-pf", env=g["env"])
        mpath = _workspace_dir("bugfixgroup", "feat-pf", g["env"]) / "manifest.json"
        with reconcile_lock(mpath.parent):
            flip_member_state_unlocked(mpath, "repo_a", "failed", reason="boom")
            flip_member_state_unlocked(mpath, "repo_b", "pending")
        code, out, _err = self._run_status(camp_cli, g, "feat-pf", monkeypatch, capsys)
        assert code == 0, "SessionStart hook must never exit non-zero"
        # still reports the failed/pending members
        assert "failed" in out.lower() or "pending" in out.lower()

    def test_status_never_mutates_creates_no_junk_dir(
        self, camp_cli, two_member_group, monkeypatch, capsys
    ):
        g = two_member_group
        self._provision(camp_cli, g, "feat-nm")
        mpath = _workspace_dir("bugfixgroup", "feat-nm", g["env"]) / "manifest.json"
        before = mpath.read_text()
        self._run_status(camp_cli, g, "feat-nm", monkeypatch, capsys)
        # No junk `status` workspace was created.
        junk = _workspace_dir("bugfixgroup", "status", g["env"])
        assert not junk.exists(), "camp setup --status must not create a `status` workspace dir"
        # The real workspace's manifest is unchanged (no reconcile/flip/write).
        assert mpath.read_text() == before, "camp setup --status must not mutate the manifest"

    def test_status_consumed_not_treated_as_slug(
        self, camp_cli, two_member_group, monkeypatch, capsys
    ):
        """`--status` must be parsed before slug resolution — never normalized to
        the slug `status`."""
        g = two_member_group
        self._provision(camp_cli, g, "feat-cons")
        self._run_status(camp_cli, g, "feat-cons", monkeypatch, capsys)
        junk = _workspace_dir("bugfixgroup", "status", g["env"])
        assert not junk.exists(), "`--status` must not be normalized into a `status` slug workspace"


# ===========================================================================
# BUG 2 — `camp rm` removes the workspace dir so the next `camp ai` is `new`
# ===========================================================================


class TestBug2RmRemovesWorkspaceDir:
    def _provision(self, g, slug):
        from provision import bring_up_workspace
        from lifecycle_cmds import cmd_setup_group

        bring_up_workspace(g["group"], slug, env=g["env"])
        cmd_setup_group(g["group"], slug, env=g["env"])

    def test_rm_removes_workspace_dir(self, two_member_group):
        from reconcile import reconcile_break
        from camp.group.manifest import workspace_dir

        g = two_member_group
        self._provision(g, "feat-rm")
        ws = workspace_dir("bugfixgroup", "feat-rm", env=g["env"])
        assert ws.exists()
        reconcile_break(g["group"], "feat-rm", env=g["env"])
        assert not ws.exists(), "camp rm must remove the workspace dir itself"

    # The `camp new` handler has no new-vs-resume launch-template choice (no
    # session lock, no harness launch), so there is no is_resume behavior to
    # assert here.

    def test_dirty_block_without_force_leaves_dir_intact(self, two_member_group):
        from reconcile import reconcile_break, ReconcileError
        from camp.group.manifest import workspace_dir

        g = two_member_group
        self._provision(g, "feat-dirty")
        ws = workspace_dir("bugfixgroup", "feat-dirty", env=g["env"])
        (ws / "repo_a" / "dirty.txt").write_text("uncommitted\n")
        with pytest.raises(ReconcileError):
            reconcile_break(g["group"], "feat-dirty", env=g["env"])
        assert ws.exists(), "a dirty-block abort must leave the workspace dir intact"

    def test_confinement_rejects_out_of_tree_workspace_dir(
        self, two_member_group, tmp_path, monkeypatch
    ):
        """The workspace-dir rmtree uses the same confinement guard — an
        out-of-tree resolved workspace dir is rejected, not rmtree'd."""
        from reconcile import reconcile_break, ConfinementError
        import reconcile
        from camp.group.manifest import workspace_dir

        g = two_member_group
        self._provision(g, "feat-conf")
        workspace_dir("bugfixgroup", "feat-conf", env=g["env"])

        outside = tmp_path / "outside-tree"
        outside.mkdir()
        sentinel = outside / "keep.txt"
        sentinel.write_text("do not delete\n")

        # Make the workspace dir resolve outside the state tree (symlink escape).
        real_workspace_dir = reconcile.workspace_dir

        def fake_workspace_dir(group_name, slug, *, env=None):
            if slug == "feat-conf":
                return outside
            return real_workspace_dir(group_name, slug, env=env)

        monkeypatch.setattr(reconcile, "workspace_dir", fake_workspace_dir)

        with pytest.raises(ConfinementError):
            reconcile_break(g["group"], "feat-conf", env=g["env"])
        assert sentinel.exists(), "confinement must reject rmtree of an out-of-tree path"


# ===========================================================================
# BUG 4 — a fast `git fetch` failure fails the member (no silent HEAD branch)
# ===========================================================================


class _FetchFailGit:
    """Fake git: `fetch` returns non-zero; `rev-parse --verify <base>` does NOT
    resolve (the remote ref is absent locally); everything else succeeds."""

    def __init__(self, base_resolves: bool = False) -> None:
        self.base_resolves = base_resolves
        self.calls: list[list[str]] = []

    def __call__(self, repo_root, *args):
        self.calls.append(list(args))

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        res = _Result()
        if args and args[0] == "fetch":
            res.returncode = 1
            res.stderr = "fatal: couldn't find remote ref refs/heads/main"
        elif args and args[0] == "rev-parse":
            if self.base_resolves:
                res.returncode = 0
                res.stdout = "deadbeef"
            else:
                res.returncode = 1
                res.stdout = ""
        elif args and args[0] == "branch":
            res.stdout = ""  # branch not present locally
        return res


def _fetch_fails(monkeypatch):
    """Patch reconcile.subprocess.run so the `git fetch` in _fetch_base returns
    non-zero (a fast fetch failure), without touching real git."""
    import reconcile
    import subprocess as _sp

    def fake_run(argv, **kwargs):
        class _R:
            returncode = 1
            stdout = ""
            stderr = "fatal: couldn't find remote ref refs/heads/main"

        if isinstance(argv, (list, tuple)) and "fetch" in argv:
            return _R()
        return _sp.run(argv, **kwargs)

    monkeypatch.setattr(reconcile.subprocess, "run", fake_run)


class TestBug4FetchFailureFailsMember:
    def test_fetch_failure_unresolved_base_flips_failed(self, two_member_group, monkeypatch):
        """Fetch fails AND origin/<base> does not resolve → member flipped to
        failed with the fetch error in the reason (not ready, not branched off HEAD)."""
        import reconcile
        from provision import bring_up_workspace
        from lifecycle_cmds import cmd_setup_group
        from camp.group.manifest import read_central_manifest

        g = two_member_group
        fake = _FetchFailGit(base_resolves=False)
        monkeypatch.setattr(reconcile, "_git", fake)
        _fetch_fails(monkeypatch)
        # Record any worktree-add attempt: a correct fix must NOT branch off HEAD
        # when the fetch fails on an unresolved remote base.
        added: list[str] = []
        monkeypatch.setattr(
            reconcile,
            "_add_worktree_for_member",
            lambda member, *a, **k: added.append(member["name"]),
        )

        bring_up_workspace(g["group"], "feat-ff", env=g["env"])
        cmd_setup_group(g["group"], "feat-ff", env=g["env"])

        assert added == [], "must not branch off HEAD when fetch fails on unresolved base"

        mpath = _workspace_dir("bugfixgroup", "feat-ff", g["env"]) / "manifest.json"
        data = read_central_manifest(mpath)
        by_name = {m["name"]: m for m in data["members"]}
        assert by_name["repo_a"]["provision_state"] == "failed", (
            "member must be flipped to failed, not ready, on unresolved-base fetch failure"
        )
        reason = by_name["repo_a"].get("reason", "").lower()
        assert "remote ref" in reason or "fetch" in reason, (
            f"failed reason must carry the fetch error, got: {reason!r}"
        )

    def test_fetch_failure_resolved_base_proceeds(self, two_member_group, monkeypatch):
        """Fetch fails but the base ref DOES resolve locally (cached) → proceed
        (member ready). The fetch failure is non-fatal."""
        import reconcile
        from provision import provision_member

        g = two_member_group
        fake = _FetchFailGit(base_resolves=True)
        monkeypatch.setattr(reconcile, "_git", fake)
        _fetch_fails(monkeypatch)
        added = []
        monkeypatch.setattr(
            reconcile,
            "_add_worktree_for_member",
            lambda member, *a, **k: added.append(member["name"]),
        )

        member = g["group"]["members"][0]
        # Should NOT raise — cached base resolves.
        provision_member(g["group"], "feat-fr", member, env=g["env"])
        assert added == ["repo_a"]

    def test_successful_fetch_branches_off_base(self, two_member_group, monkeypatch):
        """A successful fetch proceeds and branches off origin/<base>."""
        import reconcile
        from provision import provision_member

        g = two_member_group

        class _OkGit:
            def __init__(self):
                self.calls = []

            def __call__(self, repo_root, *args):
                self.calls.append(list(args))

                class _R:
                    returncode = 0
                    stdout = "deadbeef" if args and args[0] == "rev-parse" else ""
                    stderr = ""

                return _R()

        fake = _OkGit()
        monkeypatch.setattr(reconcile, "_git", fake)
        captured = {}
        monkeypatch.setattr(
            reconcile,
            "_add_worktree_for_member",
            lambda member, wt, branch, repo, *, base, slug: captured.setdefault("base", base),
        )

        member = g["group"]["members"][0]
        provision_member(g["group"], "feat-ok", member, env=g["env"])
        assert captured["base"] == "origin/main"
