"""Test-only: aim the real ``trailhead gateway register`` at a stub or temp Outpost port.

The production port is a constant the caller cannot set. A test pins the stand-in
interpreter ``register_port_interpreter`` writes, which puts this directory on PYTHONPATH
and sets ``TRAILHEAD_TEST_REGISTER_PORT``; Python imports this module at startup and moves the constant.
"""

import os
import sys

_port = os.environ.get("TRAILHEAD_TEST_REGISTER_PORT")
if _port:
    sys.path.insert(0, os.environ["TRAILHEAD_TEST_REPO_ROOT"])
    import trailhead.gateway.register as _register

    _register.PORT = int(_port)
