"""Tests for launch/tmux.py — the seam that owns every tmux invocation.

Test contract (see task/promote-camp-s-tmux-boundary-to-a-seam-that-owns-every-target):
- A kill targeting a session name that is a strict prefix of another live
  session acts on the named session only, and the other survives.
- A session created through the seam (`Tmux.spawn_session`) carries the name
  it was given, with no `=` in it — enumerated afterwards and asserted
  byte-for-byte. The `-s`-is-not-a-target half of the `=` property.
- `set_environment` and `switch_client` each address the exactly-named
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


def test_list_windows_carries_both_windows_in_order_including_a_special_name(monkeypatch):
    """Two tab-separated lines come back as a `WindowListing` carrying both
    windows, in tmux's order, with every field split correctly — including a
    window name holding a space and a `|`, which a `|`-delimited split (the
    `list_sessions` pattern) would mis-parse but a tab-separated one, with
    the name last, does not."""
    import camp.launch.tmux as tmux_module

    stdout = (
        "@1\t/home/x/one\tzsh\tfirst\n"
        "@2\t/home/x/two dir\tsleep\treview copy | draft\n"
    )
    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout=stdout),
    )

    result = tmux_module.Tmux().list_windows("x")

    assert isinstance(result, tmux_module.WindowListing)
    assert result.dropped == 0
    assert result.windows == (
        tmux_module.TmuxWindow(
            window_id="@1", current_path="/home/x/one", current_command="zsh", name="first"
        ),
        tmux_module.TmuxWindow(
            window_id="@2",
            current_path="/home/x/two dir",
            current_command="sleep",
            name="review copy | draft",
        ),
    )


def test_list_windows_no_such_session_answers_none_not_unanswered_or_empty(monkeypatch):
    """`can't find session` is an ANSWER — `None` — never `UNANSWERED` and
    never an empty `WindowListing`, which would report a missing session as
    a composed-but-windowless one."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1, stderr="can't find session: x\n"
        ),
    )

    assert tmux_module.Tmux().list_windows("x") is None


def test_list_windows_other_non_zero_exit_is_unanswered(monkeypatch):
    """Any other stderr shape on a non-zero exit — the no-server shape, an
    unsafe-socket message — is `UNANSWERED`, not `None`."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="error connecting to /tmp/x (No such file or directory)\n",
        ),
    )
    assert tmux_module.Tmux().list_windows("x") is tmux_module.UNANSWERED

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=1, stderr="unsafe permissions\n"),
    )
    assert tmux_module.Tmux().list_windows("x") is tmux_module.UNANSWERED


def test_list_windows_unreachable_tmux_is_unanswered(monkeypatch):
    """A `TimeoutExpired` or `OSError` from the runner — tmux never answered
    at all — is `UNANSWERED`, never folded into `None`."""
    import camp.launch.tmux as tmux_module

    def _raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["tmux"], timeout=5)

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise_timeout)
    assert tmux_module.Tmux().list_windows("x") is tmux_module.UNANSWERED

    def _raise_oserror(*a, **k):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'tmux'")

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise_oserror)
    assert tmux_module.Tmux().list_windows("x") is tmux_module.UNANSWERED


def test_list_windows_drops_and_counts_a_row_that_does_not_split_into_four_fields(monkeypatch):
    """A malformed row is excluded from `windows` and counted in `dropped`
    rather than failing the whole answer — the `SessionListing.dropped`
    pattern, extended: a row missing a field entirely (only three of the
    four tab-separated columns) cannot be a real window, so it is dropped
    rather than silently misassigning fields."""
    import camp.launch.tmux as tmux_module

    stdout = "@1\t/home/x/one\tzsh\tfirst\n" "@2\t/home/x/two\tsleep\n"
    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout=stdout),
    )

    result = tmux_module.Tmux().list_windows("x")

    assert isinstance(result, tmux_module.WindowListing)
    assert result.dropped == 1
    assert result.windows == (
        tmux_module.TmuxWindow(
            window_id="@1", current_path="/home/x/one", current_command="zsh", name="first"
        ),
    )


def test_list_windows_argv_is_exact_and_target_is_qualified(monkeypatch):
    """The argv reaching the runner is exactly `["tmux", "list-windows",
    "-t", "=<name>", "-F", <format>]` — the `=` property holds for this
    method too, and the format string is the literal, never an
    interpolation of *name*."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().list_windows("my-workspace")

    assert calls == [
        [
            "tmux",
            "list-windows",
            "-t",
            "=my-workspace",
            "-F",
            tmux_module._LIST_WINDOWS_FORMAT,
        ]
    ]
    assert "my-workspace" not in tmux_module._LIST_WINDOWS_FORMAT


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


def test_new_window_with_reason_returns_the_result_on_success(monkeypatch):
    """`new_window_with_reason` answers the same `NewWindowResult` on exit 0
    that `new_window` does — the richer seam does not change the success
    shape, only what a failure carries."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="@3 alpha\n"),
    )

    result = tmux_module.Tmux().new_window_with_reason(
        "ws", cwd="/tmp/a", window_name="alpha", command=()
    )

    assert result == tmux_module.NewWindowResult(window_id="@3", window_name="alpha")


def test_new_window_with_reason_carries_tmux_stderr_on_a_refusal(monkeypatch):
    """A non-zero exit answers `NewWindowFailure` carrying tmux's own
    stderr verbatim — varied against a second, distinct stderr, so the
    assertion pins that the seam forwards what tmux actually said rather
    than a fixed sentinel."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=1, stderr="can't find session: ws\n"),
    )
    first = tmux_module.Tmux().new_window_with_reason(
        "ws", cwd="/tmp/a", window_name="w", command=()
    )
    assert first == tmux_module.NewWindowFailure(stderr="can't find session: ws\n")

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=1, stderr="server not found\n"),
    )
    second = tmux_module.Tmux().new_window_with_reason(
        "ws", cwd="/tmp/a", window_name="w", command=()
    )
    assert second == tmux_module.NewWindowFailure(stderr="server not found\n")
    assert first != second


def test_new_window_with_reason_unreachable_tmux_answers_unanswered(monkeypatch):
    """An unreachable/timed-out tmux answers `UNANSWERED`, matching
    `new_window`'s own tri-state for the same condition."""
    import camp.launch.tmux as tmux_module

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["tmux"], timeout=5)

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise)

    result = tmux_module.Tmux().new_window_with_reason(
        "ws", cwd="/tmp/a", window_name="w", command=()
    )

    assert result is tmux_module.UNANSWERED


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


def test_display_message_escapes_hash_so_tmux_does_not_expand_the_message(monkeypatch):
    """`display-message` FORMAT-expands its argument: verified against real
    tmux 3.7c that `#{E:SECRETTEST}` in a message is replaced by that
    variable's value from the session environment, and that `##` is tmux's
    own escape for a literal `#`.

    Camp's refusal messages embed operator-influenced text — a workspace
    slug, a group name, a path — so an unescaped `#{...}` in any of them
    would have tmux read camp's own session environment out onto the
    operator's status line. Every `#` is doubled before the message reaches
    tmux; tmux collapses it back, so what the operator READS is unchanged.

    Varied across three messages: one with no `#` at all (must pass through
    byte-identical — the escape is not a blanket rewrite), one with a format
    expression, and one with a bare `#`.
    """
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().display_message("$3", "camp: refused — outside workspace")
    tmux_module.Tmux().display_message("$3", "camp: unknown group '#{E:SECRETTEST}'")
    tmux_module.Tmux().display_message("$3", "camp: slug a#b")

    assert [argv[-1] for argv in calls] == [
        "camp: refused — outside workspace",
        "camp: unknown group '##{E:SECRETTEST}'",
        "camp: slug a##b",
    ]


def test_prefix_window_binding_line_matches_on_the_key_position_not_the_text(monkeypatch):
    """Picking the `c` line out of `list-keys -T prefix` output has one
    trap: tmux's own stock `display-menu` bindings embed bare `c` operands
    inside their menu definitions, so any match on the TEXT ` c ` finds
    those instead. The match is anchored on the key's POSITION — the
    operand immediately after `-T prefix` — and tolerates flags such as
    `-r` that tmux renders between `bind-key` and `-T`.

    Varied across four tables so each rule is exercised by a case that
    would pass without it.
    """
    from camp.launch.tmux import prefix_window_binding_line

    menu_line = 'bind-key    -T prefix >       display-menu -T "x" c { set-buffer "y" }'
    custom = 'bind-key    -T prefix c       new-window -c "#{pane_current_path}"'
    repeating = 'bind-key -r -T prefix c       new-window'
    other_key = "bind-key    -T prefix n       next-window"

    assert prefix_window_binding_line(f"{menu_line}\n{custom}\n{other_key}\n") == custom
    assert prefix_window_binding_line(f"{other_key}\n{repeating}\n") == repeating
    # A table holding the menu line but NO `c` binding answers None — this
    # is the case a text match gets wrong.
    assert prefix_window_binding_line(f"{menu_line}\n{other_key}\n") is None
    assert prefix_window_binding_line("") is None


def test_server_option_argv_is_global_and_round_trips_the_value(monkeypatch):
    """The captured binding outlives the workspace that captured it but must
    die with the tmux server, which is exactly a server-global user option's
    lifetime. `-g` here is deliberate and the opposite of `set_option`'s
    session-local contract, so it is a separate method rather than a flag."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0, stdout="bind-key -T prefix c new-window\n")

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux = tmux_module.Tmux()
    tmux.set_server_option("@camp_prior", "bind-key -T prefix c new-window")
    value = tmux.show_server_option("@camp_prior")
    tmux.unset_server_option("@camp_prior")

    assert calls == [
        ["tmux", "set-option", "-g", "@camp_prior", "bind-key -T prefix c new-window"],
        ["tmux", "show-options", "-gv", "@camp_prior"],
        ["tmux", "set-option", "-gu", "@camp_prior"],
    ]
    assert value == "bind-key -T prefix c new-window"


def test_show_server_option_answers_none_for_an_option_that_was_never_set(monkeypatch):
    """tmux answers a non-zero exit and `invalid option` for an unset user
    option (confirmed on 3.7c) — that is "nothing captured", not a value."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda argv, **kw: _completed(returncode=1, stderr="invalid option: @camp_prior"),
    )

    assert tmux_module.Tmux().show_server_option("@camp_prior") is None


def test_source_command_hands_the_command_to_tmux_on_stdin(monkeypatch):
    """Restoring a captured binding means re-running a command string tmux
    itself wrote. `source-file -` makes TMUX re-parse it with its own
    grammar, which is the only parser guaranteed to agree with the one that
    produced it — camp never re-tokenizes the line itself."""
    import camp.launch.tmux as tmux_module

    seen: list[dict] = []

    def fake_run(argv, **kwargs):
        seen.append({"argv": list(argv), "input": kwargs.get("input")})
        return _completed(returncode=0)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().source_command('bind-key -T prefix c new-window -c "#{pane_current_path}"')
    tmux_module.Tmux().source_command("unbind-key -T prefix c")

    assert [d["argv"] for d in seen] == [["tmux", "source-file", "-"], ["tmux", "source-file", "-"]]
    assert [d["input"] for d in seen] == [
        'bind-key -T prefix c new-window -c "#{pane_current_path}"\n',
        "unbind-key -T prefix c\n",
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


def test_new_session_with_window_argv_carries_every_operand_in_order(monkeypatch):
    """The argv issued by `new_session_with_window` carries `-d -s <name>
    -n <window_name> -c <cwd> -P -F` and the command tokens verbatim, in
    that order — the exact shape the task's Delivers names."""
    import camp.launch.tmux as tmux_module

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _completed(returncode=0, stdout="@0 first\n")

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    tmux_module.Tmux().new_session_with_window(
        "ws-1",
        cwd="/tmp/ws",
        window_name="first",
        command=["sleep", "30"],
    )

    assert calls == [
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            "ws-1",
            "-n",
            "first",
            "-c",
            "/tmp/ws",
            "-P",
            "-F",
            "#{window_id} #{window_name}",
            "sleep",
            "30",
        ]
    ]


def test_new_session_with_window_a_name_holding_spaces_round_trips_whole(monkeypatch):
    """A window name holding spaces round-trips whole through the
    first-space parse, the same as `new_window`'s: two different names,
    two different answers, neither truncated at an internal space."""
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="@0 my window\n"),
    )
    first = tmux_module.Tmux().new_session_with_window(
        "ws-1", cwd="/tmp/ws", window_name="my window", command=()
    )
    assert first.window_id == "@0"
    assert first.window_name == "my window"

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="@1 other window\n"),
    )
    second = tmux_module.Tmux().new_session_with_window(
        "ws-1", cwd="/tmp/ws", window_name="other window", command=()
    )
    assert second.window_id == "@1"
    assert second.window_name == "other window"

    assert first.window_name != second.window_name


def test_new_session_with_window_exit_zero_answers_new_window_result(monkeypatch):
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="@0 first\n"),
    )

    result = tmux_module.Tmux().new_session_with_window(
        "ws-1", cwd="/tmp/ws", window_name="first", command=()
    )

    assert result == tmux_module.NewWindowResult(window_id="@0", window_name="first")


def test_new_session_with_window_duplicate_marker_answers_duplicate_sentinel(monkeypatch):
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1, stderr="duplicate session: ws-1"
        ),
    )

    result = tmux_module.Tmux().new_session_with_window(
        "ws-1", cwd="/tmp/ws", window_name="first", command=()
    )

    assert result is tmux_module.DUPLICATE


def test_new_session_with_window_other_stderr_answers_failure_carrying_it_verbatim(
    monkeypatch,
):
    import camp.launch.tmux as tmux_module

    monkeypatch.setattr(
        tmux_module.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=1, stderr="some other tmux refusal"),
    )

    result = tmux_module.Tmux().new_session_with_window(
        "ws-1", cwd="/tmp/ws", window_name="first", command=()
    )

    assert result is not tmux_module.DUPLICATE
    assert result.stderr == "some other tmux refusal"


def test_new_session_with_window_timeout_expired_propagates_uncaught(monkeypatch):
    """Exceptions from the spawn propagate exactly as `spawn_session`'s
    do — this is the server-starting call and the caller already folds
    them; the seam must not swallow them into a tri-state answer."""
    import camp.launch.tmux as tmux_module

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["tmux"], timeout=5)

    monkeypatch.setattr(tmux_module.subprocess, "run", _raise)

    with pytest.raises(subprocess.TimeoutExpired):
        tmux_module.Tmux().new_session_with_window(
            "ws-1", cwd="/tmp/ws", window_name="first", command=()
        )
