# Lore vault

This is a **lore vault** — a plain-markdown-in-git knowledge layer for your
work. It captures the durable, non-obvious things worth remembering across
sessions: area mental models, decisions, dead-ends, deferred work,
lessons, and a running session log.

The [lore](https://github.com) Claude Code plugin reads this vault at session
start (recalling what's relevant to the branch you're on) and writes to it
through capture commands (`/lore:defer`, `/lore:decision`, `/lore:dead-end`,
`/lore:radar`, `/lore:area`) and the `lore` CLI.

## Layout

| Directory | What lives here |
|---|---|
| `sessions/` | One note per working session; the running log. |
| `areas/` | Mental models of the parts of your system. |
| `decisions/` | Lightweight ADRs — why we chose X over Y. |
| `dead-ends/` | Approaches that didn't work, with a revive condition. |
| `lessons/` | Mistakes plus a concrete prevention check. |
| `deferred/` | Work set aside, with a trigger to revisit. |
| `radar/` | External things to check on periodically. |
| `collaboration/` | Working-style preferences and conventions. |
| `specs/`, `plans/`, `designs/` | Spec → plan → design artifacts. |
| `inbox/` | Raw, unprocessed captures awaiting triage. |
| `briefings/`, `reviews/`, `audits/` | Periodic syntheses. |
| `gotchas/`, `tools/` | Sharp edges and tool-specific notes. |
| `.templates/` | Note-type templates the capture commands render. |

`harvest-pending.md` is the staging area for harvest candidates emitted by
subagents; the session wrap-up ritual promotes them into the vault.

## Status vocabulary

Every note type has a canonical `status:` vocabulary enforced by the
status guard. See [glossary.md](glossary.md) for the full list — do not
invent statuses outside it.

## Phases

Sessions are also tagged by **phase** (where in the work cycle you are):
Orient → Frame → Build → Review → Ship → Close. See [phases.md](phases.md).

## History is load-bearing

This vault is a git repository. The history *is* the memory — commit often,
and never rewrite it casually.
