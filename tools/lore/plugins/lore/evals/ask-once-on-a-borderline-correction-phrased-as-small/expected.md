# ask-once-on-a-borderline-correction-phrased-as-small

## Under test

A correction that honestly fits both the amend list and the supersede list, and that the record itself does not settle, gets one yes/no question and no write until the operator answers. The claim under test is AC30, written down in the spec `spec/frozen-records-are-corrected-in-place-amend-within-scope-supersede-only-on-a-material-change` and in the plan rule "Borderline rule (AC30)" in `task/the-agent-asks-once-before-an-amend-it-chose-or-a-borderline-call-and-writes-nothing-until-answered`. The rule is not yet in the `correct` prose, so the baseline arm is expected red: it takes whichever route it guesses. In the treatment arm the prose carries the rule. The fixture was classified borderline by four independent classifiers who were shown only the record, the correction and the two materiality lists (never the `correct` prose); they split on the route. The per-run votes are recorded in `tools/lore/MANUAL-EVAL.md`.

The operator states the fix for `decision/keep-the-galleries-open-late-on-thursdays` outright. Wording cue: the prompt opens "Small fix". The cue points at amend; the right answer is neither route taken silently but the one question.

## Pass condition

The run's vault ends with the decision untouched, and the agent's final message asks. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs. The graders are identical in the baseline and the treatment arm.

| Clause | Grader | Type and target |
|---|---|---|
| The original text is still in the decision (`cafe serves until eight`) | `original-text-kept` | `regex` on `vault/decision/keep-the-galleries-open-late-on-thursdays.md`, contains |
| The decision's change history has no entry | `no-change-history` | `regex` on `vault/decision/keep-the-galleries-open-late-on-thursdays.json`, `not_contains` the `change-history` key |
| The decision was not superseded | `predecessor-not-superseded` | `regex` on `vault/decision/keep-the-galleries-open-late-on-thursdays.json`, `not_contains` `"status": "superseded"` |
| No decision was created | `no-successor-decision` | `file_exists` on `vault/decision/*.md` with `exists: false` |
| No task was created | `no-task-created` | `file_exists` on `vault/task/*.md` with `exists: false` |
| No follow-up candidate was written | `no-candidate-written` | `file_exists` on `vault/session/*.md` with `exists: false` |
| The final message asks exactly one yes/no question, whose yes means amend the decision in place, quotes the old text (`On Thursdays the galleries stay open until nine, and the cafe serves until eight.`) and the new text (`On Thursdays the galleries stay open until nine.`), and says that no starts a new decision and then supersession of this one | `asks-one-question-with-both-routes` | `llm` on the trace |

## Limitations

- Candidate writes are visible to file graders: a session candidate writes `vault/session/<key>.md`, and nothing else in a run creates a session note. So "no candidate" is a file grader, not a transcript grader.
- `file_exists` counts only files created during the run, so it cannot see seeded records. It is used only for files a run would have to create.
- A supersession's link is written on the successor's sidecar (`supersedes`), whose slug the agent picks, and a regex grader cannot glob. The successor is therefore detected by the predecessor's status plus a created decision file, never by the glob alone.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- The `llm` trace judge is a haiku-class model that elides the middle of a trace past 100,000 characters. The grader is identical in both arms, and every verdict is hand-checked against its trace in both arms. The vault-file clauses decide whether the record was left alone; the `llm` clause decides what the operator was asked.
- Whether the agent reaches the `correct` skill when the operator asked for something else is recorded per run (task unknown U3), not graded. A green baseline on clauses that need no skill is reported as green, never as a fix.
- "Borderline" is defined by the fixture and the four-classifier split, not by an exhaustive rule. A treatment run that asks about a plainly classified change is measured by the existing correction cases, not here.
- The sentence that must survive an amend (`Entry after five is by the side door on Mill Lane.`) is not graded: this case writes nothing, so `original-text-kept` and `no-change-history` already pin the whole record.
