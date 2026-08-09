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

### 3. Confirm no hooks are registered

lore is fully pull — it registers zero hooks. Orientation lives in the
installed agent-rules ruleset, and session finalization is explicit
(`lore flush`). Check the plugin's hook manifest:

```
cat plugins/lore/hooks/hooks.json
```

Pass criteria: the file is `{"hooks": {}}` — no `SessionStart`, `PostToolUse`,
or `WorktreeRemove` entries.

---

### 4. Confirm ${CLAUDE_PLUGIN_ROOT} resolves for the `lore` CLI

Run a `lore` command (e.g. `lore --help`) from within the installed plugin
context and confirm it resolves `${CLAUDE_PLUGIN_ROOT}` and imports sibling
modules from the plugin's `lore/` package without error.

Pass criteria: no import errors; the command completes and prints help output.

---

## Failure modes and what they indicate

| Symptom | Likely cause |
|---|---|
| `/plugin marketplace add` fails | Wrong path; verify the repo root exists and has `.claude-plugin/marketplace.json` |
| `lore@trailhead-local` not found | Marketplace not added, or `marketplace.json` `name`/`plugins[0].name` mismatch |
| `${CLAUDE_PLUGIN_ROOT}` not expanded | Claude Code version too old; update Claude Code |
