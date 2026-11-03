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
- Read `../_shared/council.md` now: its "How a finding reads" section governs how you tell the user
  what the reviewer found
- Fix Critical issues immediately
- Fix Important issues before proceeding
- Note Minor issues for later, except a verified finding that a different record is wrong with a
  known fix: list it as `_shared/council.md` defines under "Corrections to other records"
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

Review sits before tidy-followups and distill in the pipeline (brainstorm → gauntlet → (slice → plan
→ execute → review)* → tidy-followups → distill) — merging closes review's own job, not the spec's
lifecycle. Under the loop, one review closes one slice, not the spec. Once the reviewed diff is
merged, read the spec record (`lore record show spec/<id>`) and end with one fully-formed handoff,
the real spec id, never a `<placeholder>`. Which one depends on whether the spec's slice loop has
ended — it has when the spec carries `craft/slice-loop=complete` or `craft/slice-loop=stopped` (per
`../_shared/status-ownership.md`), or its status is `complete`.

**The slice loop is still running** (status `ready`, no slice-loop marker): hand back into
`/craft:slice` for the spec's next pass:

> "Review passed and the change is merged. Run this to choose the next slice."

```
/craft:slice spec/streaming-export
```

**The slice loop has ended:** hand off to tidy-followups, bare and fenced on its own line, so the
spec's recorded follow-ups are weighed before it is distilled. Never `/craft:distill` — tidy-followups
prints the build and distill commands once it is done:

> "The slice loop for spec/streaming-export has ended. Run this to choose which of its follow-ups to
> do now; it hands you the distill command after."

```
/craft:tidy-followups spec/streaming-export
```

When the loop *stopped* early (`craft/slice-loop=stopped`), the handoff is the same fenced
tidy-followups command, plus one prose line — not a second fenced command — saying the slice ritual
can be re-run once the blocker clears (for example: "Once the blocker is cleared, re-run `/craft:slice`
on this spec to pick up the unmet criteria.").

**Check the spec id before printing it.** It must match `^[A-Za-z0-9._/-]+$`. If it does not, refuse
the handoff and say the spec id failed the shape check, rather than printing a command built from it.

**This handoff is carryoutable.** `tidy-followups/SKILL.md` accepts exactly the shapes it is handed
here — a spec carrying a completed or stopped `craft/slice-loop` marker, or one at status `complete`
— and refuses any other with `slice-loop-not-ended`.

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
