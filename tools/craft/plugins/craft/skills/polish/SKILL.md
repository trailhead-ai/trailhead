---
name: polish
description: >
  Batch up post-implementation code fix-ups, clarify any ambiguities inline, then dispatch
  `executor` to do the actual work in a cheaper subagent context — keeping mechanical
  iteration off the main session's expensive tokens.
  TRIGGER when: user invokes `/polish`, OR provides a numbered/bulleted list of post-implementation
  CODE changes framed as "polish these fix-ups", "fix-ups", "iterate on these", "clean these up",
  or "tidy these up".
  DO NOT TRIGGER when: single small item (just do it inline), planning a new feature (use `plan`),
  debugging a specific failure, or the user wants to NOTE / capture a
  follow-up for later (that is lore's `follow-up` capture — "note a follow-up", "put this on my
  follow-ups" — not a batch of code edits to run now).
---

# Polish

Take a batch of post-implementation code fix-ups, clarify ambiguities, hand off to a subagent to do the work. Main session stays cheap; mechanical iteration runs on Sonnet.

**Not to be confused with lore's `follow-up` capture.** This skill *runs* a batch of mechanical code edits now (it dispatches `executor`). Lore's `follow-up` *records* a thing to revisit later. "Polish these fix-ups" → here; "note a follow-up" → lore.

## Why this exists

Post-implementation iteration tends to come in batches: "the button needs to be larger, the empty state isn't handled, line 42 has a typo, and that helper should be extracted." Each item is small. The whole batch is mechanical. There is no reason to burn Opus tokens running the edits, the test re-runs, and the commits in the main session.

This skill turns the batch into a brief, dispatches `executor`, and reports back.

## Skip Gate

**Just do it inline when:**

- Single item, ≤10 lines, obvious fix (typo, rename, missing import). Dispatch overhead exceeds the work.
- Item requires interactive judgment that cannot be captured in a brief (e.g. "tell me what you think about X" — that's a conversation, not a follow-up).
- The user explicitly asks you to do it ("just fix this real quick").

**Use this skill when:**

- 2+ items in a batch, OR
- 1 non-trivial item (multi-file, needs TDD, needs careful test contract), OR
- The user has framed the chunk as follow-ups regardless of size.

When in doubt, use the skill — the marginal cost of dispatch is small; the cost of burning Opus on mechanical work is real.

## Process

### 1. Capture the batch

Read every item the user provided. Number them 1..N if not already numbered. Resolve "this", "that", "the helper" to concrete files/symbols by referring back to recent conversation context — if you cannot resolve a reference, that's a clarifying question (step 2).

If the user invoked `/polish` with no items in the same message, ask for the batch and stop.

### 1b. Resolve the parent task

Before writing anything, identify the plan these follow-ups belong to — a plan is a parent `task` record — so the followup brief links back to its parent via `followup-to:` frontmatter.

Resolve the active session note (e.g. `lore current`) and read the plan task it links to (the `related` task on the session note, or the task named in its frontmatter):

- **Exactly one plan task** → that's the parent. Use its record name (e.g. `2026-04-27-survey-config-activation-cascade`) as `<parent-slug>`.
- **Multiple plan tasks** → ask the user which one these follow-ups apply to in your clarifying turn (step 2).
- **No plan task OR session note missing** → either the user is doing fix-ups on work that didn't go through `/plan`, or this is a fresh session. Ask the user for the parent task name. If they say "none / standalone", proceed without a parent (`<parent-slug>` becomes the free-form `<feature-slug>` derived from conversation context, and the brief omits `followup-to`).

Also capture the parent's `related-subsystems` from its frontmatter — you'll inherit those into the brief so the followup is associated with the right subsystems for recall.

### 2. Clarify (one consolidated turn, only if needed)

For each item, scan for ambiguity:

- **Intent unclear** — multiple valid interpretations of what to do
- **Scope unclear** — could touch one place or many; the user did not specify
- **Reference unresolved** — "that helper" / "the new field" can't be pinned down from context
- **TDD applicability ambiguous** — code change vs. docs/config; whether tests apply

**Ask ALL clarifying questions in a single consolidated message.** Multi-turn ping-pong defeats the point of the skill — you'd burn the tokens you were trying to save. If everything is clear, skip this step entirely and proceed to step 3.

If a clarifying answer reveals an item is much bigger than a follow-up (architectural change, new feature scope), pull it out of the batch and surface to the user — that item belongs in `plan`, not here.

### 3. Triage per item

For each item, decide:

- **Working directory** — which directory / worktree does this item touch? If your setup spans multiple repos, group items by directory.
- **TDD applies?** — production code → yes; pure docs/config/comments → no. Mark explicitly so the executor doesn't churn.
- **Dependency order** — if item B depends on item A, note it.

If items span multiple working directories, group them by directory. You will dispatch once per directory (executor takes a single working directory per dispatch).

### 4. Write the brief

Write one brief per working directory, persisting it with `lore record create` (`../_shared/note-storage.md`) — `printf '%s' "$BODY" | lore record create --kind task --title "<brief>" --status ready`:

```
YYYY-MM-DD-<parent-slug-stripped>-followup-<n>
```

`YYYY-MM-DD` is *today's* date (the followup's creation date), so records sort chronologically alongside other dated tasks. `<parent-slug-stripped>` is the parent task's record name with its own leading `YYYY-MM-DD-` prefix removed (e.g. parent `2026-03-05-hybrid-graphql-websocket-authority-implementation` → `hybrid-graphql-websocket-authority-implementation`). `<n>` is always present and starts at `1`; increment to `2`, `3`, ... on collision (multiple briefs against the same parent on the same date). For multi-repo, insert the directory identifier *before* `-followup-<n>`: `YYYY-MM-DD-<parent-slug-stripped>-<dir>-followup-<n>`. If there is no parent task (standalone path), substitute a free-form `<feature-slug>` for `<parent-slug-stripped>` and omit the `followup-to:` frontmatter field.

Use this structure for the brief:

```markdown
---
type: task
project: <derive from the vault/repo, or omit if not determinable>
slug: YYYY-MM-DD-<parent-slug-stripped>-followup-<n>
created: YYYY-MM-DD
followup-to: <parent-task-name>           # omit if no parent
related-subsystems:                      # inherit from parent
  - <subsystem-from-parent>
related-spec: [[specs/...]]              # optional, only if parent had one
---

# Followups: <feature description>

**Parent task:** [[task/<parent-task-name>]]   _(or "n/a — standalone follow-up")_
**Working directory:** <absolute path to worktree>

## Goal

Address <N> follow-up items raised after initial implementation of <feature>. Each item is small/mechanical; this brief batches them for one subagent dispatch.

## Slice 1: Follow-ups batch

### Delivers

1. <Item 1 — concrete, actionable, references files/symbols>
2. <Item 2 — ...>
...
N. <Item N — ...>

### Test contract

For each item that touches production code, the executor must add or update tests covering:

- <test behavior for item 1, or "no test — docs/config only">
- <test behavior for item 2, ...>
...

### Expected files

- <file:line ranges where known; "TBD by executor" where not>

### Notes

- <Any clarifications captured from step 2>
- <Dependency order between items, if any>
- <Items explicitly marked "no TDD" with one-line reason>
```

Keep the brief tight. The executor reads it and works from it — verbosity here costs nothing in dispatch but slows the executor's first read.

### 5. Dispatch executor

One dispatch per brief (per working directory). Use the `Agent` tool with `subagent_type: executor`. Pass:

- **plan path** — absolute path to the brief you just wrote
- **slice** — `Slice 1: Follow-ups batch`
- **proven unknowns** — `None`
- **assumption-prover tests to clean up** — `None`
- **working directory** — absolute path to the worktree for this brief

Default model is Sonnet (the agent's frontmatter default). Override to Opus only if a follow-up is integration-heavy (3+ files, cross-module). Most follow-ups stay on Sonnet — that's the whole point.

If you have multiple briefs (multi-repo), dispatch them **serially**, not in parallel — even though they touch different repos, dispatching serially lets you stop early if one fails and lets the user see incremental progress.

### 6. Absorb the report

The executor returns DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED.

- **DONE:** Summarize for the user — items completed, files touched, tests added, commits made. Two or three sentences.
- **DONE_WITH_CONCERNS:** Read the concerns. If the executor fixed everything but flagged observations, note them. If the executor left work undone (e.g. one item was unclear), surface it.
- **NEEDS_CONTEXT:** Re-dispatch with the missing context, OR pull the unclear item out of the batch and ask the user.
- **BLOCKED:** Do not retry blindly. Report to the user with the blocker; decide together whether to (a) re-dispatch with more context, (b) re-dispatch on Opus, (c) pull the item out and handle inline, or (d) drop it.

### 7. Update the brief file

Whatever happens, update the brief to reflect outcomes — check off completed items, note concerns, mark blocked items. The brief is the durable record of the iteration if the session ends.

## Multi-repo handling

If items span multiple working directories:

1. Write one brief per directory in step 4.
2. Dispatch serially in step 5.
3. Aggregate the reports in step 6 — one user-facing summary covering all dispatches, organized by directory.

Do NOT try to write a single brief that asks the executor to operate on multiple working directories — executor enforces worktree-only paths and will refuse.

## Red Flags

**Never:**

- Dispatch without reading the items yourself first. You need to spot ambiguities before the subagent does.
- Skip the clarifying step when items are genuinely unclear, just because it costs a turn. The cost of a wrong-direction subagent run is much higher.
- Auto-open a PR after the dispatch returns — do not auto-open a PR. The user decides when to open one.
- Bundle a real feature change into a follow-up batch. If something turns out to be big, pull it out and route it to `plan`.

## Notes

- Briefs are a useful artifact — they document what changed and why during the iteration phase. Persist them with `lore record create` (`../_shared/note-storage.md`) to write them into your vault.
