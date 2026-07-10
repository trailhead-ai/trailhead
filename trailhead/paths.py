"""OS-aware config/state/cache-dir resolver.

Resolver contract
-----------------
The three resolver functions (config_dir, state_dir, cache_dir) are PURE:
they return a Path and NEVER create anything on disk. Use ensure_dir() to
create a resolved path.

Per-OS resolution (mirrors platformdirs conventions):

  Linux (sys.platform starts with "linux"):
    config  → $XDG_CONFIG_HOME/<app>     else  ~/.config/<app>
    state   → $XDG_STATE_HOME/<app>      else  ~/.local/state/<app>
    cache   → $XDG_CACHE_HOME/<app>      else  ~/.cache/<app>

  macOS (sys.platform == "darwin"):
    trailhead adopts the freedesktop basedir spec on macOS too (see the
    "Trailhead fully adopts the basedir specification" axiom in docs/vision.md):
    the default layout mirrors Linux rather than ~/Library.
    XDG_* vars are honored when explicitly set (same as Linux).
    Otherwise:
    config  → ~/.config/<app>
    state   → ~/.local/state/<app>
    cache   → ~/.cache/<app>

  Windows (sys.platform == "win32"):
    config  → %APPDATA%/<app>
    state   → %LOCALAPPDATA%/<app>
    cache   → %LOCALAPPDATA%/<app>
    Unset %APPDATA% or %LOCALAPPDATA% raises PathResolutionError.

Per-app env override
--------------------
Each resolver honors a per-app override env var before consulting the
OS defaults. Convention: uppercase(app) + "_CONFIG_DIR" / "_STATE_DIR" /
"_CACHE_DIR". For example, state_dir("camp") checks CAMP_STATE_DIR first.

Override validation rules
-------------------------
  empty string → ignored (falls through to OS default)
  relative path → PathResolutionError (never silently build a cwd-relative path)
  absolute path → used as-is

app argument validation
-----------------------
The app argument must be a plain name with no path separators, backslashes,
or ".." components. Violations raise PathResolutionError.
"""

import os
import sys
from pathlib import Path


class PathResolutionError(Exception):
    """Raised when a path cannot be resolved due to missing env vars,
    unsupported OS, or invalid input."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_INVALID_APP_CHARS = frozenset(("/", "\\", ".."))


def _validate_app(app: str) -> None:
    """Raise PathResolutionError if app contains path separators or '..'."""
    if not app:
        raise PathResolutionError("app argument must not be empty")
    if ".." in app or "/" in app or "\\" in app or os.sep in app:
        raise PathResolutionError(
            f"app argument {app!r} must not contain path separators, backslashes, or '..'"
        )


def _validate_env_override(value: str, var_name: str) -> Path | None:
    """Validate an env-override string.

    Returns:
        None       if value is empty (fall through to default)
        Path(value) if value is absolute
    Raises:
        PathResolutionError if value is a non-empty relative path
    """
    if not value:
        return None
    p = Path(value)
    if not p.is_absolute():
        raise PathResolutionError(
            f"Environment variable {var_name!r} contains a relative path {value!r}. "
            "Override paths must be absolute."
        )
    return p


def _home(env: dict[str, str], label: str) -> Path:
    """Return the home directory from env, raising PathResolutionError if absent."""
    home = env.get("HOME")
    if not home:
        raise PathResolutionError(
            f"Cannot resolve {label}: HOME is not set in the environment. "
            "Set HOME to a valid directory path."
        )
    return Path(home)


def _per_app_override(app: str, suffix: str, env: dict[str, str]) -> Path | None:
    """Return the per-app override Path if set and valid, else None.

    suffix is one of "_CONFIG_DIR", "_STATE_DIR", "_CACHE_DIR".
    """
    var_name = f"{app.upper()}{suffix}"
    value = env.get(var_name, "")
    return _validate_env_override(value, var_name)


def _xdg_override(var_name: str, env: dict[str, str]) -> Path | None:
    """Return the XDG override Path if set and valid, else None."""
    value = env.get(var_name, "")
    return _validate_env_override(value, var_name)


# ---------------------------------------------------------------------------
# Public API — resolver functions (pure, never create dirs)
# ---------------------------------------------------------------------------


def config_dir(
    app: str,
    *,
    platform: str | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Return the config directory for app on the current OS.

    Pure: returns a Path, never creates anything on disk.

    Args:
        app:      Application name. Must not contain path separators or '..'.
        platform: Override sys.platform (for testing). Defaults to sys.platform.
        env:      Override os.environ (for testing). Defaults to os.environ.

    Returns:
        Absolute Path to the config directory (directory may not exist).

    Raises:
        PathResolutionError: On unsupported OS, missing required env vars,
                             invalid app name, or invalid override path.
    """
    _validate_app(app)
    plat = platform if platform is not None else sys.platform
    environ = env if env is not None else dict(os.environ)

    # Per-app override wins over everything.
    override = _per_app_override(app, "_CONFIG_DIR", environ)
    if override is not None:
        return override

    if plat.startswith("linux"):
        return _linux_config(app, environ)
    if plat == "darwin":
        return _macos_config(app, environ)
    if plat == "win32" or os.name == "nt":
        return _windows_config(app, environ)
    raise PathResolutionError(f"Unsupported platform {plat!r}. Cannot resolve config directory.")


def state_dir(
    app: str,
    *,
    platform: str | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Return the state directory for app on the current OS.

    Pure: returns a Path, never creates anything on disk.

    macOS note: state defaults to ~/.local/state/<app> (basedir spec), mirroring
    Linux. See the module docstring for the full per-OS table.

    Args:
        app:      Application name. Must not contain path separators or '..'.
        platform: Override sys.platform (for testing). Defaults to sys.platform.
        env:      Override os.environ (for testing). Defaults to os.environ.

    Returns:
        Absolute Path to the state directory (directory may not exist).

    Raises:
        PathResolutionError: On unsupported OS, missing required env vars,
                             invalid app name, or invalid override path.
    """
    _validate_app(app)
    plat = platform if platform is not None else sys.platform
    environ = env if env is not None else dict(os.environ)

    override = _per_app_override(app, "_STATE_DIR", environ)
    if override is not None:
        return override

    if plat.startswith("linux"):
        return _linux_state(app, environ)
    if plat == "darwin":
        return _macos_state(app, environ)
    if plat == "win32" or os.name == "nt":
        return _windows_state(app, environ)
    raise PathResolutionError(f"Unsupported platform {plat!r}. Cannot resolve state directory.")


def cache_dir(
    app: str,
    *,
    platform: str | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Return the cache directory for app on the current OS.

    Pure: returns a Path, never creates anything on disk.

    Args:
        app:      Application name. Must not contain path separators or '..'.
        platform: Override sys.platform (for testing). Defaults to sys.platform.
        env:      Override os.environ (for testing). Defaults to os.environ.

    Returns:
        Absolute Path to the cache directory (directory may not exist).

    Raises:
        PathResolutionError: On unsupported OS, missing required env vars,
                             invalid app name, or invalid override path.
    """
    _validate_app(app)
    plat = platform if platform is not None else sys.platform
    environ = env if env is not None else dict(os.environ)

    override = _per_app_override(app, "_CACHE_DIR", environ)
    if override is not None:
        return override

    if plat.startswith("linux"):
        return _linux_cache(app, environ)
    if plat == "darwin":
        return _macos_cache(app, environ)
    if plat == "win32" or os.name == "nt":
        return _windows_cache(app, environ)
    raise PathResolutionError(f"Unsupported platform {plat!r}. Cannot resolve cache directory.")


# ---------------------------------------------------------------------------
# Creator helper (the ONLY function that touches the filesystem)
# ---------------------------------------------------------------------------


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    """Create path (and any parents) with secure permissions, then return it.

    This is the single canonical creator. The resolver functions are pure;
    callers use ensure_dir() to materialize directories.

    Args:
        path: Directory to create.
        mode: Permission bits. Defaults to 0o700 (owner read/write/execute only).

    Returns:
        path (unchanged), for chaining.
    """
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


# ---------------------------------------------------------------------------
# Per-OS resolution — internal implementations
# ---------------------------------------------------------------------------


def _linux_config(app: str, env: dict[str, str]) -> Path:
    xdg = _xdg_override("XDG_CONFIG_HOME", env)
    if xdg is not None:
        return xdg / app
    return _home(env, "config directory") / ".config" / app


def _linux_state(app: str, env: dict[str, str]) -> Path:
    xdg = _xdg_override("XDG_STATE_HOME", env)
    if xdg is not None:
        return xdg / app
    return _home(env, "state directory") / ".local" / "state" / app


def _linux_cache(app: str, env: dict[str, str]) -> Path:
    xdg = _xdg_override("XDG_CACHE_HOME", env)
    if xdg is not None:
        return xdg / app
    return _home(env, "cache directory") / ".cache" / app


def _macos_config(app: str, env: dict[str, str]) -> Path:
    # basedir spec on macOS: default to ~/.config, mirroring Linux.
    return _linux_config(app, env)


def _macos_state(app: str, env: dict[str, str]) -> Path:
    # basedir spec on macOS: default to ~/.local/state, mirroring Linux.
    return _linux_state(app, env)


def _macos_cache(app: str, env: dict[str, str]) -> Path:
    # basedir spec on macOS: default to ~/.cache, mirroring Linux.
    return _linux_cache(app, env)


def _windows_config(app: str, env: dict[str, str]) -> Path:
    appdata = env.get("APPDATA")
    if not appdata:
        raise PathResolutionError(
            "Cannot resolve config directory on Windows: APPDATA environment variable is not set."
        )
    return Path(appdata) / app


def _windows_state(app: str, env: dict[str, str]) -> Path:
    localappdata = env.get("LOCALAPPDATA")
    if not localappdata:
        raise PathResolutionError(
            "Cannot resolve state directory on Windows: "
            "LOCALAPPDATA environment variable is not set."
        )
    return Path(localappdata) / app


def _windows_cache(app: str, env: dict[str, str]) -> Path:
    localappdata = env.get("LOCALAPPDATA")
    if not localappdata:
        raise PathResolutionError(
            "Cannot resolve cache directory on Windows: "
            "LOCALAPPDATA environment variable is not set."
        )
    return Path(localappdata) / app
