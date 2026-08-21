"""Claude Code plugin state is per config dir, not per composed tree.

The composed plugin *source* is global — one tree under the trailhead state dir,
shared by every Claude config dir on the machine.  The *install state* is not:
``claude plugin marketplace add`` / ``claude plugin install`` write marketplace
registration and plugin content into whichever config dir is active, so a second
config dir starts with none of it.

These tests pin that split:

  - registration/install markers are read and written under the resolved Claude
    config dir, so a config dir that has never been installed into reports as
    absent — including through ``trailhead doctor``;
  - installing for one config dir leaves every other config dir's state
    untouched;
  - each ``claude plugin …`` invocation names the config dir it is meant to
    land in, rather than inheriting whatever the ambient environment selects.
"""

import os
import subprocess
from pathlib import Path

import pytest

from trailhead.doctor import run_doctor
from trailhead.harness.claude_code import ClaudeCodeHarness


def _env(claude_dir: Path) -> dict[str, str]:
    return {**os.environ, "TRAILHEAD_CLAUDE_DIR": str(claude_dir)}


def _fake_py(cmd):
    return subprocess.CompletedProcess(cmd, 0, stdout="Python 3.11.4\n", stderr="")


@pytest.fixture()
def personal(tmp_path: Path) -> Path:
    d = tmp_path / "personal-claude"
    d.mkdir()
    return d


@pytest.fixture()
def second(tmp_path: Path) -> Path:
    d = tmp_path / "second-claude"
    d.mkdir()
    return d


class TestStateIsPerConfigDir:
    def test_registration_marker_lands_in_the_config_dir(self, composed_root, personal):
        ClaudeCodeHarness().register(
            composed_root, runner=lambda args, **kw: None, env=_env(personal)
        )
        assert (personal / ".trailhead-registered").exists()
        assert not (composed_root / ".trailhead-registered").exists()

    def test_install_marker_lands_in_the_config_dir(self, composed_root, personal):
        ClaudeCodeHarness().install_tool(
            "lore", composed_root, runner=lambda args, **kw: None, env=_env(personal)
        )
        assert (personal / ".trailhead-installed-lore").exists()
        assert not (composed_root / ".trailhead-installed-lore").exists()

    def test_a_config_dir_with_no_state_reads_as_unregistered(
        self, composed_root, personal, second
    ):
        h = ClaudeCodeHarness()
        h.register(composed_root, runner=lambda args, **kw: None, env=_env(personal))
        h.install_tool("lore", composed_root, runner=lambda args, **kw: None, env=_env(personal))

        assert h.is_registered(composed_root, env=_env(personal)) is True
        assert h.is_registered(composed_root, env=_env(second)) is False
        assert h.is_installed("lore", composed_root, env=_env(second)) is False
        assert h.installed_tools(composed_root, env=_env(second)) == []

    def test_a_nonexistent_config_dir_reads_as_unregistered(self, composed_root, tmp_path):
        h = ClaudeCodeHarness()
        env = _env(tmp_path / "never-created")
        assert h.is_registered(composed_root, env=env) is False
        assert h.installed_tools(composed_root, env=env) == []

    def test_installing_for_one_config_dir_leaves_the_other_untouched(
        self, composed_root, personal, second
    ):
        h = ClaudeCodeHarness()
        h.register(composed_root, runner=lambda args, **kw: None, env=_env(personal))
        h.install_tool("lore", composed_root, runner=lambda args, **kw: None, env=_env(personal))
        before = sorted(p.name for p in personal.iterdir())

        h.register(composed_root, runner=lambda args, **kw: None, env=_env(second))
        h.install_tool("camp", composed_root, runner=lambda args, **kw: None, env=_env(second))

        assert sorted(p.name for p in personal.iterdir()) == before
        assert h.installed_tools(composed_root, env=_env(personal)) == ["lore"]
        assert h.installed_tools(composed_root, env=_env(second)) == ["camp"]

    def test_uninstalling_for_one_config_dir_leaves_the_other_untouched(
        self, composed_root, personal, second
    ):
        h = ClaudeCodeHarness()
        for d in (personal, second):
            h.register(composed_root, runner=lambda args, **kw: None, env=_env(d))
            h.install_tool("lore", composed_root, runner=lambda args, **kw: None, env=_env(d))

        h.unregister_tool("lore", composed_root, runner=lambda args, **kw: None, env=_env(second))
        h.unregister_marketplace(composed_root, runner=lambda args, **kw: None, env=_env(second))

        assert h.is_registered(composed_root, env=_env(personal)) is True
        assert h.installed_tools(composed_root, env=_env(personal)) == ["lore"]
        assert h.is_registered(composed_root, env=_env(second)) is False
        assert h.installed_tools(composed_root, env=_env(second)) == []


class TestCliInvocationNamesTheConfigDir:
    """Each ``claude plugin …`` call must target the config dir it is installing for."""

    def _config_dirs_seen(self, calls):
        return [kw.get("env", {}).get("CLAUDE_CONFIG_DIR") for _args, kw in calls]

    def test_register_passes_the_config_dir(self, composed_root, second):
        calls = []

        def runner(args, **kw):
            calls.append((list(args), kw))

        ClaudeCodeHarness().register(composed_root, runner=runner, env=_env(second))
        assert self._config_dirs_seen(calls) == [str(second)]

    def test_install_tool_passes_the_config_dir(self, composed_root, second):
        calls = []

        def runner(args, **kw):
            calls.append((list(args), kw))

        ClaudeCodeHarness().install_tool("lore", composed_root, runner=runner, env=_env(second))
        assert self._config_dirs_seen(calls) == [str(second)]

    def test_rewire_tool_passes_the_config_dir(self, composed_root, second):
        calls = []

        def runner(args, **kw):
            calls.append((list(args), kw))

        ClaudeCodeHarness().rewire_tool("lore", composed_root, runner=runner, env=_env(second))
        assert self._config_dirs_seen(calls) == [str(second), str(second)]


class TestDoctorReadsTheConfigDir:
    """``doctor`` is the tool an operator reaches for; it must not read green on a
    config dir that holds no plugin state."""

    def _run(self, tmp_path: Path, claude_dir: Path):
        return run_doctor(
            env={
                **os.environ,
                "TRAILHEAD_STATE_DIR": str(tmp_path),
                "TRAILHEAD_CLAUDE_DIR": str(claude_dir),
            },
            which_runner=lambda n: None,
            python_version_runner=_fake_py,
        )

    def test_reports_absent_for_a_config_dir_with_no_plugin_state(self, tmp_path):
        from .test_doctor import _make_tree

        _make_tree(tmp_path, "claude_code", ["lore", "camp"])

        r = self._run(tmp_path, tmp_path / "never-created")
        info = r.data["harnesses"]["claude_code"]
        assert info["registered"] is False
        assert info["installed"] == []
        assert r.exit_code == 0

    def test_reports_present_for_the_config_dir_that_was_installed_into(self, tmp_path):
        from .test_doctor import _make_tree

        _make_tree(tmp_path, "claude_code", ["lore", "camp"])

        r = self._run(tmp_path, tmp_path / "claude")
        info = r.data["harnesses"]["claude_code"]
        assert info["registered"] is True
        assert set(info["installed"]) == {"lore", "camp"}
