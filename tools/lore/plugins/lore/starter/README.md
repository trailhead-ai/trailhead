# Lore vault

This is a **lore vault** — a plain-markdown-in-git knowledge layer for your
work. It captures the durable, non-obvious things worth remembering across
sessions: area mental models, decisions, lessons, backlog items (work to
revisit, abandoned approaches, and external things to watch), and a running
session log.

The [lore](https://github.com) Claude Code plugin reads this vault at session
start (recalling what's relevant to the branch you're on) and writes to it
through the `lore record` and `lore session` CLI commands.

## Layout

The kind set is closed — exactly these nine. One directory per kind:

| Directory | What lives here |
|---|---|
| `session/` | One note per working session; the running log. |
| `area/` | Mental models of the parts of your system. |
| `decision/` | Lightweight ADRs — why we chose X over Y. |
| `lesson/` | Mistakes plus a concrete prevention check. |
| `backlog/` | Work to revisit, approaches abandoned, and external things to watch — distinguished by `status` (`open` / `tracking` / `dropped`). |
| `collaboration/` | Working-style preferences and conventions. |
| `spec/`, `plan/` | Spec → plan artifacts. |
| `blob/` | Freeform captures that don't fit another kind. |

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
