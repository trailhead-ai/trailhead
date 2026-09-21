---
name: review
description: Use when completing tasks, implementing major features, or before merging to verify work meets requirements
---

# Review

**Recommended tier:** Sonnet/low for the caller — this skill is a dispatcher. The `code-reviewer`
subagent it dispatches is pinned to Opus/high, where the actual review reasoning happens.
`/model sonnet` *before* invoking if on Opus. (Advisory — the harness doesn't auto-switch.)

Dispatch code-reviewer subagent to catch issues before they cascade. The reviewer gets precisely
crafted context for evaluation — never your session's history. This keeps the reviewer focused on
the work product, not your thought process, and preserves your own context for continued work.

**Core principle:** Review early, review often.

## When to Request Review

**Mandatory:**
- After completing a whole plan or feature, before merge
- Before merge to main

Per-task conformance during execute is `drift-gate`'s job, not this skill's — see Integration with
Workflows below.

**Optional but valuable:**
- When stuck (fresh perspective)
- Before refactoring (baseline check)
- After fixing complex bug

## How to Request

**1. Run tests first.** Don't waste a code review on broken code. Dispatch `test-runner` to confirm
the suite passes. If anything fails, fix or diagnose (via `troubleshooter`) before proceeding.

**2. Get git SHAs:**
```bash
BASE_SHA=$(git merge-base origin/main HEAD)  # whole-change base — where this plan's work diverged
HEAD_SHA=$(git rev-parse HEAD)
```

**3. Dispatch code-reviewer subagent:**

Use Task tool with code-reviewer type, fill template at `code-reviewer.md`

**4. For security-sensitive diffs, also dispatch `security-auditor`.** If the change touches auth,
input validation, crypto, secrets, or session handling, run both reviews in parallel — they're
complementary.

**Placeholders:**
- `{WHAT_WAS_IMPLEMENTED}` - What you just built
- `{PLAN_OR_REQUIREMENTS}` - What it should do
- `{BASE_SHA}` - Starting commit
- `{HEAD_SHA}` - Ending commit
- `{DESCRIPTION}` - Brief summary

**5. Act on feedback:**
- Fix Critical issues immediately
- Fix Important issues before proceeding
- Note Minor issues for later
- Push back if reviewer is wrong (with reasoning)

## Example

```
[All tasks of the plan are built; ready for the whole-change pass before merge]

You: Let me request code review before merge.

BASE_SHA=$(git merge-base origin/main HEAD)
HEAD_SHA=$(git rev-parse HEAD)

[Dispatch code-reviewer subagent]
  WHAT_WAS_IMPLEMENTED: Full plan — conversation index verification and repair
  PLAN_OR_REQUIREMENTS: the plan/requirements the caller provides
  BASE_SHA: a7981ec
  HEAD_SHA: 3df7661
  DESCRIPTION: Added verifyIndex() and repairIndex() with 4 issue types, across N tasks

[Subagent returns]:
  Verdict: FIX_FIRST
  Important: Missing progress indicators
  Minor: Magic number (100) for reporting interval

You: [Fix progress indicators]
[Re-review or proceed to merge per the verdict]
```

## Integration with Workflows

**Execute (task-by-task subagent development):**
- Per-task conformance is handled inside execute's own step 4, which dispatches `drift-gate` (not
  this skill) after each medium+ task — see the execute skill.
- Dispatch `code-reviewer` here once the plan's tasks are all built, for the whole-change/PR pass
  against the full `base..HEAD` diff.

**Ad-Hoc Development:**
- Review before merge
- Review when stuck

## Closing handoff

Review sits before distill in the pipeline (brainstorm → gauntlet → (slice → plan → execute →
review)* → distill) — merging closes review's own job, not the spec's lifecycle. Under the loop, one
review closes one slice, not the spec: once the reviewed diff is merged, end with a fully-formed
handoff back into `/craft:slice` for the spec's next pass — the real spec id, never a
`<placeholder>`:

> "Review passed and the change is merged. Run this to choose the next slice."

```
/craft:slice spec/streaming-export
```

Hand off to distill instead, fully formed the same way, but only once the slice loop reports the
spec closed out (`craft/slice-loop=complete`, per `../_shared/status-ownership.md`) — never as the
unconditional next step from a per-slice review:

> "The slice loop reports spec/streaming-export closed out. Run this when you're ready to distill
> this work into the ADR log."

```
/craft:distill spec/streaming-export
```

**This handoff is carryoutable.** `distill/SKILL.md` enumerates `ready` specs carrying the
`craft/slice-loop` marker `/craft:slice` writes at its terminating condition, and its completion
gate accepts `craft/slice-loop=complete` — exactly the shape a spec is in when the loop reports it
closed out. A spec the loop *stopped* early is queued too, and distill records it without claiming
it complete.

## Red Flags

**Never:**
- Skip review because "it's simple"
- Ignore Critical issues
- Proceed with unfixed Important issues
- Argue with valid technical feedback

**If reviewer wrong:**
- Push back with technical reasoning
- Show code/tests that prove it works
- Request clarification

See template at: review/code-reviewer.md

## Record links

Link a record mention as `[kind/slug](<base>/records/vault/kind/slug)`: text is
kind/slug only — vault stays in the target — never a bare URL. Resolve base and vault
once per session: base is `LORE_RECORD_URL_BASE`, else `record_url_base` in
`config.json`, used only if `http`/`https`, with a host, and no query, fragment, or
`@` (credentials) — otherwise `http://127.0.0.1:7313`; vault is `lore vault ls`'s path
column matched to the record's path, never a basename. Print it bare when the vault
can't be resolved, isn't a direct child of the vaults root (`$LORE_STATE_DIR/vaults`,
else `$XDG_STATE_HOME/lore/vaults`, else `$HOME/.local/state/lore/vaults`), or vault,
kind, or slug carry a character outside ASCII `a`-`z`, `0`-`9`, `-`. Link first mention
per record per response in prose, every row in a table or list. A handoff command
stays bare; record bodies never link.
Example: `[task/example](http://127.0.0.1:7313/records/trailhead/task/example)`
