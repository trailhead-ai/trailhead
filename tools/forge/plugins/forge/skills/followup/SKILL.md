---
name: followup
description: >
  Batch up post-implementation follow-up items, clarify any ambiguities inline, then dispatch
  `sdd-implementer` to do the actual work in a cheaper subagent context — keeping mechanical
  iteration off the main session's expensive tokens.
  TRIGGER when: user invokes `/followup`, OR provides a numbered/bulleted list of post-implementation
  changes that includes the words "follow up", "follow-up", "followup", "follow ups", "fix-ups",
  "polish", or "iterate on these".
  DO NOT TRIGGER when: single small item (just do it inline), planning a new feature (use `planning`),
  debugging a specific failure (use `systematic-debugging`), or "follow up" appears as a passing
  reference ("I'll follow up on that later") rather than as the framing of a batch.
---

# Followup

Take a batch of post-implementation items, clarify ambiguities, hand off to a subagent to do the work. Main session stays cheap; mechanical iteration runs on Sonnet.

## Why this exists

Post-implementation iteration tends to come in batches: "the button needs to be larger, the empty state isn't handled, line 42 has a typo, and that helper should be extracted." Each item is small. The whole batch is mechanical. There is no reason to burn Opus tokens running the edits, the test re-runs, and the commits in the main session.

This skill is the thin wrapper that turns the batch into a brief, dispatches `sdd-implementer`, and reports back.

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

If the user invoked `/followup` with no items in the same message, ask for the batch and stop.

### 1b. Resolve the parent plan

Before writing anything, identify the plan these follow-ups belong to, so the followup brief links back to its parent via `followup-to:` frontmatter.

Resolve the active session note (e.g. `lore current`) and read its `plan:` frontmatter:

- **Exactly one plan slug listed** → that's the parent. Use its filename stem (e.g. `2026-04-27-survey-config-activation-cascade`) as `<parent-slug>`.
- **Multiple plan slugs** → ask the user which plan these follow-ups apply to in your clarifying turn (step 2).
- **No plan slugs OR session note missing** → either the user is doing follow-ups on work that didn't go through `/planning`, or this is a fresh session. Ask the user for the parent plan filename. If they say "none / standalone", proceed without a parent (`<parent-slug>` becomes the free-form `<feature-slug>` derived from conversation context, and the brief omits `followup-to`).

Also capture the parent's `related-subsystems` from its frontmatter — you'll inherit those into the brief so the followup is associated with the right subsystems for recall.

### 2. Clarify (one consolidated turn, only if needed)

For each item, scan for ambiguity:

- **Intent unclear** — multiple valid interpretations of what to do
- **Scope unclear** — could touch one place or many; the user did not specify
- **Reference unresolved** — "that helper" / "the new field" can't be pinned down from context
- **TDD applicability ambiguous** — code change vs. docs/config; whether tests apply

**Ask ALL clarifying questions in a single consolidated message.** Multi-turn ping-pong defeats the point of the skill — you'd burn the tokens you were trying to save. If everything is clear, skip this step entirely and proceed to step 3.

If a clarifying answer reveals an item is much bigger than a follow-up (architectural change, new feature scope), pull it out of the batch and surface to the user — that item belongs in `planning`, not here.

### 3. Triage per item

For each item, decide:

- **Working directory** — which directory / worktree does this item touch? If your setup spans multiple repos, group items by directory.
- **TDD applies?** — production code → yes; pure docs/config/comments → no. Mark explicitly so the implementer doesn't churn.
- **Dependency order** — if item B depends on item A, note it.

If items span multiple working directories, group them by directory. You will dispatch once per directory (sdd-implementer takes a single working directory per dispatch).

### 4. Write the brief

Write one brief per working directory using `lore new plan`:

```
YYYY-MM-DD-<parent-slug-stripped>-followup-<n>
```

`YYYY-MM-DD` is *today's* date (the followup's creation date), so files sort chronologically alongside other dated plans. `<parent-slug-stripped>` is the parent plan's filename stem with its own leading `YYYY-MM-DD-` prefix removed (e.g. parent `2026-03-05-hybrid-graphql-websocket-authority-implementation` → `hybrid-graphql-websocket-authority-implementation`). `<n>` is always present and starts at `1`; increment to `2`, `3`, ... on collision (multiple briefs against the same parent on the same date). For multi-repo, insert the directory identifier *before* `-followup-<n>`: `YYYY-MM-DD-<parent-slug-stripped>-<dir>-followup-<n>`. If there is no parent plan (standalone path), substitute a free-form `<feature-slug>` for `<parent-slug-stripped>` and omit the `followup-to:` frontmatter field.

Use this structure for the brief:

```markdown
---
type: plan
project: <derive from the vault/repo, or omit if not determinable>
slug: YYYY-MM-DD-<parent-slug-stripped>-followup-<n>
created: YYYY-MM-DD
followup-to: <parent-plan-filename>     # omit if no parent
related-subsystems:                      # inherit from parent
  - <subsystem-from-parent>
related-spec: [[specs/...]]              # optional, only if parent had one
---

# Followups: <feature description>

**Parent plan:** [[plans/<parent-plan-filename>]]   _(or "n/a — standalone follow-up")_
**Working directory:** <absolute path to worktree>
**Feature Flag:** n/a — follow-up iteration on already-shipped code

## Goal

Address <N> follow-up items raised after initial implementation of <feature>. Each item is small/mechanical; this brief batches them for one subagent dispatch.

## Slice 1: Follow-ups batch

### Delivers

1. <Item 1 — concrete, actionable, references files/symbols>
2. <Item 2 — ...>
...
N. <Item N — ...>

### Test contract

For each item that touches production code, the implementer must add or update tests covering:

- <test behavior for item 1, or "no test — docs/config only">
- <test behavior for item 2, ...>
...

### Expected files

- <file:line ranges where known; "TBD by implementer" where not>

### Notes

- <Any clarifications captured from step 2>
- <Dependency order between items, if any>
- <Items explicitly marked "no TDD" with one-line reason>
```

Keep the brief tight. The implementer reads it and works from it — verbosity here costs nothing in dispatch but slows the implementer's first read.

### 5. Dispatch sdd-implementer

One dispatch per brief (per working directory). Use the `Agent` tool with `subagent_type: sdd-implementer`. Pass:

- **plan path** — absolute path to the brief you just wrote
- **slice** — `Slice 1: Follow-ups batch`
- **proven unknowns** — `None`
- **assumption-prover tests to clean up** — `None`
- **working directory** — absolute path to the worktree for this brief

Default model is Sonnet (the agent's frontmatter default). Override to Opus only if a follow-up is integration-heavy (3+ files, cross-module). Most follow-ups stay on Sonnet — that's the whole point.

If you have multiple briefs (multi-repo), dispatch them **serially**, not in parallel — even though they touch different repos, dispatching serially lets you stop early if one fails and lets the user see incremental progress.

### 6. Absorb the report

The implementer returns DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED.

- **DONE:** Summarize for the user — items completed, files touched, tests added, commits made. Two or three sentences.
- **DONE_WITH_CONCERNS:** Read the concerns. If the implementer fixed everything but flagged observations, note them. If the implementer left work undone (e.g. one item was unclear), surface it.
- **NEEDS_CONTEXT:** Re-dispatch with the missing context, OR pull the unclear item out of the batch and ask the user.
- **BLOCKED:** Do not retry blindly. Report to the user with the blocker; decide together whether to (a) re-dispatch with more context, (b) re-dispatch on Opus, (c) pull the item out and handle inline, or (d) drop it.

### 7. Update the brief file

Whatever happens, update the brief to reflect outcomes — check off completed items, note concerns, mark blocked items. The brief is the durable record of the iteration if the session ends.

## Multi-repo handling

If items span multiple working directories:

1. Write one brief per directory in step 4.
2. Dispatch serially in step 5.
3. Aggregate the reports in step 6 — one user-facing summary covering all dispatches, organized by directory.

Do NOT try to write a single brief that asks the implementer to operate on multiple working directories — sdd-implementer enforces worktree-only paths and will refuse.

## Red Flags

**Never:**

- Dispatch without reading the items yourself first. You need to spot ambiguities before the subagent does.
- Skip the clarifying step when items are genuinely unclear, just because it costs a turn. The cost of a wrong-direction subagent run is much higher.
- Auto-open a PR after the dispatch returns — do not auto-open a PR. The user decides when to open one.
- Bundle a real feature change into a follow-up batch. If something turns out to be big, pull it out and route it to `planning`.

## Notes

- Briefs are a useful artifact — they document what changed and why during the iteration phase. Use `lore new plan` to write them into your vault.
- This skill does NOT touch an issue tracker hook. Follow-ups are post-implementation polish, not status transitions. If a follow-up is large enough that the tracker state should advance again, the user should run `/planning`, not `/followup`.
