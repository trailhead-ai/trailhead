## Outpost sites — the default home for shareable pages

Outpost serves static sites out of a shared lore vault's `sites/` tree. Every
teammate running their own local Outpost can open them; "deploy" is just a
vault commit + sync, with no build step and no hosting service involved.

**Default rule:** when you produce an HTML deliverable meant to be *looked at*
— a report, dashboard, design doc, mockup, runbook, analysis write-up,
multi-page doc set — publish it as an Outpost site unless the user says
otherwise. Prefer it over dropping a bare `.html` file in the repo or a temp
directory (nobody finds those) and over a Claude Artifact (that leaves the
team's own vault). Say where you published it and hand back the URL.

Reasonable exceptions, no need to ask: a page that is genuinely throwaway or
single-use; content the user framed as private or sensitive; a file that must
live in the repo because something else consumes it; or the user naming a
different destination.

### How to publish

Use the `/publish-site` skill (outpost plugin) — do not hand-roll the vault
write. It resolves the target vault, then runs the bundled `publish_site.py`,
which validates the payload, publishes atomically, and syncs the vault before
reporting success. Read the skill for the full contract; the shape is:

- **Input:** a source directory with `index.html` at its root (multi-page,
  CSS, and images are expected, not just one self-contained file) plus a slug
  matching `^[a-z0-9][a-z0-9._-]*$`.
- **Rejected before anything is written:** symlinks or non-regular files, and
  any path segment containing `..`, a backslash, or NUL.
- **Republishing** the same slug needs `--overwrite`; without it you get a
  file-level `add:`/`change:`/`remove:` preview and no write.
- **Result:** `http://127.0.0.1:<sites-port>/<vault>/<slug>/`, printed only
  when the sync actually succeeded. A publish that could not sync is local to
  you — never tell a teammate it is live.

Removal is a plain `rm -rf` of `<vault>/sites/<slug>/` followed by a sync of
that vault.

Sites are content, not credentials: never publish secrets, tokens, or
customer PII into a vault that syncs to the whole team.
