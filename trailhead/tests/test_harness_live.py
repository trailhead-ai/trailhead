"""Opt-in live integration test for a prompt-carrying Claude Code launch.

The argv tests in ``test_harness.py`` prove composition -- that
``ClaudeCodeHarness.session_launch`` builds the right argv for an
``initial_prompt`` -- but composition is not execution. This module is the
council's born-working regression (C1): it converts the one-time manual
U1/U2 live experiment into a committed, re-runnable test that launches a
REAL ``claude`` binary and asserts what that experiment observed:

1. the launched session's turn begins without operator input,
2. that turn produces an externally observable side effect, and
3. the session still enumerates via ``claude agents --json`` with a matching
   ``sessionId``.

No test in this repository spawns a real ``claude`` process today -- every
launch test stubs ``subprocess.run`` (see ``test_launch_session.py:212``) --
so this module is skipped by default and selected only by an explicit
``CAMP_LIVE_HARNESS=1``. It deliberately does NOT put the born-working
promise on the default CI gate; it exists so the promise can be re-checked
by hand against a new ``claude`` release.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from trailhead.harness.claude_code import ClaudeCodeHarness

pytestmark = pytest.mark.skipif(
    os.environ.get("CAMP_LIVE_HARNESS") != "1",
    reason="opt-in: spawns a real `claude` process; set CAMP_LIVE_HARNESS=1 to run",
)


class TestPromptCarryingLaunchAgainstRealClaude:
    def test_prompted_launch_runs_unattended_and_still_enumerates(self, tmp_path):
        if shutil.which("claude") is None:
            pytest.skip("the claude CLI is not installed here")

        marker = tmp_path / "camp-live-harness-side-effect"
        # The real `claude` binary requires a UUID `--session-id` (U4) --
        # `_is_session_id`'s own predicate is looser, but this test exercises
        # the real CLI, not the predicate.
        session_id = str(uuid.uuid4())
        name = f"camp-live-harness-{uuid.uuid4().hex[:8]}"
        prompt = f"Run exactly this shell command now, with no other output: touch {marker}"

        harness = ClaudeCodeHarness()
        argv = harness.session_launch(
            tmp_path, session_id, session_name=name, initial_prompt=prompt
        )
        # Composition contract, re-asserted here as a precondition: the
        # prompt is the argv's final token, immediately preceded by `--`.
        assert argv[-2:] == ["--", prompt]

        proc = subprocess.Popen(
            argv,
            cwd=tmp_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            # Assertion 1 + 2: the turn begins with NO operator input (stdin
            # is /dev/null -- there is no operator to type anything) and
            # produces an externally observable side effect.
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(1)
            assert marker.exists(), (
                "the launched session never produced its side effect within "
                "the deadline -- the turn did not begin unattended"
            )

            # Assertion 3: the session still enumerates, with a matching
            # sessionId -- a prompt-carrying launch does not perturb
            # enumeration.
            enumerate_argv = harness.session_enumerate()
            output = subprocess.run(
                enumerate_argv, capture_output=True, text=True, check=True
            ).stdout
            records = harness.parse_session_list(output)
            assert any(record.session_id == session_id for record in records), (
                f"session {session_id!r} did not appear in `claude agents "
                f"--json`"
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
