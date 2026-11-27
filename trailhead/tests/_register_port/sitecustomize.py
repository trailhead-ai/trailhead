"""Test-only: aim the `trailhead gateway register` and the gateway command and header entries of a trailhead checkout
from before the gateway socket at a stub or temp Outpost's TCP port.

Such a checkout fixes the port in a constant its caller cannot set. `register_port_interpreter` writes a stand-in
interpreter for install to pin, and `step_env` sets the same variables for the test's own steps; both put this
directory on PYTHONPATH and set `TRAILHEAD_TEST_REGISTER_PORT` and `TRAILHEAD_TEST_REPO_ROOT`. Python imports this
module at startup and moves the constant (and the gateway entries' default port).
"""

import os
import sys

_port = os.environ.get("TRAILHEAD_TEST_REGISTER_PORT")
if _port:
    sys.path.insert(0, os.environ["TRAILHEAD_TEST_REPO_ROOT"])
    import trailhead.gateway.register as _register

    _register.PORT = int(_port)

    import trailhead.gateway.command as _command

    _command.main.__kwdefaults__["port"] = int(_port)
    _command.auth_header_main.__kwdefaults__["port"] = int(_port)
