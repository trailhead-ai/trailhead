"""Shell-safe name confinement shared by every ranger boundary that turns an
externally-elected name (a vault, a camp group) into a path segment or into
an operator-facing shell command string.

Two different dangers, one check. Path-segment confinement (no separators,
no ``..``) is necessary to keep a name from escaping the directory it names a
child of, but it is not sufficient: a vault name is also rendered, verbatim,
into two shell command strings this project tells the operator to paste and
run themselves — ``report._build_answer_command`` (``lore record update
<id> --vault <vault> --diff <<'EOF' ...``) and ``lock._stale_removal_message``
(``rm <path>``, where ``<path>``'s final segment is the vault name). A name
containing a backtick, ``$()``, a semicolon, a space, or a quote passes
separator/``..`` confinement cleanly and then executes in the operator's own
shell the moment they follow the report's instructions.

So every name that reaches either a path segment or a rendered command is
validated once, here, against an explicit allowlist — alphanumerics, ``.``,
``_``, ``-``, and nothing else — rather than a blocklist of "known-bad"
characters, which is exactly the kind of check a creative name defeats. The
allowlist subsumes the separator/``..`` check it replaces: neither a path
separator nor a lone/leading ``.`` run can ever match it.
"""

from __future__ import annotations

import re

#: Alphanumeric-leading, then alphanumerics/``.``/``_``/``-`` only. No path
#: separators, no shell metacharacters (space, quote, backtick, ``$``,
#: ``;``, newline, or anything else outside the set), and no ``..`` segment
#: (its leading ``.`` can never match the required alphanumeric first
#: character).
SHELL_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_shell_safe_name(name: str, *, what: str) -> None:
    """Raise ``ValueError`` if *name* is not confined to the shell-safe allowlist.

    *what* names the kind of value in the message (``"vault name"``,
    ``"group"``) so the refusal reads clearly at each call site, which wraps
    this in its own domain error.
    """
    if not name:
        raise ValueError(f"{what} must not be empty")
    if not SHELL_SAFE_NAME_RE.match(name):
        raise ValueError(
            f"{what} {name!r} contains characters outside the allowed set "
            f"({SHELL_SAFE_NAME_RE.pattern}) — refusing to use it in a path "
            "or an operator-facing command"
        )
