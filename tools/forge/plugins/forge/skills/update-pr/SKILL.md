---
name: update-pr
description: >
  Re-run checks, push new commits, update existing PRs for all active repos in the
  camp group, then watch CI until all PRs are mergeable. Use for /update-pr,
  "update the PR", "push the latest changes", or when fixes have been applied to
  an existing PR.
---

# PR Update

**Implementation:** this skill is a thin wrapper around the `pr-updater` subagent
(Sonnet/medium), with a single follow-up step to launch `watch-pr` from the main
session. All push orchestration happens inside the subagent. The main session sees
a summary plus a `pr_pairs` line it uses to start the background watch.

**Agents:** `pr-updater` (push orchestration), `watch-pr` (background watch + merge loop)
**Scripts:** `detect_repos.py`, `merge_prs.py`, `release_prs_sidecar.py`

## Step 1 — Dispatch pr-updater

Dispatch the `pr-updater` subagent (Sonnet/medium) in `update` mode to push commits
and update existing PRs:

```
Agent({
  subagent_type: "pr-updater",
  description: "Update PRs for this camp group",
  prompt: |
    mode: update
    group: <camp group name>
    slug: <worktree slug>
    manifest_path: <absolute path to camp manifest JSON>
    group_toml_path: <absolute path to group TOML>
})
```

The subagent returns a summary with PR URLs and a `pr_pairs:` line listing each
`<repo_path>:<pr_number>:<member_name>`. Parse that line.

If preflight fails, decide whether to fix and re-dispatch, or dispatch `troubleshooter`
to diagnose. If `pr_pairs` is empty, skip Step 2.

## Step 2 — Launch watch-pr from the top-level session

**This must happen in the main session, not inside another subagent.**

```
Agent({
  subagent_type: "watch-pr",
  description: "watch-pr for <worktree-slug>",
  run_in_background: true,
  prompt: |
    group: <camp group name>
    slug: <worktree slug>
    manifest_path: <absolute path to camp manifest JSON>
    group_toml_path: <absolute path to group TOML>
    pr_pairs: <copy verbatim from pr-updater's pr_pairs line>
})
```

Tell the user: "Watching PRs in the background — I'll notify you when they're merged
or need attention." Then relay pr-updater's summary and return.

## Notes

- Conventional commits: `feat:`, `fix:`, `chore:`
- No issue tracker configured — status transitions skipped. Configure
  `[release].external_tracker` in the group TOML to wire a tracker.
