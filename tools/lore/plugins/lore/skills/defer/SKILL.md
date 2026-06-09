---
name: defer
description: Capture a deferred item — work intentionally set aside, with a trigger condition for revisiting. Use for /defer, "set this aside", "defer this", "come back to this later", "not now but don't forget".
---

# /lore:defer — Capture a deferred item

Gather the following from the user conversationally, asking only for what is missing:

1. **Title** — short noun phrase describing what is being deferred (required)
2. **Why deferred** — one sentence: why now is not the right time
3. **When to revisit** — specific trigger condition or date (`YYYY-MM-DD`)
4. **Surfaces** — comma-separated area names this item touches (optional; leave empty if unclear)
5. **Next check** — date or trigger for the next review (optional)
6. **Revisit after** — `YYYY-MM-DD` for a time-bound revisit; sets `status: scheduled` (optional)
7. **Project** — project name (infer from `git remote get-url origin` if not stated)

Once you have title and project at minimum, run:

```bash
lore new deferred \
  --title "<title>" \
  --project "<project>" \
  [--surfaces "<sub1>,<sub2>"] \
  [--next-check "<date-or-trigger>"] \
  [--revisit-after "<YYYY-MM-DD>"] \
  [--vault "$LORE_VAULT"]
```

Then open the written file, fill in the body sections (Why deferred / When to revisit / Context) with the user's answers, and confirm the note path.

If `--revisit-after` is set the note gets `status: scheduled`; otherwise `status: open`.
If no session note exists the CLI will print a skip notice — that is expected; continue normally.
