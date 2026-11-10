# council-synthesis-rates-every-concern-when-nothing-is-waived

Ported from the hand-run case `waived-concern-stands-down` (its `negative` fixture, the control) to the plugin eval runner. Written before any runner run.

## Under test

The Synthesis section of `skills/_shared/council.md`, from the other side: with no `Waives:` marker in the spec, nothing stands down, and the synthesis drops nothing. This is what keeps the two waiver cases honest: a synthesis that learned to drop "migration and backfill" whenever it appears would pass them and fail this.

## Conditions, sample and bar

The scaffold copies the branch's own `skills/_shared/council.md` into the run directory and renders `calibration.md` by piping `spec.md` through the branch's own `scripts/maturity_bars.py`, so both are the prose and renderer under test rather than frozen copies. The run is staged at synthesis: two lens responses (Reliability and Security) are already captured in `responses.md`, and the Reliability response raises "migration and backfill" independently, under its own Critical bar. The agent performs the Synthesis section of `council.md` over them and presents the review.

The spec's only Non-Goal is unrelated ("Multi-region replication of the accounts table"), so the calibration block rates "migration and backfill" Critical like every other concern.

- Runner: `claude plugin eval tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants from craft's `eval-gate.json` `runner` block.
- The case passes at 2 of 3 runs, each scoring 1.0. A scaffold failure or timeout is an error, logged apart from graded fails.
- This is a guard on the prose as it stands, not a detector: the hand-run case measured the change that added this behaviour, and that change landed long ago.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| The presented review rates both "migration and backfill" (the AC1 backfill) and "rollback and reversibility" (the AC2 same-deploy drop) as Critical | `both-critical` | `llm` on the final message |
| The review states no stand-down or waiver for this spec | `no-stand-down-claimed` | `llm` on the final message |
| No member was dispatched again | `no-redispatch` | `tool_used` `Agent`, max 0 |

## Limitations

- One spec shape, two captured lens responses, one model tier, three runs.
