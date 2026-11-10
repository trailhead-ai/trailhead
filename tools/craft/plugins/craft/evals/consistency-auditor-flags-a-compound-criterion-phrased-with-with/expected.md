# consistency-auditor-flags-a-compound-criterion-phrased-with-with

Ported from the hand-run case `compound-criterion-detection` (its fixture 2, `spec-inverted-cues.md`) to the plugin eval runner. Written before any runner run.

## Under test

The compound-criterion rule of `agents/consistency-auditor.md` (section 2, "Compoundness", and output item 3, "Compound criteria"): a criterion is compound when it carries more than one independently deliverable assertion, and surface conjunction is not the test. This fixture's compound criterion is phrased with "with", the shape the auditor's own worked example teaches as not compound:

> 1. Support engineers can search tickets by customer email, with results ranked by the relevance model.

Search by email discharges the spec's stated problem on its own and ships useful in any order; relevance ranking is a separate deliverable.

## Conditions, sample and bar

- Runner: `claude plugin eval tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants from craft's `eval-gate.json` `runner` block.
- The top-level agent dispatches the real `craft:consistency-auditor` agent on `spec.md` and relays its output verbatim.
- The case passes at 2 of 3 runs, each scoring 1.0. A scaffold failure or timeout is an error, logged apart from graded fails.
- This is a guard on the prose as it stands. The hand-run case measured the change that added the compound rule; that change landed long ago.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| The consistency auditor was dispatched | `auditor-dispatched` | `tool_used` `Agent` with input matching `consistency-auditor`, min 1 |
| The relayed output lists criterion 1 (search by email, ranked by relevance) as a compound criterion, split into searching by email and relevance ranking | `with-criterion-flagged` | `llm` on the trace |

## Limitations

- Criterion 2 is not graded. The hand-run case pre-registered it as a clean negative, then found that two of three runs were right to split it (a rejection can ship before its message); fixture 3 carries the sound negative.
- The hand-run case's third condition, that the adjudicator rates the finding Critical, needs a full gauntlet run and is not measured here.
- One fixture, one model tier, three runs.
