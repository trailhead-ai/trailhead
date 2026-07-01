"""Structural / efficiency fixes from a code review.

The hidden `camp inject --drain` PostToolUse hook fires on every Bash
  tool call. The inject route must be near-free: it must NOT import the heavy
  ~1700-line spine module on its account, and must skip the cold-subprocess
  ensure_trailhead_importable() bootstrap. The drain still works.

`_slug_from_args_or_cwd` dropped `env`, breaking the "all callers pass
  the same env" invariant. resolve_from_cwd derives camp_state_dir from
  state_dir("camp", env=env) when not given camp_state_dir, so a non-None env
  must reach it — otherwise a slug-from-cwd handler resolves against a different
  state dir than the downstream manifest/workspace ops.

The disabled-verb set + legacy-redirect map are sourced from a single
  module (verb_taxonomy) that BOTH cli/camp and spine consult, and the 5 spine
  "no group resolved" stubs collapse into one cmd_needs_group.

C1 — the rmtree-confinement guard in reconcile_break (anchored on
  central_state_dir/worktrees) is exercised: member worktree_paths stay inside
  the resolved workspace dir (pre-check passes), but the workspace dir itself is a
  symlink escaping worktrees_root, so the dedicated rmtree guard fires.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
import textwrap
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


def _load_cli_module():
    spec = importlib.util.spec_from_loader(
        "camp_cli", importlib.machinery.SourceFileLoader("camp_cli", str(_CLI_CAMP))
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# inject drain is near-free: no spine import on its account
# ===========================================================================


class TestFix6InjectIsLight:
    def test_inject_drain_does_not_import_spine(self, tmp_path: Path):
        """A subprocess that dispatches `camp inject --drain` must not have `spine`
        in sys.modules afterward — the inject route pays neither the spine
        module-load cost nor ensure_trailhead_importable()."""
        ws = tmp_path / "ws"
        (ws / ".camp").mkdir(parents=True)
        probe = textwrap.dedent(
            f"""
            import sys, importlib.machinery, importlib.util
            mod_path = {str(_CLI_CAMP)!r}
            spec = importlib.util.spec_from_loader(
                "camp_cli",
                importlib.machinery.SourceFileLoader("camp_cli", mod_path),
            )
            mod = importlib.util.module_from_spec(spec)
            sys.argv = ["camp", "inject", "--drain", "--workspace", {str(ws)!r}]
            # Loading the module runs the top-level bootstrap+dispatch detection.
            spec.loader.exec_module(mod)
            try:
                mod.main()
            except SystemExit:
                pass
            print("SPINE_IMPORTED" if "spine" in sys.modules else "SPINE_ABSENT")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert "SPINE_ABSENT" in result.stdout, (
            f"inject drain must not import spine.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_inject_drain_still_drains(self, tmp_path: Path, monkeypatch, capsys):
        """The drain still emits the queued additionalContext and clears the queue."""
        from inject import enqueue_doc

        ws = tmp_path / "ws"
        (ws / ".camp").mkdir(parents=True)
        enqueue_doc(ws, "hello from the queue")

        camp_cli = _load_cli_module()
        monkeypatch.chdir(ws)
        with pytest.raises(SystemExit) as exc:
            camp_cli._cmd_inject_cli(["--workspace", str(ws)])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "hello from the queue" in out

    def test_inject_drain_empty_is_silent(self, tmp_path: Path, capsys):
        """An empty queue emits nothing and exits 0 (crash-proof, silent)."""
        ws = tmp_path / "ws"
        (ws / ".camp").mkdir(parents=True)

        camp_cli = _load_cli_module()
        with pytest.raises(SystemExit) as exc:
            camp_cli._cmd_inject_cli(["--workspace", str(ws)])
        assert exc.value.code == 0
        assert capsys.readouterr().out == ""


# ===========================================================================
# _slug_from_args_or_cwd threads env to resolve_from_cwd
# ===========================================================================


class TestFix7SlugFromCwdThreadsEnv:
    def test_resolves_against_env_state_dir_not_os_environ(self, tmp_path, monkeypatch):
        """With a non-None env, the cwd slug resolution must use that env's state
        dir — matching the downstream manifest/workspace ops. If env is dropped,
        resolve_from_cwd derives state_dir from os.environ instead and the slug
        won't resolve from inside the env's workspace dir."""
        from camp.group.resolve import central_state_dir

        camp_cli = _load_cli_module()

        # Two distinct state roots: the env points at one; os.environ at another.
        env_state = tmp_path / "env-state"
        env_state.mkdir()
        env = {"CAMP_STATE_DIR": str(env_state)}

        os_state = tmp_path / "os-state"
        os_state.mkdir()
        monkeypatch.setenv("CAMP_STATE_DIR", str(os_state))

        group = {"group": {"name": "grp"}, "members": [], "branch_pattern": "worktree-{slug}"}

        # cwd is the workspace dir under the ENV state dir.
        ws = central_state_dir("grp", env=env) / "worktrees" / "feat-x"
        ws.mkdir(parents=True)
        monkeypatch.chdir(ws)

        slug = camp_cli._slug_from_args_or_cwd([], group, verb="status", allow_none=True, env=env)
        assert slug == "feat-x", (
            "slug-from-cwd must resolve against the env's state dir (env threaded "
            "into resolve_from_cwd), not os.environ"
        )


# ===========================================================================
# single source of truth verb taxonomy
# ===========================================================================


class TestFix9VerbTaxonomy:
    @pytest.mark.parametrize("verb", ["new", "setup"])
    def test_needs_group_configure_message(self, verb, capsys):
        import spine

        with pytest.raises(SystemExit) as exc:
            spine.cmd_needs_group(verb)
        assert exc.value.code != 0
        err = capsys.readouterr().err
        assert "no camp group resolves from this directory" in err

    @pytest.mark.parametrize("verb", ["remove", "pwd", "activate"])
    def test_needs_group_pass_group_message(self, verb, capsys):
        import spine

        with pytest.raises(SystemExit) as exc:
            spine.cmd_needs_group(verb)
        assert exc.value.code != 0
        err = capsys.readouterr().err
        assert "no group resolved from cwd" in err
        assert "pass --group" in err


# ===========================================================================
# C1 — rmtree-confinement guard fires when the workspace dir symlink-escapes
# ===========================================================================


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
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "fetch", "origin", "--quiet"], check=True, capture_output=True
    )


class TestC1RmtreeGuard:
    def test_rmtree_guard_rejects_symlink_escaping_workspace_dir(self, tmp_path, monkeypatch):
        """Member worktree_paths stay INSIDE the resolved workspace dir (pre-check
        passes), but the slug workspace dir itself is a symlink escaping
        worktrees_root. The dedicated rmtree guard must fire and refuse — without
        rmtree'ing the escape target."""
        from reconcile import reconcile_break, ConfinementError
        from provision import bring_up_workspace
        from lifecycle_cmds import cmd_setup_group
        from camp.group.manifest import workspace_dir, read_central_manifest

        repo = tmp_path / "repo_a"
        _init_git_repo(repo)
        group = {
            "group": {"name": "c1grp"},
            "members": [
                {"name": "repo_a", "repo_root": str(repo), "bootstrap": [], "base": "origin/main"}
            ],
            "branch_pattern": "worktree-{slug}",
        }
        state_root = tmp_path / "camp-state"
        state_root.mkdir()
        env = {"CAMP_STATE_DIR": str(state_root)}

        # Provision normally (real workspace dir under worktrees_root).
        import provision

        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
        bring_up_workspace(group, "feat-c1", env=env)
        cmd_setup_group(group, "feat-c1", env=env)

        real_ws = workspace_dir("c1grp", "feat-c1", env=env)
        assert real_ws.is_dir()

        # Build an escape target OUTSIDE worktrees_root and move the real workspace
        # contents there; replace the workspace dir with a symlink to it. Member
        # worktree_paths in the manifest stay as <real_ws>/<member> (lexically
        # inside real_ws), so the per-member pre-check still passes — but the
        # workspace dir RESOLVES outside worktrees_root.
        escape = tmp_path / "escape-target"
        real_ws.rename(escape)
        sentinel = escape / "DO_NOT_DELETE.txt"
        sentinel.write_text("keep me\n")
        real_ws.symlink_to(escape, target_is_directory=True)

        # Sanity: the manifest's member worktree_paths are still under real_ws
        # lexically (so they pass the per-member relative_to check, which uses
        # the unresolved ws path) — they resolve through the symlink.
        mpath = real_ws / "manifest.json"
        mdata = read_central_manifest(mpath)
        for m in mdata["members"]:
            assert str(real_ws) in m["worktree_path"]

        with pytest.raises(ConfinementError) as exc:
            reconcile_break(group, "feat-c1", env=env, force=True)

        # The error must come from the rmtree guard (anchored on worktrees_root),
        # not the per-member pre-check — the message names the workspace dir.
        assert "worktrees root" in str(exc.value), (
            f"expected the rmtree guard to fire, got: {exc.value}"
        )
        assert sentinel.exists(), (
            "the rmtree confinement guard must refuse to delete an out-of-tree "
            "(symlink-escaping) workspace dir"
        )
        # Pre-check passed and per-member removal ran (proving we reached the
        # rmtree guard, not the earlier pre-check): the member worktree is gone.
        assert not (escape / "repo_a").exists(), (
            "the per-member worktree should have been removed before the rmtree guard"
        )
