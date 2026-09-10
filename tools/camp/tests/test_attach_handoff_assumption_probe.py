"""EPHEMERAL assumption probe for task/the-attach-handoff-replaces-the-process-behind-an-injected-seam.

Not a permanent test -- resolves the third Known Unknown on
task/attaching-reaches-a-running-session-on-any-machine before Task 3 is built.
The executor should delete this whole file once Task 3 lands its own
test_attach_handoff.py.

Four questions, four test groups:

1. Does an exec survive camp's dispatch -- nothing buffered, nothing owed after
   the call?
2. Is `ssh -t <destination> <camp_bin> <ref>` argv safe, given the transport's
   existing shlex.quote-and-join reused verbatim for an interactive `-t` call?
3. Does a missing local binary surface as OSError (matching the
   provision/tasks.py:183-187 catch and the repo's `camp: <message>` shape)?
4. Does an injected-callable seam keep this testable, and what can such a test
   never observe?

No real remote host is contacted anywhere in this file.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.host.config import Host  # noqa: E402


# ---------------------------------------------------------------------------
# Q1 - does an exec survive camp's dispatch cleanly?
#
# Traced statically first: cli/camp's shim is `sys.exit(dispatch.main())`;
# dispatch.main() has no try/finally around its verb dispatch and ends with a
# bare `_spine_main()` call (dispatch.py:652-653, the last statement in
# main()); spine.main()'s `kill` branch (spine.py:1282-1285) calls
# `_cmd_kill_cli(rest)` as its last statement with no wrapping try/finally;
# grep for atexit across the whole camp package returns nothing. So an
# os.execvp from inside a verb handler has no pending context manager,
# atexit hook, or "after the call" statement to lose - PROVIDED the process's
# own stdout/stderr buffers are flushed first, since exec discards process
# memory (including unflushed C-level stdio buffers) rather than draining it.
# That proviso is proven dynamically below, both ways.
# ---------------------------------------------------------------------------

_DISPATCH_SHAPE = textwrap.dedent(
    """
    import os, sys

    def verb_handler():
        # Mirrors _cmd_kill_cli / a future _cmd_attach_cli: prints, then hands
        # off via exec as the LAST statement, no try/finally around it.
        print("PRE-EXEC-OUTPUT", end="")
        if {flush}:
            sys.stdout.flush()
        os.execvp(sys.argv[1], [sys.argv[1], "EXEC-MARKER"])
        # Unreachable if exec succeeds - written so a regression that makes
        # exec NOT replace the process is caught by AFTER-EXEC leaking out.
        print("AFTER-EXEC-SHOULD-NEVER-APPEAR")

    def spine_main():
        verb_handler()
        # Nothing follows verb_handler() in the real spine.main() kill branch.

    def dispatch_main():
        spine_main()
        # Nothing follows _spine_main() in the real dispatch.main().

    dispatch_main()
    """
)


def _run_dispatch_shape(tmp_path: Path, *, flush: bool) -> subprocess.CompletedProcess:
    script = tmp_path / "dispatch_shape.py"
    script.write_text(_DISPATCH_SHAPE.format(flush=flush))
    return subprocess.run(
        [sys.executable, "-B", str(script), "echo"],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_exec_replaces_process_when_output_is_flushed_first(tmp_path):
    """The seam's contract: flush before exec, and nothing after the call runs."""
    result = _run_dispatch_shape(tmp_path, flush=True)
    assert result.returncode == 0
    # Both the pre-exec print and the exec'd program's own output arrived,
    # nothing after the exec ran, and no lost bytes.
    assert result.stdout == "PRE-EXEC-OUTPUTEXEC-MARKER\n"
    assert "AFTER-EXEC-SHOULD-NEVER-APPEAR" not in result.stdout


def test_unflushed_output_is_lost_across_exec(tmp_path):
    """Documents WHY the seam must flush: exec discards unflushed buffers.

    This is the negative case - proves the flush in the test above is load
    bearing, not incidental. A seam that prints anything before handoff and
    forgets to flush silently drops it.
    """
    result = _run_dispatch_shape(tmp_path, flush=False)
    assert result.returncode == 0
    assert "PRE-EXEC-OUTPUT" not in result.stdout
    assert result.stdout == "EXEC-MARKER\n"


# ---------------------------------------------------------------------------
# Q2 - is the remote argv right, and is the quote-and-join reusable verbatim
# for an interactive `-t` invocation?
#
# Mechanically confirmed via the OpenSSH manual (fetched, not asserted here):
# "If supplied, the arguments will be appended to the command, separated by
# spaces, before it is sent to the server to be executed" - i.e. ssh joins
# trailing argv with a single space and hands the joined string to the far
# side's login shell. That means an unquoted reference containing shell
# metacharacters is remote code execution unless every token is quoted before
# the join - which is exactly what transport.py:228-230 already does:
#     " ".join(shlex.quote(part) for part in (host.camp_bin, *remote_argv))
#
# The test below reuses that exact expression (not a second implementation)
# and proves it survives a local shell standing in for the far side, for a
# battery of metacharacters - never touching a real remote host.
# ---------------------------------------------------------------------------

_HOST = Host(ssh="andromeda", camp_bin="/opt/camp/bin/camp")

_METACHARACTER_BATTERY = [
    "camp-foo; rm -rf /tmp/should-not-run",
    "camp-foo$(touch /tmp/should-not-run)",
    "camp-foo`touch /tmp/should-not-run`",
    "camp-foo\nrm -rf /tmp/should-not-run",
    "camp-foo'with\"quotes",
]


def _quote_and_join(camp_bin: str, remote_argv: list[str]) -> str:
    """The exact expression at host/transport.py:228-230, reused verbatim."""
    return " ".join(shlex.quote(part) for part in (camp_bin, *remote_argv))


@pytest.mark.parametrize("hostile_ref", _METACHARACTER_BATTERY)
def test_quote_and_join_survives_a_local_shell_standing_in_for_the_far_side(
    hostile_ref, tmp_path
):
    """ssh joins trailing argv with a space and hands it to the remote shell.

    Standing in for that remote shell with a LOCAL `sh -c`, never a real host:
    the joined, quoted command must parse back to the exact original argv
    (proving no metacharacter is interpreted) and must not have executed
    anything extra (proving no injection).
    """
    sentinel = tmp_path / "should-not-exist"

    # A stand-in for the far side's camp_bin: an executable that dumps its
    # own argv, one per line, to a recording file. camp_bin in the real argv
    # is a path the remote shell looks up and runs; here that path IS this
    # script, so whatever argv the far-side shell hands it is observable.
    fake_camp_bin = tmp_path / "fake_camp"
    recording = tmp_path / "recorded_argv"
    fake_camp_bin.write_text(
        "#!/bin/sh\nfor a in \"$@\"; do printf '%s\\0' \"$a\"; done > "
        + shlex.quote(str(recording)) + "\n"
    )
    fake_camp_bin.chmod(0o755)

    remote_argv = ["attach", hostile_ref]
    joined = _quote_and_join(str(fake_camp_bin), remote_argv)

    # This mirrors run_camp's own ssh_argv assembly (host/transport.py:230-239)
    # with -t added for the interactive case, minus the network hop: ssh hands
    # `joined` to the far side's login shell to parse and execute as a single
    # command line (confirmed against the OpenSSH manual: trailing arguments
    # "will be appended to the command, separated by spaces, before it is
    # sent to the server to be executed"). We hand it to a LOCAL `sh -c`
    # instead, which is the task's mandated stand-in for the far side.
    result = subprocess.run(
        ["sh", "-c", joined],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(tmp_path),
    )
    # Nothing extra executed (no sentinel file materialized from `;`, `$()`,
    # backticks, or a smuggled newline breaking out of the intended command).
    assert not sentinel.exists()
    assert result.returncode == 0, result.stderr

    # And the far side's camp invocation received exactly the original
    # remote_argv, byte for byte, as separate arguments - never re-split,
    # substituted, or truncated by the metacharacters.
    received = recording.read_bytes().split(b"\x00")[:-1]
    assert received == [a.encode() for a in remote_argv]


def test_interactive_ssh_argv_shape_matches_the_documented_argv():
    """`ssh -t <destination> <camp_bin> <ref>` - the argv the design doc names,
    built the same way run_camp builds its own (host/transport.py:230-239),
    differing only by the added `-t`.
    """
    ref = "camp-my-session-abcd1234"
    remote_argv = ["attach", ref]
    joined = _quote_and_join(_HOST.camp_bin, remote_argv)

    connect_timeout = 10.0
    ssh_argv = [
        "ssh",
        "-t",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"ConnectTimeout={connect_timeout:g}",
        _HOST.ssh,
        joined,
    ]

    assert ssh_argv[:2] == ["ssh", "-t"]
    assert ssh_argv[-2] == _HOST.ssh
    assert shlex.split(ssh_argv[-1]) == [_HOST.camp_bin, "attach", ref]
    # The three fixed options the listing transport pins are all present,
    # unmodified - same identity + auth posture, only the terminal differs.
    assert "-o BatchMode=yes" in " ".join(ssh_argv)
    assert "-o StrictHostKeyChecking=yes" in " ".join(ssh_argv)
    assert f"-o ConnectTimeout={connect_timeout:g}" in " ".join(ssh_argv)


# ---------------------------------------------------------------------------
# Q3 - process-start failure shape: does a missing binary raise OSError, and
# is the repo's `camp: <message>` / non-zero-exit shape producible from it?
# ---------------------------------------------------------------------------


def test_execvp_of_a_missing_binary_raises_oserror():
    """provision/tasks.py:183-187 catches `OSError` for a failed process
    start (subprocess.run's FileNotFoundError, a subclass of OSError). An
    execvp-based seam needs the same guarantee for a genuinely missing
    camp_bin - proven here by actually calling execvp, not by inference.
    """
    with pytest.raises(OSError) as excinfo:
        os.execvp("camp-definitely-does-not-exist-anywhere-xyz", ["camp-definitely-does-not-exist-anywhere-xyz"])
    assert isinstance(excinfo.value, FileNotFoundError)  # OSError subclass


def test_missing_binary_produces_the_repo_refusal_shape(tmp_path):
    """A seam that catches OSError around os.execvp and formats it the way
    spine.py's `_die` and provision/tasks.py already do - `camp: <message>`
    on stderr, non-zero exit - actually running end to end as a subprocess
    (so exit code and stream separation are the real OS-level values, not
    an in-process pytest.raises approximation).
    """
    script = tmp_path / "missing_binary_seam.py"
    script.write_text(
        textwrap.dedent(
            """
            import os, sys
            try:
                os.execvp("camp-definitely-does-not-exist-anywhere-xyz", ["camp-definitely-does-not-exist-anywhere-xyz"])
            except OSError as exc:
                print(f"camp attach: {exc}", file=sys.stderr)
                sys.exit(1)
            """
        )
    )
    result = subprocess.run(
        [sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("camp attach: ")


# ---------------------------------------------------------------------------
# Q4 - an injected-callable seam keeps this testable; name what it can never
# observe.
# ---------------------------------------------------------------------------


def test_injected_seam_records_argv_without_ever_execing():
    """The shape Task 3's contract needs: a callable seam (mirroring the
    `Tmux` seam at launch/stop.py:158 and the `Runner` seam at
    host/transport.py:154) that production code points at os.execvp, and
    that a test points at a recorder - so the test can assert on the argv
    that WOULD be exec'd without the test process ever disappearing.
    """
    calls: list[list[str]] = []

    def fake_exec_seam(argv: list[str]) -> None:
        calls.append(list(argv))
        # Deliberately does NOT call os.execvp - this is the whole point:
        # the test process survives to make its assertion.

    def handoff(seam, argv: list[str]) -> None:
        seam(argv)
        # Real code: unreachable after a real os.execvp. Here, reachable,
        # because the fake seam returned instead of replacing the process -
        # which is itself the one thing this kind of test can never observe.

    handoff(fake_exec_seam, ["ssh", "-t", "andromeda", "/opt/camp/bin/camp attach ref"])

    assert calls == [["ssh", "-t", "andromeda", "/opt/camp/bin/camp attach ref"]]
    # What this test can never observe, by construction: that the real
    # os.execvp call succeeds and actually replaces the process image, or
    # that the terminal/pty genuinely reaches the far-side multiplexer. That
    # is the operator-attestation half (AC35) - no injected-seam test can
    # substitute for it, because a test that actually exec'd would be gone.
