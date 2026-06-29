---
name: monitor
description: >
  Monitor CI on open PRs for a camp group, fix failures, loop until all PRs are
  mergeable, then merge in dependency order. Use for /portage:monitor, "watch the PRs",
  "monitor CI", "wait for checks", or when PRs are open and need monitoring.
---

# PR Monitor

**Implementation:** this skill dispatches the `monitor` subagent (Sonnet/medium),
which runs the watch+fix+merge loop, the action table, blocker handling, and the
merge/cleanup sequence.

**Agents:** `monitor`
**Scripts:** `wait_for_actionable.py`, `pr_evaluate_status.py`, `merge_prs.py`

## Dispatch

**This must be launched from the top-level session, never from inside another subagent.**
Nested background agents lose their notification channel when the outer subagent returns,
so monitor's merge/blocker notifications never reach the user.

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
    pr_pairs: <repo1_path>:<pr1>:<member1>, <repo2_path>:<pr2>:<member2>, ...
})
```

If `pr_pairs` is unknown, the agent will detect it via `detect_repos.py` +
`gh pr list`. The review bot (if configured in `[release].review_bot_login`) and
external tracker (if configured in `[release].external_tracker`) take no action
when absent from the group TOML.

`<repo_path>` in `pr_pairs` is a LOCAL filesystem path. The downstream scripts use
it as `cwd=` for `gh` calls. `pr_evaluate_status.py` fails fast with a clear error
if the path is not a directory.

Tell the user: "Watching PRs in the background — I'll notify you when they're merged
or need attention." Then return.

## Notes

- Merges happen explicitly via `merge_prs.py`, not `gh pr merge --auto`.
- Review bot login: configured in `[release].review_bot_login` of the group TOML;
  default none (CI-only mode). No configured review bot — no review action is emitted.
- The agent infers scripts from context; SCRIPTS_DIR is `<portage_plugin_root>/scripts/`.
