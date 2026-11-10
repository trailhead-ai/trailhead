# consistency-auditor-spares-an-inseparable-and-but-flags-a-separable-one

Ported from the hand-run case `compound-criterion-detection` (its fixture 3, `spec-inseparable-conjunction.md`) to the plugin eval runner. Written before any runner run.

## Under test

The compound-criterion rule of `agents/consistency-auditor.md`, in its negative direction: an "and" alone does not make a criterion compound. Both signal criteria contain "and" and differ only in whether the halves can ship apart.

> 1. Claiming a ticket assigns it to the claiming engineer and removes it from the unassigned queue.

Not compound: the unassigned queue is the set of tickets with no owner, so the two halves are one state change. A ticket both owned and still unassigned is a corrupt state, not a partial delivery.

> 2. The queue view shows each claimed ticket's owner, and sends that owner a daily digest of the tickets they still hold.

Compound: showing ownership discharges the second objective on its own, and the digest is a separate scheduled-delivery deliverable.

## Conditions, sample and bar

- Runner: `claude plugin eval tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants from craft's `eval-gate.json` `runner` block.
- The top-level agent dispatches the real `craft:consistency-auditor` agent on `spec.md` and relays its output verbatim.
- The case passes at 2 of 3 runs, each scoring 1.0. A scaffold failure or timeout is an error, logged apart from graded fails.
- This is a guard on the prose as it stands. The hand-run case measured the change that added the compound rule; that change landed long ago.

## Pass condition

Every clause holds, in the same run.

| Clause | Grader | Type |
|---|---|---|
| The consistency auditor was dispatched | `auditor-dispatched` | `tool_used` `Agent` with input matching `consistency-auditor`, min 1 |
| The relayed output lists criterion 2 (owner shown, daily digest) as compound | `separable-and-flagged` | `llm` on the trace |
| The relayed output does not list criterion 1 (claim assigns and removes from the queue) as compound | `inseparable-and-spared` | `llm` on the trace |

A run that flags both keys on conjunction; a run that flags neither has stopped detecting.

## Limitations

- The adjudicator's Critical rating is not measured; it needs a full gauntlet run.
- One fixture, one model tier, three runs.
