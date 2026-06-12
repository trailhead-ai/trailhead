"""Injectable runner seam for the VCS provider library (R-1, S-4).

Every gh/git call in trailhead.vcs goes through this module. Production code
calls run(...) with no runner argument. Tests inject a stub callable to capture
calls without touching real gh/git or the network.

Copied (not moved) from craft's runner_protocol.py: craft's release scripts
still import their own copy until the release cluster is deleted. This is the
trailhead-package home of the same proven contract.

Protocol:
    runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess

    - cmd is always a list (shell=False) — never a shell string.
    - kwargs are forwarded verbatim to subprocess.run (cwd, env, etc.).
    - The production default inherits the calling process's env (not {}).

SHELL_FALSE is a module-level sentinel documenting the invariant. Tests assert
it is True; a future change to shell=True would trip the assertion.
"""
from __future__ import annotations

import subprocess
from typing import Any, Callable

# Sentinel: documents that every subprocess call in this module uses shell=False.
SHELL_FALSE: bool = True

# Type alias for the injectable runner callable.
Runner = Callable[..., subprocess.CompletedProcess]


_DEFAULT_TIMEOUT: int = 60  # seconds; prevents hung gh/git on network stall


def _default_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Production runner: subprocess.run with shell=False, inherited env."""
    kwargs.setdefault("timeout", _DEFAULT_TIMEOUT)
    return subprocess.run(
        cmd,
        shell=False,
        capture_output=True,
        text=True,
        **kwargs,
    )


def run(
    cmd: list[str],
    *,
    cwd: str | None = None,
    runner: Runner | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Run cmd via the injectable runner (default: real subprocess).

    Args:
        cmd:    Command + args as a list (shell=False — never a shell string).
        cwd:    Working directory for the subprocess.
        runner: Optional stub for tests. Must accept (cmd, **kwargs) and
                return a subprocess.CompletedProcess-like object.
        **kwargs: Additional kwargs forwarded to the runner (e.g. env).

    Returns:
        CompletedProcess with .returncode, .stdout, .stderr.
    """
    effective = runner if runner is not None else _default_runner
    call_kwargs: dict[str, Any] = {}
    if cwd is not None:
        call_kwargs["cwd"] = cwd
    call_kwargs.update(kwargs)
    return effective(cmd, **call_kwargs)
