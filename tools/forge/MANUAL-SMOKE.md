# Manual Smoke Test — Plugin-System Boundary

Dev-time acceptance test for the boundary unit tests cannot reach: marketplace
install and **agent registration** (the forge-specific concern — forge ships
agents, not just skills).

For an adopter quickstart, see the README instead.

---

## KU1 — does an installed plugin register *agents* as dispatchable subagent_types?

**Result: PASS (mechanism), 2026-06-03.**

**Evidence (mechanism, no install needed):** the already-installed `lore` plugin
ships an agent, `lore-librarian`, and it appears in the running Claude Code
session's agent registry as the **namespaced** subagent_type
`lore:lore-librarian` — dispatchable via the `Agent` tool. Plugin-provided
agents are namespaced `<plugin>:<agent>`; stow-symlinked `~/.claude/agents`
copies appear *un*-namespaced (e.g. `pr-updater`). The `lore:` prefix is direct
proof that marketplace-installed plugins register agents, not merely skills.

**Implication:** forge's `forge-ping` registers as `forge:forge-ping` once
installed + the session restarts. P3's "host the 13 general dev agents in forge"
is therefore unblocked — agents migrated into `plugins/forge/agents/` will be
dispatchable as `forge:<name>`.

**Forge-specific confirmation (manual — run once):** install forge and dispatch
the proof agent in a fresh session (steps below). Record PASS/FAIL here.

| Date | Who | forge install + `forge:forge-ping` dispatch | Notes |
|------|-----|---------------------------------------------|-------|
| 2026-06-03 | — | _pending manual run_ | mechanism already PASS via `lore:lore-librarian` |

> **Fallback if forge-specific install ever FAILs** (mechanism is proven, so this
> is not expected): host the dev agents via a de-symlinked `~/.claude/agents`
> directory instead of a plugin (spec Decision-1 alternative). Do not proceed
> into P3's forge-agent migration on an un-rerun gate without noting it here.

---

## Prerequisites

- Claude Code installed and authenticated.
- This repo checked out locally (adjust the path below to match).
- Python 3.11+ on `PATH` as `python3`.

---

## Steps

### 1. Add the local marketplace

```
/plugin marketplace add /path/to/forge
```

Pass criteria: no error; the marketplace name `forge-local` is confirmed.

### 2. Install the forge plugin

```
/plugin install forge@forge-local
```

Pass criteria: no error; plugin named `forge` is listed as installed.

### 3. Restart the session (agents register at session start)

Restart Claude Code or start a new session so the plugin's agents are picked up.

### 4. Confirm `forge:forge-ping` dispatches as a subagent_type

Dispatch the proof agent via the `Agent` tool with `subagent_type: "forge:forge-ping"`
(or ask Claude to "dispatch the forge-ping agent").

Expected reply from the agent:

```
forge-ping: forge plugin agent registration OK
```

Pass criteria: the agent is found and dispatched (it appears in the available
subagent types as `forge:forge-ping`) and returns the confirmation string.
**Record the result in the KU1 table above.**

---

## Failure modes and what they indicate

| Symptom | Likely cause |
|---|---|
| `/plugin marketplace add` fails | Wrong path; verify the repo root has `.claude-plugin/marketplace.json` |
| `forge@forge-local` not found | Marketplace not added, or `marketplace.json` `name`/`plugins[0].name` mismatch |
| `forge:forge-ping` not a known subagent_type | Plugin not installed, session not restarted, or agent missing valid frontmatter at `plugins/forge/agents/forge-ping.md` |
| Agent dispatches but errors | Frontmatter `tools:`/`model:` invalid — check against `lore-librarian` |
