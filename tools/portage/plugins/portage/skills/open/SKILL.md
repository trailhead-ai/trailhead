---
name: open
description: >
  Commit, run local checks, push, open PRs for all active repos in the camp group,
  then watch CI until all PRs are mergeable. Use for /portage:open, "create a PR",
  "open a PR", "push for review", or when implementation is complete and ready for PR.
---

# PR Open

**Recommended tier:** Sonnet/medium — most work is dispatched to subagents.
The remaining inline work is judgment over review feedback.

**Agents:** `updater` (push orchestration), `monitor` (background watch + merge loop)
**Scripts:** `detect_repos.py`, `merge_prs.py`, `release_prs_sidecar.py`

## Step 1 — Code simplification

Dispatch a **code-simplifier:code-simplifier** subagent to review recently modified files
for reuse, quality, and efficiency. Apply any fixes, commit them, then proceed to Step 2.

## Step 2 — Code review

Dispatch a **code-reviewer** subagent with:
- `BASE_SHA` = merge-base with `origin/main`
- `HEAD_SHA` = current HEAD
- `WHAT_WAS_IMPLEMENTED` / `DESCRIPTION` = summary of changes on this branch

Use the template at `skills/review/code-reviewer.md` for the prompt.

Evaluate and act on feedback per the `receiving-code-review` skill:
- Fix Critical and Important issues immediately
- Commit fixes before proceeding
- Push back on incorrect feedback with technical reasoning

If the reviewer says "Ready to merge: No", fix blocking issues and re-request once.
If still blocked, stop and report to the user.

## Step 3 — Dispatch `updater` for the push tail

Once review is clean, dispatch the `updater` subagent (Sonnet/medium) to handle the
mechanical tail: detect repos, preflight, push, open PRs, link siblings, record prs.json
sidecar. Every camp group member is a peer — no privileged member.

```
Agent({
  subagent_type: "updater",
  description: "Open PRs for this camp group",
  prompt: |
    mode: create
    group: <camp group name>
    slug: <worktree slug>
    manifest_path: <absolute path to camp manifest JSON>
    group_toml_path: <absolute path to group TOML>
})
```

The subagent returns a summary with PR URLs and a `pr_pairs:` line listing each
`<repo_path>:<pr_number>:<member_name>`. Parse that line — you need it for Step 4.

## Step 4 — Launch monitor from the top-level session

**This must happen in the main session, not inside another subagent.** Background agents
dispatched from inside a subagent lose their notification channel when the subagent returns.

```
Agent({
  subagent_type: "monitor",
  description: "monitor for <worktree-slug>",
  run_in_background: true,
  prompt: |
    group: <camp group name>
    slug: <worktree slug>
    manifest_path: <absolute path to camp manifest JSON>
    group_toml_path: <absolute path to group TOML>
    pr_pairs: <copy verbatim from updater's pr_pairs line>
})
```

Tell the user: "Watching PRs in the background — I'll notify you when they're merged
or need attention." Then relay updater's summary and return.

## Notes

- Conventional commits: `feat:`, `fix:`, `chore:`
- The interactive review phase (Step 2) is kept inline — that's where human judgment
  is load-bearing. Everything after review is mechanical.
- No issue tracker configured — status transitions skipped. Configure
  `[release].external_tracker` in the group TOML to wire a tracker.
