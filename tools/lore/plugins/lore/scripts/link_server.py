"""Pure / unit-testable logic for the `lore link-server` CLI subcommand.

Kept out of `cli/lore` so the launchd-plist rendering, install-state-file I/O,
and the port probe can be tested without touching real `~/Library`, real
`launchctl`, or a real socket race. The CLI (`cmd_link_server`) supplies the
side-effecting glue (subprocess to `launchctl`, choosing the real LaunchAgents
and config dirs); everything here is hermetic given an injected directory.
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import config

STATE_FILENAME = "link-server.json"
STATE_SCHEMA = 1

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{server_script}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>LORE_VAULT</key>
        <string>{vault}</string>
        <key>LORE_LINK_PORT</key>
        <string>{port}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{out_log}</string>
    <key>StandardErrorPath</key>
    <string>{err_log}</string>
</dict>
</plist>
"""


def render_plist(
    *,
    label: str,
    python: str,
    server_script: str,
    vault: str,
    port: int,
    out_log: str,
    err_log: str,
) -> str:
    """Return the launchd plist XML for the link-server agent.

    Pure: builds and returns the final XML string, writing nothing. The CLI
    decides where to persist it.

    Every interpolated string is XML-escaped: a vault (or log) path containing
    ``&``, ``<``, or ``>`` would otherwise produce malformed XML that
    ``launchctl load`` rejects opaquely.
    """
    return _PLIST_TEMPLATE.format(
        label=escape(label),
        python=escape(python),
        server_script=escape(server_script),
        vault=escape(vault),
        port=port,
        out_log=escape(out_log),
        err_log=escape(err_log),
    )


def state_path(state_dir) -> Path:
    """The state-file path inside an injected state dir."""
    return Path(state_dir) / STATE_FILENAME


def write_state(state_dir, payload: dict) -> Path:
    """Write the install-state JSON into state_dir (creating it as needed).

    The payload must already carry ``"schema": STATE_SCHEMA``; the caller owns
    the full shape (port / plist_path / vault / plugin_root).
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_path(state_dir)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def read_state(state_dir) -> dict | None:
    """Return the parsed state dict, or None when it can't be trusted.

    Degrades to None (with a one-line stderr warning) rather than raising when
    the file is missing, unparseable, not a JSON object, or carries a ``schema``
    this version doesn't understand. A malformed state file must never crash
    ``status`` / ``install`` / ``uninstall``.
    """
    path = state_path(state_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        print(f"link-server: ignoring unreadable state file {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"link-server: ignoring malformed state file {path} (not an object).", file=sys.stderr)
        return None
    if data.get("schema") != STATE_SCHEMA:
        print(
            f"link-server: ignoring state file {path} with unknown schema "
            f"{data.get('schema')!r} (expected {STATE_SCHEMA}).",
            file=sys.stderr,
        )
        return None
    return data


def clear_state(state_dir) -> None:
    """Remove the state file if present (idempotent)."""
    state_path(state_dir).unlink(missing_ok=True)


def port_in_use(port: int) -> bool:
    """Return True iff binding 127.0.0.1:<port> fails (something already holds it)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


DEFAULT_STATE_DIR = Path.home() / ".config" / "lore"


def note_link(vault_rel_path: str, *, state_dir=DEFAULT_STATE_DIR) -> str:
    """Return a clickable link for a vault-relative path when the link server is active.

    "Active" means config.LINK_SERVER_ENABLED is True AND the install-state
    file is present and readable. When active, returns
    ``http://localhost:<port>/<vault_rel_path>`` with a single trailing ``.md``
    stripped. When not active (kill-switch off, or no state file), returns
    vault_rel_path unchanged.
    """
    if not config.LINK_SERVER_ENABLED:
        return vault_rel_path
    state = read_state(state_dir)
    if state is None:
        return vault_rel_path
    # Coerce defensively: a hand-corrupted state file could carry a non-int
    # port; fall back to the default rather than emit a broken host:port.
    try:
        port = int(state.get("port", 7777))
    except (TypeError, ValueError):
        port = 7777
    path = vault_rel_path
    if path.endswith(".md"):
        path = path[:-3]
    return f"http://localhost:{port}/{path}"
