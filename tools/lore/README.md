# lore

Agent-native project memory that works with your existing setup. Lore captures
the durable, non-obvious things worth remembering across sessions — decisions,
dead-ends, deferrals, radar items, area mental models, and a running session
log — and loads what's relevant automatically when a session starts.

**No MCP required.** Lore uses only `Read`, `Write`, `Edit`, `Glob`, `Grep`,
and `Bash(git)` — tools every Claude Code session already has.

## What lore captures

| Capture command | What it records |
|---|---|
| `/lore:defer` | Work chosen not to do now, with a trigger to revisit |
| `/lore:dead-end` | Approaches tried that didn't work, with a revive condition |
| `/lore:decision` | Non-obvious architectural choices and their reasoning |
| `/lore:radar` | External things out of your control being watched |
| `/lore:area` | Mental model of a codebase area (files, gotchas, conventions) |

Session lifecycle is automatic: `/lore:checkpoint` snapshots in-flight state;
`/lore:finished` wraps the session. `/lore:sync` commits and pushes the vault at
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

Open Claude Code in any project. The SessionStart hook creates a session note
for your current worktree and loads the baseline vault index into context —
with the area map already included so the agent knows which areas exist.

## Skills

| Skill | Description |
|---|---|
| `/lore:defer` | Capture a deferred item |
| `/lore:dead-end` | Record a dead-end approach |
| `/lore:decision` | Record an architectural decision |
| `/lore:radar` | Add a radar watch item |
| `/lore:area` | Create or update an area profile |
| `/lore:checkpoint` | Mid-session snapshot — harvest state into the session note |
| `/lore:finished` | Canonical end-of-session finish — fill, finalize, expand harvest-pending, and commit |
| `/lore:review` | Weekly vault migration — re-justify or close every open item |
| `/lore:reflect` | Narrative synthesis of vault activity over a period |
| `/lore:sync` | Commit and push the vault |
| `/lore:ping` | Confirm the plugin is installed and show the resolved vault path |

## The `lore` CLI

The `lore` CLI handles the deterministic operations skills delegate to it.

```
lore init <path>          Scaffold a new vault
lore new <type>           Render a template and write a new vault note
lore patch <file> <sec>   Append text under a named section (--text or stdin)
lore set-status <f> <v>   Validate and flip a note's frontmatter status
lore stats                Print vault counts
lore finish               Finalize the session note, expand harvest-pending into vault notes, and commit
lore sync                 Stage, commit, and push the vault
lore recall --areas <n>   Pull area-scoped memory (decisions/lessons/dead-ends/deferred)
```

Run `lore <subcommand> --help` for full options.

## How recall works

Your next session loads what's relevant without you asking — explained, not
guessed.

At session start, the SessionStart hook builds an **area map**: a compact menu
of every area profile in your vault, listing each area's name, one-line
summary, and keywords. This area map is always loaded into context.

The agent reads the area map as part of its normal task analysis, matches the
current task to one or more areas, and calls `lore recall --areas <name>` to
pull that area's accumulated memory — decisions, lessons, dead-ends, and open
deferred items — into the conversation. The recall is **scoped and explainable**:
the agent can say "I recalled the auth-module area because the task touches
login flows," not just "here is some context."

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
- **radar:** `active` → `resolved` / `dropped`
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
  radar/         External things to watch
  collaboration/ Working-style preferences
  specs/         Specification artifacts
  plans/         Implementation plans
  designs/       Design artifacts
  inbox/         Raw captures awaiting triage
  harvest-pending.md   Staging area for subagent harvest candidates
```

## Searching the vault

Use the `/lore:loremaster` agent to search and synthesize across the vault.
It uses `Glob`, `Grep`, and `Read` — no MCP — and returns a cited synthesis,
not a raw dump.

## Development

```bash
# Run the test suite
python -m pytest tests/
```

A **pre-commit leak gate** keeps project- or machine-specific tokens out of the
shipped plugin surface. Install the generic, denylist-driven gate from the
[forge](../forge) plugin — it reads a machine-local denylist
(`~/.claude/leak-gate.denylist`, untracked) so no private token lives in this
tracked repo:

```bash
forge/plugins/forge/scripts/install-hooks.sh "$(pwd)" plugins/lore tests docs
```

The `docs` tree is included so adopter-facing docs (e.g. `EXTENDING.md`) are
gated too — they ship publicly and must stay leak-clean.

See `MANUAL-SMOKE.md` for the plugin-system boundary smoke test (hook
registration, `${CLAUDE_PLUGIN_ROOT}`, skill namespacing).

## Extending lore for your project

To bolt your own project layer on top of lore and the forge dev agents — wiring
forge's extension points (feature flags, issue tracker, observability, test
commands) to your own stack — see the adopter cookbook in
[`docs/EXTENDING.md`](docs/EXTENDING.md).
