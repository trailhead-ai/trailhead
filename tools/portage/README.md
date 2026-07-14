# portage

Get the PR **merged**. portage is the PR-lifecycle plugin for Claude Code: open,
update, watch CI, and merge a camp group's pull requests in dependency order.

portage handles the PR plumbing for a camp group — repo detection, PR
status/evaluate/merge, CI poll-to-actionable, and the `prs.json` sidecar — leaving
its agents and skills to focus on the judgment calls.

## What portage covers

portage's agents and skills cover one area — the PR lifecycle for a camp group:
open, update, watch CI, and merge in dependency order. (`trailhead install`
selects the individual subagents/skills below by name; the default installs
them all.)

## Commands

| Command | What it does |
|---|---|
| `/portage:pull_request create` | Review, push, and open PRs for every active repo in the group, then watch CI |
| `/portage:pull_request update` | Push new commits to existing PRs, then watch CI |
| `/portage:pull_request monitor` | Watch CI on open PRs, triage failures, and merge when all are clean |
| `/portage:pull_request merge` | Merge the open PRs in dependency order, then clean up branches |

Replaces the retired `/portage:open`, `/portage:update`, `/portage:monitor`, and
`/portage:merge` skills — those four names are gone; use the verb form above.

## Agents

- `portage:updater` — mechanical push orchestrator: detect repos, preflight, push,
  open/link sibling PRs, record the `prs.json` sidecar.
- `portage:monitor` — background watch+fix+merge operator: monitors CI, triages
  failures, and merges when everything is mergeable and clean.
- `portage:summarizer` — compact PR brief (diff + review comments) without loading
  the full thread into context.

## How a merge flows

1. `/portage:pull_request create` reviews the diff, then dispatches `updater` to push and open PRs.
2. The top-level session launches `monitor` in the background to watch CI.
3. `monitor` triages failures, then merges the PRs in dependency order once they are
   all mergeable and clean. Multi-PR groups require a declared `merge_order` so a
   stack never merges out of order.

## Configuration

Per-group behavior is read from the `[release]` block of the group TOML:

- `merge_order` — the dependency order for a multi-PR merge (required for >1 PR).
- `review_bot_login` — the login whose review comments count as actionable
  (default: none — CI-only).
- `external_tracker` — an optional issue-tracker connector (default: none).

## Relationship to the rest of trailhead

portage ships inside the trailhead install layout. It sits alongside its
sibling plugins — [lore](../lore) (knowledge management), [craft](../craft)
(plan / execute / review), and [camp](../camp) (group worktrees) — and reuses
craft's general helper agents (`log-sifter`, `code-reviewer`) at runtime.
