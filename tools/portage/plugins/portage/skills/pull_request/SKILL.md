---
name: pull_request
description: >
  Unified PR lifecycle for a camp group: create, update, monitor CI, and merge.
  Use for /portage:pull_request create|update|monitor|merge, "create a PR",
  "open a PR", "push for review", "update the PR", "watch the PRs", "monitor
  CI", or "merge the PRs". Replaces the retired /portage:open, /portage:update,
  /portage:monitor, and /portage:merge skills — see "Renamed from" below.
---

# Pull Request

**Recommended tier:** Sonnet/medium — most work is dispatched to subagents.

**Agents:** `updater` (push orchestration), `monitor` (background watch + merge loop)
**CLI:** `portage` (subcommands `detect-repos`, `check-status`, `evaluate-status`,
`summarize`, `merge`, `wait-for-actionable`, `sidecar` — run `portage --help` for
the full list) — on `$PATH` when the plugin is enabled

## Verb parsing

`$ARGUMENTS` carries the full literal text typed after `/portage:pull_request`.
The **first whitespace-separated token is the verb**; everything after it is
the `rest`, passed through unmodified to that verb's dispatch below. `$0` (or
`$ARGUMENTS[0]`) gives the same first token via the harness's own tokenized
index access, if a stricter split than "first word of the string" is wanted.

Worked examples:

| Typed | verb | rest |
|---|---|---|
| `/portage:pull_request create` | `create` | (empty) |
| `/portage:pull_request create 123` | `create` | `123` |
| `/portage:pull_request update` | `update` | (empty) |
| `/portage:pull_request monitor` | `monitor` | (empty) |
| `/portage:pull_request merge repo1:42:alice repo2:43:bob` | `merge` | `repo1:42:alice repo2:43:bob` |

For example, `/portage:pull_request create 123` parses to `verb=create`, `rest=123`.

The verb must be one of `create` / `update` / `monitor` / `merge`. Any other
first token — or no arguments at all — is an unrecognized verb: report that
plainly rather than guessing which flow was meant.

## Renamed from

This skill replaces four retired skills. An agent or user still reaching for
the old names should use the verb form instead — the retired names are gone,
not aliased:

| Retired | Use instead |
|---|---|
| `/portage:open` | `/portage:pull_request create` |
| `/portage:update` | `/portage:pull_request update` |
| `/portage:monitor` | `/portage:pull_request monitor` |
| `/portage:merge` | `/portage:pull_request merge` |

## `create` — open PRs

Commit, run local checks, push, open PRs for all active repos in the camp
group, then watch CI until all PRs are mergeable.

### Step 1 — Dispatch `updater` for the push tail

Dispatch the `updater` subagent (Sonnet/medium) to handle the mechanical tail:
detect repos, preflight, push, open PRs, link siblings, record prs.json sidecar.
Every camp group member is a peer — no privileged member.

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
`<repo_path>:<pr_number>:<member_name>`. Parse that line — you need it for Step 2.

### Step 2 — Launch monitor from the top-level session

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

## `update` — push new commits to existing PRs

Re-run checks, push new commits, update existing PRs for all active repos in the
camp group, then watch CI until all PRs are mergeable.

### Step 1 — Dispatch updater

Dispatch the `updater` subagent (Sonnet/medium) in `update` mode to push commits
and update existing PRs:

```
Agent({
  subagent_type: "updater",
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

### Step 2 — Launch monitor from the top-level session

**This must happen in the main session, not inside another subagent.**

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

## `monitor` — watch CI

Monitor CI on open PRs for a camp group, fix failures, loop until all PRs are
mergeable, then merge in dependency order.

**Implementation:** this verb dispatches the `monitor` subagent (Sonnet/medium),
which runs the watch+fix+merge loop, the action table, blocker handling, and the
merge/cleanup sequence.

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

If `pr_pairs` is unknown (i.e. `rest` was empty), the agent will detect it via
`portage detect-repos` + `gh pr list`. The review bot (if configured in
`[release].review_bot_login`) and external tracker (if configured in
`[release].external_tracker`) take no action when absent from the group TOML.

`<repo_path>` in `pr_pairs` is a LOCAL filesystem path. The downstream scripts use
it as `cwd=` for `gh` calls. `portage evaluate-status` fails fast with a clear error
if the path is not a directory.

Tell the user: "Watching PRs in the background — I'll notify you when they're merged
or need attention." Then return.

## `merge` — merge in dependency order

Merge PRs in dependency order after verifying all are mergeable and clean.
Use when monitor reports all PRs are done and you want to merge immediately
without the watch loop.

Merges open PRs for a camp group in dependency order. Stops if any PR is not ready.
Every camp group member is a peer — there is no privileged member.

1. **Collect all open PR pairs.** If `rest` didn't supply them, detect them:

   ```bash
   portage detect-repos --manifest <manifest_path>
   ```

   For each detected repo: `gh pr list --head <branch> --json number --jq '.[0].number'`.

2. **Run `portage merge`** with the collected pairs in `<repo_path>:<pr_number>:<member_name>` format:

   ```bash
   portage merge \
     --manifest <manifest_path> \
     --toml <group_toml_path> \
     <repo1>:<pr1>:<member1> [<repo2>:<pr2>:<member2> ...]
   ```

   Output JSON: `{"merged": [...], "failed": {...}, "skipped": {...}}`.

   `portage merge` reads `merge_order` from the `[release]` block of the group TOML.
   For >1 PR without `merge_order` declared, the command refuses with a named error —
   honor that exit code:
   `BLOCKED: portage merge requires merge_order configured in [release] of the group TOML`

   `portage merge` also reads `auto_merge` from the same `[release]` block and
   refuses (exit 2) unless it is explicitly `true` — fail-closed by default. Honor
   that exit code the same way; the refusal message names the remediation:
   `refusing to merge — auto_merge is unset/false — add [release] auto_merge = true
   to the group TOML to merge automatically.`

3. **Report results.** If any failed, surface the reason and suggest next steps.

4. **Clean up** after all PRs merge:

   ```bash
   git -C <repo> checkout main && git -C <repo> pull
   ```

   for each merged repo.

### External tracker

After successful merge, check `[release].external_tracker` in the group TOML.
No issue tracker configured — status transitions skipped. Configure
`[release].external_tracker` in the group TOML to wire a tracker.

## Notes

- Conventional commits: `feat:`, `fix:`, `chore:`
- Merges happen explicitly via `portage merge`, not `gh pr merge --auto`.
- Review bot login: configured in `[release].review_bot_login` of the group TOML;
  default none (CI-only mode). No configured review bot — no review action is emitted.
- Merge order: configured in `[release].merge_order` of the group TOML; absent for
  single-PR groups (no order needed); required for >1 PR to avoid silent mis-merges.
- No issue tracker configured — status transitions skipped. Configure
  `[release].external_tracker` in the group TOML to wire a tracker.
- The `portage` CLI is on `$PATH` via trailhead's CLI shim dir (same mechanism as
  `camp`/`lore`), once `eval "$(trailhead shellenv)"` is in the user's shell profile.
