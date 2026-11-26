#!/usr/bin/env python3
"""bin/_gateway_auth_header.py — the Outpost plugin's headersHelper, run through the pinned
``trailhead-gateway-auth-header`` shim.

Puts the repo root on sys.path so that `import trailhead` resolves self-relatively, then runs
trailhead.gateway.command.auth_header_main(), which prints the session's Authorization header
as JSON (or ``{}``) and exits 0. It is a separate shim from ``outpost-gateway`` because that
one runs outside the sandbox and must never print a token; this one is not an excluded command, so
a sandboxed call of it cannot read the credential, and only the harness itself (a host process)
runs it to get a header.

Not named trailhead*.py, since its directory is sys.path[0] and such a name would sit under the
package's import name.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trailhead.gateway.command import auth_header_main  # noqa: E402

if __name__ == "__main__":
    sys.exit(auth_header_main())
