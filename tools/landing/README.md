# landing

Get it **deployed**. landing is the post-merge deploy-health plugin for Claude Code:
soak a freshly-merged deploy with a configurable health command, and diagnose a
deploy-log regression when one surfaces.

landing pairs two concerns. The soak probe is landing's own config-driven health
gate — inert by default, no vendor baked in. The deploy-health diagnosis is a thin
consumer of the `trailhead.vcs` provider library — the provider owns the GitHub
Actions deploy-log interrogation (`deploy.logs`/`deploy.status`/`deploy.workflow_runs`),
so the plugin's agents stay focused on the judgment calls.

## Capabilities

landing ships one capability group:

| Capability | What it covers |
|---|---|
| `deploy` | Post-merge soak + deploy-health incident handling for a camp group |

## Commands

| Command | What it does |
|---|---|
| `/landing:soak` | Run the configured soak health command once; escalate to `doctor` on regression |
| `/landing:resolve` | Human-in-the-loop incident handler — revert, forward-fix, recheck, or dismiss |

## Agents

- `landing:soaker` — background post-merge soak operator: runs the configured health
  command via `soak_health.py`, reports the result, escalates on regression.
- `landing:doctor` — post-merge deploy-health diagnostic: interrogates the GHA deploy
  log via `deploy.logs()`, triages the failure, and produces a recommendation.

## How a soak flows

1. portage's `monitor` finishes a merge and emits a post-merge handoff marker.
2. The top-level session launches `/landing:soak` in the background; `soaker` runs the
   configured health command once.
3. On a clean deploy, `soaker` exits clean. On a regression, `soaker` escalates to
   `doctor`, which interrogates the deploy log and recommends an action. The user then
   drives `/landing:resolve` to revert, forward-fix, recheck, or dismiss.

## Configuration

Per-group behavior is read from the `[release]` block of the group TOML:

- `soak_health_command` — the health command the soak runs (default: none — inert by
  default; `soaker` prints `soak: n/a — no health command configured` and exits clean).
- `external_tracker` — an optional reserved seam for an incident-tracker connector
  (default: none — no connector is built).

## Relationship to the rest of trailhead

landing ships inside the trailhead install layout and consumes the shared
`trailhead.vcs` library; it is not independently adoptable. It sits alongside its
sibling plugins — [lore](../lore) (knowledge management), [craft](../craft)
(plan / execute / review), [camp](../camp) (group worktrees), and
[portage](../portage) (PR lifecycle) — and reuses craft's general helper agents
(`log-sifter`, `troubleshooter`, `researcher`) at runtime.
