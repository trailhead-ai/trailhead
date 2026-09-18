"""Tests for launch/tmux.py — the seam that owns every tmux invocation.

Test contract (see task/promote-camp-s-tmux-boundary-to-a-seam-that-owns-every-target):
- A kill targeting a session name that is a strict prefix of another live
  session acts on the named session only, and the other survives.
- A session created through the seam (`Tmux.spawn_session`) carries the name
  it was given, with no `=` in it — enumerated afterwards and asserted
  byte-for-byte. The `-s`-is-not-a-target half of the `=` property.
- `capture_pane` and `set_environment` each address the exactly-named
  session, never one it prefixes.
- The per-call budget every tmux invocation carries resolves from the
  environment when the caller names none: an override moves the default, an
  explicit `timeout=` still wins, and a malformed or non-positive setting
  leaves the shipped budget alone.
- The tri-state contract: `has_session` answers True/False/None,
  `list_sessions` answers a `SessionListing` only on the no-server stderr
  shape and `UNANSWERED` otherwise.

Nothing here reaches a real tmux server: every test below either drives the
seam against a hand-rolled `subprocess.run` stand-in (to prove a property no
canned answer can — the `=` qualification actually reaching tmux) or against
the repo's own no-server stub tmux binary on `PATH` (`tests/conftest.py`'s
`_sandbox_tmux`) plumbed through `camp.launch.tmux`'s real subprocess call.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _completed(*, returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["tmux"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class _PrefixMatchingTmux:
    """A `subprocess.run` stand-in reproducing tmux's OWN target resolution:
    an `=`-qualified target requires an exact name; a bare one resolves by
    prefix, and answers for the first live session it prefixes.

    This is what makes bullets 1 and 3 of the test contract meaningful to
    write test-first: a canned, dict-keyed fake (`_FakeTmux`, used
    everywhere else in this suite) already answers exactly, by
    construction, and could never go red against a bare, unqualified `-t`.
    Only a fake that reproduces the REAL ambiguity tmux resolves — prefix
    fallback on a bare target — can distinguish "qualified" from "not".
    """

    def __init__(self, live: dict[str, str]) -> None:
        self.live = dict(live)
        self.calls: list[list[str]] = []
        self.next_window_id = 0

    def _target(self, argv: list[str]) -> str | None:
        if "-t" not in argv:
            return None
        return argv[argv.index("-t") + 1]

    def _resolve(self, raw_target: str) -> str | None:
        if raw_target.startswith("="):
            name = raw_target[1:]
            return name if name in self.live else None
        if raw_target in self.live:
            return raw_target
        matches = [name for name in self.live if name.startswith(raw_target)]
        return matches[0] if len(matches) == 1 else None

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        verb = argv[1] if len(argv) > 1 else None
        raw_target = self._target(argv)
        if verb == "kill-session":
            resolved = self._resolve(raw_target) if raw_target else None
            if resolved is None:
                return _completed(returncode=1, stderr="can't find session")
            del self.live[resolved]
            return _completed(returncode=0)
        if verb == "has-session":
            resolved = self._resolve(raw_target) if raw_target else None
            return _completed(returncode=0 if resolved else 1)
        if verb == "new-window":
            resolved = self._resolve(raw_target) if raw_target else None
            if resolved is None:
                return _completed(returncode=1, stderr="can't find session")
            self.next_window_id += 1
            window_name = argv[argv.index("-n") + 1] if "-n" in argv else ""
            return _completed(
                returncode=0, stdout=f"@{self.next_window_id} {window_name}\n"
            )
        return _completed(returncode=1, stderr="unhandled verb in test stand-in")


def test_a_kill_targeting_a_strict_prefix_of_another_live_session_survives_the_other(
    monkeypatch,
):
    """The collision this task exists to close: with `=` qualification, a
    kill of "feat" never touches the still-live "feat-longer", because tmux
    requires an exact name match for an `=`-qualified target. Reverting
    `camp.launch.tmux.target` to the identity function (the pre-task, bare
    `-t` shape) makes this go red — the bare target resolves by PREFIX and
    the survivor is killed instead."""
    import camp.launch.tmux as tmux_module

    fake = _PrefixMatchingTmux({"feat-longer": "sleep 1"})
    monkeypatch.setattr(tmux_module.subprocess, "run", fake)

    result = tmux_module.Tmux().kill_session("feat")

    assert result is not None and result.returncode != 0, (
        "a kill of a name that does not exist must not resolve to a "
        "same-prefixed session that does"
    )
    assert "feat-longer" in fake.live, "the surviving session was killed"


def test_spawn_session_names_with_s_never_carries_the_target_prefix(tmp_path, monkeypatch):
    """The `-s`-is-not-a-target half: a session created through the seam is
    enumerated back under the exact name it was given, with no leading `=`.
    Fails against a naive "prefix every target" implementation, which would
    create a session literally named `=my-session`."""
    import os
    import stat

    import camp.launch.tmux as tmux_module

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    table_file = tmp_path / "table.json"
    stub = stub_dir / "tmux"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "table_path = os.environ['TABLE_FILE']\n"
        "table = json.load(open(table_path)) if os.path.exists(table_path) else {}\n"
        "if args and args[0] == 'new-session':\n"
        "    name = args[args.index('-s') + 1]\n"
        "    table[name] = 1\n"
        "    json.dump(table, open(table_path, 'w'))\n"
        "elif args and args[0] == 'list-sessions':\n"
        "    for name, windows in table.items():\n"
        "        print(f'{windows}|{name}')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("TABLE_FILE", str(table_file))

    tmux = tmux_module.Tmux()
    tmux.spawn_session(
        "my-session",
        cwd=tmp_path,
        command=["sleep", "1"],
        env={**os.environ, "TABLE_FILE": str(table_file)},
        timeout=5,
    )

    listing = tmux.list_sessions()
    assert isinstance(listing, tmux_module.SessionListing)
    names = {session.name for session in listing.sessions}
    assert names == {"my-session"}, (
        "the enumerated name must be byte-for-byte the one `-s` was given — "
        f"got {names!r}"
    )


def test_capture_pane_targets_the_exact_name_not_a_prefix(monkeypatch):
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0, stdout="pane text\n")

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().capture_pane("feat")

    assert calls == [["tmux", "capture-pane", "-p", "-t", "=feat"]]


def test_set_environment_targets_the_exact_name_not_a_prefix(monkeypatch):
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().set_environment("feat", ["-r", "SOME_VAR"])

    assert calls == [["tmux", "set-environment", "-t", "=feat", "-r", "SOME_VAR"]]


def test_switch_client_targets_the_exact_name_not_a_prefix(monkeypatch):
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().switch_client("feat")

    assert calls == [["tmux", "switch-client", "-t", "=feat"]]


def test_has_session_tri_state_true_false_none(monkeypatch):
    """The pinned contract: True, False, or None — never a fourth answer,
    and an unanswerable call is never folded into False."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess, "run", lambda *a, **k: _completed(returncode=0)
    )
    assert tmux_module.Tmux().has_session("x") is True

    monkeypatch.setattr(
        tmux_module.subprocess, "run", lambda *a, **k: _completed(returncode=1)
    )
    assert tmux_module.Tmux().has_session("x") is False

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["tmux"], timeout=5)

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise)
    assert tmux_module.Tmux().has_session("x") is None


def test_has_session_with_reason_carries_the_exceptions_own_message(monkeypatch):
    """The one consumer-facing widen Fix 3 needs: when `has_session` itself
    would answer `None` (the call never completed), `has_session_with_reason`
    carries the exception's own message — varied across two distinct
    exceptions to prove the message is threaded through, not synthesized."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess, "run", lambda *a, **k: _completed(returncode=0)
    )
    assert tmux_module.Tmux().has_session_with_reason("x") == (True, None)

    monkeypatch.setattr(
        tmux_module.subprocess, "run", lambda *a, **k: _completed(returncode=1)
    )
    assert tmux_module.Tmux().has_session_with_reason("x") == (False, None)

    def _raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["tmux"], timeout=5)

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise_timeout)
    present, reason = tmux_module.Tmux().has_session_with_reason("x")
    assert present is None
    assert "timed out" in reason

    def _raise_oserror(*a, **k):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'tmux'")

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise_oserror)
    present, reason = tmux_module.Tmux().has_session_with_reason("x")
    assert present is None
    assert "No such file or directory" in reason


def test_kill_session_with_reason_carries_the_exceptions_own_message(monkeypatch):
    """Same widen as `has_session_with_reason`, for `kill_session`'s two
    diagnostic call sites in `launch/session.py` — varied across two
    distinct exceptions to prove the message is threaded through, not
    synthesized."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess, "run", lambda *a, **k: _completed(returncode=0)
    )
    done, reason = tmux_module.Tmux().kill_session_with_reason("x")
    assert done.returncode == 0
    assert reason is None

    def _raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["tmux"], timeout=5)

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise_timeout)
    done, reason = tmux_module.Tmux().kill_session_with_reason("x")
    assert done is None
    assert "timed out" in reason

    def _raise_oserror(*a, **k):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'tmux'")

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise_oserror)
    done, reason = tmux_module.Tmux().kill_session_with_reason("x")
    assert done is None
    assert "No such file or directory" in reason


def test_set_environment_with_reason_carries_the_exceptions_own_message(monkeypatch):
    """Same widen as `has_session_with_reason`, for `set_environment`'s
    diagnostic call site in `launch/session.py`'s session-environment
    statement — varied across two distinct exceptions to prove the message
    is threaded through, not synthesized."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess, "run", lambda *a, **k: _completed(returncode=0)
    )
    done, reason = tmux_module.Tmux().set_environment_with_reason("x", ["-r", "SOME_VAR"])
    assert done.returncode == 0
    assert reason is None

    def _raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["tmux"], timeout=5)

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise_timeout)
    done, reason = tmux_module.Tmux().set_environment_with_reason("x", ["-r", "SOME_VAR"])
    assert done is None
    assert "timed out" in reason

    def _raise_oserror(*a, **k):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'tmux'")

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise_oserror)
    done, reason = tmux_module.Tmux().set_environment_with_reason("x", ["-r", "SOME_VAR"])
    assert done is None
    assert "No such file or directory" in reason


def test_list_sessions_answers_session_listing_only_on_the_no_server_shape(monkeypatch):
    """`UNANSWERED` for every other non-zero exit, `SessionListing` only for
    the specific no-server stderr shape."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="error connecting to /tmp/x (No such file or directory)\n",
        ),
    )
    result = tmux_module.Tmux().list_sessions()
    assert isinstance(result, tmux_module.SessionListing)
    assert result.sessions == ()

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=1, stderr="unsafe permissions\n"),
    )
    assert tmux_module.Tmux().list_sessions() is tmux_module.UNANSWERED


def test_new_session_composes_no_command_and_no_argv_of_its_own(monkeypatch):
    """`new_session` is a thin wrapper over `spawn_session` with an empty
    command — it must not build a second `new-session` argv of its own.
    An empty command leaves the pane running tmux's configured default
    shell, which is exactly the login-shell window the workspace door
    needs."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().new_session(
        "my-ws", cwd="/tmp/ws", env={"FOO": "bar"}, timeout=5
    )

    assert calls == [["tmux", "new-session", "-d", "-s", "my-ws", "-c", "/tmp/ws"]]


def test_new_session_names_with_s_unprefixed_while_a_target_in_the_same_flow_is_qualified(
    monkeypatch,
):
    """The `=` property re-asserted at this new call site: `new_session`'s
    `-s` carries the bare name, never `target(name)`, while a `-t` call
    against the same name in the same flow (`has_session`) is `=`-exact.
    Reverting `new_session` to pass `target(name)` to `-s` makes this go
    red — every later `=<name>` target would then miss the session it just
    created."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    name = "my-workspace"
    tmux = tmux_module.Tmux()
    tmux.new_session(name, cwd="/tmp/ws", env={}, timeout=5)
    tmux.has_session(name)

    new_session_call, has_session_call = calls
    assert new_session_call[new_session_call.index("-s") + 1] == name
    assert (
        has_session_call[has_session_call.index("-t") + 1] == tmux_module.target(name)
    )
    assert new_session_call[new_session_call.index("-s") + 1] != tmux_module.target(
        name
    )


# ---------------------------------------------------------------------------
# The per-call budget, and the environment seam over it
# ---------------------------------------------------------------------------


class TestTmuxTimeoutEnvironmentSeam:
    """Every tmux call is bounded, and the bound is overridable for tests.

    The budget absorbs a busy tmux server, so a test driving a question tmux
    will never answer sits out the whole of it to observe the refusal it is
    asserting — and those tests drive camp as a subprocess, where the
    `timeout=` parameter cannot reach.
    """

    def _timeout_applied(self, monkeypatch, override, **kwargs):
        """Return the bound `Tmux` actually hands ``subprocess.run``."""
        import camp.launch.tmux as tmux_module

        if override is None:
            monkeypatch.delenv("CAMP_TEST_TMUX_TIMEOUT_SECONDS", raising=False)
        else:
            monkeypatch.setenv("CAMP_TEST_TMUX_TIMEOUT_SECONDS", override)
        captured: dict[str, float] = {}

        def _capture(argv, **kw):
            captured["timeout"] = kw["timeout"]
            return _completed(returncode=0, stdout="")

        monkeypatch.setattr(tmux_module.subprocess, "run", _capture)
        tmux_module.Tmux(**kwargs).has_session("camp-feat-a-11112222")
        return captured["timeout"]

    def test_an_override_shortens_the_bound_actually_applied(self, monkeypatch) -> None:
        short = self._timeout_applied(monkeypatch, "0.25")
        shipped = self._timeout_applied(monkeypatch, None)

        assert short < shipped
        assert short == pytest.approx(0.25)

    def test_an_explicit_timeout_still_beats_the_environment(self, monkeypatch) -> None:
        """The seam moves the default only — an injecting caller is untouched."""
        assert self._timeout_applied(monkeypatch, "0.25", timeout=2.0) == pytest.approx(2.0)

    @pytest.mark.parametrize("bad", ["", "abc", "0", "-1"])
    def test_a_malformed_or_non_positive_override_leaves_the_shipped_bound(
        self, monkeypatch, bad
    ) -> None:
        """A stray setting must not shrink a real caller's bound to nothing."""
        import camp.launch.tmux as tmux_module

        applied = self._timeout_applied(monkeypatch, bad)

        assert applied == pytest.approx(tmux_module.TMUX_TIMEOUT_SECONDS)


def test_new_window_argv_carries_target_cwd_and_command_with_qualified_target(
    monkeypatch,
):
    """The argv `new_window` issues carries the session target, working
    directory, and command — with the `-t` target `=`-qualified while no
    bare session name argument appears anywhere in the call."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0, stdout="@7\n")

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().new_window(
        "feat", cwd="/tmp/ws", window_name="my-window", command=["sleep", "30"]
    )

    assert calls == [
        [
            "tmux",
            "new-window",
            "-t",
            "=feat",
            "-P",
            "-F",
            "#{window_id} #{window_name}",
            "-n",
            "my-window",
            "-c",
            "/tmp/ws",
            "sleep",
            "30",
        ]
    ]


def test_new_window_targeting_a_strict_prefix_of_another_live_session_does_not_resolve_to_it(
    monkeypatch,
):
    """A name that is a prefix of another live session's name must not
    resolve to that other session: `new_window("feat", ...)` against a
    live `feat-longer` (and no `feat`) must fail, never silently create the
    window inside `feat-longer`. Reverting `target` to the identity
    function makes this go red — the bare target resolves by prefix and
    the window is created in the wrong session."""
    import camp.launch.tmux as tmux_module

    fake = _PrefixMatchingTmux({"feat-longer": "sleep 1"})
    monkeypatch.setattr(tmux_module.subprocess, "run", fake)

    result = tmux_module.Tmux().new_window(
        "feat", cwd="/tmp/ws", window_name="w", command=()
    )

    assert result is None, (
        "a new-window targeting a name that does not exist must not "
        "resolve to a same-prefixed session that does"
    )
    new_window_calls = [c for c in fake.calls if c[1] == "new-window"]
    assert len(new_window_calls) == 1


def test_new_window_returns_the_id_and_name_tmux_reported_varying_across_two_calls(
    monkeypatch,
):
    """The returned id AND name are the ones the stand-in tmux printed on
    THIS call — not a fixed sentinel, and not the `window_name` argument
    echoed back unread. Two different canned answers produce two different
    results."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="@3 alpha\n"),
    )
    first = tmux_module.Tmux().new_window("ws", cwd="/tmp/a", window_name="alpha", command=())
    assert first.window_id == "@3"
    assert first.window_name == "alpha"

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="@9 alpha (1)\n"),
    )
    second = tmux_module.Tmux().new_window("ws", cwd="/tmp/a", window_name="alpha", command=())
    assert second.window_id == "@9"
    assert second.window_name == "alpha (1)"

    assert first.window_id != second.window_id
    assert first.window_name != second.window_name


def test_new_window_non_zero_exit_returns_none_not_an_exception_and_no_id(monkeypatch):
    """A tmux that exits non-zero (the session does not exist, say) produces
    the seam's own failure result — `None` — never a raw exception escaping
    to the caller, and never a window id."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=1, stderr="can't find session"),
    )

    result = tmux_module.Tmux().new_window("ws", cwd="/tmp/a", window_name="w", command=())

    assert result is None


def test_new_window_unreachable_tmux_is_distinguishable_from_an_answered_failure(
    monkeypatch,
):
    """A tmux that cannot be reached at all (timeout, unlaunchable binary)
    answers `UNANSWERED` — the same tri-state sentinel `pane_command`
    already uses — never the same value a completed, non-zero exit
    produces. Folding the two together would report a hung tmux as an
    ordinary create failure."""
    import camp.launch.tmux as tmux_module

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["tmux"], timeout=5)

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise)

    result = tmux_module.Tmux().new_window("ws", cwd="/tmp/a", window_name="w", command=())

    assert result is tmux_module.UNANSWERED
    assert result is not None


def test_set_option_states_a_session_local_option_never_global(monkeypatch):
    """`set_option` issues plain `set-option -t <target> <key> <value>` —
    no `-g` — so the value is scoped to the addressed session only, and
    *target* is used exactly as given (no `=` qualification applied by this
    method — see its own docstring)."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().set_option("=feat", "@camp_group", "trailhead")

    assert calls == [["tmux", "set-option", "-t", "=feat:", "@camp_group", "trailhead"]]
    assert "-g" not in calls[0]


def test_set_option_addresses_a_raw_session_id_target_verbatim(monkeypatch):
    """The window-dispatch verb's call shape: *target* is a tmux-minted
    session id (`$3`), never run through `=`-name qualification."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().set_option("$3", "@camp_slug", "camp-cli")

    assert calls == [["tmux", "set-option", "-t", "$3:", "@camp_slug", "camp-cli"]]


def test_show_option_reads_back_the_bare_value(monkeypatch):
    """`-v` prints the value alone; the trailing newline tmux always adds is
    stripped, and two distinct values prove the method reads what tmux
    actually answered rather than echoing a fixed string."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="trailhead\n"),
    )
    assert tmux_module.Tmux().show_option("$3", "@camp_group") == "trailhead"

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="camp-cli\n"),
    )
    assert tmux_module.Tmux().show_option("$3", "@camp_slug") == "camp-cli"


def test_show_option_answers_none_on_a_non_zero_exit(monkeypatch):
    """An unset option, or a session that no longer exists, both surface as
    a non-zero tmux exit — read back as `None`, never an empty string that
    could be mistaken for a genuinely empty value."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess, "run", lambda *a, **k: _completed(returncode=1)
    )
    assert tmux_module.Tmux().show_option("$99", "@camp_group") is None


def test_install_window_binding_issues_the_exact_argv_shape(monkeypatch):
    """The binding argv this seam builds: server-global (no `-t`), bound to
    `if-shell -F '#{@camp_workspace}'`, whose else-branch is tmux's own real
    compiled-in default for prefix+c — `new-window` — since tmux has no
    revert-to-default primitive."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().install_window_binding('run-shell "camp window-dispatch"')

    assert calls == [
        [
            "tmux",
            "bind-key",
            "-T",
            "prefix",
            "c",
            "if-shell",
            "-F",
            "#{@camp_workspace}",
            'run-shell "camp window-dispatch"',
            "new-window",
        ]
    ]


def test_list_window_binding_reads_the_whole_prefix_table_not_a_per_key_filter(monkeypatch):
    """`list-keys -T prefix c` is NOT a per-key filter on real tmux (there
    is no such flag) — it answers empty every time, which would silently
    defeat the first-install/re-install distinction. Pinned here as the
    exact argv this method must issue: the whole table, no trailing key
    token — confirmed against a real tmux 3.7c server in
    test_window_binding_end_to_end.py."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0, stdout="bind-key -T prefix c new-window\n")

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    result = tmux_module.Tmux().list_window_binding()

    assert calls == [["tmux", "list-keys", "-T", "prefix"]]
    assert result == "bind-key -T prefix c new-window\n"


def test_display_message_targets_a_raw_session_id_with_no_qualification(monkeypatch):
    """The refusal surface a detached `run-shell` dispatch has: addresses
    the client attached to *target* verbatim, never `=`-qualified — proven
    across two distinct messages so this is not a fixed-string echo."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().display_message("$3", "camp: refused — outside workspace")
    tmux_module.Tmux().display_message("$3", "camp: refused — credential store")

    assert calls == [
        ["tmux", "display-message", "-t", "$3", "camp: refused — outside workspace"],
        ["tmux", "display-message", "-t", "$3", "camp: refused — credential store"],
    ]


def test_reset_window_binding_issues_the_stock_bind_key_argv(monkeypatch):
    """Removal re-installs tmux's own compiled-in default literally —
    `bind-key -T prefix c new-window`, server-global (no `-t`) — since tmux
    has no revert-to-default primitive to call instead. Same argv shape
    `install_window_binding` uses, minus the `if-shell` wrapping."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().reset_window_binding()

    assert calls == [["tmux", "bind-key", "-T", "prefix", "c", "new-window"]]


def test_reset_window_binding_returns_none_when_tmux_is_unreachable(monkeypatch):
    import camp.launch.tmux as tmux_module

    def fake_run(argv, **kwargs):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    assert tmux_module.Tmux().reset_window_binding() is None
