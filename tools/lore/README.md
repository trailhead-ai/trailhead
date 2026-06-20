# lore

Agent-native project memory that works with your existing setup. Lore captures
the durable, non-obvious things worth remembering across sessions — decisions,
dead-ends, deferrals, follow-up items, area mental models, and a running session
log — and loads what's relevant automatically when a session starts.

**No MCP required.** Lore uses only `Read`, `Write`, `Edit`, `Glob`, `Grep`,
and `Bash(git)` — tools every Claude Code session already has.

## What lore captures

| Capture command | What it records |
|---|---|
| `/lore:defer` | Work chosen not to do now, with a trigger to revisit |
| `/lore:dead-end` | Approaches tried that didn't work, with a revive condition |
| `/lore:decision` | Non-obvious architectural choices and their reasoning |
| `/lore:follow-up` | External things out of your control being watched |
| `/lore:area` | Mental model of a codebase area (files, gotchas, conventions) |

Session lifecycle is automatic: `/lore:checkpoint` snapshots in-flight state;
`/lore:finish` wraps the session. `/lore:sync` commits and pushes the vault at
any point.

## Install

Lore is installed as part of Trailhead — see the [root README](../../README.md)
for `trailhead install` instructions.

After install, set up your vault:

```
lore init <path>   # scaffold the vault taxonomy and starter docs
export LORE_VAULT=<path>   # add to ~/.bashrc, ~/.zshrc, or ~/.config/fish/config.fish
```

`$LORE_VAULT` tells every hook and CLI call where the vault lives. If it is
unset, lore defaults to `~/lore` and emits a one-time warning at session start.

Open Claude Code in any project. lore is fully pull: there is no SessionStart
hook — the `lore` CLI resolves the active session note for your worktree itself
and lazy-creates it on first capture. Orientation (the area map and vault index)
is loaded via the agent-rules surface, and agents pull prior context on demand
with `lore search`.

## Skills

| Skill | Description |
|---|---|
| `/lore:record` | Log a single deliberate item now (`lore record` / `lore session`) |
| `/lore:search` | Query the vault (KQL-subset) — the read path |
| `/lore:research` | Dispatch the `investigator` (deep) or `researcher` (light) agent |
| `/lore:checkpoint` | Mid-session sweep — catch capture-worthy items not yet logged; status stays active |
| `/lore:finish` | Canonical end-of-session finish — finalize (`status: complete` + `ended:`) and commit |
| `/lore:sync` | Commit and push the vault |

## The `lore` CLI

The `lore` CLI handles the deterministic operations skills delegate to it.

<!-- Intentionally an untagged block (no sh/bash): this is a CLI reference listing,
     not a runnable snippet. A tagged sh/bash block here is scanned by the landing-claims
     inverse gate, which would require every line's subcommand to be registered. -->
```
lore init <path>          Scaffold a new vault
lore new <type>           Render a template and write a new vault note
lore set-status <f> <v>   Validate and flip a note's frontmatter status
lore stats                Print vault counts
lore finish               Finalize the session note (status: complete + ended:) and commit
lore sync                 Stage, commit, and push the vault
lore search <query>       Query all records (KQL-subset: field:value, full-text, and/or/not)
lore areas                List the area profiles in the vault
```

Run `lore <subcommand> --help` for full options.

## How search works

Your next session loads what's relevant without you asking — explained, not
guessed.

At session start, the SessionStart hook builds an **area map**: a compact menu
of every area profile in your vault, listing each area's name, one-line
summary, and keywords. This area map is always loaded into context.

The agent reads the area map as part of its normal task analysis, matches the
current task to one or more areas, and runs `lore search 'area:<name>'` to
pull that area's accumulated memory — decisions, lessons, dead-ends, and open
deferred items linked to that area — into the conversation. The search is
**scoped and explainable**: the agent can say "I searched the auth-module area
because the task touches login flows," not just "here is some context." Search
also covers full-text and the other record facets (`kind:`, `status:`,
`keyword:`, date ranges) — `lore search 'area:<name>'` is the area-membership
case of the one general query interface.

**To register an area**, use `/lore:area`. Give it a name, a one-line summary,
and a set of keywords. The profile feeds the area map; the agent uses keywords
and the summary to decide when to pull the area's memory.

```yaml
---
type: area
name: auth-module
summary: OAuth login flow, token refresh, and session middleware
keywords: [auth, login, oauth, token, session]
---
```

## Status vocabulary

Every note type has a canonical `status:` set. The pre-commit guard rejects
non-canonical values. See the vault's `glossary.md` for the full list.

Key transitions:

- **sessions:** `active` → `complete` (or `shelved` for handoffs)
- **deferred:** `open` → `resolved` / `dropped` / `graduated`
- **follow-ups:** `active` → `resolved` / `dropped`
- **dead-ends:** `active` → `archived`

## Vault layout

```
lore/
  sessions/      One note per working session
  areas/         Mental models of codebase areas
  decisions/     Lightweight ADRs
  dead-ends/     Failed approaches with revive conditions
  lessons/       Mistakes plus prevention checks
  deferred/      Work set aside with revisit triggers
  follow-ups/    External things to watch
  collaboration/ Working-style preferences
  specs/         Specification artifacts
  plans/         Implementation plans
  designs/       Design artifacts
  inbox/         Raw captures awaiting triage
```

## Searching the vault

Use the `/lore:librarian` agent to search and synthesize across the vault.
It uses `Glob`, `Grep`, and `Read` — no MCP — and returns a cited synthesis,
not a raw dump.

## Development

```bash
# Run the test suite
python -m pytest tests/
```

A **pre-commit leak gate** keeps project- or machine-specific tokens out of the
shipped plugin surface. Install the generic, denylist-driven gate from the
[craft](../craft) plugin — it reads a machine-local denylist
(`~/.claude/leak-gate.denylist`, untracked) so no private token lives in this
tracked repo:

```bash
craft/plugins/craft/scripts/install-hooks.sh "$(pwd)" plugins/lore tests docs
```

The `docs` tree is included so adopter-facing docs (e.g. `EXTENDING.md`) are
gated too — they ship publicly and must stay leak-clean.

See `MANUAL-SMOKE.md` for the plugin-system boundary smoke test (hook
registration, `${CLAUDE_PLUGIN_ROOT}`, skill namespacing).

## Extending lore for your project

To bolt your own project layer on top of lore and the craft dev agents — wiring
craft's extension points (feature flags, issue tracker, observability, test
commands) to your own stack — see the adopter cookbook in
[`docs/EXTENDING.md`](docs/EXTENDING.md).
