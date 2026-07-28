# Refine — standalone task promotion, shared procedure

**Refine** promotes a standalone task — one that is both childless and parentless —
from `open` to `ready`: an executor could pick it up with nothing left to guess.
This file is the single source of truth for the procedure. `/craft:refine` is a thin
user-invocable wrapper over it, and the `execute` skill runs it **inline** when its
Loop preamble finds a standalone `open` task. Neither re-inlines the steps; both read
them from here, so the two callers can never drift apart.

Refine is **self-serve by default**. It fills gaps from the code and from the vault
before it ever involves a human. Only an irreducible operator decision escalates.

## Two modes, one procedure, one flag

Mode follows the caller. Everything below is identical in both modes **except the
escalation step**:

| Invocation | Mode |
|---|---|
| `/craft:refine <task>` | **unattended** — the default posture |
| `/craft:refine <task> --interactive` | interactive — the one-question-at-a-time path |
| execute's inline run | `--interactive` only when execute itself has a human channel; otherwise unattended |

## Status gate

Read the task record's status before anything else.

| Status | Behavior |
|---|---|
| `open` | The core case. Draft, then promote or escalate. |
| `blocked` | Draft the payload and cite the evidence, but **NEVER flip a `blocked` task's status**. `blocked` encodes an external condition refine can neither observe nor clear. |
| `ready` | Re-refine. Update the existing sections in place (see [Re-refine](#re-refine)). |
| `in-progress`, `done`, `dropped`, `superseded` | Refuse. Say which status was found and stop. |

One more refusal, independent of status:
**any task with children — refuse and route to `/craft:plan`**. Refine
promotes a leaf; a task that already has children is a plan, and reshaping a plan is
planning's job, not refine's.

## Step 1 — Triage is the draft attempt

There is no separate sizing step. **Attempt to draft the payload** by reading the
actual code the task touches — not just the task prose. What you learn from that
attempt is the triage.

A **gap** is a payload field the code read cannot fill with exactly one answer: if
two or more materially different fills remain defensible, that field is a gap.

**No size gate.** Line counts do not decide leaf versus plan; the draft attempt is
the whole bar. A task that drafts cleanly is a leaf however large it is, and a task
whose payload will not resolve is not a leaf however small it looks.

Three outcomes:

1. **Drafts cleanly** → append the payload, promote to `ready` (Steps 3-4).
2. **Hits a gap** → run the self-serve resolution pass (Step 2). Only a gap that
   survives it escalates (Step 5).
3. **Needs two or more independently-committable cuts, or the what/why itself is
   unsettled** → do not promote. Route to `/craft:plan` (or `/craft:brainstorm` when
   the objectives are still open) per Step 5.

## Step 2 — Self-serve resolution (mandatory, both modes)

A gap is not an escalation until both passes have run:

- **(a) Read the touched code.** Open the files the task names and the ones they
  call. Does only one answer remain consistent with what already exists — the
  surrounding naming, the established layout, the test convention on that surface?
- **(b) Search the vault.** Run `lore search` across the touched areas: does a prior
  decision, lesson, spec, or area profile already settle it? Search **wide** — vary
  the vocabulary, search the subsystem name and the concept and the file path, and
  read the neighbors of anything that hits. **An empty result is not proof** that no
  precedent exists; it is one query returning nothing, which is exactly what a badly
  worded query also does.

A derived answer is **cited in the drafted body**. An answer you cannot cite is not
derived — it escalates.

### The citation rule

This is planning's Given Axioms rule, quoted in full. A fact is citable when it is:

- (a) verifiable at a **file:line**, or
- (b) traced to a **recorded decision** (`[[record]]`), or
- (c) a **constraint stated by the user** — which is exactly what an answer given
  through the interactive path becomes.

Citations are **pointers only — never inline code excerpts**. Copying a source
excerpt into a task body copies whatever that line holds — a key, a token, a
customer name — into a git-backed vault that syncs to a remote. Point at the line;
let the reader open it.

## Step 3 — Citation-resolution gate

Every citation in the drafted payload **must resolve before** any status flip to
`ready` — mechanically, not by eye:

- a cited `file:line` names a file that exists, with the line number in range;
- a cited `[[record]]` resolves through the lore CLI (`lore record show <id>`).

A citation that fails to resolve **is a gap** — take it back to Step 2, and if it
survives, escalate it in Step 5. It never promotes. A fabricated pointer reads
exactly like a real one to every downstream consumer, so this is the only place it
can be caught.

## Step 4 — Write the payload

Append the payload in the **bold inline label** form of
`${CLAUDE_PLUGIN_ROOT}/templates/task.md` — exactly the child-task shape, **not**
`##` headings, so a promoted standalone leaf and a planned child slice read
identically to execute, `drift-gate`, and `code-reviewer`:

```markdown
**Delivers:** <what is complete and testable once this task is done>

**Test contract:** <the behaviors tests must verify — the failing tests to write first>

**Files:** <files created or modified>
```

Then append the `## Flow-out` checklist from
`${CLAUDE_PLUGIN_ROOT}/templates/plan.md` (that one stays a heading — lore's
completion guard looks for it), so the standalone task is its own lifecycle handle
without a parent plan:

```markdown
## Flow-out

- [ ] Touched area/subsystem profiles updated with what changed
- [ ] Prover-validated assumptions captured as session candidates (durable at flush)
- [ ] New decisions / lessons / follow-ups surfaced during the build recorded
```

**Test contract on a non-test surface.** A change with no automated test surface
still states its verification explicitly: a command plus its expected output, or
`manual: <check>` naming what a human confirms. An empty or absent test contract
**is a gap, not a pass** — the field existing is not the field being filled.

**Append, never overwrite.** The captured prose is the task's *why* and refine has
no mandate to rewrite it. Refine adds its sections below what is already there.

**Write through the CLI.** Every body change goes through
`lore record update <record-id>` (full-replace body on stdin, or `--diff` for a
unified diff), and every status flip through `lore record update <record-id>
--status ready`. Never edit a vault file directly — a direct write bypasses the
index and the sidecar and silently corrupts the record.

Any graph or record reference you write uses **bare task names** (`--parent
<name>`, `--depends-on <name>`) — a prefixed, pathed, or bracketed name is accepted
and then renders as a detached node.

## Step 5 — Escalation

Only a gap that survived both self-serve passes reaches here: a genuine operator
preference among valid alternatives, with no recorded precedent.

### Unattended escalation

Write the drafted **partial** payload — everything you could fill — then append a
single section:

```markdown
## Refine — unresolved

**Question:** <the one surviving decision, stated so it can be answered in a sentence>

**Evidence gathered:** <what the code read and the vault search turned up, as pointers>

**Recommended answer:** <your best call, and why>

Route: /craft:plan
```

The `Route:` line appears **only** for a route outcome (outcome 3 in Step 1): use
`Route: /craft:plan` when the work needs two or more independently-committable cuts,
or `/craft:brainstorm` when the what/why itself is unsettled. Omit the line for an
ordinary surviving question.

Then stop: **status stays `open`**. **Never invent** an answer to an operator
decision — the entire safety case for dispatching refine as a subagent with no
human channel rests on that one rule.

`## Refine — unresolved` is the canonical discovery handle: any later sweep or
operator scan finds escalated drafts by that heading, and a task carrying payload
fields while still `open` is by definition an escalated draft.

### Interactive escalation

Ask the user **one question at a time**, each pre-loaded with the evidence gathered
and your recommended answer. One at a time is deliberate: an answer routinely
reshapes the drafting that follows it, so batching questions wastes the later ones.

- **An answer** is a citable *constraint stated by the user* — cite it in the payload
  (arm (c) of the citation rule) and continue drafting.
- **A defer** ("skip it", "not now", no answer) falls back to the unattended
  escalation behavior for that task: record the question in the section above, leave
  the task `open`, and move on.

### Promotion clears the escalation

**On promotion, remove the `## Refine — unresolved` section.** The answered question
survives as a citation in the payload, so a `ready` task never carries the escalation
heading or a stale `Route:` line. Leaving it behind hands the next reader two
authoritative-looking statements of intent that disagree, with no way to tell which
one is current.

## Re-refine

Running refine on a task that already carries a payload is normal — `ready` tasks and
escalated `open` drafts both come back through here. Idempotency keys on the three
label strings (`**Delivers:**`, `**Test contract:**`, `**Files:**`) **and** on the
`## Refine — unresolved` heading:

- Found once → **update in place**. Never append a second set.
- Found twice (a hand-edited body drifted) → **report the conflict** and stop.
  Guessing which set is canonical is how the wrong payload reaches an executor.
- Not found → append, per Step 4.

## Trust boundary

Unattended promotion is a real authority: it flips a task to `ready`, and `ready` is
what execute dispatches to an `executor` with full tools — with **no human review in
between**. Text that influences the drafted payload comes from captured task prose
and from code comments, neither of which is trusted input.

The mitigations are the **pointer-only citation rule** (Step 2) and the
**citation-resolution gate** (Step 3): nothing enters a promoted body that does not
point at something that exists. The residual — a conclusion that is wrong but
citable — is accepted for a single-operator vault. When you want to see the draft
before it can reach a dispatch, run `/craft:refine --interactive` standalone first.
