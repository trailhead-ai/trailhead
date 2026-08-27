"""Best-effort vault refresh run immediately before camp spawns a session.

Why it exists
-------------
A plan, task, or ADR authored on one machine only reaches another machine's
session if that machine's vault has been pulled. Doing it inside the session
costs the agent's tokens; doing it by hand costs a login. Doing it here costs
neither: the launch engine is the one choke point every session passes through,
and it already blocks briefly on the tmux spawn.

Contract & invariants
---------------------
* **Read-only, always.** The argv is exactly ``lore sync --pull-only`` — the
  same read-only route outpost's daemon shells for its fast-forward. No vault
  narrowing: lore's own configuration decides which vaults exist, and every one
  of them is worth being current. Nothing here can stage, commit, or push.
* **Best-effort, never fatal.** Every outcome is a token, never an exception:
  ``pulled``, ``skipped`` (no ``lore`` on PATH), ``failed``, ``timed-out``. A
  session that could not refresh its lore is still a session worth launching.
* **Bounded.** ``communicate(timeout=…)`` caps the wait. The bound must exceed
  lore's own 60s per-git-call cap for a slow-but-succeeding pull, yet a launch
  is interactive — so the default sits below it and a genuinely slow remote is
  abandoned rather than waited out.
* **Signals reach the GROUP.** lore has no signal handling anywhere, so a
  SIGTERM to its pid alone would leave its ``git`` child orphaned and free to
  keep mutating the vault after camp has moved on. The child is spawned
  ``start_new_session=True`` (making it a process-group leader) and a timeout
  signals the GROUP — SIGTERM, then SIGKILL after a bounded grace.
* **Streams are captured, never surfaced.** On an unreachable remote lore's
  stderr embeds the remote path/URL verbatim; the outcome token carries no part
  of it.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time

#: Bound on one `lore sync --pull-only`. Under lore's own 60s per-git-call cap
#: on purpose: this runs in front of an interactive launch, so a remote slow
#: enough to approach that cap is abandoned rather than waited out.
DEFAULT_TIMEOUT_SECONDS = 30

#: How long a SIGTERM'd group is given to exit before SIGKILL.
GRACE_PERIOD_SECONDS = 5

#: The one argv. Spelled as a constant so a test can assert on the exact list
#: the module can ever hand to a subprocess.
PULL_ARGV = ["lore", "sync", "--pull-only"]


def pull_lore(
    *,
    env: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Fetch and integrate origin for every configured vault. Never raises.

    Returns one of ``pulled`` / ``skipped`` / ``failed`` / ``timed-out``.
    """
    if shutil.which("lore") is None:
        return "skipped"

    try:
        child = subprocess.Popen(
            PULL_ARGV,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
            env=env,
        )
    except Exception:  # noqa: BLE001 — best-effort; a launch outlives this
        return "failed"

    try:
        child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_group(child)
        return "timed-out"
    except Exception:  # noqa: BLE001 — best-effort; a launch outlives this
        return "failed"

    return "pulled" if child.returncode == 0 else "failed"


def _terminate_group(child) -> None:
    """SIGTERM the child's process group, then SIGKILL it after the grace period.

    Every signal is best-effort: the group may already be gone, in which case
    there is nothing to do and nothing to report.
    """
    for sig, delay in ((signal.SIGTERM, GRACE_PERIOD_SECONDS), (signal.SIGKILL, 0)):
        try:
            os.killpg(child.pid, sig)
        except Exception:  # noqa: BLE001 — the group may already be gone
            return
        if delay:
            time.sleep(delay)
