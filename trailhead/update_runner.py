"""`trailhead update --detach --json`: start an update as its own background job.

The job is ``<checkout>/bin/trailhead update --yes --run-id <id>`` with a run
id generated here, started so that it does not die with the caller (Outpost's
request handler, which may itself be restarted by the update). The run lock is
read first, so a request during a running update starts nothing; after the
start the lock is read again for up to ``wait_seconds`` to learn what became
of the job. The checkout is the install checkout the provenance stamp names.

Three ways to start the job, chosen with the predicate the Outpost lifecycle
uses for "is a host supervisor managing Outpost" (``_is_supervised``):

* Supervised, Linux: ``systemd-run --user --unit=trailhead-update-<id>
  --collect``. The job lives in its own cgroup under the user manager, so
  restarting ``outpost.service`` does not touch it. A transient unit does NOT
  inherit the caller's environment, so every caller variable is forwarded as a
  name-only ``--setenv=NAME`` argv element and ``systemd-run`` itself runs with
  the caller's environment, from which it reads each value. No environment
  value appears in any argv element, so none is visible in the process table.
  The working directory is the checkout
  (``--working-directory``), not the manager's ``$HOME``. ``--collect`` removes
  the unit after it exits; the run lock and ``update-result.json`` are the
  record of the run, never the unit.
* Supervised, macOS: a one-shot launchd job. The job definition is written with
  ``plistlib`` to ``state_dir("trailhead")/update-job.plist`` (mode 0600; it
  carries the environment) under the label ``com.trailhead.update``, distinct
  from ``com.trailhead.outpost``, with ``RunAtLoad`` and no ``KeepAlive``, and
  loaded with ``launchctl bootstrap gui/<uid> <plist>``. Not ``launchctl
  submit``, which cannot carry an environment. The plist is not in
  ``~/Library/LaunchAgents``, so nothing re-runs it at login. Cleanup: the next
  detach first runs ``launchctl bootout gui/<uid>/com.trailhead.update`` for the
  previous run's finished job. UNPROVEN on a real Mac (task K2): this path is
  only exercised against an injected runner.
* Unsupervised: a child in its own session (``start_new_session=True``), stdio
  detached, output appended to ``state_dir("trailhead")/update-run.log``.

Every job is started from an argv list, never through a shell. The job's output
goes only to ``update-run.log``; this module never echoes it.

The answer is one of ``{"started": true, "run_id"}``,
``{"started": false, "running": true, "run_id": <holder>}`` and
``{"started": false, "running": false, "error": "could_not_start"}``.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from trailhead import outpost_lifecycle, outpost_supervisor, update_run
from trailhead.paths import ensure_dir, state_dir
from trailhead.provenance import read_stamp_with_reason

WAIT_SECONDS = 10.0
POLL_SECONDS = 0.1
LAUNCHD_LABEL = "com.trailhead.update"
_LOG_NAME = "update-run.log"
_PLIST_NAME = "update-job.plist"


def _could_not_start() -> dict:
    return {"started": False, "running": False, "error": "could_not_start"}


def _running(holder: dict) -> dict:
    return {"started": False, "running": True, "run_id": holder["run_id"]}


def _update_argv(checkout: Path, run_id: str) -> list[str]:
    return [str(checkout / "bin" / "trailhead"), "update", "--yes", "--run-id", run_id]


def _systemd_argv(env: dict[str, str], checkout: Path, log: Path, run_id: str) -> list[str]:
    return [
        "systemd-run",
        "--user",
        f"--unit=trailhead-update-{run_id}",
        "--collect",
        f"--working-directory={checkout}",
        f"--property=StandardOutput=append:{log}",
        f"--property=StandardError=append:{log}",
        *[f"--setenv={name}" for name in env],
        "--",
        *_update_argv(checkout, run_id),
    ]


def _launchd_plist(env: dict[str, str], checkout: Path, log: Path, run_id: str) -> bytes:
    return plistlib.dumps(
        {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": _update_argv(checkout, run_id),
            "EnvironmentVariables": dict(env),
            "WorkingDirectory": str(checkout),
            "RunAtLoad": True,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }
    )


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data)
    finally:
        os.close(fd)


def _run_with_env(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, env=env, capture_output=True, text=True)


def _start_systemd(argv: list[str], env: dict[str, str], run) -> bool:
    return run(argv, env=env).returncode == 0


def _start_launchd(
    env: dict[str, str], checkout: Path, log: Path, run_id: str, run, uid: int | None
) -> bool:
    domain = outpost_supervisor.launchd_domain(uid)
    plist = ensure_dir(log.parent) / _PLIST_NAME
    _write_private(plist, _launchd_plist(env, checkout, log, run_id))
    run(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"])
    return run(["launchctl", "bootstrap", domain, str(plist)]).returncode == 0


def _start_unsupervised(env: dict[str, str], checkout: Path, log: Path, run_id: str) -> bool:
    ensure_dir(log.parent)
    with open(log, "ab") as out:
        subprocess.Popen(
            _update_argv(checkout, run_id),
            cwd=checkout,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            close_fds=True,
            start_new_session=True,
        )
    return True


def _await_lock(run_id: str, env: dict[str, str], wait_seconds: float, poll: float) -> dict:
    deadline = time.monotonic() + wait_seconds
    while True:
        holder = update_run.current_holder(env=env)
        record = update_run.read_lock_record(env=env)
        if record is not None and record["run_id"] == run_id:
            return {"started": True, "run_id": run_id}
        if holder is not None:
            return _running(holder)
        if time.monotonic() >= deadline:
            return _could_not_start()
        time.sleep(poll)


def detach(
    *,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    supervisor_dir: Path | None = None,
    runner=None,
    uid: int | None = None,
    wait_seconds: float = WAIT_SECONDS,
    poll_interval: float = POLL_SECONDS,
) -> dict:
    """Start the update as a background job and report what became of it."""
    environ = env if env is not None else dict(os.environ)

    holder = update_run.current_holder(env=environ)
    if holder is not None:
        return _running(holder)

    stamp, _reason = read_stamp_with_reason(env=environ)
    if stamp is None:
        print("trailhead: no usable install provenance stamp; nothing to update", file=sys.stderr)
        return _could_not_start()
    checkout = Path(stamp["checkout"])
    log = state_dir("trailhead", env=environ) / _LOG_NAME
    run_id = update_run.new_run_id()

    try:
        if outpost_lifecycle._is_supervised(
            environ, platform=platform, supervisor_dir=supervisor_dir
        ):
            if outpost_supervisor._platform_kind(platform) == "darwin":
                run = runner if runner is not None else outpost_supervisor.default_runner
                ok = _start_launchd(environ, checkout, log, run_id, run, uid)
            else:
                run = runner if runner is not None else _run_with_env
                ok = _start_systemd(_systemd_argv(environ, checkout, log, run_id), environ, run)
        else:
            ok = _start_unsupervised(environ, checkout, log, run_id)
    except OSError as exc:
        print(f"trailhead: could not start the update job: {exc}", file=sys.stderr)
        return _could_not_start()
    if not ok:
        return _could_not_start()
    return _await_lock(run_id, environ, wait_seconds, poll_interval)
