# camp — group worktree orchestration

camp is an agent-native workflow plugin that gives Claude structured primitives for
managing git worktrees across a configured group of repositories. It handles the
"where is the work happening" question so agents don't have to.

**Standalone use:** camp stands alone — adopt it without lore or craft if you only
want the worktree orchestration.

**Status:** Slice 0 (scaffold + worktree spine). Group config wiring is Slice 1;
full multi-member lifecycle is Slice 2. See the root README for install instructions.

## PATH setup

`trailhead install` builds a shim for the `camp` CLI. To put it on your PATH, add
the brew-style `shellenv` line to your shell profile (fish/zsh/bash all handled):

```sh
eval "$(/path/to/trailhead/bin/trailhead shellenv)"
```

Then `camp <slug>` works from a plain shell. See the [root README](../../README.md)
for the full install flow.

## Quick start

```
camp <slug>          # create or resume a worktree
camp ls              # list all worktrees
camp status          # show git + drift status
camp break           # tear down a worktree
camp --help          # full command reference
camp --version       # show version + resolved binary path
```

## Dev-env commands

`camp fire` (dev-env management) is deferred — it will cover provision and
teardown of local dev-env instances once the engine ships.
