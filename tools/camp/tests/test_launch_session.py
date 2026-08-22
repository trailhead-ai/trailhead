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

#: A resumed session id, spelled once so the tests that assert on its first
#: eight characters cannot drift from the id actually handed to the engine.
RESUME_ID = "11111111-2222-3333-4444-666677778888"


def _completed(*, returncode=0, stdout="", stderr=""):
    """A `subprocess.run` result stand-in — returncode plus the two streams."""
    return type(
        "CompletedProcess",
        (),
        {"returncode": returncode, "stdout": stdout, "stderr": stderr},
    )()

GROUP = {"group": {"name": "testgroup"}}
GROUP_NO_PRETRUST = {"group": {"name": "testgroup"}, "harness": {"pretrust": False}}


class FakeHarness:
    """Stand-in for the trailhead harness seam.

    `launch_argv` None models a harness that cannot launch at all;
    `resume_argv` None models one that cannot resume; `enumerate_argv` None
    models one with no enumeration concept.
    """

    name = "fakeharness"

    def __init__(
        self, *, launch_argv=..., resume_argv=..., enumerate_argv=None, records=None
    ):
        self._launch_argv = launch_argv
        self._resume_argv = resume_argv
        self._enumerate_argv = enumerate_argv
        self._records = records or []
        self.launch_calls: list[tuple[Path, str, str | None]] = []
        self.resume_calls: list[str] = []

    def session_launch(self, workspace, session_id, *, session_name=None):
        self.launch_calls.append((workspace, session_id, session_name))
        if self._launch_argv is ...:
            return ["fakeharness", "--rc", "--sid", session_id]
        return self._launch_argv

    def session_resume(self, session_id):
        self.resume_calls.append(session_id)
        if self._resume_argv is ...:
            return ["fakeharness", "--reattach", session_id]
        return self._resume_argv

    def session_launch_env_unset(self):
        return list(SCRUB)

    def session_enumerate(self, workspace=None):
        return self._enumerate_argv

    def parse_session_list(self, output):
        return list(self._records)


class Recorder:
    """Captures the single tmux spawn the engine is allowed to make.

    The engine reads tmux's exit status — the session-name claim is what makes a
    second launch of the same session refuse — so the recorder answers like a
    completed process, with a `returncode` and `stderr` each test can set.
    """

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.returncode = 0
        self.stderr = ""

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})
        return _completed(returncode=self.returncode, stderr=self.stderr)

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
        "pretrust_calls": [],
        "workspace": ws,
        "which": "/usr/bin/tmux",
        "spawn": Recorder(),
        "enumerate": lambda *a, **k: pytest.fail("unexpected enumeration"),
        "module": session,
    }

    def fake_pretrust(launch_dir, *, workspace_root, env=None):
        state["pretrust_calls"].append(
            {"launch_dir": launch_dir, "workspace_root": workspace_root}
        )
        return state["pretrust"]

    def fake_run(argv, **kwargs):
        """Split the engine's two subprocess uses: the spawn, and everything else."""
        if list(argv[:2]) == ["tmux", "new-session"]:
            return state["spawn"](argv, **kwargs)
        return state["enumerate"](argv, **kwargs)

    monkeypatch.setattr(session, "harness_for", lambda group: state["harness"])
    monkeypatch.setattr(
        session, "workspace_dir", lambda group, slug, env=None: state["workspace"]
    )
    monkeypatch.setattr(session, "pretrust_workspace", fake_pretrust)
    monkeypatch.setattr(session.shutil, "which", lambda binary: state["which"])
    monkeypatch.setattr(session.subprocess, "run", fake_run)
    monkeypatch.setattr(
        session.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("the engine must spawn through subprocess.run"),
    )
    return state


def _launch(rig, group=GROUP, slug="feat-x", env=None):
    return rig["module"].launch_session(group, slug, env=env or {"PATH": "/usr/bin"})


def _pane_command(argv: list[str]) -> list[str]:
    """The part of the tmux argv that is the pane's own command."""
    return argv[argv.index("env") :]


def _dir_env(tmp_path: Path) -> dict[str, str]:
    """An environment whose HOME is sandboxed, so the deny list never reads the
    invoking operator's real credential stores."""
    return {"PATH": "/usr/bin", "HOME": str(tmp_path / "home")}


def _dir_group(tmp_path: Path, roots=None) -> dict[str, Any]:
    """A group whose launch-roots allowlist covers *tmp_path* unless told otherwise."""
    return {
        "group": {"name": "testgroup"},
        "launch": {"roots": [str(tmp_path)] if roots is None else roots},
    }


def _launch_at(rig, tmp_path, root, *, group=None, name_component="odd-handle", resume=None):
    """Launch rooted at *root* — the shape both `--dir` and a resume take."""
    return rig["module"].launch_session(
        group if group is not None else _dir_group(tmp_path),
        root=root,
        name_component=name_component,
        trust_scope=root,
        resume_session_id=resume,
        env=_dir_env(tmp_path),
    )


# ---------------------------------------------------------------------------
# the workspace-slug launch, pinned whole
# ---------------------------------------------------------------------------


class TestSlugLaunchShapeIsPinnedWhole:
    """The slug launch is the default caller; its observable shape is frozen.

    Every other test in this file pins one property of the spawn. This one pins
    all of them at once, spelled out literally rather than recomputed from the
    engine's own inputs, so a change that widens the engine for another launch
    flavor cannot quietly move the shape the existing callers depend on.
    """

    def test_argv_cwd_env_name_and_stderr_are_all_exactly_as_specified(
        self, rig, capsys
    ):
        result = rig["module"].launch_session(
            GROUP, "feat-x", env={"PATH": "/usr/bin", "KEEP": "yes"}
        )

        sid = result.session_id
        workspace = str(rig["workspace"].resolve())

        assert rig["spawn"].argv == [
            "tmux",
            "new-session",
            "-d",
            "-s",
            f"camp-feat-x-{sid[:8]}",
            "-c",
            workspace,
            "env",
            "-u",
            "FAKE_CHILD_SESSION",
            "-u",
            "FAKECODE",
            "-u",
            "FAKE_SESSION_TOKEN",
            "fakeharness",
            "--rc",
            "--sid",
            sid,
        ]
        assert rig["spawn"].kwargs["cwd"] == workspace
        assert rig["spawn"].kwargs["env"] == {"PATH": "/usr/bin", "KEEP": "yes"}
        assert rig["spawn"].kwargs["start_new_session"] is True
        assert result.tmux_name == f"camp-feat-x-{sid[:8]}"
        assert result.launch_dir == rig["workspace"].resolve()
        assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# the call contract — exactly one way to name where a session is rooted
# ---------------------------------------------------------------------------


class TestCallContract:
    """A launch is addressed either by workspace slug or by explicit directory.

    Supplying neither, both, or an incomplete directory triple is a bug in the
    caller, not a launch to refuse — so it raises before the engine resolves
    anything, and every one of these cases asserts that nothing was spawned.
    """

    def test_neither_slug_nor_root_raises_before_any_work(self, rig):
        with pytest.raises(ValueError):
            rig["module"].launch_session(GROUP, env={"PATH": "/usr/bin"})

        assert rig["spawn"].calls == []
        assert rig["pretrust_calls"] == []

    def test_both_slug_and_root_raises(self, rig, tmp_path):
        with pytest.raises(ValueError):
            rig["module"].launch_session(
                GROUP,
                "feat-x",
                root=tmp_path,
                name_component="proj",
                trust_scope=tmp_path,
                env={"PATH": "/usr/bin"},
            )

        assert rig["spawn"].calls == []
        assert rig["pretrust_calls"] == []

    def test_root_without_a_name_component_raises(self, rig, tmp_path):
        with pytest.raises(ValueError):
            rig["module"].launch_session(
                GROUP, root=tmp_path, trust_scope=tmp_path, env={"PATH": "/usr/bin"}
            )

        assert rig["spawn"].calls == []
        assert rig["pretrust_calls"] == []

    def test_root_without_a_trust_scope_raises(self, rig, tmp_path):
        with pytest.raises(ValueError):
            rig["module"].launch_session(
                GROUP, root=tmp_path, name_component="proj", env={"PATH": "/usr/bin"}
            )

        assert rig["spawn"].calls == []
        assert rig["pretrust_calls"] == []


# ---------------------------------------------------------------------------
# the env scrub reaches the pane
# ---------------------------------------------------------------------------


class TestScrubReachesThePane:
    def test_every_scrub_var_is_an_env_u_operand_in_the_pane_command(self, rig):
        _launch(rig)
        argv = rig["spawn"].argv

        pane = _pane_command(argv)
        assert pane[0] == "env"
        # Everything between `env` and the harness argv is `-u VAR` pairs.
        harness_start = pane.index("fakeharness")
        operands = pane[1:harness_start]
        assert operands == [tok for var in SCRUB for tok in ("-u", var)]

    def test_env_sits_after_tmux_options_immediately_before_the_harness_argv(self, rig):
        _launch(rig)
        argv = rig["spawn"].argv

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
        assert _pane_command(rig["spawn"].argv) == expected


# ---------------------------------------------------------------------------
# spawn shape
# ---------------------------------------------------------------------------


class TestSpawnShape:
    def test_a_spawn_timeout_reclaims_the_session_name_before_refusing(self, rig):
        """A timed-out spawn is indeterminate, so the name is reclaimed.

        Reading tmux's exit status means waiting for it, and a wait that times
        out proves nothing about whether the session was created — tmux may
        already have claimed the name. Refusing without reclaiming it leaves
        the operator told the launch failed while the session runs, and their
        next attempt refusing as already-running for a session they never got.
        """
        import subprocess as subprocess_mod

        killed: list[list[str]] = []
        rig["enumerate"] = lambda argv, **kwargs: killed.append(list(argv))

        def timing_out_spawn(argv, **kwargs):
            raise subprocess_mod.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

        rig["spawn"] = timing_out_spawn

        with pytest.raises(rig["module"].LaunchError):
            _launch(rig)

        assert killed, "a timed-out spawn left the session name unreclaimed"
        assert killed[0][:3] == ["tmux", "kill-session", "-t"]
        assert killed[0][3].startswith("camp-feat-x-")

    def test_a_spawn_that_fails_outright_reclaims_nothing(self, rig):
        """An OSError means the process never ran, so there is nothing to kill.

        Only an indeterminate outcome earns a reclaim; issuing one for a spawn
        that provably never started would kill a same-named session belonging
        to somebody else.
        """
        killed: list[list[str]] = []
        rig["enumerate"] = lambda argv, **kwargs: killed.append(list(argv))

        def failing_spawn(argv, **kwargs):
            raise OSError("no tmux here")

        rig["spawn"] = failing_spawn

        with pytest.raises(rig["module"].LaunchError):
            _launch(rig)

        assert killed == []

    def test_popen_env_omits_every_scrub_var(self, rig):
        env = {"PATH": "/usr/bin", "KEEP": "yes"}
        for var in SCRUB:
            env[var] = "leaked"

        rig["module"].launch_session(GROUP, "feat-x", env=env)

        popen_env = rig["spawn"].kwargs["env"]
        for var in SCRUB:
            assert var not in popen_env, f"{var} survived camp's own Popen env"
        assert popen_env["KEEP"] == "yes"

    def test_cwd_and_the_c_operand_are_the_one_resolved_directory(self, rig, tmp_path):
        link = tmp_path / "link"
        link.symlink_to(rig["workspace"])
        rig["workspace"] = link

        result = _launch(rig)

        resolved = str(link.resolve())
        argv = rig["spawn"].argv
        assert argv[argv.index("-c") + 1] == resolved
        assert rig["spawn"].kwargs["cwd"] == resolved
        assert str(result.launch_dir) == resolved

    def test_start_new_session_is_true(self, rig):
        _launch(rig)
        assert rig["spawn"].kwargs["start_new_session"] is True

    def test_command_is_argv_not_a_shell_string(self, rig):
        _launch(rig)
        assert isinstance(rig["spawn"].argv, list)
        assert all(isinstance(word, str) for word in rig["spawn"].argv)
        assert rig["spawn"].kwargs.get("shell", False) is False

    def test_shell_active_workspace_path_stays_one_word(self, rig, tmp_path):
        hostile = tmp_path / "a b; rm -rf $HOME && echo 'x'"
        hostile.mkdir()
        rig["workspace"] = hostile

        _launch(rig)

        argv = rig["spawn"].argv
        assert argv[argv.index("-c") + 1] == str(hostile.resolve())
        assert argv.count(str(hostile.resolve())) == 1


class TestSessionIdentity:
    def test_tmux_name_is_camp_slug_and_first_eight_of_the_uuid(self, rig):
        result = _launch(rig, slug="feat-x")
        argv = rig["spawn"].argv

        assert result.tmux_name == f"camp-feat-x-{result.session_id[:8]}"
        assert argv[argv.index("-s") + 1] == result.tmux_name

    def test_the_uuid_handed_to_the_seam_is_the_one_reported_back(self, rig):
        result = _launch(rig)

        workspace, session_id, _ = rig["harness"].launch_calls[0]
        assert session_id == result.session_id
        assert workspace == rig["workspace"].resolve()

    def test_the_seam_is_asked_to_name_the_session_after_the_tmux_handle(self, rig):
        """One handle everywhere: the name requested from the harness is the
        same string the tmux session is created under."""
        result = _launch(rig, slug="feat-x")

        _, _, session_name = rig["harness"].launch_calls[0]
        assert session_name == result.tmux_name

    def test_the_name_the_seam_is_given_is_the_folded_one(self, rig):
        """The handle is folded BEFORE the harness is told it, not after.

        Folding and naming-the-harness are two rules over the same string, and
        the only place they can disagree is the order they run in. A name folded
        after being handed over would have the harness's clients display one
        session name while tmux answers to another.
        """
        result = _launch(rig, slug="my.proj")

        _, _, session_name = rig["harness"].launch_calls[0]
        assert session_name == result.tmux_name
        assert result.tmux_name.startswith("camp-my-proj-")
        assert "." not in session_name

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
        assert rig["spawn"].calls == []

    def test_pretrust_raising_refuses_naming_the_workspace_no_process_spawned(
        self, rig, monkeypatch
    ):
        """pretrust_workspace's atomic-write path can raise (e.g. a disk error
        mid os.replace) rather than returning False. That must still surface as
        a LaunchError refusal with no process spawned — never a raw traceback."""

        def boom(launch_dir, workspace_root, env=None):
            raise OSError("disk full")

        monkeypatch.setattr(rig["module"], "pretrust_workspace", boom)

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig)

        assert str(rig["workspace"].resolve()) in str(excinfo.value)
        assert rig["spawn"].calls == []

    def test_unlaunchable_harness_refuses_naming_the_harness(self, rig):
        rig["harness"] = FakeHarness(launch_argv=None)

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig)

        assert "fakeharness" in str(excinfo.value)
        assert rig["spawn"].calls == []

    def test_unresolvable_harness_refuses(self, rig):
        rig["harness"] = None

        with pytest.raises(rig["module"].LaunchError):
            _launch(rig)

        assert rig["spawn"].calls == []

    def test_missing_tmux_refuses_naming_the_binary(self, rig):
        rig["which"] = None

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig)

        assert "tmux" in str(excinfo.value)
        assert rig["spawn"].calls == []

    def test_unresolvable_launch_dir_refuses(self, rig, tmp_path):
        rig["workspace"] = tmp_path / "never-created"

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig)

        assert "never-created" in str(excinfo.value)
        assert rig["spawn"].calls == []

    def test_trust_refusal_precedes_any_tmux_spawn(self, rig):
        """The trust gate must fire before the process exists, not after."""
        rig["pretrust"] = False
        with pytest.raises(rig["module"].LaunchError):
            _launch(rig)
        assert rig["spawn"].calls == []

    def test_none_scrub_refuses_naming_the_harness_no_process_spawned(self, rig, monkeypatch):
        """`None` from session_launch_env_unset means launch is unsupported for
        this harness — never "nothing to scrub". Collapsing it to `[]` would
        spawn an unscrubbed pane; the contract instead demands a refusal."""
        monkeypatch.setattr(rig["harness"], "session_launch_env_unset", lambda: None)

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig)

        assert "fakeharness" in str(excinfo.value)
        assert rig["spawn"].calls == []

    def test_empty_list_scrub_is_genuinely_nothing_to_scrub_and_proceeds(self, rig, monkeypatch):
        """`[]` is the honest "nothing to scrub" answer — distinct from `None` —
        and must proceed with the launch, with no `-u` operands in the pane."""
        monkeypatch.setattr(rig["harness"], "session_launch_env_unset", lambda: [])

        _launch(rig)

        pane = _pane_command(rig["spawn"].argv)
        assert pane[0] == "env"
        assert pane[1] == "fakeharness"


# ---------------------------------------------------------------------------
# warnings that do NOT refuse
# ---------------------------------------------------------------------------


class TestParseSessionListNoneIsTolerated:
    """Regression pin: every caller of enumerate_records treats a None answer
    (no enumeration concept / a harness whose parse_session_list itself answers
    None) as "not yet found" / "nothing to report", never as an iterable it
    crashes on. Removing the `records or ()` / falsy guard at any caller must
    fail one of these."""

    def test_already_live_probe_stays_silent_when_parse_session_list_returns_none(
        self, rig, capsys, monkeypatch
    ):
        rig["harness"] = FakeHarness(enumerate_argv=["fakeharness", "agents"])
        monkeypatch.setattr(rig["harness"], "parse_session_list", lambda output: None)
        rig["enumerate"] = lambda *a, **k: _completed()

        result = _launch(rig)

        assert len(rig["spawn"].calls) == 1
        assert result.session_id
        assert capsys.readouterr().err == ""

    def test_confirm_poll_treats_a_none_answer_as_not_yet_found(self, confirm_rig, monkeypatch):
        session = confirm_rig["module"]
        harness = SequencedHarness([[]])
        monkeypatch.setattr(harness, "parse_session_list", lambda output: None)

        with pytest.raises(session.LaunchError):
            session.confirm_session(
                harness,
                confirm_rig["launched"],
                interval=0.5,
                timeout=1.0,
                sleep=lambda s: None,
                clock=FakeClock(0.5),
            )

    def test_confirm_poll_none_then_a_real_match_still_confirms(self, confirm_rig, monkeypatch):
        session = confirm_rig["module"]
        harness = SequencedHarness([[]])
        answers = iter([None, [_record("target-id")]])
        monkeypatch.setattr(harness, "parse_session_list", lambda output: next(answers))

        session.confirm_session(
            harness,
            confirm_rig["launched"],
            interval=0.5,
            timeout=10.0,
            sleep=lambda s: None,
            clock=FakeClock(0.5),
        )

        assert next(answers, "exhausted") == "exhausted"


class TestNonRefusingWarnings:
    def test_pretrust_disabled_by_config_spawns_with_a_stderr_warning(self, rig, capsys):
        result = _launch(rig, group=GROUP_NO_PRETRUST)

        assert len(rig["spawn"].calls) == 1
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
        self, rig, capsys
    ):
        rig["harness"] = FakeHarness(
            enumerate_argv=["fakeharness", "agents"],
            records=[object(), object()],
        )
        rig["enumerate"] = lambda *a, **k: _completed(stdout="[]")

        result = _launch(rig)

        assert len(rig["spawn"].calls) == 1
        assert result.session_id
        err = capsys.readouterr().err
        assert "camp:" in err
        assert "2" in err

    def test_enumeration_failure_does_not_refuse(self, rig):
        def boom(*a, **k):
            raise OSError("no such binary")

        rig["harness"] = FakeHarness(enumerate_argv=["fakeharness", "agents"])
        rig["enumerate"] = boom

        result = _launch(rig)

        assert len(rig["spawn"].calls) == 1
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

        pane = _pane_command(rig["spawn"].argv)
        assert pane[-4:] == ["odd-binary", "-x", "--weird=1", "tail"]

    def test_scrub_var_names_are_taken_from_the_seam(self, rig, monkeypatch):
        """The scrubbed names come from the seam, not from a list in camp core."""
        monkeypatch.setattr(
            rig["harness"], "session_launch_env_unset", lambda: ["ONLY_THIS_ONE"]
        )

        _launch(rig)

        pane = _pane_command(rig["spawn"].argv)
        assert pane[:3] == ["env", "-u", "ONLY_THIS_ONE"]


# ---------------------------------------------------------------------------
# rooting at an explicitly named directory
# ---------------------------------------------------------------------------


class TestDirectoryRootedLaunch:
    def test_the_named_root_is_the_cwd_the_c_operand_and_the_trust_scope(
        self, rig, tmp_path
    ):
        """One directory again, but supplied rather than derived — and the trust
        scope is that same directory, which is exactly what makes the pre-seed's
        containment check vacuous here. The name component is a parameter, not a
        basename: it is deliberately unlike the directory's own name."""
        root = tmp_path / "proj"
        root.mkdir()

        result = _launch_at(rig, tmp_path, root)

        argv = rig["spawn"].argv
        assert argv[argv.index("-c") + 1] == str(root)
        assert rig["spawn"].kwargs["cwd"] == str(root)
        assert result.launch_dir == root
        assert result.tmux_name == f"camp-odd-handle-{result.session_id[:8]}"
        assert argv[argv.index("-s") + 1] == result.tmux_name
        assert rig["pretrust_calls"] == [
            {"launch_dir": root, "workspace_root": root}
        ]

    def test_an_ineligible_root_refuses_before_the_trust_preseed_runs(
        self, rig, tmp_path
    ):
        """The eligibility gate is the containment boundary for a named
        directory, and the trust pre-seed's own confinement check cannot be it —
        that check compares the launch directory against the trust scope, which
        for this flavor is the same directory. So the gate must have already
        answered before the pre-seed is reached, and asserting the pre-seed was
        never CALLED is what pins that order."""
        root = tmp_path / "proj"
        root.mkdir()
        group = _dir_group(tmp_path, roots=[str(tmp_path / "elsewhere")])

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch_at(rig, tmp_path, root, group=group)

        assert str(root) in str(excinfo.value)
        assert rig["pretrust_calls"] == []
        assert rig["spawn"].calls == []

    def test_a_camp_managed_claim_cannot_launder_an_ineligible_root(self, rig, tmp_path):
        """`camp_managed_root` opens the gate only where the name rule agrees.

        The claim says the directory came out of camp's own workspace layout
        rather than out of an operator's argument, and that is the whole
        difference between a fence and no fence. So it is VERIFIED here: a caller
        that claims it for a directory the name rule does not recognize as a
        workspace is gated exactly as if it had claimed nothing, and one wrong
        argument at one call site can never be the whole containment boundary.
        """
        root = tmp_path / "proj"
        root.mkdir()
        group = _dir_group(tmp_path, roots=[str(tmp_path / "elsewhere")])

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            rig["module"].launch_session(
                group,
                root=root,
                name_component="odd-handle",
                trust_scope=root,
                env=_dir_env(tmp_path),
                camp_managed_root=True,
            )

        assert str(root) in str(excinfo.value)
        assert rig["pretrust_calls"] == []
        assert rig["spawn"].calls == []

    def test_a_root_that_is_not_an_existing_directory_refuses(self, rig, tmp_path):
        """An eligible-but-absent directory must not reach tmux: `new-session -c`
        falls back to the invoking environment's home rather than failing, which
        would root the session somewhere nobody named and nothing fenced."""
        root = tmp_path / "never-created"

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch_at(rig, tmp_path, root)

        assert "never-created" in str(excinfo.value)
        assert rig["pretrust_calls"] == []
        assert rig["spawn"].calls == []

    def test_the_scrub_rides_in_the_pane_and_leaves_the_spawn_env(self, rig, tmp_path):
        """Both halves of the scrub apply to this flavor too — neither is
        inherited from the slug path by accident."""
        root = tmp_path / "proj"
        root.mkdir()
        env = _dir_env(tmp_path) | {var: "leaked" for var in SCRUB}

        rig["module"].launch_session(
            _dir_group(tmp_path),
            root=root,
            name_component="odd-handle",
            trust_scope=root,
            env=env,
        )

        pane = _pane_command(rig["spawn"].argv)
        assert pane[: 1 + 2 * len(SCRUB)] == ["env"] + [
            tok for var in SCRUB for tok in ("-u", var)
        ]
        for var in SCRUB:
            assert var not in rig["spawn"].kwargs["env"]


# ---------------------------------------------------------------------------
# resuming a session the harness already knows
# ---------------------------------------------------------------------------


class TestResumeFlavor:
    def test_the_resume_argv_comes_from_the_seam_and_the_id_is_not_minted(
        self, rig, tmp_path
    ):
        root = tmp_path / "proj"
        root.mkdir()

        result = _launch_at(rig, tmp_path, root, resume=RESUME_ID)

        assert _pane_command(rig["spawn"].argv) == (
            ["env"]
            + [tok for var in SCRUB for tok in ("-u", var)]
            + ["fakeharness", "--reattach", RESUME_ID]
        )
        assert rig["harness"].resume_calls == [RESUME_ID]
        assert rig["harness"].launch_calls == []
        assert result.session_id == RESUME_ID
        assert result.tmux_name == f"camp-odd-handle-{RESUME_ID[:8]}"

    def test_a_workspace_resume_keeps_the_slug_as_the_name_component(self, rig):
        """A resume rooted at a workspace is the slug flavor carrying a session
        id — so it reclaims the very name its original launch used."""
        result = rig["module"].launch_session(
            GROUP, "feat-x", resume_session_id=RESUME_ID, env={"PATH": "/usr/bin"}
        )

        assert result.session_id == RESUME_ID
        assert result.tmux_name == f"camp-feat-x-{RESUME_ID[:8]}"
        assert rig["spawn"].kwargs["cwd"] == str(rig["workspace"].resolve())

    def test_a_harness_that_cannot_resume_refuses_naming_it(self, rig, tmp_path):
        """An absent answer from the seam at a spawn site is a refusal, never a
        fall-through to a fresh launch."""
        root = tmp_path / "proj"
        root.mkdir()
        rig["harness"] = FakeHarness(resume_argv=None)

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch_at(rig, tmp_path, root, resume=RESUME_ID)

        assert "fakeharness" in str(excinfo.value)
        assert rig["spawn"].calls == []

    def test_a_session_already_live_under_the_root_refuses_as_already_running(
        self, rig, tmp_path
    ):
        root = tmp_path / "proj"
        root.mkdir()
        rig["harness"] = FakeHarness(
            enumerate_argv=["fakeharness", "agents"], records=[_record(RESUME_ID)]
        )
        rig["enumerate"] = lambda *a, **k: _completed()

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch_at(rig, tmp_path, root, resume=RESUME_ID)

        assert "already running" in str(excinfo.value)
        assert RESUME_ID in str(excinfo.value)
        assert rig["spawn"].calls == []

    def test_the_two_already_running_refusals_are_one_message(self, rig, tmp_path):
        """Enumeration answers before the spawn; tmux's own session-name claim
        answers atomically at the spawn. They catch the same condition at two
        moments, so the operator must never be able to tell which one fired."""
        root = tmp_path / "proj"
        root.mkdir()
        rig["harness"] = FakeHarness(
            enumerate_argv=["fakeharness", "agents"], records=[_record(RESUME_ID)]
        )
        rig["enumerate"] = lambda *a, **k: _completed()

        with pytest.raises(rig["module"].LaunchError) as enumerated:
            _launch_at(rig, tmp_path, root, resume=RESUME_ID)

        rig["harness"] = FakeHarness(
            enumerate_argv=["fakeharness", "agents"], records=[]
        )
        rig["spawn"].returncode = 1
        rig["spawn"].stderr = f"duplicate session: camp-odd-handle-{RESUME_ID[:8]}\n"

        with pytest.raises(rig["module"].LaunchError) as claimed:
            _launch_at(rig, tmp_path, root, resume=RESUME_ID)

        assert str(claimed.value) == str(enumerated.value)

    def test_an_unrelated_tmux_failure_is_a_distinct_refusal(self, rig, tmp_path):
        """A misdiagnosed failure is worse than a generic one: only tmux's
        duplicate-name error means the session is already running."""
        root = tmp_path / "proj"
        root.mkdir()
        rig["harness"] = FakeHarness(
            enumerate_argv=["fakeharness", "agents"], records=[]
        )
        rig["enumerate"] = lambda *a, **k: _completed()
        rig["spawn"].returncode = 1
        rig["spawn"].stderr = "width invalid: 0"

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch_at(rig, tmp_path, root, resume=RESUME_ID)

        assert "already running" not in str(excinfo.value)
        assert "width invalid" in str(excinfo.value)

    def test_an_unanswerable_enumeration_falls_through_to_the_atomic_claim(
        self, rig, tmp_path
    ):
        """The pre-spawn lookup is a courtesy that produces a legible refusal; it
        is not the guarantee. When it cannot answer, the launch proceeds and
        tmux's session-name claim is what actually prevents a doubled session."""
        root = tmp_path / "proj"
        root.mkdir()
        rig["harness"] = FakeHarness(enumerate_argv=["fakeharness", "agents"])

        def boom(*a, **k):
            raise OSError("no such binary")

        rig["enumerate"] = boom

        result = _launch_at(rig, tmp_path, root, resume=RESUME_ID)

        assert result.session_id == RESUME_ID
        assert len(rig["spawn"].calls) == 1


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

    def test_missing_harness_binary_mid_poll_is_tolerated_and_polling_continues(
        self, confirm_rig, monkeypatch
    ):
        """A harness binary absent from PATH raises FileNotFoundError from Popen —
        camp only which-checks tmux, not the harness — and that must be treated
        as a failed poll, same as HarnessError, not escape as a raw traceback."""
        session = confirm_rig["module"]
        calls = {"n": 0}

        def fake_run(argv, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise FileNotFoundError("no such file or directory: 'fakeharness'")
            return type("R", (), {"returncode": 0, "stdout": "target-id", "stderr": ""})()

        monkeypatch.setattr(session.subprocess, "run", fake_run)
        harness = SequencedHarness([["target-id"]])
        monkeypatch.setattr(harness, "parse_session_list", lambda output: [_record("target-id")])

        session.confirm_session(
            harness,
            confirm_rig["launched"],
            interval=0.5,
            timeout=10.0,
            sleep=lambda s: None,
            clock=FakeClock(0.5),
        )

        assert calls["n"] == 2

    def test_permission_error_mid_poll_is_tolerated_and_polling_continues(
        self, confirm_rig, monkeypatch
    ):
        """A PermissionError (e.g. a launch dir yanked out from under a running
        enumeration) is a failed POLL, not a failed launch — every other
        enumerate_records caller in this module tolerates OSError broadly."""
        session = confirm_rig["module"]
        calls = {"n": 0}

        def fake_run(argv, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError("permission denied")
            return type("R", (), {"returncode": 0, "stdout": "target-id", "stderr": ""})()

        monkeypatch.setattr(session.subprocess, "run", fake_run)
        harness = SequencedHarness([["target-id"]])
        monkeypatch.setattr(harness, "parse_session_list", lambda output: [_record("target-id")])

        session.confirm_session(
            harness,
            confirm_rig["launched"],
            interval=0.5,
            timeout=10.0,
            sleep=lambda s: None,
            clock=FakeClock(0.5),
        )

        assert calls["n"] == 2

    def test_timeout_expired_mid_poll_is_tolerated_and_polling_continues(
        self, confirm_rig, monkeypatch
    ):
        """A hung enumeration subprocess raises subprocess.TimeoutExpired; that is
        a failed poll, not a failed launch — the caller keeps polling to timeout."""
        import subprocess as subprocess_mod

        session = confirm_rig["module"]
        calls = {"n": 0}

        def fake_run(argv, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise subprocess_mod.TimeoutExpired(cmd=argv, timeout=10)
            return type("R", (), {"returncode": 0, "stdout": "target-id", "stderr": ""})()

        monkeypatch.setattr(session.subprocess, "run", fake_run)
        harness = SequencedHarness([["target-id"]])
        monkeypatch.setattr(harness, "parse_session_list", lambda output: [_record("target-id")])

        session.confirm_session(
            harness,
            confirm_rig["launched"],
            interval=0.5,
            timeout=10.0,
            sleep=lambda s: None,
            clock=FakeClock(0.5),
        )

        assert calls["n"] == 2

    def test_missing_harness_binary_through_timeout_still_kills_tmux(
        self, confirm_rig, monkeypatch
    ):
        """A FileNotFoundError that persists to timeout is a normal timed-out
        confirmation — tmux gets killed and the refusal is reported, no
        traceback."""
        session = confirm_rig["module"]

        def fake_run(argv, **kwargs):
            if argv[0] == "tmux":
                return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            raise FileNotFoundError("no such file or directory: 'fakeharness'")

        monkeypatch.setattr(session.subprocess, "run", fake_run)
        harness = SequencedHarness([[]])

        with pytest.raises(session.LaunchError):
            session.confirm_session(
                harness,
                confirm_rig["launched"],
                interval=0.5,
                timeout=1.0,
                sleep=lambda s: None,
                clock=FakeClock(0.5),
            )

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

    def test_a_resumed_session_that_never_confirms_is_killed_and_refused(
        self, confirm_rig
    ):
        """Confirmation is flavor-blind: a resumed session that never registers
        is killed by the exact name camp claimed for it and refused, the same as
        a fresh launch. Empirically this is a common outcome, not an edge."""
        session = confirm_rig["module"]
        tmux_name = f"camp-odd-handle-{RESUME_ID[:8]}"
        launched = session.LaunchedSession(
            session_id=RESUME_ID,
            tmux_name=tmux_name,
            launch_dir=confirm_rig["launched"].launch_dir,
        )
        harness = SequencedHarness([[]])

        with pytest.raises(session.LaunchError) as excinfo:
            session.confirm_session(
                harness,
                launched,
                interval=0.5,
                timeout=1.0,
                sleep=lambda s: None,
                clock=FakeClock(0.5),
            )

        kill_calls = [c for c in confirm_rig["calls"] if c[0] == "tmux"]
        assert kill_calls == [["tmux", "kill-session", "-t", tmux_name]]
        assert RESUME_ID in str(excinfo.value)

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

    def test_kill_session_call_carries_a_timeout(self, confirm_rig, monkeypatch):
        """A wedged tmux must not hang the refusal — the kill call itself needs
        a bound, same posture as every other subprocess call in this module."""
        session = confirm_rig["module"]
        harness = SequencedHarness([[]])
        captured_kwargs = {}

        def fake_run(argv, **kwargs):
            if argv[0] == "tmux":
                captured_kwargs.update(kwargs)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(session.subprocess, "run", fake_run)

        with pytest.raises(session.LaunchError):
            session.confirm_session(
                harness,
                confirm_rig["launched"],
                interval=0.5,
                timeout=1.0,
                sleep=lambda s: None,
                clock=FakeClock(0.5),
            )

        assert captured_kwargs.get("timeout") is not None

    def test_kill_session_erroring_is_reported_not_raised(
        self, confirm_rig, monkeypatch, capsys
    ):
        """A vanished/wedged tmux raising from the kill call (e.g.
        TimeoutExpired or an OSError) must not escape as a traceback — the
        failure was already reported, so a failing kill degrades the same way."""
        import subprocess as subprocess_mod

        session = confirm_rig["module"]
        harness = SequencedHarness([[]])

        def fake_run(argv, **kwargs):
            if argv[0] == "tmux":
                raise subprocess_mod.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(session.subprocess, "run", fake_run)

        with pytest.raises(session.LaunchError):
            session.confirm_session(
                harness,
                confirm_rig["launched"],
                interval=0.5,
                timeout=1.0,
                sleep=lambda s: None,
                clock=FakeClock(0.5),
            )

        err = capsys.readouterr().err
        assert "camp-feat-x-abcd1234" in err

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


class TestCampManagedClaimBoundary:
    """What the camp-managed waiver does and does not buy.

    The waiver exists because a directory camp computed from its own layout has
    already answered the question the allowlist asks. These tests pin the two
    halves of that: the waiver must not be obtainable for a directory camp did
    not create, and it must not extend to the one rule that never depended on
    who chose the directory.
    """

    def test_a_symlinked_worktrees_container_confers_nothing(self, rig, tmp_path):
        """A link standing in for the container cannot redefine camp-managed.

        The container is what every camp-managed answer is measured against, so
        a symlink in its place pointed at a root would make every directory on
        the machine answer as a workspace — and the waiver is granted for
        exactly those. camp created the directory or it did not.
        """
        state = tmp_path / "state"
        (state / "testgroup").mkdir(parents=True)
        (state / "testgroup" / "worktrees").symlink_to(tmp_path)
        target = tmp_path / "not-a-workspace"
        target.mkdir()
        env = _dir_env(tmp_path) | {"CAMP_STATE_DIR": str(state)}

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            rig["module"].launch_session(
                _dir_group(tmp_path, roots=[str(tmp_path / "elsewhere")]),
                root=target,
                name_component="odd-handle",
                trust_scope=target,
                env=env,
                camp_managed_root=True,
            )

        assert "[launch] roots" in str(excinfo.value)
        assert rig["spawn"].calls == []

    def test_the_credential_rule_still_answers_inside_a_real_workspace(
        self, rig, tmp_path
    ):
        """The waiver covers the allowlist, never the credential rule.

        The allowlist asks who chose this directory and the waiver answers it.
        The credential rule asks what is IN the directory, and that answer does
        not change with the asker — so a workspace that happens to sit on a
        credential store is refused exactly as a named one would be.
        """
        home = tmp_path / "home"
        state = home / ".claude" / "state"
        workspace = state / "testgroup" / "worktrees" / "feat-x"
        workspace.mkdir(parents=True)
        env = _dir_env(tmp_path) | {"CAMP_STATE_DIR": str(state)}

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            rig["module"].launch_session(
                _dir_group(tmp_path),
                root=workspace,
                name_component="feat-x",
                trust_scope=workspace,
                env=env,
                camp_managed_root=True,
            )

        assert "credential store" in str(excinfo.value)
        assert rig["spawn"].calls == []

    def test_a_dotted_session_id_cannot_produce_an_unaddressable_tmux_name(
        self, rig, tmp_path
    ):
        """Both halves of the tmux name are folded, not just the component.

        An id reaches camp as a transcript FILENAME, so it carries whatever the
        filesystem allowed. tmux reads a dot as a window/pane separator: it
        creates such a session happily and then cannot address it, so the
        confirm-timeout kill silently fails and leaves running the very process
        camp just told the operator it had cleaned up.
        """
        root = tmp_path / "proj"
        root.mkdir()

        launched = _launch_at(rig, tmp_path, root, resume="aa.bb.cc.dd")

        assert launched.tmux_name == "camp-odd-handle-aa-bb-cc"
        assert "." not in launched.tmux_name


# ---------------------------------------------------------------------------
# the config dir reaches the pane
# ---------------------------------------------------------------------------


class TestConfigDirReachesThePane:
    """`CLAUDE_CONFIG_DIR` is carried into the pane as an `env` assignment.

    `tmux new-session` against an already-running server is a client request, so
    the pane inherits the SERVER's environment rather than camp's. A server
    started under a different Claude account therefore places every session on
    that account, whatever camp's own environment says. The assignment rides the
    same pane-level `env` invocation the scrub already uses, which is the one
    mechanism that holds for a fresh server and a pre-existing one alike.
    """

    def test_config_dir_is_an_env_assignment_in_the_pane_command(self, rig):
        _launch(rig, env={"PATH": "/usr/bin", "CLAUDE_CONFIG_DIR": "/somewhere/.claude-levr"})

        pane = _pane_command(rig["spawn"].argv)
        harness_start = pane.index("fakeharness")
        assert "CLAUDE_CONFIG_DIR=/somewhere/.claude-levr" in pane[1:harness_start]

    def test_assignment_follows_the_scrub_operands_and_precedes_the_harness(self, rig):
        _launch(rig, env={"PATH": "/usr/bin", "CLAUDE_CONFIG_DIR": "/somewhere/.claude-levr"})

        pane = _pane_command(rig["spawn"].argv)
        assert pane[0] == "env"
        harness_start = pane.index("fakeharness")
        operands = pane[1:harness_start]
        assert operands == [tok for var in SCRUB for tok in ("-u", var)] + [
            "CLAUDE_CONFIG_DIR=/somewhere/.claude-levr"
        ]

    def test_no_assignment_when_the_variable_is_unset(self, rig):
        _launch(rig, env={"PATH": "/usr/bin"})

        pane = _pane_command(rig["spawn"].argv)
        harness_start = pane.index("fakeharness")
        assert pane[1:harness_start] == [tok for var in SCRUB for tok in ("-u", var)]

    def test_a_relative_config_dir_is_not_carried_into_the_pane(self, rig):
        """A relative value resolves against the pane's cwd — the launch dir —
        rather than the operator's, which would silently point the session at a
        config dir inside the workspace. The trust pre-seed refuses a relative
        value for the same reason; the pane must not disagree with it."""
        _launch(rig, env={"PATH": "/usr/bin", "CLAUDE_CONFIG_DIR": "relative/.claude"})

        pane = _pane_command(rig["spawn"].argv)
        harness_start = pane.index("fakeharness")
        assert pane[1:harness_start] == [tok for var in SCRUB for tok in ("-u", var)]
