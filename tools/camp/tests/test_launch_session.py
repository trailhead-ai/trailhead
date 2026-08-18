"""Tests for camp.launch.session — the exec-owning detached launch engine.

Test contract:
- The env scrub is asserted against the ACTUAL argv handed to a mocked
  subprocess.Popen: every variable from session_launch_env_unset appears as an
  `env -u` operand, and the `env` invocation sits INSIDE the pane command (after
  tmux's own options, immediately preceding the harness argv) — not applying to
  tmux itself. Asserting that the seam merely RETURNED a non-empty list does not
  establish this: the scrub only protects anything if it reaches the pane.
- Popen `env=` omits every scrub-listed variable; `cwd=` and the `-c` operand
  equal the ONE resolved directory; start_new_session=True.
- The command is argv (a list), never a shell string; a workspace path holding
  shell-active characters survives as exactly one word.
- tmux session name is camp-<slug>-<first 8 of uuid>; the uuid handed to
  session_launch is the one reported back.
- Refusals with NO process spawned: trust pre-seed returns False; harness
  unresolvable; session_launch returns None; tmux absent; unresolvable launch dir.
- Pretrust disabled by config → spawn proceeds, stderr warning emitted.
- Already-live sessions are reported on stderr and never refuse.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


SCRUB = ["FAKE_CHILD_SESSION", "FAKECODE", "FAKE_SESSION_TOKEN"]

GROUP = {"group": {"name": "testgroup"}}
GROUP_NO_PRETRUST = {"group": {"name": "testgroup"}, "harness": {"pretrust": False}}


class FakeHarness:
    """Stand-in for the trailhead harness seam.

    `launch_argv` None models a harness that cannot launch at all;
    `enumerate_argv` None models one with no enumeration concept.
    """

    name = "fakeharness"

    def __init__(self, *, launch_argv=..., enumerate_argv=None, records=None):
        self._launch_argv = launch_argv
        self._enumerate_argv = enumerate_argv
        self._records = records or []
        self.launch_calls: list[tuple[Path, str]] = []

    def session_launch(self, workspace, session_id):
        self.launch_calls.append((workspace, session_id))
        if self._launch_argv is ...:
            return ["fakeharness", "--rc", "--sid", session_id]
        return self._launch_argv

    def session_launch_env_unset(self):
        return list(SCRUB)

    def session_enumerate(self, workspace=None):
        return self._enumerate_argv

    def parse_session_list(self, output):
        return list(self._records)


class Recorder:
    """Captures the single Popen call the engine is allowed to make."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})

        class _Proc:
            pid = 4242

        return _Proc()

    @property
    def argv(self):
        assert len(self.calls) == 1, f"expected exactly one spawn, got {len(self.calls)}"
        return self.calls[0]["argv"]

    @property
    def kwargs(self):
        assert len(self.calls) == 1, f"expected exactly one spawn, got {len(self.calls)}"
        return self.calls[0]["kwargs"]


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """Wire the engine's collaborators to fakes; hand back the knobs."""
    import camp.launch.session as session

    ws = tmp_path / "ws"
    ws.mkdir()

    state: dict[str, Any] = {
        "harness": FakeHarness(),
        "pretrust": True,
        "workspace": ws,
        "which": "/usr/bin/tmux",
        "popen": Recorder(),
        "module": session,
    }

    monkeypatch.setattr(session, "harness_for", lambda group: state["harness"])
    monkeypatch.setattr(
        session, "workspace_dir", lambda group, slug, env=None: state["workspace"]
    )
    monkeypatch.setattr(
        session,
        "pretrust_workspace",
        lambda launch_dir, workspace_root, env=None: state["pretrust"],
    )
    monkeypatch.setattr(session.shutil, "which", lambda binary: state["which"])
    monkeypatch.setattr(session.subprocess, "Popen", state["popen"])
    monkeypatch.setattr(
        session.subprocess, "run", lambda *a, **k: pytest.fail("unexpected subprocess.run")
    )
    return state


def _launch(rig, group=GROUP, slug="feat-x", env=None):
    return rig["module"].launch_session(group, slug, env=env or {"PATH": "/usr/bin"})


def _pane_command(argv: list[str]) -> list[str]:
    """The part of the tmux argv that is the pane's own command."""
    return argv[argv.index("env") :]


# ---------------------------------------------------------------------------
# the env scrub reaches the pane
# ---------------------------------------------------------------------------


class TestScrubReachesThePane:
    def test_every_scrub_var_is_an_env_u_operand_in_the_pane_command(self, rig):
        _launch(rig)
        argv = rig["popen"].argv

        pane = _pane_command(argv)
        assert pane[0] == "env"
        # Everything between `env` and the harness argv is `-u VAR` pairs.
        harness_start = pane.index("fakeharness")
        operands = pane[1:harness_start]
        assert operands == [tok for var in SCRUB for tok in ("-u", var)]

    def test_env_sits_after_tmux_options_immediately_before_the_harness_argv(self, rig):
        _launch(rig)
        argv = rig["popen"].argv

        env_at = argv.index("env")
        assert argv[0] == "tmux"
        # `env` is not tmux's own argv[1] — it does not apply to tmux itself.
        assert env_at > 1
        # Every tmux option precedes it, and the harness argv follows the operands.
        assert "-c" in argv[:env_at] and "-s" in argv[:env_at]
        assert argv[env_at + 1 + 2 * len(SCRUB)] == "fakeharness"

    def test_pane_command_is_exactly_env_scrub_then_the_seam_argv(self, rig):
        _launch(rig)
        session_id = rig["harness"].launch_calls[0][1]

        expected = ["env"]
        for var in SCRUB:
            expected += ["-u", var]
        expected += ["fakeharness", "--rc", "--sid", session_id]
        assert _pane_command(rig["popen"].argv) == expected


# ---------------------------------------------------------------------------
# spawn shape
# ---------------------------------------------------------------------------


class TestSpawnShape:
    def test_popen_env_omits_every_scrub_var(self, rig):
        env = {"PATH": "/usr/bin", "KEEP": "yes"}
        for var in SCRUB:
            env[var] = "leaked"

        rig["module"].launch_session(GROUP, "feat-x", env=env)

        popen_env = rig["popen"].kwargs["env"]
        for var in SCRUB:
            assert var not in popen_env, f"{var} survived camp's own Popen env"
        assert popen_env["KEEP"] == "yes"

    def test_cwd_and_the_c_operand_are_the_one_resolved_directory(self, rig, tmp_path):
        link = tmp_path / "link"
        link.symlink_to(rig["workspace"])
        rig["workspace"] = link

        result = _launch(rig)

        resolved = str(link.resolve())
        argv = rig["popen"].argv
        assert argv[argv.index("-c") + 1] == resolved
        assert rig["popen"].kwargs["cwd"] == resolved
        assert str(result.launch_dir) == resolved

    def test_start_new_session_is_true(self, rig):
        _launch(rig)
        assert rig["popen"].kwargs["start_new_session"] is True

    def test_command_is_argv_not_a_shell_string(self, rig):
        _launch(rig)
        assert isinstance(rig["popen"].argv, list)
        assert all(isinstance(word, str) for word in rig["popen"].argv)
        assert rig["popen"].kwargs.get("shell", False) is False

    def test_shell_active_workspace_path_stays_one_word(self, rig, tmp_path):
        hostile = tmp_path / "a b; rm -rf $HOME && echo 'x'"
        hostile.mkdir()
        rig["workspace"] = hostile

        _launch(rig)

        argv = rig["popen"].argv
        assert argv[argv.index("-c") + 1] == str(hostile.resolve())
        assert argv.count(str(hostile.resolve())) == 1


class TestSessionIdentity:
    def test_tmux_name_is_camp_slug_and_first_eight_of_the_uuid(self, rig):
        result = _launch(rig, slug="feat-x")
        argv = rig["popen"].argv

        assert result.tmux_name == f"camp-feat-x-{result.session_id[:8]}"
        assert argv[argv.index("-s") + 1] == result.tmux_name

    def test_the_uuid_handed_to_the_seam_is_the_one_reported_back(self, rig):
        result = _launch(rig)

        workspace, session_id = rig["harness"].launch_calls[0]
        assert session_id == result.session_id
        assert workspace == rig["workspace"].resolve()

    def test_session_id_is_a_fresh_uuid_each_launch(self, rig):
        import uuid

        first = _launch(rig)
        second = _launch(rig)

        assert first.session_id != second.session_id
        assert first.tmux_name != second.tmux_name
        uuid.UUID(first.session_id)
        uuid.UUID(second.session_id)


# ---------------------------------------------------------------------------
# refusals — no process spawned
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_trust_preseed_failure_refuses_naming_the_workspace(self, rig):
        rig["pretrust"] = False

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig)

        assert str(rig["workspace"].resolve()) in str(excinfo.value)
        assert rig["popen"].calls == []

    def test_unlaunchable_harness_refuses_naming_the_harness(self, rig):
        rig["harness"] = FakeHarness(launch_argv=None)

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig)

        assert "fakeharness" in str(excinfo.value)
        assert rig["popen"].calls == []

    def test_unresolvable_harness_refuses(self, rig):
        rig["harness"] = None

        with pytest.raises(rig["module"].LaunchError):
            _launch(rig)

        assert rig["popen"].calls == []

    def test_missing_tmux_refuses_naming_the_binary(self, rig):
        rig["which"] = None

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig)

        assert "tmux" in str(excinfo.value)
        assert rig["popen"].calls == []

    def test_unresolvable_launch_dir_refuses(self, rig, tmp_path):
        rig["workspace"] = tmp_path / "never-created"

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig)

        assert "never-created" in str(excinfo.value)
        assert rig["popen"].calls == []

    def test_trust_refusal_precedes_any_tmux_spawn(self, rig):
        """The trust gate must fire before the process exists, not after."""
        rig["pretrust"] = False
        with pytest.raises(rig["module"].LaunchError):
            _launch(rig)
        assert rig["popen"].calls == []


# ---------------------------------------------------------------------------
# warnings that do NOT refuse
# ---------------------------------------------------------------------------


class TestNonRefusingWarnings:
    def test_pretrust_disabled_by_config_spawns_with_a_stderr_warning(self, rig, capsys):
        result = _launch(rig, group=GROUP_NO_PRETRUST)

        assert len(rig["popen"].calls) == 1
        assert result.session_id
        err = capsys.readouterr().err
        assert "camp:" in err
        assert "trust" in err.lower()

    def test_pretrust_disabled_never_calls_the_trust_preseed(self, rig, monkeypatch):
        calls: list[Any] = []
        monkeypatch.setattr(
            rig["module"],
            "pretrust_workspace",
            lambda *a, **k: calls.append(a) or True,
        )

        _launch(rig, group=GROUP_NO_PRETRUST)

        assert calls == []

    def test_already_live_sessions_are_reported_on_stderr_and_do_not_refuse(
        self, rig, capsys, monkeypatch
    ):
        rig["harness"] = FakeHarness(
            enumerate_argv=["fakeharness", "agents"],
            records=[object(), object()],
        )
        monkeypatch.setattr(
            rig["module"].subprocess,
            "run",
            lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "[]"})(),
        )

        result = _launch(rig)

        assert len(rig["popen"].calls) == 1
        assert result.session_id
        err = capsys.readouterr().err
        assert "camp:" in err
        assert "2" in err

    def test_enumeration_failure_does_not_refuse(self, rig, monkeypatch):
        def boom(*a, **k):
            raise OSError("no such binary")

        rig["harness"] = FakeHarness(enumerate_argv=["fakeharness", "agents"])
        monkeypatch.setattr(rig["module"].subprocess, "run", boom)

        result = _launch(rig)

        assert len(rig["popen"].calls) == 1
        assert result.session_id


# ---------------------------------------------------------------------------
# the seam boundary — camp spells only tmux and env -u
# ---------------------------------------------------------------------------


class TestSeamBoundary:
    def test_harness_argv_is_taken_from_the_seam_whole(self, rig):
        """camp places the seam's argv into the pane command verbatim.

        Proven with argv the engine could not have composed itself: if any part
        of the harness command were hardcoded in camp core rather than read from
        the seam, this argv could not survive intact.
        """
        rig["harness"] = FakeHarness(launch_argv=["odd-binary", "-x", "--weird=1", "tail"])

        _launch(rig)

        pane = _pane_command(rig["popen"].argv)
        assert pane[-4:] == ["odd-binary", "-x", "--weird=1", "tail"]

    def test_scrub_var_names_are_taken_from_the_seam(self, rig, monkeypatch):
        """The scrubbed names come from the seam, not from a list in camp core."""
        monkeypatch.setattr(
            rig["harness"], "session_launch_env_unset", lambda: ["ONLY_THIS_ONE"]
        )

        _launch(rig)

        pane = _pane_command(rig["popen"].argv)
        assert pane[:3] == ["env", "-u", "ONLY_THIS_ONE"]


# ---------------------------------------------------------------------------
# confirmation by enumeration membership
# ---------------------------------------------------------------------------


def _record(session_id: str):
    from trailhead.harness.base import SessionRecord

    return SessionRecord(
        session_id=session_id,
        cwd=Path("/tmp"),
        kind="fake",
        controllable=True,
        name=None,
        pid=1234,
        started_at=None,
    )


class SequencedHarness:
    """A harness whose enumeration answer is scripted call-by-call.

    Each entry in *script* is either a list of live session ids, or an
    exception instance/class to raise from that poll.
    """

    def __init__(self, script):
        self._script = list(script)
        self.enumerate_calls: list[Path] = []
        self.run_calls = 0

    def session_enumerate(self, workspace=None):
        self.enumerate_calls.append(workspace)
        return ["fakeharness", "agents"]

    def parse_session_list(self, output):
        self.run_calls += 1
        outcome = self._script[min(self.run_calls - 1, len(self._script) - 1)]
        if isinstance(outcome, Exception) or (
            isinstance(outcome, type) and issubclass(outcome, Exception)
        ):
            raise outcome
        return [_record(sid) for sid in outcome]


class FakeClock:
    """Advances by *step* seconds on every read after the first."""

    def __init__(self, step):
        self._step = step
        self._t = 0.0
        self._first = True

    def __call__(self):
        if self._first:
            self._first = False
            return self._t
        self._t += self._step
        return self._t


@pytest.fixture
def confirm_rig(monkeypatch, tmp_path):
    import camp.launch.session as session

    launch_dir = tmp_path / "resolved"
    launch_dir.mkdir()
    launched = session.LaunchedSession(
        session_id="target-id", tmux_name="camp-feat-x-abcd1234", launch_dir=launch_dir
    )

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(session.subprocess, "run", fake_run)

    return {"module": session, "launched": launched, "run": fake_run, "calls": calls}


class TestConfirmSession:
    def test_confirmed_on_a_later_poll_returns_without_raising(self, confirm_rig, monkeypatch):
        session = confirm_rig["module"]
        harness = SequencedHarness([[], [], ["target-id"]])

        sleeps: list[float] = []
        session.confirm_session(
            harness,
            confirm_rig["launched"],
            interval=0.5,
            timeout=10.0,
            sleep=sleeps.append,
            clock=FakeClock(0.5),
        )

        assert harness.run_calls == 3
        assert sleeps == [0.5, 0.5]

    def test_membership_is_exact_string_not_prefix(self, confirm_rig, monkeypatch):
        session = confirm_rig["module"]
        harness = SequencedHarness([["target-id-extra"]])

        with pytest.raises(session.LaunchError):
            session.confirm_session(
                harness,
                confirm_rig["launched"],
                interval=0.5,
                timeout=0.5,
                sleep=lambda s: None,
                clock=FakeClock(1.0),
            )

    def test_harness_error_mid_poll_is_tolerated_and_polling_continues(
        self, confirm_rig
    ):
        session = confirm_rig["module"]
        from trailhead.harness import HarnessError

        harness = SequencedHarness([HarnessError("boom"), ["target-id"]])

        session.confirm_session(
            harness,
            confirm_rig["launched"],
            interval=0.5,
            timeout=10.0,
            sleep=lambda s: None,
            clock=FakeClock(0.5),
        )

        assert harness.run_calls == 2

    def test_poll_count_is_bounded_by_timeout_over_interval(self, confirm_rig):
        session = confirm_rig["module"]
        harness = SequencedHarness([[]])

        with pytest.raises(session.LaunchError):
            session.confirm_session(
                harness,
                confirm_rig["launched"],
                interval=0.5,
                timeout=2.0,
                sleep=lambda s: None,
                clock=FakeClock(0.5),
            )

        assert harness.run_calls <= 5

    def test_never_enumerated_through_timeout_kills_the_exact_tmux_name(self, confirm_rig):
        session = confirm_rig["module"]
        harness = SequencedHarness([[]])

        with pytest.raises(session.LaunchError) as excinfo:
            session.confirm_session(
                harness,
                confirm_rig["launched"],
                interval=0.5,
                timeout=1.0,
                sleep=lambda s: None,
                clock=FakeClock(0.5),
            )

        kill_calls = [c for c in confirm_rig["calls"] if c[0] == "tmux"]
        assert len(kill_calls) == 1
        assert kill_calls[0] == ["tmux", "kill-session", "-t", "camp-feat-x-abcd1234"]
        assert "trust" in str(excinfo.value).lower()

    def test_failing_kill_is_reported_on_stderr_naming_the_session(
        self, confirm_rig, monkeypatch, capsys
    ):
        session = confirm_rig["module"]
        harness = SequencedHarness([[]])
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[0] == "tmux":
                return type(
                    "R", (), {"returncode": 1, "stdout": "", "stderr": "no such session"}
                )()
            output = harness.parse_session_list("")
            return type("R", (), {"returncode": 0, "stdout": output, "stderr": ""})()

        monkeypatch.setattr(session.subprocess, "run", fake_run)

        with pytest.raises(session.LaunchError):
            session.confirm_session(
                harness,
                confirm_rig["launched"],
                interval=0.5,
                timeout=0.5,
                sleep=lambda s: None,
                clock=FakeClock(1.0),
            )

        err = capsys.readouterr().err
        assert "camp-feat-x-abcd1234" in err
        assert len([c for c in calls if c[0] == "tmux"]) == 1

    def test_enumerate_argument_is_the_resolved_directory_symlink_case(
        self, confirm_rig, tmp_path, monkeypatch
    ):
        session = confirm_rig["module"]
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real_dir)
        resolved = link.resolve()

        launched = session.LaunchedSession(
            session_id="target-id", tmux_name="camp-feat-x-abcd1234", launch_dir=resolved
        )
        harness = SequencedHarness([["target-id"]])

        session.confirm_session(
            harness,
            launched,
            interval=0.5,
            timeout=10.0,
            sleep=lambda s: None,
            clock=FakeClock(0.5),
        )

        assert harness.enumerate_calls == [resolved]

    def test_constants_exist_and_are_the_defaults_used_by_confirm_session(self):
        import inspect

        import camp.launch.session as session

        assert session._CONFIRM_POLL_INTERVAL_SECONDS > 0
        assert session._CONFIRM_POLL_TIMEOUT_SECONDS > 0
        sig = inspect.signature(session.confirm_session)
        assert sig.parameters["interval"].default == session._CONFIRM_POLL_INTERVAL_SECONDS
        assert sig.parameters["timeout"].default == session._CONFIRM_POLL_TIMEOUT_SECONDS
