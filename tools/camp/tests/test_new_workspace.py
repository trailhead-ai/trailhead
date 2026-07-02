"""Tests for camp new — create-or-re-enter, launch/session surface stripped.

Exercises _cmd_new_group_cli in-process. The launch seam, session lock, and
session-id derivation are GONE from this handler, so nothing is stubbed
to suppress an exec — there is no exec to suppress.

Test contract:
- camp new <slug> (stub group) seeds + exits 0; stdout is EXACTLY the workspace
  abs path (one line, no trailing whitespace); the background-provisioning notice
  + next-step guidance (camp status / camp activate) go to stderr.
- Existing workspace → re-enters, prints the same path, does NOT re-seed/clobber
  the manifest (bring_up_workspace not called again).
- No `claude` exec is attempted (os.execvp never called).
- Seed/provision failure → nonzero exit + stderr message; stdout empty.
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
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _load_cli_module():
    """Import cli/camp (extensionless) as a module for in-process dispatch tests."""
    spec = importlib.util.spec_from_loader(
        "camp_cli", importlib.machinery.SourceFileLoader("camp_cli", str(_CLI_CAMP))
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def camp_cli():
    return _load_cli_module()


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


@pytest.fixture()
def group_env(tmp_path):
    repo_a = tmp_path / "repo_a"
    _init_git_repo(repo_a)
    group = {
        "group": {"name": "g"},
        "members": [
            {"name": "repo_a", "repo_root": str(repo_a), "bootstrap": [], "base": "origin/main"}
        ],
        "branch_pattern": "worktree-{slug}",
    }
    env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
    return {"group": group, "env": env, "tmp_path": tmp_path}


def _workspace_dir(env, slug):
    from camp.group.manifest import workspace_dir

    return workspace_dir("g", slug, env=env)


def _manifest_path(env, slug):
    from camp.group.manifest import manifest_path_for

    return manifest_path_for("g", slug, env=env)


@pytest.fixture(autouse=True)
def _stub_spawn(monkeypatch):
    """Never spawn a real detached provisioner in these tests."""
    import camp.provision.provision as provision

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)


class TestNewSlug:
    def test_new_slug_seeds_and_exits_zero(self, camp_cli, group_env):
        g = group_env
        # Returns normally (no SystemExit) on success.
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        assert _manifest_path(g["env"], "feat-x").is_file(), "new slug should seed the manifest"

    def test_stdout_is_exactly_the_workspace_abs_path(self, camp_cli, group_env, capsys):
        g = group_env
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        out = capsys.readouterr().out
        ws = _workspace_dir(g["env"], "feat-x")
        assert ws.is_absolute()
        assert out == f"{ws}\n", "stdout must be exactly the workspace abs path, one line"

    def test_stderr_carries_background_and_next_step_guidance(self, camp_cli, group_env, capsys):
        g = group_env
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        err = capsys.readouterr().err
        assert "background" in err.lower(), "stderr must announce background provisioning"
        assert "camp status" in err, "stderr must name 'camp status' to check provisioning"
        assert "camp activate" in err, "stderr must name 'camp activate' for activation"

    def test_no_claude_exec_attempted(self, camp_cli, group_env, monkeypatch):
        import os

        execs = []
        monkeypatch.setattr(os, "execvp", lambda *a, **k: execs.append(a))
        g = group_env
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        assert execs == [], "the launch path is gone — no harness exec must be attempted"


class TestExistingWorkspace:
    def test_existing_workspace_reenters_same_path_no_reseed(
        self, camp_cli, group_env, monkeypatch, capsys
    ):
        import camp.provision.provision as provision

        g = group_env
        # First create the workspace for real.
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        first_out = capsys.readouterr().out
        manifest = _manifest_path(g["env"], "feat-x")
        snapshot = manifest.read_bytes()

        # Second invocation must NOT re-seed/spawn — spy on bring_up_workspace.
        calls = []
        monkeypatch.setattr(
            provision, "bring_up_workspace", lambda *a, **k: calls.append(True) or manifest
        )
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        second_out = capsys.readouterr().out
        assert calls == [], "existing workspace must NOT re-provision"
        assert second_out == first_out, "re-enter prints the same workspace path"
        assert manifest.read_bytes() == snapshot, "re-enter must not clobber the manifest"

    def test_manifestless_partial_dir_is_reseeded_not_reentered(
        self, camp_cli, group_env, monkeypatch, capsys
    ):
        """A workspace dir present but manifest-less (crash between
        seed's mkdir and its manifest write) must be RE-SEEDED, not re-entered.
        Keying re-enter on ws_dir.exists() would re-enter the broken dir forever
        and make `camp remove` unrecoverable; keying on manifest presence repairs
        it."""
        import camp.provision.provision as provision

        g = group_env
        # Simulate the partial state: ws_dir exists, NO manifest.
        ws = _workspace_dir(g["env"], "feat-x")
        ws.mkdir(parents=True, exist_ok=True)
        manifest = _manifest_path(g["env"], "feat-x")
        assert ws.exists() and not manifest.exists()

        calls = []
        monkeypatch.setattr(
            provision,
            "bring_up_workspace",
            lambda *a, **k: calls.append(True) or provision.seed_pending_workspace(*a, **k),
        )
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        err = capsys.readouterr().err
        assert calls == [True], "manifest-less dir must be re-seeded (bring_up called)"
        assert "created workspace" in err, "partial-state repair takes the create branch"


class TestFailure:
    def test_seed_failure_nonzero_stderr_empty_stdout(self, camp_cli, group_env, monkeypatch, capsys):
        import camp.provision.provision as provision

        def _boom(*a, **k):
            raise RuntimeError("seed exploded")

        monkeypatch.setattr(provision, "bring_up_workspace", _boom)
        g = group_env
        with pytest.raises(SystemExit) as exc:
            camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        assert exc.value.code != 0
        captured = capsys.readouterr()
        assert captured.out == "", "stdout must be empty on failure"
        assert "camp new" in captured.err and "seed exploded" in captured.err


class TestBringUpInjectHook:
    """Inject hook gate in bring_up_workspace (provision.py:194) is covered via _cmd_new_group_cli.

    The gate `if profile.inject == "claude-hook": write_workspace_inject_hook(...)` is surviving
    code that test_provision.py does not cover at the bring-up path. These tests verify the gate
    via the full handler so a regression in the condition or its callsite would surface here.
    """

    def test_claude_hook_strategy_installs_posttooluse_hook(self, camp_cli, group_env):
        """Default (no [harness] block) → inject='claude-hook' → hook IS wired."""
        from camp.harness.hooks_writer import has_inject_drain_hook

        g = group_env
        # No [harness] block: resolve_harness_profile returns inject='claude-hook' (the default).
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        ws = _workspace_dir(g["env"], "feat-x")
        assert has_inject_drain_hook(ws), (
            "claude-hook inject strategy must wire a PostToolUse inject --drain hook on bring-up"
        )

    def test_stdout_strategy_does_not_install_posttooluse_hook(self, camp_cli, group_env):
        """Explicit inject='stdout' → hook must NOT be installed (negative guard)."""
        from camp.harness.hooks_writer import has_inject_drain_hook

        g = group_env
        g["group"]["harness"] = {"inject": "stdout"}
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        ws = _workspace_dir(g["env"], "feat-x")
        assert not has_inject_drain_hook(ws), (
            "stdout inject strategy must NOT wire a PostToolUse inject hook"
        )

    def test_inject_hook_idempotent_on_reentry(self, camp_cli, group_env):
        """Re-entering an existing workspace must not duplicate the inject hook."""
        from camp.harness.hooks_writer import has_inject_drain_hook
        import json

        g = group_env
        # First create (new slug path).
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        ws = _workspace_dir(g["env"], "feat-x")
        assert has_inject_drain_hook(ws)

        # Read the raw hook list count before re-entry.
        settings = ws / ".claude" / "settings.json"
        before = json.loads(settings.read_text())
        before_hooks = before.get("hooks", {}).get("PostToolUse", [])
        before_drain_count = sum(
            1
            for entry in before_hooks
            for h in entry.get("hooks", [])
            if "inject --drain" in (h.get("command") or "")
        )

        # Second invocation: existing workspace → re-enter path (no re-seed).
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        # Hook count must be unchanged — re-entry must not touch settings at all.
        after = json.loads(settings.read_text())
        after_hooks = after.get("hooks", {}).get("PostToolUse", [])
        after_drain_count = sum(
            1
            for entry in after_hooks
            for h in entry.get("hooks", [])
            if "inject --drain" in (h.get("command") or "")
        )
        assert after_drain_count == before_drain_count, (
            f"Re-entering an existing workspace must not duplicate the inject hook "
            f"(before={before_drain_count}, after={after_drain_count})"
        )


class TestShellIntegrationNudge:
    """Bare `camp new` nudges to install the trailhead shellenv wrapper.

    The `camp()` shell wrapper exports CAMP_SHELL_INTEGRATION=1 around `camp new`
    so the handler can tell a wrapper-driven run (which will cd for the user) from
    a bare-binary run (which leaves the user where they were and so needs the nudge).
    """

    def test_bare_run_prints_shellenv_nudge_to_stderr(
        self, camp_cli, group_env, capsys, monkeypatch
    ):
        monkeypatch.delenv("CAMP_SHELL_INTEGRATION", raising=False)
        g = group_env
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        captured = capsys.readouterr()
        assert "trailhead shellenv" in captured.err, (
            "a bare `camp new` must nudge the user to install the shellenv wrapper"
        )
        # The path still goes to stdout untouched.
        ws = _workspace_dir(g["env"], "feat-x")
        assert captured.out == f"{ws}\n"

    def test_marker_present_suppresses_the_nudge(
        self, camp_cli, group_env, capsys, monkeypatch
    ):
        monkeypatch.setenv("CAMP_SHELL_INTEGRATION", "1")
        g = group_env
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        captured = capsys.readouterr()
        assert "trailhead shellenv" not in captured.err, (
            "with the wrapper active (marker set) the handler must stay quiet — "
            "the wrapper does the cd"
        )

    def test_nudge_also_fires_on_existing_workspace_reentry(
        self, camp_cli, group_env, capsys, monkeypatch
    ):
        monkeypatch.delenv("CAMP_SHELL_INTEGRATION", raising=False)
        g = group_env
        # Create then re-enter.
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        capsys.readouterr()
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        err = capsys.readouterr().err
        assert "trailhead shellenv" in err


class TestInputCharset:
    """Confirm the inputs that flow into the emitted
    `camp` call cannot introduce shell metacharacters.

    Both slugs AND group names are constrained to the slug charset ^[a-z0-9-]+$.
    The emitted wrapper does not interpolate either value (it is static text; slug
    and group flow only as runtime argv) and the cd is quote-safe; constraining the
    group name to the slug charset adds a defense-in-depth second layer at the
    input so a group name can never carry a space or shell metacharacter into the
    workspace path at all.
    """

    def test_slug_is_constrained_to_safe_charset(self):
        from camp.spine import _VALID_SLUG_RE

        assert _VALID_SLUG_RE.pattern == r"^[a-z0-9-]+$"
        assert _VALID_SLUG_RE.match("feat-x")
        assert not _VALID_SLUG_RE.match("feat x")
        assert not _VALID_SLUG_RE.match("feat;rm")

    def test_group_name_is_constrained_to_slug_charset(self):
        from camp.group.resolve import validate_group_name, GroupConfinementError

        # Path-separator confinement and slug-charset confinement are both enforced:
        # a group name now rejects the same characters a slug does.
        for bad in ("a/b", "a\\b", "..", "a\x00b", "a group", "a;b", "a$b", "AB", "ab\n"):
            with pytest.raises(GroupConfinementError):
                validate_group_name(bad)
        # A slug-charset group name still passes.
        validate_group_name("a-group")
        validate_group_name("trailhead")


class TestNoSlug:
    def test_no_slug_names_camp_new(self, camp_cli, group_env, capsys):
        """Carry-forward fix: the no-slug error names `camp new`, not `camp ai`."""
        g = group_env
        with pytest.raises(SystemExit) as exc:
            camp_cli._cmd_new_group_cli([], g["group"], g["env"], dry_run=False)
        assert exc.value.code != 0
        err = capsys.readouterr().err
        assert "camp new" in err
        assert "camp ai" not in err
