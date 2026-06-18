---
name: area
description: Create an area profile — overview, key files, keywords, and a one-line summary. The profile feeds the always-loaded area map; the agent matches tasks to areas and runs `lore search 'area:<name>'` to pull the area's memory. Use for /area, "profile this area", "document this area of the code", "create an area note".
---

# /lore:area — Create an area profile

Gather the following from the user conversationally, asking only for what is missing:

1. **Title / name** — the area identifier slug (e.g. `auth-module`, `data-pipeline`) — required
2. **Overview** — one paragraph: what this area does, where it lives
3. **Key files** — paths to load or grep when working here (comma-separated)
4. **Keywords** — short words associated with this area (comma-separated); feed the always-loaded area map so the agent can match a task to this area, then run `lore search 'area:<name>'` to pull its memory
5. **Project** — project name (infer from `git remote get-url origin` if not stated)

Once you have title and project at minimum, run:

```bash
lore new area \
  --title "<name>" \
  --project "<project>" \
  [--keywords "<kw1>,<kw2>"] \
  [--key-files "<path1>,<path2>"] \
  [--vault "$LORE_VAULT"]
```

Then open the written file, fill in the body sections with the user's answers, and confirm the note path.

The `keywords:` field is written as an inline list (e.g. `[auth, login, oauth]`). Together with the `summary:` one-liner, keywords feed the always-loaded area map — the compact menu the agent reads at session start to match a task to areas. Once the agent identifies relevant areas, it runs `lore search 'area:<name>'` to pull that area's decisions, lessons, dead-ends, and open deferred items. The `key-files:` field is likewise an inline list. Area profiles do not backlink to the session note.
