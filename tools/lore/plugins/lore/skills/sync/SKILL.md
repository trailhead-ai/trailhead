---
name: sync
description: Commit and push the lore vaults. Use for /lore:sync, "commit the vault", "sync the vault", "push the vault".
---

# /lore:sync — Commit and push the vaults

Stage all vault changes, commit, and push to origin (if configured). **Every
configured vault is synced**, not just one.

**Optional:** ask the user for a commit message, or use the default.

Run:

```bash
lore sync [--message "<message>"]
```

Output is one labeled block per vault:

```
  default:      Committed: lore: sync vault
                Pushed to origin.
  trailhead:    Committed: lore: sync vault
                No origin remote — skipping push.
  home-manager: Nothing to commit — vault is clean.
```

- Relay the per-vault outcomes. A vault reporting "No origin remote" holds records
  that exist only on this disk — worth telling the user.
- If every vault is clean, report that nothing needed committing.

**Sync one vault only** when the user names one:

```bash
lore sync --vault trailhead
```

An unknown name exits 1 and lists the configured vaults; relay that list rather
than guessing a correction.

## Why the default covers everything

Record writes route **by scope** — from a repo bound to a product-scope vault,
`lore record create` writes there, not to `default`. A sync covering only
`default` would commit none of what the session just wrote while still printing
"Committed / Pushed to origin". Syncing every vault is what keeps that success
message true.

## Failure handling

- **Push failures are soft — the commit is durable.** If `git push` fails
  (offline, auth rejected, network error), that vault prints "committed locally;
  push failed — re-run `lore sync` when online" and the run still exits 0 for it.
  The commit is safe. Do NOT retry the commit; relay the notice and ask the user
  to re-run when the network is back.
- **A vault that fails hard is skipped, not fatal.** A vault that is missing or is
  not its own git toplevel is reported and skipped; the remaining vaults are still
  synced and the command exits 1 with a summary naming the failures. Relay which
  vaults failed — the others did commit.

Do not pass `--no-gpg-sign` or force `-S`; signing is controlled by the adopter's
git config.
