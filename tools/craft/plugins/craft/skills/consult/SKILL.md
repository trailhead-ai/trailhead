---
name: consult
description: >
  Convene the four-lens council panel (builder / breaker / attacker / advocate) on a question,
  decision, or design and synthesize their perspectives.
  TRIGGER when: user says "convene the council", "consult the council", "get the panel on this",
  "four-lens review", "run a council review on", "what do builder/breaker/attacker/advocate think",
  or wants buildability + reliability + security + UX weighed together on a specific question.
  DO NOT TRIGGER when: the question fits a single lens (dispatch that one agent directly —
  `architect`, `troubleshooter`, `security-auditor`), the user is running the planning skill
  (its Council Review step convenes the panel itself), or this is a code review (use review).
---

# Consult the council

Convene the four-lens **council** on a specific question and synthesize what comes back. This is the
council-review step made standalone — dispatchable on any decision, not just a freshly-written plan.

The council membership is defined once in `_shared/council.md`. Read it — it names the four agents
and the dispatch contract. This skill does **not** redefine the roster; it reads it from there so
`consult` and `planning` can never drift apart.

## When to use this vs. a single agent

Use `consult` when a question genuinely spans buildability **and** reliability **and** security
**and** UX, and the cross-lens synthesis is the value. If it fits one lens, dispatch that agent
directly instead — `architect` for a standalone architecture call, `troubleshooter` for an existing
failure, `security-auditor` for an audit of existing code. The panel is more expensive and widens
the prompt-injection surface; don't convene it for a single-lens question.

## Process

### 1. Frame the question

Pin down exactly what the council is reviewing — a design, a decision between options, a diff, a
plan section. State it in one or two sentences the members can each answer from their own lens. If
the framing is ambiguous, ask the user one clarifying question before dispatching; four agents on a
vague question return four vague answers.

Gather the context the members need to read: the file(s), the diff, the linked spec or plan. Members
run in isolated contexts and only see what the prompt points them at.

### 2. Dispatch the four members (parallel, isolated)

Per `_shared/council.md`: make **four parallel `Agent` tool calls** — one each to `builder`,
`breaker`, `attacker`, `advocate` — in a **single message** so they run concurrently in isolated
contexts. Use the **prompt template, per-lens Critical bars, and synthesis rules in
`_shared/council.md`** — do not re-inline them here. Fill the template's substitution tokens BEFORE
sending each member its prompt (never ship a literal `<token>`):

- the context-pointer line → these two lines, with `<question>` and `<context-pointers>` filled from
  the framing in Step 1:
  ```text
  Question: <question>
  Context to read: <context-pointers>
  ```
- `<lens-critical-bars>` → the matching block from "Per-lens Critical bars" in `_shared/council.md`.
  Those bars are phrased for plan review; read each as applying to the question / design / diff
  under review (map "task" → the unit under review) and skip any bar with no analogue for this
  question.
- `<cross-cutting>` → the empty string (the cross-cutting plan-drift block is planning-only; consult
  reviews a standalone question, not a plan)
- `<maturity-calibration>` → the calibration block `scripts/maturity_bars.py` renders. `consult` has
  no spec pointer to review — **it pipes nothing on stdin, and an empty pipe is a legitimate exit-0
  case here, not an error**: the renderer reads empty stdin the same way it reads a spec with no
  `## Maturity` section, and falls through to the agent-instruction-file input. Name the current
  repository's agent-instruction file so that fallback is reachable:
  ```sh
  : | ${CLAUDE_PLUGIN_ROOT}/scripts/maturity_bars.py --agent-instruction-file <repo-root>/CLAUDE.md
  ```
**A non-zero exit still refuses the dispatch** — do not review at an uncalibrated severity. Read the
`reason-code:` stderr line and refuse with the remedy the code names — mirror `plan/SKILL.md`'s step
8.5 remedy table and worked refusal example rather than re-deriving a second one. The only
reason-codes reachable here are the agent-instruction-file ones
(`agent-instruction-file-unreadable`) plus whatever `maturity_resolve.py` reports as ITS reason for
that file, since there is no spec body to carry the other stamp-grammar violations.

**Surface the resolved level and its basis** in the synthesis you present — restate the renderer's
own `maturity: <level> (basis: <basis>)` line, so the user can tell a read agent-instruction-file
declaration from a silent default without re-deriving it.

The shared template's "the synthesizer may strip your role label" line applies here: members write
in a voice that stands on content, not on the role tag.

### 3. Synthesize (main session, NOT a subagent)

Synthesize per `_shared/council.md`: de-duplicate by issue (not by member), auto-downgrade
speculative Criticals (stating which and why), then **lead with the narrative synthesis** in the
shape "How the synthesis reads" defines there — what the lenses found, whether it holds and where it
came from, and what to do about it. The consolidated list follows it as supporting detail, grouped
Critical → Important → Minor with the member count behind each multi-lens finding, writing every
finding in the shape "How a finding reads" defines there.

The prose is what the user reads first, and on a standalone consult it is often all they read: there
is no disposition gate here forcing them back through the list, so a `consult` that leads with the
list hands back a question's worth of raw material instead of an answer.

The synthesis is the deliverable. Unlike the planning Council Review there is no plan file to
persist into and no disposition gate — `consult` answers a question and hands the synthesized view
back to the user to act on.
