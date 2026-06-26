# Manual Smoke Test — Plugin-System Boundary

Dev-time acceptance test for the boundary unit tests cannot reach: marketplace
install and **agent registration** (the craft-specific concern — craft ships
agents, not just skills).

For an adopter quickstart, see the README instead.

---

## Agent registration — does an installed plugin register *agents* as dispatchable subagent_types?

**Result: PASS (mechanism), 2026-06-03.**

**Evidence (mechanism, no install needed):** the already-installed `lore` plugin
ships an agent, `librarian`, and it appears in the running Claude Code
session's agent registry as the **namespaced** subagent_type
`lore:librarian` — dispatchable via the `Agent` tool. Plugin-provided
agents are namespaced `<plugin>:<agent>`; stow-symlinked `~/.claude/agents`
copies appear *un*-namespaced (e.g. `pr-updater`). The `lore:` prefix is direct
proof that marketplace-installed plugins register agents, not merely skills.

**Implication:** craft's agents register as `craft:<name>` once installed + the
session restarts. Hosting the general dev agents in craft is therefore viable —
agents migrated into `plugins/craft/agents/` are dispatchable as `craft:<name>`.

**Craft-specific confirmation (manual — run once):** install craft and dispatch
a craft agent in a fresh session (steps below). Record PASS/FAIL here.

| Date | Who | craft install + `craft:<name>` dispatch | Notes |
|------|-----|------------------------------------------|-------|
| 2026-06-03 | — | _pending manual run_ | mechanism already PASS via `lore:librarian` |

> **Fallback if craft-specific install ever FAILs** (mechanism is proven, so this
> is not expected): host the dev agents via a de-symlinked `~/.claude/agents`
> directory instead of a plugin. Do not proceed into the craft-agent migration
> on an un-rerun gate without noting it here.

---

## Prerequisites

- Claude Code installed and authenticated.
- This repo checked out locally (adjust the path below to match).
- Python 3.11+ on `PATH` as `python3`.

---

## Steps

### 1. Add the local marketplace

```
/plugin marketplace add <repo-root>
```

Pass criteria: no error; the marketplace name `trailhead-local` is confirmed.

### 2. Install the craft plugin

```
/plugin install craft@trailhead-local
```

Pass criteria: no error; plugin named `craft` is listed as installed.

### 3. Restart the session (agents register at session start)

Restart Claude Code or start a new session so the plugin's agents are picked up.

### 4. Confirm a craft agent dispatches as a subagent_type

Dispatch a cheap craft helper via the `Agent` tool with
`subagent_type: "craft:doc-finder"` (or ask Claude to "dispatch the craft
doc-finder agent") on a trivial lookup.

Pass criteria: the agent is found and dispatched (it appears in the available
subagent types as `craft:doc-finder`) and returns a result.
**Record the result in the agent-registration table above.**

---

## Failure modes and what they indicate

| Symptom | Likely cause |
|---|---|
| `/plugin marketplace add` fails | Wrong path; verify the repo root has `.claude-plugin/marketplace.json` |
| `craft@trailhead-local` not found | Marketplace not added, or `marketplace.json` `name`/`plugins[0].name` mismatch |
| `craft:doc-finder` not a known subagent_type | Plugin not installed, session not restarted, or agent missing valid frontmatter at `plugins/craft/agents/doc-finder.md` |
| Agent dispatches but errors | Frontmatter `tools:`/`model:` invalid — check against `librarian` |
