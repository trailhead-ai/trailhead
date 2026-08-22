# Agent cost convention — choosing `model:` and `effort:`

`model:` and `effort:` are the two largest per-dispatch cost multipliers under
trailhead's control. Every agent must declare both;
`trailhead/tests/test_agent_tier_convention.py` enforces declaration (not any
particular value) so the choice cannot quietly revert to inheritance.

## The two axes

**`model:` tracks task difficulty.**

| tier | for work that is | examples |
|---|---|---|
| `haiku` | mechanical: retrieval, extraction, running a command and reporting | `doc-finder`, `test-runner`, `log-sifter`, `summarizer`, lore `researcher` |
| `sonnet` | contract-driven: a defined output shape, judgment applied within it | `executor`, `drift-gate`, the council lenses, `librarian`, `updater` |
| `opus` | open-ended: deciding what the right answer *is*, or adversarial review | `code-reviewer`, `planner`, `premise-attacker`, `divergence-prober` |

**`effort:` tracks task ambiguity.** Effort buys deliberation, so it is worth
paying where the agent must work out what to do, and close to wasted where a
contract already says.

| level | for work where |
|---|---|
| `low` | the procedure is fully specified; the agent executes it |
| `medium` | there is a contract, with judgment calls inside it |
| `high` | the agent decides what the right answer is |
| `xhigh` | deep multi-step synthesis where a missed connection costs a whole rerun |

The two are independent. `sonnet`/`low` and `opus`/`medium` are both coherent.

## The rule that was missing

**Justify a setting against dispatch frequency, not against the agent's
self-image.** Cost is `frequency × tier × effort`. An expensive setting on an
agent that runs twice a year is not worth arguing about; a default-by-habit on
an agent dispatched hundreds of times is where the money is.

Before setting or changing either field, check how often the agent is actually
dispatched. Attribute `subagent_type` across harness transcripts — the same
method used for the 2026-08-22 audit.

**Corollary — a per-dispatch `model:` override is evidence.** If callers
routinely override an agent's declared tier, the declaration is wrong. Overrides
in one direction are a bug report about the frontmatter.

## Current assignments (audited 2026-08-22, 832 dispatches)

Changed, on evidence:

- **`craft:researcher` opus/xhigh → sonnet/high.** 65% of its dispatches already
  overrode it down to sonnet. Opus remains available as a deliberate
  per-dispatch escalation.
- **`craft:executor` effort unset → `medium`.** The most-dispatched agent (140).
  It builds against a spec and a test contract — well-specified work, which is
  where effort earns least. The execute ritual's reactive escalation paths
  remain the safety net.
- **`craft:assumption-prover` effort unset → `medium`.** Narrow contract: write
  one test, run it, report VALIDATED/INVALIDATED.
- **`ranger:execute`, `ranger:refine` effort unset → `medium`.** Both run a
  fixed ritual unattended; the judgment is in when to escalate, not in what to do.

Affirmed, with reasons:

- **`opus`/`xhigh` on `planner` and `lore:investigator`** — the most expensive
  combination available, and correct for deep synthesis. Both had **zero**
  dispatches in the audited window, so the setting costs nothing today. Revisit
  if either starts running regularly.
- **`sonnet`/`high` on `drift-gate` (79 dispatches)** — a gate that under-thinks
  admits drift, and the cost of a missed regression exceeds the deliberation
  saved. `model: sonnet` is separately pinned by
  `tools/craft/tests/test_review_altitude_contract.py`.
- **`sonnet`/`high` on the four council lenses (148 combined)** — each generates
  an argued single-perspective position, which is open-ended within its lens.
- **`haiku`/`low` on `doc-finder`, `test-runner`, `summarizer`, lore
  `researcher`** — mechanical work already at the floor.

## Adding a new agent

1. Name the tier from the difficulty column, and the effort from the ambiguity
   column.
2. State the reason in the agent's own description, so a reviewer can check it.
3. If it will be dispatched often, prefer the cheaper setting and let a
   per-dispatch override handle exceptions — the reverse leaks money quietly.
