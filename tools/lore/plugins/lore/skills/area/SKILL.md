---
name: area
description: Create an area profile — overview, key files, and keywords (recorded for future branch-based recall, currently inactive). Use for /area, "profile this area", "document this area of the code", "create an area note".
---

# /lore:area — Create an area profile

Gather the following from the user conversationally, asking only for what is missing:

1. **Title / name** — the area identifier slug (e.g. `auth-module`, `data-pipeline`) — required
2. **Overview** — one paragraph: what this area does, where it lives
3. **Key files** — paths to load or grep when working here (comma-separated)
4. **Keywords** — short words that, when appearing in a branch name or prompt, should trigger loading this profile (comma-separated; load-bearing for SessionStart recall)
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

The `keywords:` field is written as an inline list (e.g. `[auth, login, oauth]`) — recorded for future branch-based recall (currently inactive; the SessionStart recall path was removed pending a smarter redesign). The `key-files:` field is likewise an inline list. Area profiles do not backlink to the session note.
