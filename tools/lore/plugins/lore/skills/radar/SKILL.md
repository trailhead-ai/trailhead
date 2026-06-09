---
name: radar
description: Add a radar item — an external thing out of our control to check on periodically (GitHub issue, dependency release, upstream fix, vendor status). Use for /radar, "put this on my radar", "watch this issue", "track this release", "keep an eye on X".
---

# /lore:radar — Add a radar watch item

Gather the following from the user conversationally, asking only for what is missing:

1. **Title** — short noun phrase describing what is being watched (required)
2. **What we're watching** — one sentence: the external thing being tracked
3. **Why we care** — what unlocks or changes when this moves
4. **Source** — where updates appear (e.g. GitHub releases, changelog URL)
5. **Target** — the version or state being waited for (optional)
6. **Check cadence** — how often to check (e.g. "weekly", "monthly")
7. **Project** — project name (infer from `git remote get-url origin` if not stated)

Once you have title and project at minimum, run:

```bash
lore new radar \
  --title "<title>" \
  --project "<project>" \
  [--source "<url-or-feed>"] \
  [--target "<version-or-state>"] \
  [--check "<cadence>"] \
  [--vault "$LORE_VAULT"]
```

Then open the written file, fill in the body sections (What we're watching / Why we care / On change) with the user's answers, and confirm the note path.

Radar items do not backlink to the session note.
