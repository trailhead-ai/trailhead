---
name: sync
description: Commit, pull, and push the lore vaults. Use for /lore:sync, "commit the vault", "sync the vault", "push the vault", "pull the vault".
---

# /lore:sync — Commit, pull, and push the vaults

Stage all vault changes, commit, pull down commits made on other devices, and
push to origin (if configured) — in that order, so local records are committed
before any remote history is integrated. **Every configured vault is synced**,
not just one.

**Optional:** ask the user for a commit message, or use the default.

Run:

```bash
lore sync [--message "<message>"]
```

Output is one labeled block per vault:

```
  default:      Committed: lore: sync vault
                Pulled 2 commit(s) from origin.
                Pushed to origin.
  trailhead:    Committed: lore: sync vault
                No origin remote — skipping push.
  home-manager: Nothing to commit — vault is clean.
  Reindexed 214 record(s) after pull.
```

- Relay the per-vault outcomes. A vault reporting "No origin remote" holds records
  that exist only on this disk — worth telling the user.
- If every vault is clean, report that nothing needed committing.
- A "Pulled N commit(s)" line means records captured on another device just
  landed here; the trailing reindex line confirms `lore search` can see them.

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

- **Network failures are soft — the commit is durable.** If the fetch or push
  fails (offline, auth rejected, network error), that vault prints a notice and
  the run still exits 0 for it. The commit is safe. Do NOT retry the commit;
  relay the notice and ask the user to re-run when the network is back.
- **A rebase conflict is hard — but the vault is left consistent.** When the same
  record was edited on two devices and git cannot auto-merge, sync aborts the
  rebase (never leaving a mid-rebase vault), reports the conflict with a manual
  remedy (`cd <vault> && git pull --rebase`, resolve, re-run `lore sync`), and
  exits 1. Relay the remedy verbatim; do NOT attempt to resolve the conflict
  unless the user asks.
- **A vault that fails hard is skipped, not fatal.** A vault that is missing or is
  not its own git toplevel is reported and skipped; the remaining vaults are still
  synced and the command exits 1 with a summary naming the failures. Relay which
  vaults failed — the others did commit.

Do not pass `--no-gpg-sign` or force `-S`; signing is controlled by the adopter's
git config.
