# borderline-answered-no-routes-to-supersession

## Under test

After the agent has asked the one borderline question, the operator's no routes the correction to supersession and leaves the seeded decision's body as it was. The claim under test is AC30, written down in the spec `spec/frozen-records-are-corrected-in-place-amend-within-scope-supersede-only-on-a-material-change` and in the plan rule "Borderline rule (AC30)" in `task/the-agent-asks-once-before-an-amend-it-chose-or-a-borderline-call-and-writes-nothing-until-answered`: a yes amends in place, and a no routes to supersession. The prose under test is whatever the agent reads about correcting a frozen record. The fixture was classified borderline by four independent classifiers who were shown only the record, the correction and the two materiality lists (never the `correct` prose); they split on the route. The per-run votes are recorded in `tools/lore/MANUAL-EVAL.md`.

The case seeds a prior conversation (`context.history_file`, `fixtures/history.jsonl`, resumed with `--resume`) in which the operator stated the fix for `decision/rebalance-bike-docks-with-overnight-van-runs` in their own words, the agent read the record, put the borderline question once and wrote nothing. The case prompt is the operator's next turn: "No." Wording cue: none beyond the bare answer; the answer carries the whole instruction.

## Pass condition

The run's vault ends with a new decision created and the seeded decision superseded, its body and history unchanged. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs. The graders are identical in the baseline and the treatment arm. "Writes nothing" is not a clause here: the answer calls for a write.

| Clause | Grader | Type and target |
|---|---|---|
| A new decision exists | `successor-decision-exists` | `file_exists` on `vault/decision/*.md` |
| The predecessor's status is `superseded` | `predecessor-superseded` | `regex` on `vault/decision/rebalance-bike-docks-with-overnight-van-runs.json`, contains `"status": "superseded"` |
| The old text is still in the predecessor (`workshop crew checks`) | `old-text-kept` | `regex` on `vault/decision/rebalance-bike-docks-with-overnight-van-runs.md`, contains |
| Untouched text survives: `The van crew plans its route from the dock fill report at eleven.` | `unrelated-text-kept` | `regex` on `vault/decision/rebalance-bike-docks-with-overnight-van-runs.md`, contains |
| The predecessor's change history has no entry | `predecessor-no-history` | `regex` on `vault/decision/rebalance-bike-docks-with-overnight-van-runs.json`, `not_contains` the `change-history` key |

## Limitations

- The run's `trace.jsonl` and the `llm` trace judge see only the new turns, never the seeded ones. This case is therefore graded on vault files only, and no clause needs a grader to see the seeded question.
- The seeded tool result carries the real `revision:` of the scaffolded record, so the fixture's history is regenerated whenever the record body changes.
- `file_exists` counts only files created during the run, so a successor is seen and the seeded decision is not.
- A supersession's link is written on the successor's sidecar (`supersedes`), whose slug the agent picks, and a regex grader cannot glob. The successor is detected by the predecessor's status plus a created decision file.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- A run that asks again or does nothing writes no successor and fails the first two clauses; the operator has answered, so that is not the expected route.
- (f) is a decision, whose supersession is one record. A no on an ADR or a spec, whose route stops at a draft spec, is unmeasured.
