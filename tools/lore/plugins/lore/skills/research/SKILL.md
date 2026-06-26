---
name: research
description: Dispatch a lore research agent — the deep `investigator` for open-ended how/why/tradeoff investigations that span multiple files and systems, or the lighter `researcher` for quick lookups and polling `tracking`-status backlog items. Use for /lore:research, "investigate how X works", "research the tradeoffs of Y", "look up where Z is documented", "poll the tracking backlog".
---

# /lore:research — Dispatch a lore research agent

`lore:research` routes a question to one of the two lore research agents and
relays the synthesized answer. It dispatches a subagent so the research dump
stays out of the main conversation.

Two targets — **pick the right one for the question:**

## `investigator` — deep, expensive

Dispatch the `investigator` agent (Opus / xhigh) for **deep investigation**:
open-ended *how does X work*, *why was Y built this way*, *what are the tradeoffs
of Z* questions that span **multiple files, systems, or vault notes** and need
cross-referenced synthesis before acting. This is the thorough, costly path —
use it when a quick lookup won't do.

Good fits:
- "How does the session-note resolve→finalize chain actually work end-to-end?"
- "What are the tradeoffs between approach A and B, given our prior dead-ends?"
- "Trace how a record flows from `lore record create` to the index."

## `researcher` — lighter, cheap

Dispatch the `researcher` agent (Haiku / low) for **lighter lookups**: locate a
specific note, API, function, config option, or doc and return a pointer plus a
short excerpt. It's the cheap path when you don't need the investigator's deep
synthesis.

It is also the agent for **polling `tracking`-status backlog items** — periodic
status checks on the tracking backlog (has this follow-up moved? did this
deferred item's revisit condition fire?), where each poll is a quick lookup, not
an investigation.

Good fits:
- "Where is the `lore search` query shape documented?"
- "Find the area profile for the vault-resolution code."
- "Poll the `tracking`-status backlog items and report any that changed state."

## Choosing — signal to yourself before dispatching

- Open-ended, multi-file, "understand before acting" → **`investigator`** (deep,
  expensive).
- Single-target lookup, or a `tracking`-backlog status poll → **`researcher`**
  (cheap, light).

When in doubt and the question is genuinely open-ended, prefer `investigator`;
when it's "find me X" or "did Y move", prefer `researcher`.

## Process

1. Classify the question (deep investigation vs. lookup / tracking poll).
2. Dispatch the matching lore agent (`investigator` or `researcher`) with the
   question and any relevant context.
3. Relay the agent's synthesized answer to the user. Both agents return a
   structured summary, not raw dumps — pass it through.

## Edge cases

- **You want to *write* a finding, not research one.** Use `/lore:record` (single
  capture) or `/lore:flush` (evaluate all outstanding session candidates and flip
  the session clean).
- **You just need to query the index.** A direct `/lore:search` is faster than a
  dispatched agent for a simple facet query.
