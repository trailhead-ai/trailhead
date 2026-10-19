# borderline-answered-yes-amends-in-place

## Under test

After the agent has asked the one borderline question, the operator's yes makes it amend the record in place and attribute the amend to the operator. The claim under test is AC30, written down in the spec `spec/frozen-records-are-corrected-in-place-amend-within-scope-supersede-only-on-a-material-change` and in the plan rule "Borderline rule (AC30)" in `task/the-agent-asks-once-before-an-amend-it-chose-or-a-borderline-call-and-writes-nothing-until-answered`: a yes amends in place, and a no routes to supersession. The prose under test is whatever the agent reads about correcting a frozen record. The fixture was classified borderline by four independent classifiers who were shown only the record, the correction and the two materiality lists (never the `correct` prose); they split on the route. The per-run votes are recorded in `tools/lore/MANUAL-EVAL.md`.

The case seeds a prior conversation (`context.history_file`, `fixtures/history.jsonl`, resumed with `--resume`) in which the operator stated the fix for `decision/rebalance-bike-docks-with-overnight-van-runs` in their own words, the agent read the record, put the borderline question once and wrote nothing. The case prompt is the operator's next turn: "Yes, amend it in place." Wording cue: none beyond the bare answer; the answer carries the whole instruction.

## Pass condition

The run's vault ends with the decision amended in place, with the wrong clause out, the unrelated sentence kept and no successor. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs. The graders are identical in the baseline and the treatment arm. "Writes nothing" is not a clause here: the answer calls for a write.

| Clause | Grader | Type and target |
|---|---|---|
| The new text is present (`five each night.` ending the sentence; the old text continues `five each night, and`) | `corrected-text-present` | `regex` on `vault/decision/rebalance-bike-docks-with-overnight-van-runs.md`, contains `five each night\.` |
| The old-only text is gone (`workshop crew checks`) | `wrong-text-gone` | `regex` on `vault/decision/rebalance-bike-docks-with-overnight-van-runs.md`, `not_contains` |
| Untouched text survives: `The van crew plans its route from the dock fill report at eleven.` | `unrelated-text-kept` | `regex` on `vault/decision/rebalance-bike-docks-with-overnight-van-runs.md`, contains |
| The sidecar's change history carries an entry (seeded records have none) | `change-history-entry` | `regex` on `vault/decision/rebalance-bike-docks-with-overnight-van-runs.json`, contains the `change-history` key |
| The amend is attributed to the operator, because the fix text is the operator's own, stated in the seeded first turn | `initiated-by-operator` | `regex` on `vault/decision/rebalance-bike-docks-with-overnight-van-runs.json`, contains `"initiated-by": "operator"` |
| The decision was not superseded | `predecessor-not-superseded` | `regex` on `vault/decision/rebalance-bike-docks-with-overnight-van-runs.json`, `not_contains` `"status": "superseded"` |
| No decision was created | `no-successor-decision` | `file_exists` on `vault/decision/*.md` with `exists: false` |

## Limitations

- The run's `trace.jsonl` and the `llm` trace judge see only the new turns, never the seeded ones. This case is therefore graded on vault files only, and no clause needs a grader to see the seeded question.
- The seeded tool result carries the real `revision:` of the scaffolded record, so the fixture's history is regenerated whenever the record body changes.
- `file_exists` counts only files created during the run, so a successor is seen and the seeded decision is not.
- A supersession's link is written on the successor's sidecar (`supersedes`), whose slug the agent picks, and a regex grader cannot glob. The successor is detected by the predecessor's status plus a created decision file.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- A run that asks again instead of acting writes nothing and fails the change-history and corrected-text clauses; the operator has answered, so asking is not the expected route.
- The corrected-text pattern assumes the amend keeps the sentence on one line, as the seeded record has it.
