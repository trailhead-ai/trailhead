---
name: review
description: Use when completing tasks, implementing major features, or before merging to verify work meets requirements
---

# Review

**Recommended tier:** Sonnet/low for the caller — this skill is a dispatcher. The `code-reviewer` subagent it dispatches is pinned to Opus/high, where the actual review reasoning happens. `/model sonnet` *before* invoking if on Opus. (Advisory — the harness doesn't auto-switch.)

Dispatch code-reviewer subagent to catch issues before they cascade. The reviewer gets precisely crafted context for evaluation — never your session's history. This keeps the reviewer focused on the work product, not your thought process, and preserves your own context for continued work.

**Core principle:** Review early, review often.

## When to Request Review

**Mandatory:**
- After completing a whole plan or feature, before merge
- Before merge to main

Per-slice conformance during execute is `drift-gate`'s job, not this skill's — see Integration with Workflows below.

**Optional but valuable:**
- When stuck (fresh perspective)
- Before refactoring (baseline check)
- After fixing complex bug

## How to Request

**1. Run tests first.** Don't waste a code review on broken code. Dispatch `test-runner` to confirm the suite passes. If anything fails, fix or diagnose (via `troubleshooter`) before proceeding.

**2. Get git SHAs:**
```bash
BASE_SHA=$(git rev-parse HEAD~1)  # or origin/main
HEAD_SHA=$(git rev-parse HEAD)
```

**3. Dispatch code-reviewer subagent:**

Use Task tool with code-reviewer type, fill template at `code-reviewer.md`

**4. For security-sensitive diffs, also dispatch `security-auditor`.** If the change touches auth, input validation, crypto, secrets, or session handling, run both reviews in parallel — they're complementary.

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
[Just completed Task 2: Add verification function]

You: Let me request code review before proceeding.

BASE_SHA=$(git log --oneline | grep "Task 1" | head -1 | awk '{print $1}')
HEAD_SHA=$(git rev-parse HEAD)

[Dispatch code-reviewer subagent]
  WHAT_WAS_IMPLEMENTED: Verification and repair functions for conversation index
  PLAN_OR_REQUIREMENTS: Task 2 from the plan/requirements the caller provides
  BASE_SHA: a7981ec
  HEAD_SHA: 3df7661
  DESCRIPTION: Added verifyIndex() and repairIndex() with 4 issue types

[Subagent returns]:
  Strengths: Clean architecture, real tests
  Issues:
    Important: Missing progress indicators
    Minor: Magic number (100) for reporting interval
  Assessment: Ready to proceed

You: [Fix progress indicators]
[Continue to Task 3]
```

## Integration with Workflows

**Execute (slice-by-slice subagent development):**
- Per-slice conformance is handled inside execute's own step 4, which dispatches `drift-gate` (not this skill) after each medium+ slice — see the execute skill.
- Dispatch `code-reviewer` here once the plan's slices are all built, for the whole-change/PR pass against the full `base..HEAD` diff.

**Ad-Hoc Development:**
- Review before merge
- Review when stuck

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
