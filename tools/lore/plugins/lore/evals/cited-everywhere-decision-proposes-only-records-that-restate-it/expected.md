# cited-everywhere-decision-proposes-only-records-that-restate-it

## Under test

Among many records that link to or name a corrected decision, the agent proposes only the one whose own text restates the false claim in other words, and leaves the eight that merely cite the decision alone, unlisted and unedited. The criteria are AC28 (a dependent that restates the claim, here by paraphrase and not by copying, is proposed in one batched question) and AC29 (a record that only links to or names the corrected one is left untouched), in `spec/frozen-records-are-corrected-in-place-amend-within-scope-supersede-only-on-a-material-change`, planned in `task/correcting-one-record-fixes-the-dependents-that-repeat-its-claim-and-nothing-else` (its "Dependents" step). It also exercises the turn budget: the new searches and the reading of nine hits must fit the case's `max_turns`. The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose, which has no dependents step; in the treatment arm, the same plus the dependents step added to the `correct` skill.

The prompt is the operator ruling on one decision's figure. Wording cue: "this decision is cited all over the place" points at touching every citer, and the answer is to propose only the one record that restates the fact. The fixture (a community pantry's shelf rules) shares no wording with any skill example and is a different domain from the sibling dependents cases.

Seeded vault, in file order:

- `decision/set-the-shelf-hold-for-donated-produce`, `active`, no links: `Donated produce stays on the shelf for 48 hours.` followed by a sentence on discard, then the unrelated sentence `The volunteer writes the arrival time on the crate label when produce comes in.`
- `decision/plan-saturday-restocking-around-the-discard-window` (P), `active`, sidecar link to the first decision. It paraphrases the figure: `Produce goes into the compost bin two days after it arrives`, then restocking plans. It never says `48` or `hours`.
- Seven records with a sidecar link to the first decision that cite it without asserting any hold time: two `active` decisions, three `done` tasks, one `open` task and one `active` lesson.
- `area/produce-table`, `active`, no sidecar link, whose body names `decision/set-the-shelf-hold-for-donated-produce` in running text and asserts no hold time.

The eight link-only bodies carry no duration at all (no `48`, `72`, `hours`, `days`, `two` or `three` as a time). Counting the paraphrasing record, a correct run checks 9 linked or named records.

The prompt: in `decision/set-the-shelf-hold-for-donated-produce` the shelf hold is 72 hours, not 48; this decision is cited all over the place. The old and new text the question must give for P is `Produce goes into the compost bin two days after it arrives` and the same sentence with `three days`.

## Pass condition

The run's vault ends with the named decision amended in place, P and the eight link-only records untouched, no new record, and the final message putting one yes/no question that lists P with its old and new text and none of the eight. "Dependents untouched" holds because the operator has not answered the dependents question: the turn ends waiting. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs. The graders are identical in the baseline and the treatment arm.

| Clause | Grader | Type and target |
|---|---|---|
| The corrected text (`72 hours`) is present in the named decision | `c0-corrected-text-present` | `regex` on `vault/decision/set-the-shelf-hold-for-donated-produce.md`, contains |
| The wrong text (`48 hours`) is gone from it | `c0-wrong-text-gone` | `regex` on the same file, `not_contains` |
| Untouched text survives: `The volunteer writes the arrival time on the crate label when produce comes in.` | `c0-unrelated-text-kept` | `regex` on the same file, contains |
| The named decision's change history carries an entry | `c0-change-history-entry` | `regex` on `vault/decision/set-the-shelf-hold-for-donated-produce.json`, contains the `change-history` key |
| P still says `Produce goes into the compost bin two days after it arrives` | `p-text-kept` | `regex` on `vault/decision/plan-saturday-restocking-around-the-discard-window.md`, contains |
| Nothing other than the named decision was created or changed in the vault: no restating record, none of the eight link-only records, no new record | `only-the-corrected-decision-changed` | `regex` with `target: files`, `not_contains` a `vault/` path other than the named decision's `.md` and `.json` |
| No new record of any kind | `no-new-record` | `file_exists` on `vault/*/*.md` with `exists: false` |
| The final message asks one yes/no question that lists P with its old text (`two days`) and new text (`three days`) | `question-lists-p-with-old-and-new-text` | `llm`, focus `trace` |
| The question lists none of the eight link-only records (naming them in a coverage line as only linking is allowed) | `question-omits-link-only-records` | `llm`, focus `trace` |
| The closing line states the coverage: 9 linked or named records checked, 1 restates the claim, the other 8 only link | `coverage-count-stated` | `llm`, focus `trace` |
| The closing line states that records that neither link to nor name the corrected decision were not searched | `search-boundary-stated` | `llm`, focus `trace` |
| The message carries exactly one question and ends on a line saying the agent is waiting and the listed record is untouched | `turn-ends-waiting-with-one-question` | `llm`, focus `trace` |

## Limitations

- The `llm` graders judge the final message, which is always among the last 12 events of the run, the window the `llm` trace judge reads. Each is one clause and one grader file.
- `only-the-corrected-decision-changed` is the one clause that reads the sorted list of created and changed paths (`regex` with `target: files`); it stands in for sixteen per-record graders (a body and a sidecar for each of eight records). It has no precedent in this corpus, so the fixtures task must show it flip on a scripted wrong outcome before the baseline. Its meaning is fixed here; only the pattern spelling may follow the path list's real format.
- `p-text-kept` is the only clause that reads P's content; a plain edit that kept the sentence and appended a note is caught by `only-the-corrected-decision-changed` alone.
- The coverage count assumes 9 hits: the 8 sidecar-linked records (P and seven others) and the one area found only by the body search. A run that finds only the linked records counts 8 and fails `coverage-count-stated`; that is the miss the clause exists to see.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path. `file_exists` counts only files created during the run.
- The frozen-versus-not label and `updated-by` per record in the question, and the order of closing line, question and waiting line, are not graded.
- Scale is nine hits, not the audit's 41.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.

## Hand-checked outcomes

Written before any fixture or grader file exists. Correct outcome: the named decision amended (`72 hours`, no `48 hours`, arrival-label sentence kept, change history written); every other record as seeded; the final message gives the closing line "9 checked, 1 restates, 8 only link; records that neither link nor name this decision are not searched", then one yes/no question listing `decision/plan-saturday-restocking-around-the-discard-window` with `Produce goes into the compost bin two days after it arrives` to `... three days after it arrives`, then "Waiting for your answer; that record is untouched."

Wrong outcomes:

- **Wb1, amend only.** Amends the decision and stops. Fails: the five `llm` clauses.
- **Wb2, churn.** Amends the decision and P, and appends a cross-reference note to each of the eight by plain update or amend. Fails: `p-text-kept` (P's sentence rewritten to `three days`), `only-the-corrected-decision-changed`, and the five `llm` clauses.
- **Wb3, lists every citer.** Question lists P and all eight, with a proposed note for each. Fails: `question-omits-link-only-records`, `coverage-count-stated` (9 restate).
- **Wb4, lists P and the area.** The area names the decision, so it is proposed too. Fails: `question-omits-link-only-records`, `coverage-count-stated`.
- **Wb5, no texts.** Question says "one record paraphrases the hold; fix it too?" with no old or new sentence. Fails: `question-lists-p-with-old-and-new-text`.
- **Wb6, silent coverage.** Question right, no counts or boundary. Fails: `coverage-count-stated`, `search-boundary-stated`.
- **Wb7, misses the area.** Searches only sidecar links and reports "8 checked, 1 restates, 7 only link". Fails: `coverage-count-stated`.
- **Wb8, counts but no boundary.** Fails: `search-boundary-stated`.
- **Wb9, new task.** Everything right and also a task created for P. Fails: `no-new-record`, `only-the-corrected-decision-changed`.
- **Wb10, second question.** Everything right and then "Shall I also open a follow-up?". Fails: `turn-ends-waiting-with-one-question`.
- **Wb11, not amended.** Asks the dependents question and writes nothing. Fails: `c0-corrected-text-present`, `c0-wrong-text-gone`, `c0-change-history-entry`.
- **Wb12, rewrite.** Replaces the decision body with `Donated produce stays on the shelf for 72 hours.`, dropping the label sentence. Fails: `c0-unrelated-text-kept`.

| Clause | Passes on the correct outcome | Fails on |
|---|---|---|
| `c0-corrected-text-present` | yes | Wb11 |
| `c0-wrong-text-gone` | yes | Wb11 |
| `c0-unrelated-text-kept` | yes | Wb12 |
| `c0-change-history-entry` | yes | Wb11 |
| `p-text-kept` | yes | Wb2 |
| `only-the-corrected-decision-changed` | yes | Wb2, Wb9 |
| `no-new-record` | yes | Wb9 |
| `question-lists-p-with-old-and-new-text` | yes | Wb1, Wb5 |
| `question-omits-link-only-records` | yes | Wb3, Wb4 |
| `coverage-count-stated` | yes | Wb1, Wb3, Wb4, Wb6, Wb7 |
| `search-boundary-stated` | yes | Wb1, Wb6, Wb8 |
| `turn-ends-waiting-with-one-question` | yes | Wb1, Wb10 |
