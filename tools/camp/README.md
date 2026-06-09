# camp — group worktree orchestration

camp is an agent-native workflow plugin that gives Claude structured primitives for
managing git worktrees across a configured group of repositories. It handles the
"where is the work happening" question so agents don't have to.

**Standalone use:** camp stands alone — adopt it without lore or forge if you only
want the worktree orchestration.

**Status:** Slice 0 (scaffold + quarried worktree spine). Group config wiring is
Slice 1; full multi-member lifecycle is Slice 2. See the trailhead root README for
install instructions.

## PATH setup (until `trailhead install` automates it)

Add camp's `bin/` to your shell PATH so `camp <slug>` works from a plain shell:

**fish:**
```fish
fish_add_path ~/code/trailhead/tools/camp/plugins/camp/bin
```

**bash/zsh:**
```bash
export PATH="$HOME/code/trailhead/tools/camp/plugins/camp/bin:$PATH"
```

Add the appropriate line to your shell rc file (`~/.config/fish/config.fish`,
`~/.bashrc`, or `~/.zshrc`).

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

`camp fire` (dev-env management) is deferred — see
`deferred/2026-06/camp-dev-env-engine-half` for the revive trigger.
