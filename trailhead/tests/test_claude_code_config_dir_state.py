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

They also pin the DEFAULT account's authentication read: the credentials
signal varies with the file's content (authenticated / not authenticated /
cannot tell, never collapsed into each other), the config-dir resolution
precedence a caller's `env` decides (``CLAUDE_CONFIG_DIR`` over
``HOME``/``USERPROFILE`` over the real home — deliberately not
``TRAILHEAD_CLAUDE_DIR``, the test-only seam the real launched session never
reads for this account), and that the read never spawns a subprocess, never
makes a network call, and never blocks on interaction.
"""

import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

from trailhead.doctor import run_doctor
from trailhead.harness.base import AccountAuthentication
from trailhead.harness.claude_code import ClaudeCodeHarness

from .test_doctor import _claude_dir, _fake_py, _make_tree


def _env(claude_dir: Path) -> dict[str, str]:
    return {**os.environ, "TRAILHEAD_CLAUDE_DIR": str(claude_dir)}


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
        _make_tree(tmp_path, "claude_code", ["lore", "camp"])

        r = self._run(tmp_path, tmp_path / "never-created")
        info = r.data["harnesses"]["claude_code"]
        assert info["registered"] is False
        assert info["installed"] == []
        assert r.exit_code == 0

    def test_reports_present_for_the_config_dir_that_was_installed_into(self, tmp_path):
        _make_tree(tmp_path, "claude_code", ["lore", "camp"])

        r = self._run(tmp_path, _claude_dir(tmp_path))
        info = r.data["harnesses"]["claude_code"]
        assert info["registered"] is True
        assert set(info["installed"]) == {"lore", "camp"}


class TestComposedTreeSharing:
    """The composed tree is shared; deleting it is only safe once nobody points at it.

    Registration is per config dir but the marketplace source it registers is the
    one global composed tree, so a teardown run under one config dir must not
    delete the tree the *other* config dir is still registered against — that
    would leave the other account with plugins whose source is gone and markers
    claiming they are installed.
    """

    def test_registering_records_the_config_dir_against_the_tree(
        self, composed_root, personal
    ):
        h = ClaudeCodeHarness()
        assert h.composed_tree_in_use_elsewhere(composed_root, env=_env(personal)) is False

        h.register(composed_root, runner=lambda args, **kw: None, env=_env(personal))

        # Its own registration is not "elsewhere".
        assert h.composed_tree_in_use_elsewhere(composed_root, env=_env(personal)) is False

    def test_another_config_dirs_registration_holds_the_tree(
        self, composed_root, personal, second
    ):
        h = ClaudeCodeHarness()
        for d in (personal, second):
            h.register(composed_root, runner=lambda args, **kw: None, env=_env(d))

        assert h.composed_tree_in_use_elsewhere(composed_root, env=_env(personal)) is True
        assert h.composed_tree_in_use_elsewhere(composed_root, env=_env(second)) is True

    def test_the_hold_is_released_when_the_other_dir_unregisters(
        self, composed_root, personal, second
    ):
        h = ClaudeCodeHarness()
        for d in (personal, second):
            h.register(composed_root, runner=lambda args, **kw: None, env=_env(d))

        h.unregister_marketplace(composed_root, runner=lambda args, **kw: None, env=_env(second))

        assert h.composed_tree_in_use_elsewhere(composed_root, env=_env(personal)) is False
        assert h.composed_tree_in_use_elsewhere(composed_root, env=_env(second)) is True


def _write_credentials(config_dir: Path, access_token: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": access_token, "expiresAt": 1}})
    )


class TestAccountAuthenticationVariesWithTheSignal:
    """The Claude Code override answers from the account's credentials file —
    same input shape (a declared account), different content, different
    verdict. This is the dependency the method exists to protect."""

    def test_a_non_empty_access_token_reads_as_authenticated(self, tmp_path):
        account_dir = tmp_path / "acct"
        _write_credentials(account_dir, "sk-ant-oat01-fake")

        result = ClaudeCodeHarness().session_launch_account_authentication(
            str(account_dir), env={"HOME": str(tmp_path / "home")}
        )

        assert result is AccountAuthentication.AUTHENTICATED

    def test_an_empty_access_token_reads_as_not_authenticated(self, tmp_path):
        account_dir = tmp_path / "acct"
        _write_credentials(account_dir, "")

        result = ClaudeCodeHarness().session_launch_account_authentication(
            str(account_dir), env={"HOME": str(tmp_path / "home")}
        )

        assert result is AccountAuthentication.NOT_AUTHENTICATED

    def test_a_missing_credentials_file_reads_as_not_authenticated(self, tmp_path):
        account_dir = tmp_path / "acct"

        result = ClaudeCodeHarness().session_launch_account_authentication(
            str(account_dir), env={"HOME": str(tmp_path / "home")}
        )

        assert result is AccountAuthentication.NOT_AUTHENTICATED
        assert result is not AccountAuthentication.CANNOT_TELL

    def test_credentials_missing_the_oauth_key_reads_as_not_authenticated(self, tmp_path):
        account_dir = tmp_path / "acct"
        account_dir.mkdir(parents=True)
        (account_dir / ".credentials.json").write_text(json.dumps({"unrelated": True}))

        result = ClaudeCodeHarness().session_launch_account_authentication(
            str(account_dir), env={"HOME": str(tmp_path / "home")}
        )

        assert result is AccountAuthentication.NOT_AUTHENTICATED
        assert result is not AccountAuthentication.CANNOT_TELL


class TestAccountAuthenticationCannotTell:
    """Unreadable or malformed signal reads as CANNOT_TELL — distinguishable
    by the caller from NOT_AUTHENTICATED, never collapsed into it."""

    def test_a_credentials_file_that_cannot_be_read_is_cannot_tell(self, tmp_path):
        account_dir = tmp_path / "acct"
        account_dir.mkdir()
        # A directory in place of the file: reading it raises OSError (not
        # FileNotFoundError), simulating an unreadable signal without
        # depending on chmod/root-privilege behavior.
        (account_dir / ".credentials.json").mkdir()

        result = ClaudeCodeHarness().session_launch_account_authentication(
            str(account_dir), env={"HOME": str(tmp_path / "home")}
        )

        assert result is AccountAuthentication.CANNOT_TELL
        assert result is not AccountAuthentication.NOT_AUTHENTICATED

    def test_malformed_json_is_cannot_tell(self, tmp_path):
        account_dir = tmp_path / "acct"
        account_dir.mkdir()
        (account_dir / ".credentials.json").write_text("{not valid json")

        result = ClaudeCodeHarness().session_launch_account_authentication(
            str(account_dir), env={"HOME": str(tmp_path / "home")}
        )

        assert result is AccountAuthentication.CANNOT_TELL
        assert result is not AccountAuthentication.NOT_AUTHENTICATED


class TestAccountAuthenticationNeverBlocks:
    """Invoking the probe against a realistic-sized fixture returns a value
    without spawning a subprocess, opening a network connection, or
    otherwise blocking on interaction — the actual behaviour "no
    subprocess, no network call" promises. Pinned directly, on the
    subprocess and socket seams themselves, rather than through a
    wall-clock budget: a millisecond threshold flakes under parallel test
    execution (`pytest -n auto`) for reasons that have nothing to do with
    whether this method blocks."""

    def test_reading_a_realistic_sized_credentials_file_never_spawns_or_dials_out(
        self, tmp_path, monkeypatch
    ):
        account_dir = tmp_path / "acct"
        account_dir.mkdir()
        padding = {f"Claude_Code_Remote|{i:x}": {"accessToken": ""} for i in range(40)}
        (account_dir / ".credentials.json").write_text(
            json.dumps(
                {
                    "mcpOAuth": padding,
                    "claudeAiOauth": {"accessToken": "sk-ant-oat01-fake", "expiresAt": 1},
                }
            )
        )

        def _boom_subprocess(*args, **kwargs):
            raise AssertionError("must never spawn a subprocess")

        def _boom_connect(*args, **kwargs):
            raise AssertionError("must never open a network connection")

        monkeypatch.setattr(subprocess, "run", _boom_subprocess)
        monkeypatch.setattr(subprocess, "Popen", _boom_subprocess)
        monkeypatch.setattr(socket.socket, "connect", _boom_connect)

        result = ClaudeCodeHarness().session_launch_account_authentication(
            str(account_dir), env={"HOME": str(tmp_path / "home")}
        )

        assert result is AccountAuthentication.AUTHENTICATED


class TestDefaultAccountCredentialsDirPrecedence:
    """`_default_account_credentials_dir`'s precedence chain, pinned one
    branch at a time: `CLAUDE_CONFIG_DIR` beats `HOME`, `USERPROFILE`
    stands in when `HOME` is absent, and `Path.home()` is the final
    fallback when neither environment variable is set. Each test plants a
    DECOY credentials file at the location a wrong precedence would read
    instead, so a swapped or dropped branch reads NOT_AUTHENTICATED from
    the decoy rather than accidentally passing."""

    def test_claude_config_dir_wins_over_home(self, tmp_path: Path):
        winner = tmp_path / "winner"
        _write_credentials(winner, "sk-ant-oat01-winner")
        decoy_home = tmp_path / "decoy-home"
        _write_credentials(decoy_home / ".claude", "")

        env = {"CLAUDE_CONFIG_DIR": str(winner), "HOME": str(decoy_home)}
        result = ClaudeCodeHarness().session_launch_account_authentication(None, env=env)

        assert result is AccountAuthentication.AUTHENTICATED

    def test_userprofile_used_when_home_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        profile_home = tmp_path / "userprofile-home"
        _write_credentials(profile_home / ".claude", "sk-ant-oat01-fake")
        decoy_real_home = tmp_path / "decoy-real-home"
        _write_credentials(decoy_real_home / ".claude", "")
        monkeypatch.setattr(Path, "home", lambda: decoy_real_home)

        env = {"USERPROFILE": str(profile_home)}
        result = ClaudeCodeHarness().session_launch_account_authentication(None, env=env)

        assert result is AccountAuthentication.AUTHENTICATED

    def test_falls_back_to_path_home_when_neither_env_var_is_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        real_home = tmp_path / "real-home"
        _write_credentials(real_home / ".claude", "sk-ant-oat01-fake")
        monkeypatch.setattr(Path, "home", lambda: real_home)

        result = ClaudeCodeHarness().session_launch_account_authentication(None, env={})

        assert result is AccountAuthentication.AUTHENTICATED


class TestAccountAuthenticationDefaultAccountResolution:
    """``None`` means "the caller declared nothing" and resolves to the
    harness's own default rather than inheriting from the ambient
    environment. ``TRAILHEAD_CLAUDE_DIR`` is a trailhead-only test seam the
    real launched process never reads for the default account (the same
    axiom ``session_launch_account_identity`` pins against ``_claude_dir``)
    — so a conflicting value planted there must be ignored in favor of the
    real default, ``HOME``/.claude."""

    def test_default_account_ignores_the_trailhead_seam_not_the_real_default(self, tmp_path):
        real_home = tmp_path / "home"
        _write_credentials(real_home / ".claude", "sk-ant-oat01-real")

        decoy = tmp_path / "decoy-claude"
        _write_credentials(decoy, "")

        env = {"HOME": str(real_home), "TRAILHEAD_CLAUDE_DIR": str(decoy)}

        result = ClaudeCodeHarness().session_launch_account_authentication(None, env=env)

        assert result is AccountAuthentication.AUTHENTICATED
