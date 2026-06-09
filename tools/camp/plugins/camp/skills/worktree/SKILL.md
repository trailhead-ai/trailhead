---
name: worktree
description: "Create, manage, and tear down git worktrees across a camp group."
version: "0.1.0"
---

# camp worktree skill

This skill gives Claude the ability to orchestrate multi-repo git worktrees
using `camp`. It covers the full worktree lifecycle for a configured group.

## Commands

### Create or resume a worktree
```
camp <slug>
```
Creates a new worktree named `worktree-<slug>` across all group members, or
resumes an existing one. Prints a one-line success summary (members · bootstrap
status · manifest path).

### List all worktrees
```
camp ls [--json]
```

### Show worktree status
```
camp status [--name <slug>] [--json] [--stale [--days N]]
```
Reconciles manifest membership, per-member git state (branch / dirty / unpushed),
and drift detection. Retains `dev_env_instance` / `fire_state` keys as null
when no dev-env registry exists (contract stability for future dev-env half).

### Break down a worktree
```
camp break [--force] [--name <slug>]
```
Tears down the worktree. `--force` discards uncommitted changes.

### Sync canonical siblings
```
camp sync [--force] [--json]
```
Fast-forwards each group member's canonical checkout to `origin/main`. Safe by
default (skips dirty / off-main members). `--force` resets.

### Rebase worktree branches
```
camp rebase [--onto <branch>] [--name <slug>]
```

### Refresh dep caches
```
camp restock [--json]
```
Runs each member's configured bootstrap commands against the canonical checkout.

### Run a command in each member
```
camp foreach [--name <slug>] [--fail-fast] [--json] <cmd…>
```
Executes `<cmd>` in each member worktree (shell=False; metacharacters are
literal).

### Report / prune orphan worktrees
```
camp sweep [--prune [--force]] [--json]
```
Reports orphaned `.claude/worktrees/<slug>` dirs with no manifest entry
(classified SAFE / DIRTY / UNMERGED). `--prune` removes SAFE orphans.
Note: dev-env instance teardown in `--prune` is deferred to a future slice.

## Configuration

A camp group is defined by a TOML config at
`trailhead.paths.config_dir("camp")/groups/<group>.toml`. See
`groups.example/trailhead.toml` for the schema.

## PATH setup (interim, until `trailhead install` automates it)

```fish
fish_add_path ~/code/trailhead/tools/camp/plugins/camp/bin
```

Bash/zsh:
```bash
export PATH="$HOME/code/trailhead/tools/camp/plugins/camp/bin:$PATH"
```
