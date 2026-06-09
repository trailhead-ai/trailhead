---
name: decision
description: Record a non-obvious architectural or design decision as a lightweight ADR. Use for /decision, "record this decision", "ADR this", "document why we chose X over Y".
---

# /lore:decision — Record an architectural decision

Gather the following from the user conversationally, asking only for what is missing:

1. **Title** — short noun phrase describing the decision (required)
2. **Context** — what situation forced a choice
3. **Decision** — what was chosen (one or two sentences)
4. **Rationale** — the load-bearing reason; if this disappears the decision may no longer hold
5. **Consequences** — what this constrains or enables downstream
6. **Areas** — comma-separated area names this decision touches (optional)
7. **Project** — project name (infer from `git remote get-url origin` if not stated)

Once you have title and project at minimum, run:

```bash
lore new decision \
  --title "<title>" \
  --project "<project>" \
  [--areas "<area1>,<area2>"] \
  [--vault "$LORE_VAULT"]
```

Then open the written file, fill in the body sections with the user's answers, and confirm the note path.

Decisions are immutable records — they carry no `status` field. If no session note exists the CLI will print a skip notice — that is expected; continue normally.
