"""Tests for launch/tmux.py — the seam that owns every tmux invocation.

Test contract (see task/promote-camp-s-tmux-boundary-to-a-seam-that-owns-every-target):
- A kill targeting a session name that is a strict prefix of another live
  session acts on the named session only, and the other survives.
- A session created through the seam (`Tmux.spawn_session`) carries the name
  it was given, with no `=` in it — enumerated afterwards and asserted
  byte-for-byte. The `-s`-is-not-a-target half of the `=` property.
- `capture_pane` and `set_environment` each address the exactly-named
  session, never one it prefixes.
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
