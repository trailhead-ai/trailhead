---
name: worktree
description: "Create, manage, and tear down git worktrees across a camp group."
version: "0.2.0"
---

# camp worktree skill

This skill gives Claude the ability to orchestrate multi-repo git worktrees
using `camp`. It covers the full worktree lifecycle for a configured group.

## Commands

### Create or resume a workspace
```
camp ai <slug>
```
Creates a new workspace named `worktree-<slug>` across all group members, or
resumes an existing one. Prints a one-line success summary (members · bootstrap
status · manifest path).

### Navigate to a workspace
```
camp cd <slug>
```
Prints the resolved workspace path on stdout for shell cd integration.
Fish shell integration available via `camp shellenv` (see PATH setup below).

### List all worktrees
```
camp ls [--json]
```

### Show worktree status
```
camp status [--name <slug>] [--json]
```
Reconciles manifest membership and per-member git state (branch / dirty / unpushed).

### Activate a member
```
camp enter <member>
```
Fires the member's activation hooks (idempotent) and prints its `CLAUDE.md`
to stdout so the calling agent ingests it.

### Provision member worktrees
```
camp setup [--retry]
```
Provisions or retries pending/failed member worktrees. Runs synchronously;
`--retry` re-runs only non-ready members.

### Tear down a worktree
```
camp rm [--force] [--name <slug>]
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

### Run a command in each member
```
camp foreach [--name <slug>] [--fail-fast] [--json] <cmd…>
```
Executes `<cmd>` in each member worktree (shell=False; metacharacters are
literal).

## Configuration

A camp group is defined by a TOML config at
`trailhead.paths.config_dir("camp")/groups/<group>.toml`. See
`groups.example/trailhead.toml` for the schema.

Author a group config:
```
camp group <name> --member NAME=PATH [--member NAME=PATH ...]
```

## PATH setup (interim, until `trailhead install` automates it)

```fish
fish_add_path ~/code/trailhead/tools/camp/plugins/camp/bin
```

Bash/zsh:
```bash
export PATH="$HOME/code/trailhead/tools/camp/plugins/camp/bin:$PATH"
```
