---
name: consult
description: >
  Convene the four-lens circle panel (builder / breaker / attacker / advocate) on a question,
  decision, or design and synthesize their perspectives.
  TRIGGER when: user says "convene the circle", "consult the circle", "get the panel on this",
  "four-lens review", "run a circle review on", "what do builder/breaker/attacker/advocate think",
  or wants buildability + reliability + security + UX weighed together on a specific question.
  DO NOT TRIGGER when: the question fits a single lens (dispatch that one agent directly —
  `architect`, `troubleshooter`, `security-auditor`), the user is running the planning skill
  (its Circle Review step convenes the panel itself), or this is a code review (use review).
---

# Consult the circle

Convene the four-lens **circle** on a specific question and synthesize what comes
back. This is the circle-review step made standalone — dispatchable on any
decision, not just a freshly-written plan.

The circle membership is defined once in `_shared/circle.md`. Read it — it names
the four agents and the dispatch contract. This skill does **not** redefine the
roster; it reads it from there so `consult` and `planning` can never drift apart.

## When to use this vs. a single agent

Use `consult` when a question genuinely spans buildability **and** reliability
**and** security **and** UX, and the cross-lens synthesis is the value. If it
fits one lens, dispatch that agent directly instead — `architect` for a
standalone architecture call, `troubleshooter` for an existing failure,
`security-auditor` for an audit of existing code. The panel is more expensive and
widens the prompt-injection surface; don't convene it for a single-lens question.

## Process

### 1. Frame the question

Pin down exactly what the circle is reviewing — a design, a decision between
options, a diff, a plan section. State it in one or two sentences the members can
each answer from their own lens. If the framing is ambiguous, ask the user one
clarifying question before dispatching; four agents on a vague question return
four vague answers.

Gather the context the members need to read: the file(s), the diff, the linked
spec or plan. Members run in isolated contexts and only see what the prompt points
them at.

### 2. Dispatch the four members (parallel, isolated)

Per `_shared/circle.md`: make **four parallel `Agent` tool calls** — one each to
`builder`, `breaker`, `attacker`, `advocate` — in a **single message** so they
run concurrently in isolated contexts. Use the same prompt for every member,
substituting only the lens label.

**Prompt template** (substitute `<lens>` with `Builder` / `Reliability` /
`Security` / `Advocate`, and `<question>` / `<context-pointers>` with the framing
from Step 1):

```text
You are being dispatched by the consult skill to review a question as one lens of the circle panel (<lens>).

Question: <question>
Context to read: <context-pointers>

Read the referenced context in full. Apply YOUR lens (<lens>) only. The other three members answer the same question in parallel from their lenses; you will not see their responses. Write in a voice that stands on its content — the synthesizer may strip your role label.

Output shape:
- ≤300 words total
- Categorize findings as Critical / Important / Minor
- ≤2 Critical findings (downgrade overflow to Important; forced prioritization is the point)
- Every Critical includes a one-line "what concretely fails" (a specific failure scenario, not "this could be a problem") and a one-line suggested fix
- No speculative Criticals — if it requires guessing about future state, scale, or user behavior, downgrade to Important
- One-line Confidence at the end

Required output format:

## Findings
- [Critical] <issue>: <one-line what concretely fails>. Suggested: <one-line fix>.
- [Important] <issue>: <one-line>. Suggested: <one-line>.
- [Minor] <issue>: <one-line>.

## Confidence
<one line — low | medium | high, with brief reason>
```

### 3. Synthesize (main session, NOT a subagent)

After all four return:

1. **De-duplicate by issue, not by member.** If two members raised the same
   finding, present it once, noting which lenses raised it.
2. **Auto-downgrade speculative Criticals.** A Critical that is vague, requires
   guessing about scale / future state / user behavior, or names no concrete
   failure scenario is reclassified Important — state which were downgraded and why.
3. **Present the consolidated list** to the user, grouped Critical → Important →
   Minor, noting the member count behind each multi-lens finding.

The synthesis is the deliverable. Unlike the planning Circle Review there is no
plan file to persist into and no disposition gate — `consult` answers a question
and hands the synthesized view back to the user to act on.
