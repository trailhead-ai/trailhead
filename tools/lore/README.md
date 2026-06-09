# lore

A portable, git-backed second-brain for Claude Code. Lore captures the
durable, non-obvious things worth remembering across sessions — decisions,
dead-ends, deferrals, radar items, subsystem mental models, and a running
session log — and loads what's relevant automatically when a session starts.

**No MCP required.** Lore uses only `Read`, `Write`, `Edit`, `Glob`, `Grep`,
and `Bash(git)` — tools every Claude Code session already has.

## What lore captures

| Capture command | What it records |
|---|---|
| `/lore:defer` | Work chosen not to do now, with a trigger to revisit |
| `/lore:dead-end` | Approaches tried that didn't work, with a revive condition |
| `/lore:decision` | Non-obvious architectural choices and their reasoning |
| `/lore:radar` | External things out of your control being watched |
| `/lore:subsystem` | Mental model of a system area (files, gotchas, conventions) |

Session lifecycle is automatic: `/lore:checkpoint` snapshots in-flight state;
`/lore:finished` wraps the session. `/lore:vault-sync` commits and pushes the
vault at any point.

## Install

### 1. Add the marketplace

```
/plugin marketplace add https://github.com/<your-fork-or-this-repo>
```

Or for local development:

```
/plugin marketplace add /path/to/lore
```

### 2. Install the plugin

```
/plugin install lore@lore-local
```

### 3. Set up your vault

```bash
export LORE_VAULT=~/lore   # add to ~/.bashrc or ~/.zshrc
lore init ~/lore
```

`lore init` scaffolds the vault taxonomy, copies the starter docs (README,
glossary, phases, harvest-protocol), initializes a git repo, and installs a
pre-commit guard that enforces the canonical status vocabulary.

`$LORE_VAULT` tells every hook and CLI call where the vault lives. If it is
unset, lore defaults to `~/lore` and emits a one-time warning at session start.

### 4. Start a session

Open Claude Code in any project. The SessionStart hook creates a session note
for your current worktree and loads the baseline vault index into context.

## Skills

| Skill | Description |
|---|---|
| `/lore:defer` | Capture a deferred item |
| `/lore:dead-end` | Record a dead-end approach |
| `/lore:decision` | Record an architectural decision |
| `/lore:radar` | Add a radar watch item |
| `/lore:subsystem` | Create or update a subsystem profile |
| `/lore:checkpoint` | Mid-session snapshot — harvest state into the session note |
| `/lore:finished` | Canonical end-of-session finish — fill, finalize, expand harvest-pending, and commit |
| `/lore:vault-sync` | Commit and push the vault |
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
```

Run `lore <subcommand> --help` for full options.

## How recall works

Declare `keywords:` on a subsystem profile:

```yaml
---
type: subsystem
name: auth-service
keywords: [auth, login, oauth]
---
```

When the current git branch contains any of those keywords, the SessionStart
hook loads that subsystem profile plus related deferred items, dead-ends,
lessons, and recent sessions into the context — before you type anything.

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
  subsystems/    Mental models of system areas
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

Use the `/lore:lore-librarian` agent to search and synthesize across the vault.
It uses `Glob`, `Grep`, and `Read` — no MCP — and returns a cited synthesis,
not a raw dump.

## Development

```bash
# Run the test suite
python -m pytest tests/
```

A **pre-commit leak gate** keeps project- or machine-specific tokens out of the
shipped plugin surface. Install the generic, denylist-driven gate from the
[forge](https://github.com/tduffield/forge) plugin — it reads a machine-local
denylist (`~/.claude/leak-gate.denylist`, untracked) so no private token lives
in this tracked repo:

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
