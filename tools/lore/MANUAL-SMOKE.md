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
/plugin marketplace add /path/to/lore
```

Expected output (approximately):

```
Marketplace "lore-local" added successfully.
```

Pass criteria: no error; the marketplace name `lore-local` is confirmed.

---

### 2. Install the lore plugin

```
/plugin install lore@lore-local
```

Expected output (approximately):

```
Installing lore from lore-local...
Plugin "lore" installed successfully.
```

Pass criteria: no error; plugin named `lore` is listed as installed.

---

### 3. Start a fresh session (confirm SessionStart hook fires)

Either restart Claude Code, or start a new session. Look for the hook's
`additionalContext` output in the session-start banner or the tool-use log.

Expected: a line similar to

```
lore vault: /home/<you>/lore
```

(or the value of `$LORE_VAULT` if you set it).

**Variant:** set the env var before starting, then confirm it is honoured:

```bash
export LORE_VAULT=/tmp/my-test-vault
claude   # start a new session
```

Expected context line:

```
lore vault: /tmp/my-test-vault
```

Pass criteria: the session-start context contains the resolved vault path.

---

### 4. Confirm /lore:ping is invocable

In the session, run:

```
/lore:ping
```

Expected: Claude executes the skill body — it runs the inline Python snippet and
prints something like:

```
lore vault path: /home/<you>/lore
```

Pass criteria: the skill runs without error and the vault path is printed.

---

### 5. Confirm ${CLAUDE_PLUGIN_ROOT} resolved and sibling import succeeded

The SessionStart hook (`plugins/lore/hooks/session-context.py`) runs via
`python3 "${CLAUDE_PLUGIN_ROOT}/hooks/session-context.py"`. It imports
sibling modules (`vault`, `sessions`, `recall`) from the same plugin's
`scripts/` directory.

Check: if the hook fired in step 3 and produced the vault-path context, both
`${CLAUDE_PLUGIN_ROOT}` expansion and the sibling imports succeeded.

For explicit confirmation, look at the session startup log for any Python
traceback or import error. There should be none.

Pass criteria: no import errors; the `additionalContext` field in the hook's
JSON output was populated (not `{}`).

---

## Failure modes and what they indicate

| Symptom | Likely cause |
|---|---|
| `/plugin marketplace add` fails | Wrong path; verify the repo root exists and has `.claude-plugin/marketplace.json` |
| `lore@lore-local` not found | Marketplace not added, or `marketplace.json` `name`/`plugins[0].name` mismatch |
| Hook fires but emits `{}` | Python error in `session-context.py`; run `python3 plugins/lore/hooks/session-context.py` manually with `echo '{}' | ...` to debug |
| `/lore:ping` not found | Plugin not installed, or skill dir not at `plugins/lore/skills/ping/SKILL.md` |
| `${CLAUDE_PLUGIN_ROOT}` not expanded | Claude Code version too old; update Claude Code |
