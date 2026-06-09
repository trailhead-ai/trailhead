# Link server

A localhost bridge that makes lore-vault notes openable from terminal output by
**clicking a link**.

## Why this exists

Most terminals (Warp, and Claude Code's renderer) only auto-linkify bare `http(s)://`
URLs — custom URI schemes like `obsidian://` render as plain, non-clickable text. So the
only clickable thing a tool can emit is a bare `http://` URL. The link server turns such
a URL into an Obsidian open:

    click  http://localhost:7777/areas/workflow-brain-context
      -> GET to this server
      -> macOS `open obsidian://open?path=<vault>/areas/workflow-brain-context.md`
      -> Obsidian focuses the note

It is **macOS + Obsidian only**, and entirely **opt-in** — nothing runs until you install
it. The lore SessionStart banner renders note pointers as clickable links *only when the
server is installed* (see [Gating](#gating-how-links-go-live)); otherwise it prints plain
vault-relative paths, so a machine without the server never emits dead links.

## Requirements

- macOS (the server shells out to `open`).
- Obsidian installed, with your lore vault registered as an Obsidian vault. The server
  uses `obsidian://open?path=<absolute-path>`, so Obsidian resolves the owning vault from
  the absolute path — no vault-name configuration is needed.

## Install

    lore link-server install

This renders a launchd agent (`com.lore.link-server`) into `~/Library/LaunchAgents/`,
loads it (`RunAtLoad` + `KeepAlive`, so it survives logout/reboot/crash), and records an
install-state file at `~/.config/lore/link-server.json`. `install` refuses cleanly — with
no partial state written — if you are not on macOS, if Obsidian is not found, or if the
port is already held (see [Migrating](#migrating-from-another-link-server-on-the-same-port)).

## Manage

    lore link-server status      # resolved port, install state, and whether the agent is running
    lore link-server uninstall   # unload the agent, remove the plist + state file

`status` exits non-zero (with a distinct warning) when the state file says the server is
installed but the agent is not actually running, or when the recorded plugin path no
longer matches the current one (a stale plist after moving/reinstalling the plugin) — so a
silently-dead bridge surfaces here rather than as a dead link in a later session.

## Configuration

| Knob | Default | Effect |
|---|---|---|
| `LORE_VAULT` | `~/lore` | Vault root the server serves (same var the rest of lore uses). Baked into the launchd agent's environment at install time. |
| `LORE_LINK_PORT` | `7777` | Port the server binds on `127.0.0.1`. Baked into the agent at install time; re-run `install` after changing it to re-render and reload. |
| `LINK_SERVER_ENABLED` | `True` | Source-level kill-switch in `scripts/config.py`. Set to `False` to force plain vault-relative paths in the banner even while the server is installed. |

The server binds `127.0.0.1` only — it is never network-reachable.

## Gating — how links go live

The banner emits clickable `http://localhost:<port>/…` links when **both** hold:

1. the install-state file (`~/.config/lore/link-server.json`) exists — i.e. you ran
   `lore link-server install`; and
2. `config.LINK_SERVER_ENABLED` is `True` (the default).

`uninstall` removes the state file, so links automatically revert to plain paths. There is
no separate "enable links" step — link-liveness tracks the install state.

## URL shape

    http://localhost:<port>/<vault-relative note path, no .md>

- `?query` is ignored, a trailing `.md` is stripped, subfolders are preserved.
- Design emitted links so the path tail reads as the note name — the visible URL doubles
  as the label.

## Security

The server maps request paths to filesystem paths under the vault root. It contains every
request with `os.path.realpath` + `os.path.commonpath` (a `..`-traversal or escaping
symlink yields `404` and never reaches `open`), and invokes `open` via `subprocess` with
`shell=False` (no shell interpolation of vault paths).

## Migrating from another link server on the same port

Only one process can hold `:7777`. If an older link server is already bound there,
`lore link-server install` refuses with an actionable message. To migrate:

1. Run `lore link-server install` — it refuses because the port is held. That's expected.
2. Unload whatever currently holds the port (e.g. an older, vault-specific launchd agent).
3. Re-run `lore link-server install`, then `lore link-server status` and confirm it
   reports **running**.
4. Only once `status` is green, retire the old agent's plist.

Keep the old agent's plist on disk until step 3 is green — if the new install ever fails,
you can reload the old agent and the bridge is back.

## Logs

`/tmp/lore-link-server.out.log` and `/tmp/lore-link-server.err.log`.

## Caveats

- Clicking opens your default browser first (terminals route clicks through `open`), which
  shows a brief "Opened in Obsidian — you can close this tab" page. Browsers ignore the
  `window.close()` attempt for user-opened tabs, so the tab lingers harmlessly.
- The server must be running for links to resolve; launchd keeps it alive, but a dead
  server means a hung/refused tab. `lore link-server status` tells you which.
