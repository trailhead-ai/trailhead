---
name: publish-site
description: Publish a multi-file static site into a shared lore vault's sites/ tree so every teammate can view it from their own local Outpost — validates the payload, publishes atomically, and syncs the vault. Use for /publish-site, "publish this as a site", "publish a static site", "put this doc site in the vault", "update the published site", "remove a published site".
---

# Publish site

Publish a directory of static files (an `index.html` plus whatever pages,
CSS, and images it references) into a shared lore vault's `sites/` tree, so
every teammate viewing that vault from their own local Outpost can open it.
"Deploy" is nothing more than a vault commit + sync — there is no separate
build or hosting step.

This skill has two parts: **you** resolve which vault the site belongs in and
call the bundled script; the script (`publish_site.py`, stdlib-only) does the
deterministic work — validating, staging, atomically publishing, and syncing.

## Inputs

- **Source directory** — a directory on disk containing the site's files,
  with `index.html` at its root. Multi-page sites (extra HTML pages, CSS,
  images, subdirectories) are expected, not just a single self-contained
  file.
- **Slug** — the site's URL segment and its directory name under `sites/`.
  Must match `^[a-z0-9][a-z0-9._-]*$`.

## Validation rules

The script enforces these before touching the vault — nothing is written
until every check passes:

- A root `index.html` must exist in the source directory.
- Every entry in the source tree must be a regular file or a plain
  directory — **symlinks and any other non-regular entry (fifo, device,
  socket) are rejected**, at publish time and (separately, in the daemon
  that serves sites) at serve time, so a payload that publishes cleanly
  never fails to serve its own content.
- No path segment in the payload may contain `..`, a backslash, or a NUL
  byte — the same rule the daemon applies to every request segment. The
  `..` check is a *substring* test, so a file named `notes..v2.html` is
  rejected here rather than publishing and then 404ing.
- The slug must match `^[a-z0-9][a-z0-9._-]*$`.
- The vault directory's own name must match that same pattern — it becomes
  the first URL segment, and the daemon gates it identically.
- A payload over 5 MB total is **advisory only** — a warning is printed but
  the publish still proceeds. There is no hard size limit.

## 1. Resolve the target vault

Run:

```
lore vault resolve --kind blob --json
```

This reports the vault a `blob`-kind record would land in right now, as a
fixed JSON object. Two fields matter here:

- `path` — the vault's root directory. Pass this as `--vault-path` below.
- `vault` — the vault's real configured name, or `null` when resolution
  landed on the unconditional default floor. Pass this as `--vault` below
  **only when it is a real string** — omit the flag entirely when it is
  `null` (see the sync step for why this distinction matters).

**To publish into a different vault than the one `resolve` picks** (an
override), skip `resolve` and look the target vault up directly with
`lore vault ls`, which lists every configured vault's name, scope, and path
— pass that vault's real name and path the same way.

## 2. Publish

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/publish-site/publish_site.py <source-dir> <slug> \
  --vault-path <path-from-resolve> \
  [--vault <name-from-resolve, if not null>] \
  [--overwrite]
```

The script:

1. Validates the source directory against the rules above.
2. Confirms `--vault-path` is an existing directory and a direct child of
   the vaults root (a mistyped vault name, or a vault configured with an
   explicit non-standard path, fails this check with an explicit error
   rather than publishing somewhere unexpected). Because a vault path is a
   direct child of the vaults root, its basename *is* the vault's
   configured name — so if `--vault` is given and disagrees with it, the
   publish stops rather than writing one vault and syncing another.
3. Stages the source into a temporary directory inside `<vault>/sites/`,
   then swaps it into place as `<vault>/sites/<slug>/`. An existing site is
   renamed aside first and deleted only after the new tree is in place, so
   the served directory is always either the whole old site or the whole
   new one — a failure anywhere leaves the previous site serving, and a
   first publish leaves no partial directory behind.
4. If `<vault>/sites/<slug>/` already exists: **without `--overwrite`** the
   publish refuses and prints a file-level summary of what a replace would
   do (`add:` / `change:` / `remove:`, one line each, listing the affected
   relative paths). **With `--overwrite`** the target is replaced wholesale
   — it ends up mirroring the new source exactly, including removing files
   the new source no longer has.

## 3. Sync gate

After a successful publish, the script itself runs `lore sync` — it never
leaves that step to be done later, so a printed "published" success can
never precede an actual sync. Whether the sync is scoped depends on what you
passed as `--vault`:

- **`--vault <name>` given** (the vault has a real configured name) → the
  script runs `lore sync --vault <name>`, scoped to just that vault.
- **`--vault` omitted** (resolution's `vault` field was `null` — the
  default-floor case) → the script runs bare `lore sync`, which syncs every
  configured vault. This is deliberate, not a fallback to guess at: the
  default-floor vault's configured name is not guaranteed to be the literal
  string `"default"`, so there is no name to scope to that is verified safe
  in every configuration. Bare `lore sync` is the only targeting that is
  correct in all cases for this vault.

Sync's own output is **streamed through to the console**, not captured. Read
it: `lore sync` degrades gracefully when there is no remote configured or the
network is down — it can commit locally, report that, and still exit 0. The
exit code alone therefore cannot tell you the site reached your teammates;
sync's streamed output is the only place that shows up.

The success URL (below) is printed **only when the sync subprocess exits
0**. If sync fails, the script exits nonzero and states plainly, below
sync's own output, that the site was published locally but **NOT synced** — so
a teammate who hasn't pulled cannot be told the site is live. Pass
`--no-sync` to skip the sync step entirely (for offline work or testing);
it always prints the same "NOT synced" warning in place of a success URL.

## Reported URL

On success, the script's last line is the site's local URL, in trailing-slash
form (anything above it is sync's own output):

```
http://127.0.0.1:<sites-port>/<vault>/<slug>/
```

`<sites-port>` defaults to `7314` (override with `--sites-port`).
`<vault>` is the basename of the resolved vault path — always present, even
when `--vault` (the sync-scoping name) was omitted for the default floor.

**If the operator moved the sites port, pass it** — otherwise the printed URL
points at a port nothing is listening on. The daemon resolves its sites port
as `SITES_PORT` (environment) → `sites_port` under `[daemon]` in Outpost's
`config.toml` (`$XDG_CONFIG_HOME/outpost/config.toml`) → `7314`. Check those
two in that order before publishing; if either names a different port, pass it
as `--sites-port`.

## Removal

To remove a published site: delete `<vault>/sites/<slug>/` with a plain
`rm -rf`, then sync that vault the same way the publish step did — scoped
(`lore sync --vault <name>`) when the vault has a real configured name, bare
`lore sync` for the default floor. The `sites/` directory is a free-write
zone for plain file operations; deletion needs no special tooling.
