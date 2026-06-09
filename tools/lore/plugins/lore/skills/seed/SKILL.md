---
name: seed
description: >
  Pull a raw capture from `$LORE_VAULT/inbox/` and hand it off to `/lore:brainstorm` as the
  seed material so it gets shaped into a frozen spec instead of rotting in inbox/.
  Use for /lore:seed <doc>, "seed from the X note", "run seed on <doc>", "brainstorm from the inbox".
---

# /lore:seed

Take a rough capture from the inbox and feed it into the brainstorming flow so it gets shaped
into a frozen spec instead of rotting in `inbox/`. Inbox is staging, not storage.

## Process

### 1. Resolve the input

Examine the user's `<doc>` argument against `$LORE_VAULT/inbox/`:

- If the exact file exists under `$LORE_VAULT/inbox/`, use it.
- Otherwise, list `$LORE_VAULT/inbox/` and fuzzy-match against the argument. If ambiguous,
  present the candidates and ask the user which one.
- If nothing matches, list the inbox contents and ask the user to pick.

If `$LORE_VAULT` is not set, default to `~/lore` and announce the resolved vault path.

One doc per run. If the user names multiple, process them sequentially, not merged.

### 2. Read the content

Read the full document. Do not summarize silently — the brainstorming skill benefits from the
raw text.

### 3. Hand off to brainstorm

Invoke the `brainstorm` skill via the Skill tool, passing the raw content (or a tight framing
of it) as the seed idea. The skill takes over from step 1 of its own process (Frame → Poke at
Edges → ...).

Treat the inbox doc as the user's fuzzy starting thought, not a spec. It is not frozen, and
brainstorming is free to reframe, challenge, or split it.

### 4. Issue-tracker linkage (extension point — `issue_tracker`)

After brainstorm produces a `status: ready` spec:

**no issue tracker configured — ticket linkage skipped.** The spec written to
`$LORE_VAULT/specs/` is the output of seed; issue-tracker linkage is optional metadata.

If your environment has an issue-tracker integration, configure it in a local skill extension
and hook it here. See `docs/DEGRADATION.md` for the re-add path.

### 5. Archive the inbox doc (after spec is frozen)

Once brainstorming produces a `status: ready` spec (and after step 4 completes or is skipped):

- Move the original inbox file to `$LORE_VAULT/inbox/_archive/` (create dir if missing) so
  the inbox stays signal-only.
- Append a one-line pointer to the new spec inside the archived file:
  `> Promoted to [[specs/YYYY-MM-DD-<topic>]]`

If brainstorming is paused or deferred rather than finalized, leave the inbox file where it is.

## Key Principles

- **Inbox is staging, not storage.** Every seed run should either promote the doc to a spec
  or leave it with a clear next action.
- **Don't skip brainstorming.** Even if the inbox doc looks plan-ready, run it through
  brainstorming's Poke-at-Edges — inbox drops are written fast and almost always have hidden
  assumptions.
- **One doc per run.** Process multiple docs sequentially, never merged.
- **The spec is the output of seed; issue-tracker linkage is optional metadata.** Never let
  tracker-integration failures block or delay the spec.
