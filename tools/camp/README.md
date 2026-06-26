# camp — group worktree orchestration

camp is an agent-native workflow plugin that gives Claude structured primitives for
managing git worktrees across a configured group of repositories. It handles the
"where is the work happening" question so agents don't have to.

**Standalone use:** camp stands alone — adopt it without lore or craft if you only
want the worktree orchestration.

## PATH setup

`trailhead install` builds a shim for the `camp` CLI. To put it on your PATH, add
the brew-style `shellenv` line to your shell profile (fish/zsh/bash all handled):

```sh
eval "$(/path/to/trailhead/bin/trailhead shellenv)"
```

Then `camp new <slug>` works from a plain shell. See the [root README](../../README.md)
for the full install flow.

## Quick start

```
camp new <slug>      # create or enter a workspace
camp pwd <slug>      # print workspace path
camp list            # list all worktrees (alias: ls)
camp status          # show git + drift status
camp remove          # tear down a worktree (alias: rm)
camp --help          # full command reference
camp --version       # show version + resolved binary path
```

## Shell integration

`camp pwd <slug>` prints the resolved workspace path on stdout (exactly one line).
Use it directly to change directory:

```sh
cd "$(camp pwd <slug>)"
```

Wrap it in your own alias or shell function if you use it frequently. For example, in fish:

```fish
function camp_cd
    cd (camp pwd $argv)
end
```

## Group setup

```
camp group <name> --member NAME=PATH [--member NAME=PATH ...]
```
Authors a group config TOML and wires SessionStart hooks into each member repo.
