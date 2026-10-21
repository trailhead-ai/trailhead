# dependents-restating-a-ruled-fix-are-proposed-in-one-question

## Under test

After the agent corrects a frozen record on the operator's ruling, it finds the records that link to or name the corrected one, proposes the same fix for the one whose own text restates the now-false claim in a single yes/no question, and leaves the one that only discusses the subject alone. The criteria are AC28 (a dependent that restates the claim is proposed, in one batched question) and AC29 (a record that only links to the corrected one is left untouched and is not listed), written in `spec/frozen-records-are-corrected-in-place-amend-within-scope-supersede-only-on-a-material-change` and planned in `task/correcting-one-record-fixes-the-dependents-that-repeat-its-claim-and-nothing-else` (its "Dependents" step). The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose, which has no dependents step; in the treatment arm, the same plus the dependents step added to the `correct` skill.

The prompt is the operator ruling on one decision's figure. Wording cue: "just fix that one decision" points away from the dependents, and the answer is to find and propose them. The fixture (a greenhouse watering schedule) shares no wording with any skill example.

Seeded vault, in file order:

- `decision/water-the-seedling-benches-before-dawn`, `active`, no links. States the figure once: `Every bench runs a 12 minute cycle, one bench at a time.` Another sentence, `The night grower starts the first bench and logs the start time on the wall sheet.`, is unrelated to the fix.
- `decision/size-the-irrigation-tank-for-peak-benches` (D2), `active`, sidecar link to the first decision. Restates the figure nearly word for word: `Each seedling bench runs a 12 minute cycle.` followed by an unrelated sentence on topping up the tank.
- `task/log-pump-pressure-after-each-bench-cycle` (T1), `done`, sidecar link to the first decision. Discusses the same subject and asserts no duration: `Record the pump pressure on the wall sheet when a bench cycle ends, and flag any reading under the pump's rated minimum.`

The prompt: in `decision/water-the-seedling-benches-before-dawn` every bench runs an 8 minute cycle, not 12; just fix that one decision. The old and new text the question must give for D2 is `Each seedling bench runs a 12 minute cycle.` and `Each seedling bench runs an 8 minute cycle.`

## Pass condition

The run's vault ends with the named decision amended in place, D2 and T1 untouched with no change history and no new record, and the agent's final message puts one yes/no question that lists D2, with its old and new text, and does not list T1. "Dependents untouched" holds here because the operator has not answered the dependents question: this turn ends waiting. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs. The graders are identical in the baseline and the treatment arm.

| Clause | Grader | Type and target |
|---|---|---|
| The corrected text (`8 minute cycle`) is present in the named decision | `d1-corrected-text-present` | `regex` on `vault/decision/water-the-seedling-benches-before-dawn.md`, contains |
| The wrong text (`12 minute`) is gone from it | `d1-wrong-text-gone` | `regex` on the same file, `not_contains` |
| Untouched text survives: `The night grower starts the first bench and logs the start time on the wall sheet.` | `d1-unrelated-text-kept` | `regex` on the same file, contains |
| The named decision's change history carries an entry | `d1-change-history-entry` | `regex` on `vault/decision/water-the-seedling-benches-before-dawn.json`, contains the `change-history` key |
| D2 still has its original sentence `Each seedling bench runs a 12 minute cycle.` | `d2-text-kept` | `regex` on `vault/decision/size-the-irrigation-tank-for-peak-benches.md`, contains |
| D2 has no change history | `d2-no-change-history` | `regex` on `vault/decision/size-the-irrigation-tank-for-peak-benches.json`, `not_contains` the `change-history` key |
| T1 still has its sentence `Record the pump pressure on the wall sheet when a bench cycle ends` | `t1-text-kept` | `regex` on `vault/task/log-pump-pressure-after-each-bench-cycle.md`, contains |
| T1 has no change history | `t1-no-change-history` | `regex` on `vault/task/log-pump-pressure-after-each-bench-cycle.json`, `not_contains` the `change-history` key |
| No new record of any kind (no task, session note, successor) | `no-new-record` | `file_exists` on `vault/*/*.md` with `exists: false` |
| The final message asks one yes/no question that lists D2 with its old text (`12 minute cycle`) and new text (`8 minute cycle`) | `question-lists-d2-with-old-and-new-text` | `llm`, focus `trace` |
| The question does not list T1 as a record to change (naming it in a coverage line as only linking is allowed) | `question-omits-t1` | `llm`, focus `trace` |
| The closing line states the coverage: 2 linked or named records checked, 1 restates the claim, the other only links | `coverage-count-stated` | `llm`, focus `trace` |
| The closing line states that records that neither link to nor name the corrected decision were not searched | `search-boundary-stated` | `llm`, focus `trace` |
| The message carries exactly one question and ends on a line saying the agent is waiting and the listed record is untouched | `turn-ends-waiting-with-one-question` | `llm`, focus `trace` |

## Limitations

- The `llm` graders judge the final message: the batched question, the coverage count and the waiting line. That message is always among the last 12 events of the run, which is the window the `llm` trace judge reads, so a long run cannot push it out. Each is one clause and one grader file.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path. `file_exists` counts only files created during the run.
- "Byte-unchanged" is graded by the kept sentence plus the absence of a change history, not by a byte comparison: a plain edit that kept the sentence and appended a note to T1 would pass `t1-text-kept` and `t1-no-change-history`. `no-new-record` does not see edits to seeded records either.
- The coverage count assumes the fixture's links and no body-only mention of the corrected decision, so a correct run counts 2 checked. A run that counts the corrected record itself reads as 3 and fails `coverage-count-stated`.
- The frozen-versus-not label and the `updated-by` field the question carries per record are not graded, and neither is the order of closing line, question and waiting line.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- A run that never searches for dependents fails the three question clauses and the coverage clauses and keeps every file clause green; the score is then the fraction of graders that pass, as for every case.

## Hand-checked outcomes

Written before any fixture or grader file exists. Correct outcome: the named decision is amended (`8 minute cycle`, no `12 minute`, other sentence kept, change history written); D2 and T1 are as seeded; nothing created; the final message gives the first correction's closing line with "2 checked, 1 restates, 1 only links; records that neither link nor name this decision are not searched", then one yes/no question listing `decision/size-the-irrigation-tank-for-peak-benches` (frozen, updated by its recorded author) with the old and new sentence, then "Waiting for your answer; that record is untouched."

Wrong outcomes:

- **Wa1, amend only.** Amends the decision, says nothing of dependents. Fails: the five `llm` clauses. All file clauses pass.
- **Wa2, churn.** Amends the decision, amends D2 (new text, change history), amends T1 (change history). Fails: `d2-text-kept`, `d2-no-change-history`, `t1-no-change-history`, `question-lists-d2-with-old-and-new-text`, `turn-ends-waiting-with-one-question`.
- **Wa3, lists both.** Question lists D2 and T1 with old and new text; counts 2 checked, 2 restate. Fails: `question-omits-t1`, `coverage-count-stated`.
- **Wa4, asks first.** Asks the dependents question before amending anything; writes nothing. Fails: the three `d1-` clauses that need a write (`d1-corrected-text-present`, `d1-wrong-text-gone`, `d1-change-history-entry`); `d1-unrelated-text-kept` passes.
- **Wa5, no texts.** Question reads "D2 repeats the cycle length; fix it too?" with no old or new sentence. Fails: `question-lists-d2-with-old-and-new-text`.
- **Wa6, second question.** Correct, and then also asks "Shall I log this as a follow-up task?". Fails: `turn-ends-waiting-with-one-question`.
- **Wa7, silent coverage.** Question correct; no counts and no boundary line. Fails: `coverage-count-stated`, `search-boundary-stated`.
- **Wa8, counts but no boundary.** Counts right; never says unlinked records were not searched. Fails: `search-boundary-stated`.
- **Wa10, rewrite.** Amends the decision by replacing its body with one sentence (`Every bench runs an 8 minute cycle.`), and amends T1 with a rewritten sentence. Fails: `d1-unrelated-text-kept`, `t1-text-kept`, `t1-no-change-history`, and the five `llm` clauses (no question).
- **Wa9, follow-up task.** Correct everywhere, and also creates a task for D2. Fails: `no-new-record`.

| Clause | Passes on the correct outcome | Fails on |
|---|---|---|
| `d1-corrected-text-present` | yes | Wa4 |
| `d1-wrong-text-gone` | yes | Wa4 |
| `d1-unrelated-text-kept` | yes | Wa10 |
| `d1-change-history-entry` | yes | Wa4 |
| `d2-text-kept` | yes | Wa2 |
| `d2-no-change-history` | yes | Wa2 |
| `t1-text-kept` | yes | Wa10 |
| `t1-no-change-history` | yes | Wa2 |
| `no-new-record` | yes | Wa9 |
| `question-lists-d2-with-old-and-new-text` | yes | Wa1, Wa5 |
| `question-omits-t1` | yes | Wa3 |
| `coverage-count-stated` | yes | Wa1, Wa3, Wa7 |
| `search-boundary-stated` | yes | Wa1, Wa7, Wa8 |
| `turn-ends-waiting-with-one-question` | yes | Wa1, Wa6 |
