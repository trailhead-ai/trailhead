"""Who am I — the id of the harness session camp itself is running inside.

Distinct from every other session lookup in camp: there is no harness object to
ask and no store to read, because the question is about the CURRENT process's
environment. The harness publishes its session id there on launch; camp reads it
back out.

An absent id is ``None`` rather than an error — refusing is the caller's call, and
each caller words its refusal in its own verb's terms.
"""

from __future__ import annotations

import os

#: Where the harness publishes the id of the running session. camp resolves it
#: generically here (rather than in a harness module) because it is read from the
#: environment camp itself is running in — there is no harness object to ask.
SESSION_ID_ENV_VARS = ("CLAUDE_CODE_SESSION_ID",)


def current_session_id(env: dict[str, str] | None = None) -> str | None:
    """Return the running session's id, or None when none is exported."""
    source = env if env is not None else os.environ
    for name in SESSION_ID_ENV_VARS:
        value = source.get(name)
        if value:
            return value
    return None
