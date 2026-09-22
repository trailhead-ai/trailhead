"""Host-supervisor entries for the outpost daemon: ``trailhead outpost enable|disable``.

Registers the outpost daemon with the host's own process supervisor — launchd on
macOS, systemd (user) on Linux — so it starts at boot/login and is restarted by
the supervisor when it exits on failure. Everything else about the daemon (its
build, its entrypoint, its port) is resolved the same way ``outpost_lifecycle``
already resolves it for the unsupervised ``start`` path; this module only adds
the supervisor registration on top.

One deliberate carve-out from ``trailhead/paths.py``: the macOS LaunchAgents
directory (``~/Library/LaunchAgents``) is not routed through
``trailhead.paths`` — launchd mandates that exact location and it has no
basedir/XDG analogue to resolve through. The Linux unit directory
(``$XDG_CONFIG_HOME/systemd/user`` else ``~/.config/systemd/user``) already
follows the basedir convention ``paths.py`` implements elsewhere, but systemd
mandates ``systemd/user`` as a fixed subpath beneath it rather than an
app-named directory, so it is resolved locally here too rather than forced
through ``config_dir()``.

Rendering is done in Python (``plistlib`` for the plist, ``configparser`` for
the unit) rather than as a shell/string template (the pattern lookout's
``deploy/*.template`` files use): a value that carries a newline or an
unescaped delimiter can silently become a second directive in a hand-templated
file, where a structured writer either encodes it safely or raises. trailhead
ships zero third-party runtime dependencies, and both writers are stdlib.

Contract & invariants
----------------------
* **PATH is composed, never inherited whole.** The entry's ``PATH`` is built
  from the resolved ``node``, ``git``, and ``lore`` binaries' directories plus
  the standard system directories — never the enabling shell's full ``PATH``.
  An unattended daemon re-resolving bare command names only inside directories
  an operator vetted at enable time is a materially smaller hijack surface
  than handing it every directory the shell happened to have on PATH.
* **``enable`` validates before writing anything.** The daemon entrypoint is
  resolved through the existing ``outpost_lifecycle._resolve_entrypoint`` (so a
  missing build fails loudly), and ``node``/``git``/``lore`` must each resolve
  on the enabling shell's PATH — any failure raises
  :class:`~trailhead.outpost_lifecycle.OutpostLifecycleError` naming what is
  missing, and nothing is written or run.
* **A live detached daemon is stopped first.** ``enable`` calls the existing
  ``stop`` verb before writing the entry, so the supervised process it starts
  never fails to bind the port because a detached daemon from the unsupervised
  path is still holding it.
* **Restart posture matches lookout's**: restart only on a failure exit
  (``KeepAlive.SuccessfulExit=false`` / ``Restart=on-failure``), with a
  breather between attempts, so an operator's own ``stop`` stays stopped and a
  persistent misconfiguration does not restart-loop.
* **The runner is injectable and never touches a shell.** The default runner
  calls ``subprocess.run`` with a list argv (``shell`` unset), captures output,
  and returns the ``CompletedProcess`` unchanged, so a caller reading its
  stdout (e.g. a later status check) gets the real result.
* **Linux lingering is best-effort, loudly.** When ``loginctl enable-linger``
  fails, the unit is left enabled (nothing is rolled back) and ``enable``
  raises a named error carrying the exact command to run by hand — the
  operator is never left with a headless host that silently never starts
  Outpost at boot.
* **Both verbs are idempotent.** ``enable`` twice re-renders and re-registers
  the same entry. ``disable`` on an unregistered host prints a message and
  runs nothing.
"""

from __future__ import annotations

import configparser
import getpass
import io
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from trailhead import outpost_lifecycle
from trailhead.outpost_lifecycle import OutpostLifecycleError
from trailhead.paths import ensure_dir, state_dir

LAUNCHD_LABEL = "com.trailhead.outpost"
SYSTEMD_UNIT_NAME = "outpost.service"

# Standard system directories, appended after the resolved binaries' own
# directories when composing the supervised entry's PATH.
_SYSTEM_PATH_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

# The three binaries the supervised daemon's environment must be able to
# re-resolve: node to run it, git and lore because trailhead/outpost shell out
# to both.
_REQUIRED_BINARIES = ("node", "git", "lore")


@dataclass(frozen=True)
class SupervisorEntry:
    """Resolved inputs for one rendered supervisor entry."""

    entrypoint: Path
    checkout: Path
    node_bin: str
    port: int
    log_path: Path
    path_value: str


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_plist(entry: SupervisorEntry) -> bytes:
    """Render the launchd LaunchAgent plist for *entry*."""
    data = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [entry.node_bin, str(entry.entrypoint)],
        "WorkingDirectory": str(entry.checkout),
        "EnvironmentVariables": {
            "HTTP_PORT": str(entry.port),
            "PATH": entry.path_value,
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "StandardOutPath": str(entry.log_path),
        "StandardErrorPath": str(entry.log_path),
    }
    return plistlib.dumps(data)


def render_systemd_unit(entry: SupervisorEntry) -> str:
    """Render the systemd user unit for *entry*."""
    cp = configparser.ConfigParser()
    cp.optionxform = str  # keys are case-sensitive in a systemd unit

    cp["Unit"] = {
        "Description": "Outpost dashboard daemon (managed by trailhead)",
        "StartLimitIntervalSec": "600",
        "StartLimitBurst": "5",
    }
    exec_start = " ".join(shlex.quote(part) for part in (entry.node_bin, str(entry.entrypoint)))
    cp["Service"] = {
        "Type": "simple",
        "WorkingDirectory": str(entry.checkout),
        "Environment": f"HTTP_PORT={entry.port} PATH={entry.path_value}",
        "ExecStart": exec_start,
        "Restart": "on-failure",
        "RestartSec": "10",
        "StandardOutput": f"append:{entry.log_path}",
        "StandardError": "inherit",
    }
    cp["Install"] = {"WantedBy": "default.target"}

    buf = io.StringIO()
    cp.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Platform / path resolution
# ---------------------------------------------------------------------------


def _platform_kind(platform: str | None) -> str:
    plat = platform if platform is not None else sys.platform
    if plat == "darwin":
        return "darwin"
    if plat.startswith("linux"):
        return "linux"
    raise OutpostLifecycleError(
        f"outpost supervisor entries are not supported on platform {plat!r}; "
        "only macOS (launchd) and Linux (systemd --user) are."
    )


def _home_dir(env: dict[str, str]) -> Path:
    home = env.get("HOME")
    if not home:
        raise OutpostLifecycleError(
            "cannot resolve a supervisor entry directory: HOME is not set in the environment."
        )
    return Path(home)


def _default_launch_agents_dir(env: dict[str, str]) -> Path:
    return _home_dir(env) / "Library" / "LaunchAgents"


def _default_systemd_user_dir(env: dict[str, str]) -> Path:
    xdg = env.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else _home_dir(env) / ".config"
    return base / "systemd" / "user"


def _target_path(kind: str, env: dict[str, str], supervisor_dir: Path | None) -> Path:
    if kind == "darwin":
        base = supervisor_dir if supervisor_dir is not None else _default_launch_agents_dir(env)
        return base / f"{LAUNCHD_LABEL}.plist"
    base = supervisor_dir if supervisor_dir is not None else _default_systemd_user_dir(env)
    return base / SYSTEMD_UNIT_NAME


# ---------------------------------------------------------------------------
# Runner seam
# ---------------------------------------------------------------------------

Runner = Callable[[list], subprocess.CompletedProcess]


def default_runner(argv: list) -> subprocess.CompletedProcess:
    """Run a supervisor command. List argv, no shell, output captured as text."""
    return subprocess.run(argv, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# uid / user resolution — shared with outpost_lifecycle's supervised verbs
# ---------------------------------------------------------------------------


def resolve_uid(uid: int | None) -> int:
    """The launchd GUI domain's uid — the caller's own uid unless overridden."""
    return uid if uid is not None else os.getuid()


def resolve_user(env: dict[str, str], user: str | None) -> str:
    """The Linux account name for ``loginctl`` — env ``USER`` unless overridden."""
    return user if user is not None else (env.get("USER") or getpass.getuser())


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def _resolve_binary(name: str, which_runner: Callable[[str], Optional[str]]) -> str:
    resolved = which_runner(name)
    if not resolved:
        raise OutpostLifecycleError(
            f"outpost enable: '{name}' was not found on the shell's PATH; "
            "install it or fix PATH before enabling the supervisor entry."
        )
    return resolved


def _compose_path(binaries: list) -> str:
    dirs: list = []
    for binary in binaries:
        parent = str(Path(binary).parent)
        if parent not in dirs:
            dirs.append(parent)
    for d in _SYSTEM_PATH_DIRS:
        if d not in dirs:
            dirs.append(d)
    return ":".join(dirs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_enabled(
    env: dict[str, str] | None = None,
    *,
    platform: str | None = None,
    supervisor_dir: Path | None = None,
) -> bool:
    """True if a supervisor entry is currently registered on disk."""
    environ = env if env is not None else dict(os.environ)
    kind = _platform_kind(platform)
    return _target_path(kind, environ, supervisor_dir).exists()


def enable(
    *,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    supervisor_dir: Path | None = None,
    port: int = outpost_lifecycle.DAEMON_PORT,
    runner: Runner | None = None,
    which_runner: Callable[[str], Optional[str]] | None = None,
    uid: int | None = None,
    user: str | None = None,
) -> int:
    """Render and register the supervisor entry for the running platform.

    Resolves and validates the daemon entrypoint and the ``node``/``git``/
    ``lore`` binaries before writing or running anything. Stops any detached
    daemon still running the unsupervised way, then writes the entry (mode
    0o644) and registers it with the platform's supervisor through *runner*.
    """
    environ = env if env is not None else dict(os.environ)
    kind = _platform_kind(platform)

    checkout, entrypoint = outpost_lifecycle._resolve_entrypoint(environ)

    _which = which_runner or (lambda name: shutil.which(name, path=environ.get("PATH")))
    node_bin = _resolve_binary("node", _which)
    git_bin = _resolve_binary("git", _which)
    lore_bin = _resolve_binary("lore", _which)
    path_value = _compose_path([node_bin, git_bin, lore_bin])

    state = ensure_dir(state_dir(outpost_lifecycle.APP, env=environ))
    log_path = state / outpost_lifecycle.LOG_NAME

    entry = SupervisorEntry(
        entrypoint=entrypoint,
        checkout=checkout,
        node_bin=node_bin,
        port=port,
        log_path=log_path,
        path_value=path_value,
    )

    target = _target_path(kind, environ, supervisor_dir)

    # A supervised daemon must own the port cleanly; a detached daemon left
    # running from the unsupervised `start` path would otherwise still be
    # holding it when the supervisor tries to bind.
    outpost_lifecycle.stop(env=environ, port=port)

    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == "darwin":
        target.write_bytes(render_plist(entry))
    else:
        target.write_text(render_systemd_unit(entry))
    target.chmod(0o644)

    run = runner if runner is not None else default_runner

    if kind == "darwin":
        domain = f"gui/{resolve_uid(uid)}"
        run(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"])
        run(["launchctl", "bootstrap", domain, str(target)])
    else:
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT_NAME])
        actual_user = resolve_user(environ, user)
        linger_result = run(["loginctl", "enable-linger", actual_user])
        if linger_result.returncode != 0:
            command = f"loginctl enable-linger {actual_user}"
            raise OutpostLifecycleError(
                "outpost enable: the systemd unit is enabled, but lingering could "
                f"not be turned on; the daemon will not start at boot with nobody "
                f"logged in until you run: {command}"
            )

    print(f"outpost supervisor entry written and registered at {target}.")
    return 0


def disable(
    *,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    supervisor_dir: Path | None = None,
    runner: Runner | None = None,
    uid: int | None = None,
) -> int:
    """Deregister and remove the supervisor entry. No-op if none is registered."""
    environ = env if env is not None else dict(os.environ)
    kind = _platform_kind(platform)
    target = _target_path(kind, environ, supervisor_dir)

    if not target.exists():
        print("outpost supervisor entry is not enabled.")
        return 0

    run = runner if runner is not None else default_runner

    if kind == "darwin":
        run(["launchctl", "bootout", f"gui/{resolve_uid(uid)}/{LAUNCHD_LABEL}"])
    else:
        run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME])

    target.unlink()
    print(f"outpost supervisor entry removed ({target}).")
    return 0
