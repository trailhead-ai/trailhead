# camp — group worktree orchestration

camp is an agent-native workflow plugin that gives Claude structured primitives for
managing git worktrees across a configured group of repositories. It handles the
"where is the work happening" question so agents don't have to.

**Standalone use:** camp stands alone — adopt it without lore or craft if you only
want the worktree orchestration.

**Status:** Slice 1 (command skeleton). Full provisioning is Slice 3;
session attach + harness launch is Slice 6. See the root README for install instructions.

## PATH setup

`trailhead install` builds a shim for the `camp` CLI. To put it on your PATH, add
the brew-style `shellenv` line to your shell profile (fish/zsh/bash all handled):

```sh
eval "$(/path/to/trailhead/bin/trailhead shellenv)"
```

Then `camp ai <slug>` works from a plain shell. See the [root README](../../README.md)
for the full install flow.

## Quick start

```
camp ai <slug>       # create or resume a workspace
camp ls              # list all worktrees
camp status          # show git + drift status
camp rm              # tear down a worktree
camp --help          # full command reference
camp --version       # show version + resolved binary path
```

## Group setup

```
camp group <name> --member NAME=PATH [--member NAME=PATH ...]
```
Authors a group config TOML and wires SessionStart hooks into each member repo.

## Deferred commands

`camp restock`, `camp sweep`, `camp code`, and `camp fire` are temporarily
disabled while the worktree flow stabilizes. They will be re-enabled or replaced
in a future slice.
