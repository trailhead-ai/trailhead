# council-synthesis-stands-down-a-waived-concern

Ported from the hand-run case `waived-concern-stands-down` (its `positive` fixture) to the plugin eval runner. Written before any runner run.

## Under test

The Synthesis section of `skills/_shared/council.md`: its first step, reconciling stand-downs, and its rule to restate any `stand-down:` line. When the spec waives a concern through a `Waives:` Non-Goal and the calibration block stands it down, a lens that raises that concern anyway does not make it a rated finding in the presented review, and every concern that was not waived is still reported. Also exercised: `scripts/maturity_bars.py`, which renders the stand-down.

## Conditions, sample and bar

The scaffold copies the branch's own `skills/_shared/council.md` into the run directory and renders `calibration.md` by piping `spec.md` through the branch's own `scripts/maturity_bars.py`, so both are the prose and renderer under test rather than frozen copies. The run is staged at synthesis: two lens responses (Reliability and Security) are already captured in `responses.md`, and the Reliability response raises "migration and backfill" independently, under its own Critical bar. The agent performs the Synthesis section of `council.md` over them and presents the review.

The spec's Non-Goal waives "migration and backfill" with a benign reason.

- Runner: `claude plugin eval tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants from craft's `eval-gate.json` `runner` block.
- The case passes at 2 of 3 runs, each scoring 1.0. A scaffold failure or timeout is an error, logged apart from graded fails.
- This is a guard on the prose as it stands, not a detector: the hand-run case measured the change that added this behaviour, and that change landed long ago.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| The presented review does not rate "migration and backfill" (the AC1 backfill) as a Critical, Important or Minor finding | `waived-concern-not-rated` | `llm` on the final message |
| The review restates the stand-down, or says in its own words that migration and backfill was waived and is not rated | `stand-down-restated` | `llm` on the final message |
| The review still rates "rollback and reversibility" (the AC2 same-deploy drop with no path back) as Critical | `rollback-still-critical` | `llm` on the final message |
| No member was dispatched again | `no-redispatch` | `tool_used` `Agent`, max 0 |

## Limitations

- One spec shape, two captured lens responses rather than four live ones, one model tier, three runs.
- Staged at synthesis, so it does not measure how often a live lens raises a waived concern.
- The hand-run re-measurement found the stand-down line legible enough that a synthesizer without the reconciliation step mostly drops the concern too, so this guards the outcome more than it isolates the rule.
