---
name: sync
description: Commit and push the lore vault. Use for /lore:sync, "commit the vault", "sync the vault", "push the vault".
---

# /lore:sync — Commit and push the vault

Stage all vault changes, commit, and push to origin (if configured).

**Optional:** ask the user for a commit message, or use the default.

Run:

```bash
lore sync [--message "<message>"]
```

- If the vault is clean (nothing staged), the command exits with "Nothing to commit" — report this to the user.
- If no `origin` remote is configured, the commit is made locally and a notice is printed — relay that notice.
- On success, confirm the commit was made and whether it was pushed.

**Push failures are soft — the commit is durable.** If `git push` fails (offline, auth rejected, or network error), `lore sync` exits 0 and prints a notice: "committed locally; push failed — re-run `lore sync` when online". The commit has already been made and is safe. Do NOT retry the commit; just relay the notice to the user and ask them to run `lore sync` again when the network is available.

Do not pass `--no-gpg-sign` or force `-S`; signing is controlled by the adopter's git config.
