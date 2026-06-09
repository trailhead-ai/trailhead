---
name: dead-end
description: Record a dead-end — an approach tried that didn't work, with a revive condition. Use for /dead-end, "that didn't work", "mark this as a dead end", "don't try this again".
---

# /lore:dead-end — Record a dead-end approach

Gather the following from the user conversationally, asking only for what is missing:

1. **Title** — short noun phrase describing the failed approach (required)
2. **Goal** — what was being attempted
3. **What was tried** — concrete approach with enough detail to recognize it next time
4. **Why it failed** — root cause (not just symptoms)
5. **Revive condition** — specific condition that would make retrying worthwhile ("Never" is valid)
6. **Areas** — comma-separated area names this dead-end is associated with (optional)
7. **Tried date** — when this was attempted (`YYYY-MM-DD`; defaults to today)

Dead-ends are universal (not project-specific). Once you have the title at minimum, run:

```bash
lore new dead-end \
  --title "<title>" \
  [--areas "<area1>,<area2>"] \
  [--revive-condition "<condition>"] \
  [--tried "<YYYY-MM-DD>"] \
  [--vault "$LORE_VAULT"]
```

Then open the written file, fill in the body sections with the user's answers, and confirm the note path.

If no session note exists the CLI will print a skip notice — that is expected; continue normally.
