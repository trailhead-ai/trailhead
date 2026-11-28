#!/usr/bin/env bash
# runs the shared docket scaffold, then plants a user-level `backlog` skill in the run's config dir,
# the only place the runner's session sees a user-level skill, so the docket competes with a rival for the old name
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$here/../../docket-scaffold.sh"
decoy="$(dirname "$HOME")/config/skills/backlog"
mkdir -p "$decoy"
cat > "$decoy/SKILL.md" <<'SKILL'
---
name: backlog
description: Show and keep the backlog's Work and Personal lanes (next, list, add, move, remove), start an item in a camp workspace (start), and ignore, unignore, pin or unpin a workspace, and refresh the backlog page. Use when the user invokes /backlog, or asks "what's next", "what should I work on", "open my backlog", "add this to my backlog", "move that up", or "remove that from the backlog". `next` is the default with no argument; `scan` arrives in a later slice.
---

# Backlog

Show or change the backlog lanes the user asks about.
SKILL
