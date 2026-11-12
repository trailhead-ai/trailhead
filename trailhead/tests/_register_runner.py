"""Test entry for the registration step: ``_register_runner.py <port> <cli argv...>``.

Runs the real ``trailhead`` CLI ``main`` with the registration step pointed at a
stub Outpost port (the production port is a constant the caller cannot set).
Refuses any subprocess, so the step can only find the harness through /proc.
"""

import os
import subprocess
import sys


def _no_subprocess(*args, **kwargs):
    raise AssertionError("the registration step started a subprocess")


subprocess.Popen = _no_subprocess

import trailhead.harness  # noqa: E402

if os.environ.get("REGISTER_RUNNER_NO_HARNESS"):
    trailhead.harness._HARNESSES.clear()

import trailhead.gateway.register as register  # noqa: E402
from trailhead.cli import main  # noqa: E402

register.PORT = int(sys.argv[1])
sys.argv = ["trailhead", *sys.argv[2:]]
sys.exit(main())
