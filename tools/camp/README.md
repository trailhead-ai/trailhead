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
camp bookmark        # bookmark this workspace's harness session under a short ref
camp bookmark ls     # list every bookmark (all groups)
camp bookmark rm <ref>  # drop a bookmark
camp resume <ref>    # re-enter a bookmarked session (needs the shellenv wrapper)
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

### `camp resume` requires the shellenv wrapper

camp never starts, stops, or replaces a process. `camp resume <ref>` only *answers*
where to go and what to run — two lines on stdout: the workspace directory, then
the harness command to run there. Acting on that answer takes a shell function,
which is what the `shellenv` line installs:

```sh
eval "$(/path/to/trailhead/bin/trailhead shellenv)"
```

The wrapper it defines intercepts `camp resume` (and `camp new` / `camp remove`),
does the `cd`, and runs the command. Without it, `camp resume` refuses rather than
printing two inert lines that look like success — so if `camp resume` tells you the
shell integration is not active, add the `shellenv` line to your shell profile.

## Bookmarks

A bookmark is a durable named pointer from a camp workspace to the harness session
you were running in it.

```
camp bookmark [--ref <ref>] [--note <text>]   # capture; run from inside the workspace
camp bookmark ls                              # every bookmark, most recent first
camp bookmark rm <ref>                        # drop one
camp resume <ref>                             # go back
```

Capture is cwd-scoped — it bookmarks the workspace you are standing in, and defaults
the ref to that workspace's slug. `ls`, `rm`, and `resume` address a ref instead, so
they work from any directory, including outside every group. One workspace holds at
most one bookmark: re-capturing under the same ref updates it in place, and moving it
to a different ref means `camp bookmark rm <old-ref>` first.

`camp bookmark ls` marks a row whose workspace or transcript has since disappeared,
and warns with `expires ~Nd` while a transcript is still recoverable but nearing the
harness's own retention cleanup.

## Group setup

```
camp group <name> --member NAME=PATH [--member NAME=PATH ...]
```
Authors a group config TOML and wires SessionStart hooks into each member repo.
