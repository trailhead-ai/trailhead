## Outpost sites — the default home for shareable pages

Outpost serves static sites from two places, and picking the right one is the
whole judgment call:

- **A lore vault's `sites/` tree** — shared with the team. Every teammate
  running their own local Outpost can open it; "deploy" is a vault commit +
  sync, no build step and no hosting service. Reviewed, committed, durable.
- **A camp workspace's `sites/` tree** — scoped to one piece of work. Syncs
  nowhere, is visible only on this machine, and **dies when the workspace is
  torn down**. That lifetime is the point, not a limitation.

**Default rule:** when you produce an HTML deliverable meant to be *looked at*
— a report, dashboard, design doc, mockup, runbook, analysis write-up,
multi-page doc set — publish it as an Outpost site unless the user says
otherwise. Prefer it over dropping a bare `.html` file in the repo or a temp
directory (nobody finds those) and over a Claude Artifact (that leaves the
team's own vault). Say where you published it and hand back the URL.

**Choose the target by how long the page should live**, not by how polished it
is. If the team needs it after this work is done, or there is no workspace to
scope it to, publish it to a vault. If you are working inside a camp workspace
and the page belongs to that work with no reason to outlive it — a PR review
tour, a one-off analysis, a scratch dashboard — publish it to that workspace
instead.

Never provision a camp workspace just to host a page. A workspace with no work
in it is never torn down, which makes it exactly the orphan workspace-scoped
sites exist to prevent. No workspace already in play means there is no
workspace target — fall back to the vault rule, or to the throwaway exception
below.

Reasonable exceptions, no need to ask: a page that is genuinely throwaway or
single-use **and has no camp workspace to scope it to** — with a workspace in
play, publish it there rather than withholding it; content the user framed as
private or sensitive; a file that must live in the repo because something else
consumes it; or the user naming a different destination.

### How to publish

Use the `/publish-site` skill (outpost plugin) — do not hand-roll either write.
You resolve the target; the bundled `publish_site.py` validates the payload and
publishes atomically. Pass exactly one of `--vault-path` or `--workspace-path`.
Read the skill for the full contract; the shape is:

- **Input:** a source directory with `index.html` at its root (multi-page,
  CSS, and images are expected, not just one self-contained file) plus a slug
  matching `^[a-z0-9][a-z0-9._-]*$`.
- **Rejected before anything is written:** symlinks or non-regular files in the
  payload, any path segment containing `..`, a backslash, or NUL — and a
  destination `sites/` tree that is itself a symlink.
- **Republishing** the same slug needs `--overwrite`; without it you get a
  file-level `add:`/`change:`/`remove:` preview and no write.
- **Vault result:** `http://127.0.0.1:<sites-port>/<vault>/<slug>/`, printed
  only when the sync actually succeeded. A publish that could not sync is local
  to you — never tell a teammate it is live.
- **Workspace result:** `http://127.0.0.1:<sites-port>/<group>-<slug>/<site-slug>/`.
  No sync runs, because there is nothing to share. It is local to this machine
  and dies with the workspace — say so when you hand back the link, rather than
  letting it read like a vault URL someone else can open.

Removal is a plain `rm -rf` of the site's directory under the target's
`sites/`, followed — for a vault only — by a sync of that vault.

Sites are content, not credentials: never publish secrets, tokens, or customer
PII. That holds for a workspace too. It syncs nowhere, but every site on the
listener shares one browser origin, so any other site served there can read it
once opened in a tab.

## Record links

Link a record mention as `[kind/slug](<base>/records/vault/kind/slug)`: text is
kind/slug only — vault stays in the target — never a bare URL. Resolve base and vault
once per session: base is `LORE_RECORD_URL_BASE`, else `record_url_base` in
`config.json`, used only if `http`/`https`, with a host, and no query, fragment, or
`@` (credentials) — otherwise `http://127.0.0.1:7313`; vault is `lore vault ls`'s path
column matched to the record's path, never a basename. Print it bare when the vault
can't be resolved, isn't a direct child of the vaults root (`$LORE_STATE_DIR/vaults`,
else `$XDG_STATE_HOME/lore/vaults`, else `$HOME/.local/state/lore/vaults`), or vault,
kind, or slug carry a character outside ASCII `a`-`z`, `0`-`9`, `-`. A handoff
command stays bare; record bodies never link.
Example: `[task/example](http://127.0.0.1:7313/records/trailhead/task/example)`
In prose, link only the first mention per record per response. In a table or
list, link every row instead — do not stop at the first row.
Example row: `| [task/example](http://127.0.0.1:7313/records/trailhead/task/example) | ... |`
