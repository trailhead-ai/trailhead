"""Test entry for the gateway command: ``_gateway_runner.py <port> <env-dump> <argv...>``.

Runs the command's ``main`` against a stub Outpost port, refuses any subprocess,
(with GATEWAY_RUNNER_NO_HARNESS set, no process counts as a harness, as in the sandbox)
and writes ``os.environ`` as it stands after the call to *env-dump*.
"""

import json
import os
import subprocess
import sys


def _no_subprocess(*args, **kwargs):
    raise AssertionError("the gateway command started a subprocess")


subprocess.Popen = _no_subprocess

import trailhead.harness  # noqa: E402

if os.environ.get("GATEWAY_RUNNER_NO_HARNESS"):
    trailhead.harness._HARNESSES.clear()

from trailhead.gateway.command import main  # noqa: E402

port, env_dump, argv = int(sys.argv[1]), sys.argv[2], sys.argv[3:]
rc = main(argv, port=port)
with open(env_dump, "w") as fh:
    json.dump(dict(os.environ), fh)
sys.exit(rc)
