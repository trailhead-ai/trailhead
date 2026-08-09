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
(`lore flush`). `tests/test_manifest_validity.py` pins this for the shipped
source manifest (it reads `tools/lore/plugins/lore/hooks/hooks.json` in the
repo tree) — run it:

```
python -m pytest tools/lore/tests/test_manifest_validity.py -v
```

Pass criteria: the test passes.

That test can't reach the installed boundary this document exists for. To
confirm the *installed* plugin carries the same empty hooks dict, find the
install location (e.g. under the harness's plugin directory for `lore@trailhead-local`)
and inspect its `hooks/hooks.json` directly:

```
cat <installed-lore-plugin-dir>/hooks/hooks.json
```

Pass criteria: the file exists and its `hooks` value is `{}`.

---

### 4. Confirm ${CLAUDE_PLUGIN_ROOT} resolves for the `lore` CLI

Run a `lore` command (e.g. `lore --help`) from within the installed plugin
context and confirm it resolves `${CLAUDE_PLUGIN_ROOT}` and imports sibling
modules from the plugin's `lore/` package without error.

`bin/lore` falls back to a self-relative path when `${CLAUDE_PLUGIN_ROOT}`
isn't set, so a bare pass/fail can't tell you which path resolved. Make the
step tell them apart by echoing the resolved CLI path, e.g.:

```
lore --help; python3 -c "import os; print(os.environ.get('CLAUDE_PLUGIN_ROOT', '<unset>'))"
```

Pass criteria: no import errors; the command completes and prints help
output; `CLAUDE_PLUGIN_ROOT` is set and non-empty (confirming
`${CLAUDE_PLUGIN_ROOT}` resolution was exercised, not the self-relative
fallback).

---

## Failure modes and what they indicate

| Symptom | Likely cause |
|---|---|
| `/plugin marketplace add` fails | Wrong path; verify the repo root exists and has `.claude-plugin/marketplace.json` |
| `lore@trailhead-local` not found | Marketplace not added, or `marketplace.json` `name`/`plugins[0].name` mismatch |
| `${CLAUDE_PLUGIN_ROOT}` not expanded | Claude Code version too old; update Claude Code |
