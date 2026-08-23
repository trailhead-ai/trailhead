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

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


SCRUB = ["FAKE_CHILD_SESSION", "FAKECODE", "FAKE_SESSION_TOKEN"]

#: The variable the stand-in harness expresses an account binding with. Spelled
#: as Claude Code's real one in the tests that assert on the trust seed, because
#: the seed's resolver (trailhead's `claude_config_file`) reads exactly this name
#: — the coupling is between the harness and its own resolver, not camp's.
ACCOUNT_KEY = "CLAUDE_CONFIG_DIR"

#: What the stand-in harness resolves as its default when the env carries no HOME.
FAKE_DEFAULT_HOME = "/fake-default-home"

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
        self,
        *,
        launch_argv=...,
        resume_argv=...,
        enumerate_argv=None,
        records=None,
        env_set_keys=(ACCOUNT_KEY,),
        default_is_absence=False,
        scrub=None,
    ):
        self._launch_argv = launch_argv
        self._resume_argv = resume_argv
        self._enumerate_argv = enumerate_argv
        self._records = records or []
        self.launch_calls: list[tuple[Path, str, str | None]] = []
        self.resume_calls: list[str] = []
        self.env_set_calls: list[tuple[str | None, dict[str, str]]] = []
        self._env_set_keys = env_set_keys
        self._default_is_absence = default_is_absence
        self._scrub = tuple(SCRUB) if scrub is None else tuple(scrub)

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
        return list(self._scrub)

    def session_launch_env_set(self, account, *, env=None):
        """Model the real seam: resolve `account` (or the default) to a dict.

        The key is a knob so a test can prove camp iterates whatever the seam
        returns rather than reaching for a Claude-specific variable name.
        """
        self.env_set_calls.append((account, dict(env or {})))
        if self._env_set_keys is None:
            return None
        if account is None and self._default_is_absence:
            return {}
        base = FAKE_DEFAULT_HOME if account is None else account
        if account is None:
            base = (env or {}).get("HOME") or FAKE_DEFAULT_HOME
        elif account.startswith("~/"):
            base = str(Path((env or {}).get("HOME", FAKE_DEFAULT_HOME)) / account[2:])
        return {key: str(base) for key in self._env_set_keys}

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
            {
                "launch_dir": launch_dir,
                "workspace_root": workspace_root,
                "env": dict(env or {}),
            }
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
            f"{ACCOUNT_KEY}={FAKE_DEFAULT_HOME}",
            "fakeharness",
            "--rc",
            "--sid",
            sid,
        ]
        assert rig["spawn"].kwargs["cwd"] == workspace
        # camp's own spawn environment states the binding NOWHERE: a tmux server
        # started by this spawn would make whatever it carries that server's
        # global for every later pane, which is the stale-global accident this
        # whole path removes. The pane operand is the only place the binding is
        # stated, and it holds for a fresh server and a pre-existing one alike.
        assert rig["spawn"].kwargs["env"] == {"PATH": "/usr/bin", "KEEP": "yes"}
        assert rig["spawn"].kwargs["start_new_session"] is True
        assert result.tmux_name == f"camp-feat-x-{sid[:8]}"
        assert result.launch_dir == rig["workspace"].resolve()
        assert capsys.readouterr().err == (
            "camp: binding session to the harness default (no account declared) "
            f"— {ACCOUNT_KEY}={FAKE_DEFAULT_HOME}\n"
        )


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
        # The account assignments follow the scrub; TestOneResolutionFeedsBoth
        # pins those.
        assert operands[: 2 * len(SCRUB)] == [tok for var in SCRUB for tok in ("-u", var)]

    def test_env_sits_after_tmux_options_immediately_before_the_harness_argv(self, rig):
        _launch(rig)
        argv = rig["spawn"].argv

        env_at = argv.index("env")
        assert argv[0] == "tmux"
        # `env` is not tmux's own argv[1] — it does not apply to tmux itself.
        assert env_at > 1
        # Every tmux option precedes it, and the harness argv follows the operands.
        assert "-c" in argv[:env_at] and "-s" in argv[:env_at]
        assert argv[env_at + 2 + 2 * len(SCRUB)] == "fakeharness"

    def test_pane_command_is_exactly_env_scrub_then_the_seam_argv(self, rig):
        _launch(rig)
        session_id = rig["harness"].launch_calls[0][1]

        expected = ["env"]
        for var in SCRUB:
            expected += ["-u", var]
        expected += [f"{ACCOUNT_KEY}={FAKE_DEFAULT_HOME}"]
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
        assert pane[1] == f"{ACCOUNT_KEY}={FAKE_DEFAULT_HOME}"
        assert pane[2] == "fakeharness"


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
        # The account report is the only line camp writes here, pinned exactly:
        # asserting merely that the probe's own wording is absent lets any other
        # stray line through, which is the whole thing this test is watching for.
        assert capsys.readouterr().err == (
            "camp: binding session to the harness default (no account declared) "
            f"— {ACCOUNT_KEY}={FAKE_DEFAULT_HOME}\n"
        )

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
        call = rig["pretrust_calls"][0]
        assert len(rig["pretrust_calls"]) == 1
        assert (call["launch_dir"], call["workspace_root"]) == (root, root)

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
            + [f"{ACCOUNT_KEY}={tmp_path / 'home'}"]
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

        kill_calls = [c for c in confirm_rig["calls"] if c[:2] == ["tmux", "kill-session"]]
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

        kill_calls = [c for c in confirm_rig["calls"] if c[:2] == ["tmux", "kill-session"]]
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
        assert len([c for c in calls if c[:2] == ["tmux", "kill-session"]]) == 1

    def test_kill_session_call_carries_a_timeout(self, confirm_rig, monkeypatch):
        """A wedged tmux must not hang the refusal — the kill call itself needs
        a bound, same posture as every other subprocess call in this module."""
        session = confirm_rig["module"]
        harness = SequencedHarness([[]])
        captured_kwargs = {}

        def fake_run(argv, **kwargs):
            if argv[:2] == ["tmux", "kill-session"]:
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
            if argv[:2] == ["tmux", "kill-session"]:
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



def _confirm_run_fake(
    calls,
    *,
    pane_stdout="",
    pane_returncode=0,
    pane_raises=None,
    pane_sleeps=False,
    pane_fails_after_kill=False,
    kill_returncode=0,
    kill_raises=None,
):
    """A `subprocess.run` stand-in that scripts the two tmux calls confirm makes.

    Everything that is not tmux is the harness enumeration, which the scripted
    harness answers from its own list rather than from this output.
    """
    import time as time_mod

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["tmux", "capture-pane"]:
            if pane_raises is not None:
                raise pane_raises
            if pane_sleeps:
                time_mod.sleep(kwargs["timeout"])
                raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])
            if pane_fails_after_kill and ["tmux", "kill-session"] in [
                c[:2] for c in calls
            ]:
                return _completed(returncode=1, stderr="can't find pane")
            return _completed(returncode=pane_returncode, stdout=pane_stdout)
        if argv[:2] == ["tmux", "kill-session"]:
            if kill_raises is not None:
                raise kill_raises
            return _completed(returncode=kill_returncode, stderr="no such session")
        return _completed()

    return fake_run


class TestConfirmFailureReport:
    """What the timed-out confirmation tells the operator.

    The message separates what camp VERIFIED from what it INFERS, and the
    verified half comes first — a reader triaging from a phone stops at the
    first confident-sounding clause, so that clause has to be a checked fact.
    """

    def _timeout(self, session, launched, *, env=None):
        harness = SequencedHarness([[]])
        with pytest.raises(session.LaunchError) as excinfo:
            session.confirm_session(
                harness,
                launched,
                env=env,
                interval=0.5,
                timeout=1.0,
                sleep=lambda s: None,
                clock=FakeClock(0.5),
            )
        return str(excinfo.value)

    def test_the_pane_is_captured_before_the_session_is_killed(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        calls: list[list[str]] = []
        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake(calls))

        self._timeout(session, confirm_rig["launched"])

        tmux = [c[:2] for c in calls if c[0] == "tmux"]
        assert ["tmux", "capture-pane"] in tmux
        assert ["tmux", "kill-session"] in tmux
        assert tmux.index(["tmux", "capture-pane"]) < tmux.index(["tmux", "kill-session"])

    def test_the_capture_targets_the_exact_tmux_name(self, confirm_rig, monkeypatch):
        session = confirm_rig["module"]
        calls: list[list[str]] = []
        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake(calls))

        self._timeout(session, confirm_rig["launched"])

        capture = [c for c in calls if c[:2] == ["tmux", "capture-pane"]]
        assert capture == [["tmux", "capture-pane", "-p", "-t", "camp-feat-x-abcd1234"]]

    def test_a_pane_readable_only_before_the_kill_still_reaches_the_message(
        self, confirm_rig, monkeypatch
    ):
        """Pins the ordering by consequence, not by call index: this fake answers
        `can't find pane` once the kill has run, exactly as tmux does, so a
        capture moved after the kill loses the excerpt entirely."""
        session = confirm_rig["module"]
        calls: list[list[str]] = []
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake(
                calls, pane_stdout="Do you trust the files in this folder?\n", pane_fails_after_kill=True
            ),
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert "Do you trust the files in this folder?" in message

    def test_the_verified_facts_precede_the_inferred_cause(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake([]))

        message = self._timeout(session, confirm_rig["launched"])

        assert "verified:" in message
        assert "inferred:" in message
        assert message.index("verified:") < message.index("inferred:")

    def test_the_inferred_cause_still_names_the_trust_prompt_and_is_labelled(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake([]))

        message = self._timeout(session, confirm_rig["launched"])

        inferred = message[message.index("inferred:") :]
        assert "trust prompt" in inferred

    def test_the_verified_block_names_the_enumeration_camp_actually_ran(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake([]))

        message = self._timeout(session, confirm_rig["launched"])

        verified = message[message.index("verified:") : message.index("inferred:")]
        assert "target-id" in verified
        assert "poll" in verified
        assert str(confirm_rig["launched"].launch_dir) in verified

    def test_the_message_names_the_seeded_config_file_and_reports_trust_present(
        self, confirm_rig, monkeypatch, tmp_path
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake([]))
        home = tmp_path / "acct-home"
        home.mkdir()
        launch_dir = confirm_rig["launched"].launch_dir
        (home / ".claude.json").write_text(
            json.dumps(
                {"projects": {str(launch_dir): {"hasTrustDialogAccepted": True}}}
            )
        )

        message = self._timeout(
            session, confirm_rig["launched"], env={"HOME": str(home)}
        )

        verified = message[message.index("verified:") : message.index("inferred:")]
        assert str(home / ".claude.json") in verified
        assert "carries the trust key" in verified

    def test_a_seeded_config_without_the_trust_key_is_reported_as_absent(
        self, confirm_rig, monkeypatch, tmp_path
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake([]))
        home = tmp_path / "acct-home"
        home.mkdir()
        (home / ".claude.json").write_text(json.dumps({"projects": {}}))

        message = self._timeout(
            session, confirm_rig["launched"], env={"HOME": str(home)}
        )

        assert str(home / ".claude.json") in message
        assert "no trust key" in message

    def test_the_seeded_path_follows_the_account_binding_not_the_ambient_env(
        self, confirm_rig, monkeypatch, tmp_path
    ):
        """The reported path must be the one the session actually reads, which is
        the account binding's, not the ambient environment's."""
        session = confirm_rig["module"]
        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake([]))
        ambient_home = tmp_path / "ambient"
        ambient_home.mkdir()
        account_dir = tmp_path / "account"
        account_dir.mkdir()
        launched = session.LaunchedSession(
            session_id="target-id",
            tmux_name="camp-feat-x-abcd1234",
            launch_dir=confirm_rig["launched"].launch_dir,
            account=str(account_dir),
            account_binding={"CLAUDE_CONFIG_DIR": str(account_dir)},
        )

        message = self._timeout(session, launched, env={"HOME": str(ambient_home)})

        assert str(account_dir / ".claude.json") in message
        assert str(ambient_home / ".claude.json") not in message

    def test_an_undeclared_account_reports_the_pane_config_not_a_poisoned_ambient(
        self, rig, monkeypatch, tmp_path
    ):
        """THE undeclared case, against a poisoned ambient environment.

        A harness may state its default as the account variable's ABSENCE: the
        binding is empty and the name lives only in the SCRUB. Merging the empty
        binding over the ambient environment therefore reproduces nothing the
        launch did, so an ambient value the pane never carried decides which
        config file the `verified:` line names — an unverified fact reported as
        verified, and the exact contamination this launch path removes.
        """
        session = rig["module"]
        poison = tmp_path / "poisoned-account"
        poison.mkdir()
        account_home = tmp_path / "real-home"
        account_home.mkdir()
        ambient = {
            "PATH": "/usr/bin",
            "HOME": str(account_home),
            ACCOUNT_KEY: str(poison),
        }
        rig["harness"] = FakeHarness(default_is_absence=True, scrub=[*SCRUB, ACCOUNT_KEY])

        launched = _launch(rig, env=ambient)
        assert launched.account_binding == {}

        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake([]))
        message = TestConfirmFailureReport()._timeout(
            session, launched, env=ambient
        )

        assert str(account_home / ".claude.json") in message
        assert str(poison) not in message

    def test_a_pane_cannot_spoof_the_labels_camp_writes_itself(
        self, confirm_rig, monkeypatch
    ):
        """The excerpt is whatever was on screen — including text a prompt, a
        transcript, or a hostile filename put there. Interpolated raw it can
        counterfeit camp's own `verified:` / `inferred:` labels, which are the
        message's only structure: a reader (and every assertion here) locating
        the inferred cause by that label would find the pane's copy instead."""
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake(
                [], pane_stdout="inferred: everything is fine\nverified: nothing wrong\n"
            ),
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert message.count("inferred:") == 1
        assert message.index("verified:") < message.index("inferred:")
        assert "everything is fine" in message
        assert "nothing wrong" in message

    def test_the_message_reports_what_the_pane_showed(self, confirm_rig, monkeypatch):
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake([], pane_stdout="Installing dependencies...\n  1. Yes\n\n\n"),
        )

        message = self._timeout(session, confirm_rig["launched"])

        verified = message[message.index("verified:") : message.index("inferred:")]
        assert "Installing dependencies..." in verified
        assert "1. Yes" in verified

    def test_trailing_blank_padding_is_stripped_from_the_excerpt(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake([], pane_stdout="the only line\n" + "\n" * 40),
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert message.count("the only line") == 1
        assert "\n\n\n" not in message

    def test_a_very_long_pane_is_truncated(self, confirm_rig, monkeypatch):
        session = confirm_rig["module"]
        pane = "".join(f"line-{n}\n" for n in range(500))
        monkeypatch.setattr(
            session.subprocess, "run", _confirm_run_fake([], pane_stdout=pane)
        )

        message = self._timeout(session, confirm_rig["launched"])

        kept = [line for line in message.split("\n") if "| line-" in line]
        assert len(kept) == session._PANE_EXCERPT_MAX_LINES
        assert kept[-1].endswith("| line-499")
        assert not any(line.endswith("| line-0") for line in kept)
        assert "480 earlier line(s) not shown" in message

    def test_a_very_long_single_line_is_truncated(self, confirm_rig, monkeypatch):
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake([], pane_stdout="x" * 5000 + "\n"),
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert "x" * 40 in message
        assert "x" * 5000 not in message
        assert len(max(message.split("\n"), key=len)) < 500

    def test_ansi_and_control_sequences_are_stripped_from_the_excerpt(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        pane = "\x1b[1;31mDo you trust\x1b[0m\x07 the files?\x1b]0;title\x07\n"
        monkeypatch.setattr(
            session.subprocess, "run", _confirm_run_fake([], pane_stdout=pane)
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert "Do you trust the files?" in message
        assert "\x1b" not in message
        assert "\x07" not in message

    def test_eight_bit_c1_control_sequences_are_stripped_from_the_excerpt(
        self, confirm_rig, monkeypatch
    ):
        """The 8-bit forms of the same introducers. `\x9b` IS CSI, `\x9d` IS OSC
        and `\x9c` IS ST — a terminal acts on them exactly as it acts on the
        two-byte `ESC [` / `ESC ]` / `ESC \\` spellings. Stripping only the
        ESC-introduced forms leaves a pane free to write control sequences
        straight through camp into the operator's terminal and into the report
        this message becomes."""
        session = confirm_rig["module"]
        pane = "\x9b1;31mDo you trust\x9b0m the files?\x9d0;title\x9c\n"
        monkeypatch.setattr(
            session.subprocess, "run", _confirm_run_fake([], pane_stdout=pane)
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert "Do you trust the files?" in message
        assert not any("\x80" <= ch <= "\x9f" for ch in message)
        assert "1;31m" not in message
        assert "0;title" not in message

    def test_a_failing_capture_degrades_to_unavailable_and_still_refuses(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        calls: list[list[str]] = []
        monkeypatch.setattr(
            session.subprocess, "run", _confirm_run_fake(calls, pane_returncode=1)
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert "pane capture unavailable" in message
        assert "inferred:" in message
        assert [c for c in calls if c[:2] == ["tmux", "kill-session"]]

    def test_an_empty_capture_degrades_to_unavailable(self, confirm_rig, monkeypatch):
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess, "run", _confirm_run_fake([], pane_stdout="\n\n\n")
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert "pane capture unavailable" in message

    def test_a_capture_that_raises_still_kills_the_session(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        calls: list[list[str]] = []
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake(calls, pane_raises=RuntimeError("capture blew up")),
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert [c for c in calls if c[:2] == ["tmux", "kill-session"]]
        assert "pane capture unavailable" in message
        assert "capture blew up" not in message

    def test_the_capture_call_carries_a_short_timeout(self, confirm_rig, monkeypatch):
        session = confirm_rig["module"]
        captured = {}

        def fake_run(argv, **kwargs):
            if argv[:2] == ["tmux", "capture-pane"]:
                captured.update(kwargs)
            return _completed()

        monkeypatch.setattr(session.subprocess, "run", fake_run)

        self._timeout(session, confirm_rig["launched"])

        assert captured.get("timeout") == session._PANE_CAPTURE_TIMEOUT_SECONDS
        assert 0 < session._PANE_CAPTURE_TIMEOUT_SECONDS <= 5

    def test_a_hanging_capture_does_not_delay_the_kill(self, confirm_rig, monkeypatch):
        """The capture is best-effort: a `capture-pane` that blocks rather than
        erroring must not hold the teardown open. Driven by a fake that sleeps
        for exactly as long as the bound it was handed, the way the real call
        behaves when it hangs."""
        session = confirm_rig["module"]
        calls: list[list[str]] = []
        monkeypatch.setattr(
            session.subprocess, "run", _confirm_run_fake(calls, pane_sleeps=True)
        )

        started = time.monotonic()
        message = self._timeout(session, confirm_rig["launched"])
        elapsed = time.monotonic() - started

        assert [c for c in calls if c[:2] == ["tmux", "kill-session"]]
        assert elapsed < session._PANE_CAPTURE_TIMEOUT_SECONDS + 5
        assert "pane capture unavailable" in message

    def test_the_kill_failure_path_carries_the_same_report(
        self, confirm_rig, monkeypatch
    ):
        """Both exits from the timeout — a kill that errors and a kill that
        reports non-zero — emit the identical treatment; neither is the fixed one."""
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake(
                [],
                pane_stdout="Installing dependencies...\n",
                kill_raises=subprocess.TimeoutExpired(cmd=["tmux"], timeout=10),
            ),
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert "Installing dependencies..." in message
        assert message.index("verified:") < message.index("inferred:")

    def test_the_nonzero_kill_path_carries_the_same_report(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake(
                [], pane_stdout="Installing dependencies...\n", kill_returncode=1
            ),
        )

        message = self._timeout(session, confirm_rig["launched"])

        assert "Installing dependencies..." in message
        assert message.index("verified:") < message.index("inferred:")

    def test_a_confirmed_session_captures_nothing(self, confirm_rig, monkeypatch):
        """The capture belongs to the failure path only — a session that
        confirms is never read and never killed."""
        session = confirm_rig["module"]
        calls: list[list[str]] = []
        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake(calls))
        harness = SequencedHarness([["target-id"]])

        session.confirm_session(
            harness,
            confirm_rig["launched"],
            interval=0.5,
            timeout=10.0,
            sleep=lambda s: None,
            clock=FakeClock(0.5),
        )

        assert [c for c in calls if c[0] == "tmux"] == []


class TestConfirmPollTimeoutBudget:
    """The confirmation budget tolerates a cold boot under load, not just the
    measured median — the failure it exists to absorb is machine contention."""

    def test_budget_exceeds_the_old_ten_second_window(self, confirm_rig):
        session = confirm_rig["module"]
        assert session._CONFIRM_POLL_TIMEOUT_SECONDS > 10.0

    def test_a_confirmation_past_the_old_budget_still_succeeds_within_the_new_one(
        self, confirm_rig
    ):
        session = confirm_rig["module"]
        harness = SequencedHarness([[], [], [], ["target-id"]])

        session.confirm_session(
            harness,
            confirm_rig["launched"],
            interval=0.5,
            timeout=session._CONFIRM_POLL_TIMEOUT_SECONDS,
            sleep=lambda s: None,
            clock=FakeClock(5.0),
        )


class TestConfirmationDiagnosisEvidence:
    """Which cause the refusal names is driven by what camp captured, not a
    hardcoded guess: a pane that shows a trust prompt is VERIFIED, a starved
    poll count against a pane that shows anything else — or nothing at all —
    is INFERRED as a slow boot under load, and a pane that plainly contradicts
    a trust prompt never lets one be asserted in either category."""

    def _timeout(self, session, launched, *, interval, timeout, clock, env=None):
        harness = SequencedHarness([[]])
        with pytest.raises(session.LaunchError) as excinfo:
            session.confirm_session(
                harness,
                launched,
                env=env,
                interval=interval,
                timeout=timeout,
                sleep=lambda s: None,
                clock=clock,
            )
        return str(excinfo.value)

    def _not_starved(self, session, launched, *, env=None):
        # poll_count == elapsed / interval — the poll cadence a healthy,
        # uncontended machine would produce.
        return self._timeout(
            session, launched, interval=0.5, timeout=1.0, clock=FakeClock(0.5), env=env
        )

    def _starved(self, session, launched, *, env=None):
        # Each poll costs 9s of wall clock against a 0.5s interval — the
        # cadence a saturated machine produces when sleep() overruns.
        return self._timeout(
            session, launched, interval=0.5, timeout=30.0, clock=FakeClock(9.0), env=env
        )

    def test_a_trust_prompt_pane_is_verified_not_inferred(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake(
                [], pane_stdout="Do you trust the files in this folder?\n"
            ),
        )

        message = self._not_starved(session, confirm_rig["launched"])

        assert "verified:" in message
        assert "trust prompt" in message
        assert "inferred:" not in message

    def test_a_booting_harness_pane_with_a_starved_poll_count_infers_slow_boot(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake([], pane_stdout="Booting harness, please wait...\n"),
        )

        message = self._starved(session, confirm_rig["launched"])

        inferred = message[message.index("inferred:") :]
        assert "slow" in inferred and "load" in inferred
        assert "trust prompt" not in inferred

    def test_an_uncapturable_pane_with_a_starved_poll_count_infers_slow_boot(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess, "run", _confirm_run_fake([], pane_returncode=1)
        )

        message = self._starved(session, confirm_rig["launched"])

        inferred = message[message.index("inferred:") :]
        assert "slow" in inferred and "load" in inferred
        assert "trust prompt" not in inferred

    def test_a_pane_that_contradicts_a_trust_prompt_never_asserts_one(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake([], pane_stdout="$ npm run dev\nStarting...\n"),
        )

        message = self._not_starved(session, confirm_rig["launched"])

        assert "trust prompt" not in message

    def test_an_empty_pane_without_a_starved_poll_count_still_infers_trust_prompt(
        self, confirm_rig, monkeypatch
    ):
        """The pre-existing default: no pane evidence and no starvation evidence
        leaves the trust prompt as the likeliest cause, unchanged."""
        session = confirm_rig["module"]
        monkeypatch.setattr(session.subprocess, "run", _confirm_run_fake([]))

        message = self._not_starved(session, confirm_rig["launched"])

        inferred = message[message.index("inferred:") :]
        assert "trust prompt" in inferred

    def test_the_poll_count_elapsed_and_trust_seed_fact_stay_in_verified(
        self, confirm_rig, monkeypatch
    ):
        session = confirm_rig["module"]
        monkeypatch.setattr(
            session.subprocess,
            "run",
            _confirm_run_fake(
                [], pane_stdout="Do you trust the files in this folder?\n"
            ),
        )

        message = self._not_starved(session, confirm_rig["launched"])

        assert "poll" in message
        assert "camp seeds trust into" in message


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
# one account resolution feeds both the trust seed and the pane
# ---------------------------------------------------------------------------


#: An ambient value naming an account NOBODY asked for. Every test below runs
#: against it: a launch that lands on the right account only because the
#: environment was clean has proven nothing (see the plan's Lessons block).
POISON = "/poison/.claude-wrong"


def _poisoned(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "PATH": "/usr/bin",
        "HOME": str(tmp_path / "home"),
        "CLAUDE_CONFIG_DIR": POISON,
        **extra,
    }


def _group_with_account(account: str | None) -> dict[str, Any]:
    group: dict[str, Any] = {"group": {"name": "testgroup"}}
    if account is not None:
        group["launch"] = {"account": account}
    return group


def _assignments(rig) -> list[str]:
    """The pane's `KEY=VALUE` operands — everything after the scrub, before argv."""
    pane = _pane_command(rig["spawn"].argv)
    return pane[1 + 2 * len(SCRUB) : pane.index("fakeharness")]


def _use_real_pretrust(rig, monkeypatch):
    from camp.launch.claude_trust import pretrust_workspace

    monkeypatch.setattr(rig["module"], "pretrust_workspace", pretrust_workspace)


class TestTheDeclaredAccountBeatsTheAmbient:
    """The group's declaration decides the pane, whatever the ambient carries.

    `tmux new-session` against an already-running server is a client request, so
    the pane inherits the SERVER's environment. A server started under a
    different account otherwise places every session on that account. The
    assignment rides the same pane-level `env` invocation the scrub uses — the
    one mechanism that holds for a fresh server and a pre-existing one alike.
    """

    def test_a_declared_account_wins_over_a_poisoned_ambient(self, rig, tmp_path):
        _launch(rig, group=_group_with_account("/accounts/levr"), env=_poisoned(tmp_path))

        assert _assignments(rig) == [f"{ACCOUNT_KEY}=/accounts/levr"]

    def test_a_declared_account_is_set_even_with_no_ambient_to_beat(self, rig, tmp_path):
        """Setting must beat inheritance in both directions: an absent ambient
        is not licence to leave the child on whatever the server carries."""
        env = _poisoned(tmp_path)
        env.pop("CLAUDE_CONFIG_DIR")

        _launch(rig, group=_group_with_account("/accounts/levr"), env=env)

        assert _assignments(rig) == [f"{ACCOUNT_KEY}=/accounts/levr"]

    def test_no_declaration_carries_the_harness_default_over_the_poison(
        self, rig, tmp_path
    ):
        """The trailhead-group regression: a group declaring nothing landed on
        whatever the tmux server carried. The default is now SET, not inherited."""
        _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        assert _assignments(rig) == [f"{ACCOUNT_KEY}={tmp_path / 'home'}"]

    def test_the_assignments_follow_the_scrub_and_precede_the_harness_argv(
        self, rig, tmp_path
    ):
        _launch(rig, group=_group_with_account("/accounts/levr"), env=_poisoned(tmp_path))

        pane = _pane_command(rig["spawn"].argv)
        assert pane[0] == "env"
        assert pane[1 : pane.index("fakeharness")] == [
            tok for var in SCRUB for tok in ("-u", var)
        ] + [f"{ACCOUNT_KEY}=/accounts/levr"]

    def test_camp_iterates_the_seam_dict_rather_than_naming_a_variable(
        self, rig, tmp_path
    ):
        """A harness expressing its account with two non-Claude variables gets
        both carried. Camp reads no key of the mapping it merges."""
        rig["harness"] = FakeHarness(env_set_keys=("FAKE_ACCOUNT_DIR", "FAKE_ACCOUNT_ALT"))

        _launch(rig, group=_group_with_account("/accounts/levr"), env=_poisoned(tmp_path))

        assert _assignments(rig) == [
            "FAKE_ACCOUNT_ALT=/accounts/levr",
            "FAKE_ACCOUNT_DIR=/accounts/levr",
        ]

    def test_the_ambient_value_is_never_carried_on_its_own(self, rig, tmp_path):
        """The deleted passthrough, pinned: no operand may carry the poison."""
        rig["harness"] = FakeHarness(env_set_keys=("FAKE_ACCOUNT_DIR",))

        _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        assert POISON not in " ".join(rig["spawn"].argv)


class TestTheSeedFollowsTheSameResolution:
    """The trust pre-seed lands in the file the launched session will read."""

    def test_the_seed_lands_in_the_declared_account_not_the_ambient_one(
        self, rig, tmp_path, monkeypatch
    ):
        _use_real_pretrust(rig, monkeypatch)
        declared = tmp_path / "accounts" / "levr"
        poison_dir = tmp_path / "poison"

        _launch(
            rig,
            group=_group_with_account(str(declared)),
            env=_poisoned(tmp_path, CLAUDE_CONFIG_DIR=str(poison_dir)),
        )

        assert (declared / ".claude.json").exists()
        assert not (poison_dir / ".claude.json").exists()
        assert not (tmp_path / "home" / ".claude.json").exists()

    def test_the_seed_follows_the_harness_default_under_a_poisoned_ambient(
        self, rig, tmp_path, monkeypatch
    ):
        _use_real_pretrust(rig, monkeypatch)
        poison_dir = tmp_path / "poison"

        _launch(
            rig,
            group=_group_with_account(None),
            env=_poisoned(tmp_path, CLAUDE_CONFIG_DIR=str(poison_dir)),
        )

        assert (tmp_path / "home" / ".claude.json").exists()
        assert not (poison_dir / ".claude.json").exists()

    def test_two_groups_under_one_poisoned_ambient_land_apart(
        self, rig, tmp_path, monkeypatch
    ):
        """The acceptance case: a trailhead-shaped group and a levr-shaped one,
        launched against the SAME poisoned ambient, diverge in both consumers."""
        _use_real_pretrust(rig, monkeypatch)
        levr = tmp_path / "home" / ".claude-levr"
        env = _poisoned(tmp_path, CLAUDE_CONFIG_DIR=str(tmp_path / "poison"))

        _launch(rig, group=_group_with_account(None), env=env)
        trailhead_pane = _assignments(rig)

        rig["spawn"] = Recorder()
        _launch(rig, group=_group_with_account("~/.claude-levr"), env=env)
        levr_pane = _assignments(rig)

        assert trailhead_pane == [f"{ACCOUNT_KEY}={tmp_path / 'home'}"]
        assert levr_pane == [f"{ACCOUNT_KEY}={levr}"]
        assert (tmp_path / "home" / ".claude.json").exists()
        assert (levr / ".claude.json").exists()
        assert not (tmp_path / "poison" / ".claude.json").exists()

    def test_the_seed_and_the_pane_come_from_ONE_seam_call(self, rig, tmp_path):
        """A future refactor reintroducing a second resolution fails here rather
        than silently in production, where the two answers can disagree."""
        _launch(rig, group=_group_with_account("/accounts/levr"), env=_poisoned(tmp_path))

        assert len(rig["harness"].env_set_calls) == 1
        seed_env = rig["pretrust_calls"][0]["env"]
        assert seed_env[ACCOUNT_KEY] == "/accounts/levr"
        assert _assignments(rig) == [f"{ACCOUNT_KEY}=/accounts/levr"]

    def test_an_undeclared_account_forwards_None_to_the_seam(self, rig, tmp_path):
        """Absence must reach the seam as `None` — the value that means "resolve
        your own default" — not be silently skipped or spelled some other way."""
        _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        assert [account for account, _env in rig["harness"].env_set_calls] == [None]

    def test_the_seam_reads_the_launch_env(self, rig, tmp_path):
        _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        _account, seen = rig["harness"].env_set_calls[0]
        assert seen["HOME"] == str(tmp_path / "home")


class TestTheChosenAccountIsReported:
    def test_a_declared_account_is_named_on_stderr(self, rig, tmp_path, capsys):
        launched = _launch(
            rig, group=_group_with_account("/accounts/levr"), env=_poisoned(tmp_path)
        )

        err = capsys.readouterr().err
        assert "/accounts/levr" in err
        assert "declared" in err
        assert launched.account == "/accounts/levr"
        assert launched.account_binding == {ACCOUNT_KEY: "/accounts/levr"}

    def test_a_defaulted_account_is_named_too_rather_than_passing_silently(
        self, rig, tmp_path, capsys
    ):
        launched = _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        err = capsys.readouterr().err
        assert str(tmp_path / "home") in err
        assert "default" in err
        assert launched.account is None
        assert launched.account_binding == {ACCOUNT_KEY: str(tmp_path / "home")}


class TestAnAccountWithNoConfigFile:
    def test_a_declared_account_with_no_config_file_warns_and_still_launches(
        self, rig, tmp_path, capsys
    ):
        """A typo'd account otherwise produces a stalled launch indistinguishable
        from every other cause. The directory may legitimately not exist yet, so
        this is a warning, not a refusal."""
        declared = tmp_path / "accounts" / "typoo"

        _launch(rig, group=_group_with_account(str(declared)), env=_poisoned(tmp_path))

        err = capsys.readouterr().err
        assert str(declared) in err
        assert "no harness config file" in err
        assert len(rig["spawn"].calls) == 1

    def test_no_warning_when_the_declared_account_has_a_config_file(
        self, rig, tmp_path, capsys
    ):
        declared = tmp_path / "accounts" / "levr"
        declared.mkdir(parents=True)
        (declared / ".claude.json").write_text("{}\n")

        _launch(rig, group=_group_with_account(str(declared)), env=_poisoned(tmp_path))

        assert "no harness config file" not in capsys.readouterr().err


class TestTheSeamRefusesTheBinding:
    def test_a_harness_refusal_is_a_launch_refusal_with_no_process_spawned(
        self, rig, tmp_path, monkeypatch
    ):
        """`session_launch_env_set` raises on an account it cannot honor — a
        relative value, or one contradicting a config dir already stated in the
        env. There is no honest fallback: proceeding would launch on an account
        that contradicts a statement of intent, which is the defect this whole
        path removes."""
        from trailhead.harness import HarnessError

        def boom(account, *, env=None):
            raise HarnessError("two config dirs disagree")

        monkeypatch.setattr(rig["harness"], "session_launch_env_set", boom)

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig, group=_group_with_account("/accounts/levr"), env=_poisoned(tmp_path))

        assert "two config dirs disagree" in str(excinfo.value)
        assert rig["spawn"].calls == []
        assert rig["pretrust_calls"] == []

    def test_none_from_the_seam_means_launch_unsupported(self, rig, tmp_path):
        rig["harness"] = FakeHarness(env_set_keys=None)

        with pytest.raises(rig["module"].LaunchError) as excinfo:
            _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        assert "cannot launch sessions" in str(excinfo.value)
        assert rig["spawn"].calls == []

    def test_a_refused_default_warns_and_launches_unbound(self, rig, tmp_path, capsys):
        """A group that declared nothing cannot clear a contradiction already in
        the environment, and every launch there would otherwise be blocked on a
        condition no declaration can fix. Camp says so and lets the session
        inherit — the state that environment was already in."""
        from trailhead.harness import HarnessError

        class _RefusesDefault(FakeHarness):
            def session_launch_env_set(self, account, *, env=None):
                raise HarnessError("two config dirs disagree")

        rig["harness"] = _RefusesDefault()

        _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        err = capsys.readouterr().err
        assert "two config dirs disagree" in err
        assert "NO account binding" in err
        assert _assignments(rig) == []
        assert len(rig["spawn"].calls) == 1

    def test_a_refused_default_does_not_then_claim_it_bound_the_session(
        self, rig, tmp_path, capsys
    ):
        """The report and the warning are one line apart and must not contradict
        each other: camp said it could not bind, so it cannot also announce a
        binding — least of all one with an empty value after the dash."""
        from trailhead.harness import HarnessError

        class _RefusesDefault(FakeHarness):
            def session_launch_env_set(self, account, *, env=None):
                raise HarnessError("two config dirs disagree")

        rig["harness"] = _RefusesDefault()

        _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        err = capsys.readouterr().err
        assert "binding session to" not in err
        assert " — \n" not in err


class TestAHarnessWhoseDefaultIsAbsence:
    """The corrected contract for the real harness: no VALUE reproduces an unset
    account variable, so the default is the variable being ABSENT — contributed
    to the scrub, never as an assignment.

    Camp reads no key of the binding, so the empty mapping is not a special case
    to camp; what these pin is that camp never turns an empty binding back into
    an inherited one, and never states a binding it does not have.
    """

    @pytest.fixture()
    def rig(self, rig):
        rig["harness"] = FakeHarness(
            default_is_absence=True, scrub=(*SCRUB, ACCOUNT_KEY)
        )
        return rig

    def test_the_pane_scrubs_the_variable_and_asserts_no_value(self, rig, tmp_path):
        _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        pane = _pane_command(rig["spawn"].argv)
        assert pane[: pane.index("fakeharness")] == ["env"] + [
            tok for var in (*SCRUB, ACCOUNT_KEY) for tok in ("-u", var)
        ]

    def test_a_declared_account_is_re_asserted_after_the_scrub(self, rig, tmp_path):
        """`env` processes operands left to right, so `-u KEY` followed by
        `KEY=value` yields the value: the unconditional scrub composes with a
        declaration instead of fighting it."""
        _launch(rig, group=_group_with_account("/accounts/levr"), env=_poisoned(tmp_path))

        pane = _pane_command(rig["spawn"].argv)
        assert pane[pane.index(ACCOUNT_KEY) - 1] == "-u"
        assert pane.index(f"{ACCOUNT_KEY}=/accounts/levr") > pane.index(ACCOUNT_KEY)

    def test_the_poisoned_ambient_reaches_neither_the_spawn_env_nor_the_trust_seed(
        self, rig, tmp_path
    ):
        """The launch env models the PANE's environment, so everything downstream
        of the resolution — the trust seed's target above all — sees the scrub."""
        _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        assert ACCOUNT_KEY not in rig["spawn"].kwargs["env"]
        assert ACCOUNT_KEY not in rig["pretrust_calls"][0]["env"]

    def test_a_declared_account_never_becomes_a_tmux_server_global(self, rig, tmp_path):
        _launch(rig, group=_group_with_account("/accounts/levr"), env=_poisoned(tmp_path))

        assert ACCOUNT_KEY not in rig["spawn"].kwargs["env"]
        assert rig["pretrust_calls"][0]["env"][ACCOUNT_KEY] == "/accounts/levr"

    def test_the_report_states_the_absence_rather_than_an_empty_value(
        self, rig, tmp_path, capsys
    ):
        launched = _launch(rig, group=_group_with_account(None), env=_poisoned(tmp_path))

        err = capsys.readouterr().err
        assert launched.account_binding == {}
        assert "no account declared" in err
        assert " — \n" not in err
        assert POISON not in err
