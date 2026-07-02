# Manual Smoke Test — Plugin-System Boundary

This document is the dev-time acceptance test for the plugin-system boundary
that unit tests cannot reach: hook registration, `${CLAUDE_PLUGIN_ROOT}`
resolution, and skill namespacing.

For an adopter quickstart, see the README instead.

---

## Prerequisites

- Claude Code installed and authenticated.
- This repo checked out locally (adjust the path below to match).
- Python 3.11+ on `PATH` as `python3`.

---

## Steps

### 1. Add the local marketplace

In a Claude Code session, run:

```
/plugin marketplace add <repo-root>
```

Expected output (approximately):

```
Marketplace "trailhead-local" added successfully.
```

Pass criteria: no error; the marketplace name `trailhead-local` is confirmed.

---

### 2. Install the lore plugin

```
/plugin install lore@trailhead-local
```

Expected output (approximately):

```
Installing lore from trailhead-local...
Plugin "lore" installed successfully.
```

Pass criteria: no error; plugin named `lore` is listed as installed.

---

### 3. Confirm PostToolUse hook fires on Agent/Task tool use

After installing, run an Agent or Task tool call and check the tool-use log.
The PostToolUse hook (`plugins/lore/hooks/harvest-candidates.py`) runs on
every `Agent|Task` tool result and scans for a `## Harvest candidates` block.

Pass criteria: no Python traceback or import error in the tool-use log.

Note (S5, F5): lore is fully pull — the SessionStart and WorktreeRemove hooks
were retired. Orientation lives in agent-rules and S6 skill descriptions;
session finalization is explicit (`lore finish`). Only the PostToolUse
harvest hook remains.

---

### 4. Confirm ${CLAUDE_PLUGIN_ROOT} resolved and sibling import succeeded

The PostToolUse hook (`plugins/lore/hooks/harvest-candidates.py`) runs via
`python3 "${CLAUDE_PLUGIN_ROOT}/hooks/harvest-candidates.py"`. It imports
sibling modules from the plugin's `lore/` package.

For explicit confirmation, look at the tool-use log for any Python traceback
or import error. There should be none.

Pass criteria: no import errors; the hook completes without error on Agent/Task.

---

## Failure modes and what they indicate

| Symptom | Likely cause |
|---|---|
| `/plugin marketplace add` fails | Wrong path; verify the repo root exists and has `.claude-plugin/marketplace.json` |
| `lore@trailhead-local` not found | Marketplace not added, or `marketplace.json` `name`/`plugins[0].name` mismatch |
| `${CLAUDE_PLUGIN_ROOT}` not expanded | Claude Code version too old; update Claude Code |
