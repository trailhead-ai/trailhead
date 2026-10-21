# yes-to-dependent-fixes-corrects-restating-records-only

## Under test

When the operator answers yes to the batched dependents question, the agent corrects each record the question listed, a frozen one by amendment and a non-frozen one by plain update, changes nothing else in either body, and leaves the linked-only record alone. The criteria are AC28 (the restating dependents are corrected in the same session) and AC29 (a record that only links is not touched), in `spec/frozen-records-are-corrected-in-place-amend-within-scope-supersede-only-on-a-material-change`, planned in `task/correcting-one-record-fixes-the-dependents-that-repeat-its-claim-and-nothing-else` (its "Dependents" step and its "On yes" paragraph: `--initiated-by agent`, no task for any dependent, each new body differing from its read-back only in the quoted span). The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose, which has no dependents step; in the treatment arm, the same plus the dependents step added to the `correct` skill.

The case seeds a prior conversation (`context.history_file`, `fixtures/history.jsonl`, resumed with `--resume`), built from the output of the real `lore` commands run against the scaffolded vault, in which the operator ruled that a power tool is lent for 6 days, not 4, on `decision/set-the-loan-period-for-power-tools`, the agent amended it and put one yes/no question listing the two restating records with their old and new text. The same seeded history serves the sibling case `no-to-dependent-fixes-leaves-every-record-as-it-was`. The prompt is the operator's next turn: "Yes." Wording cue: none beyond the bare answer; the answer carries the whole instruction.

Seeded vault, in file order (the first decision is as the history left it; no clause reads it):

- `decision/set-the-loan-period-for-power-tools`: the corrected record.
- `decision/keep-a-wipe-down-step-on-every-tool-return` (D2), `active`, sidecar link to the first decision: `A power tool is lent for 4 days and must be wiped down before it goes back on the shelf.` then `The desk volunteer signs the tool back in on the return sheet.`
- `area/tool-desk` (A1), `active`, no sidecar link, names `decision/set-the-loan-period-for-power-tools` in its body: `Members borrow a power tool for 4 days; the desk opens at ten on weekdays.`
- `task/check-the-drill-batteries-when-a-loan-comes-back` (T1), `done`, sidecar link to the first decision, asserts no loan period: `Test each drill battery when a loan comes back, and put flat ones on the charger.`

The question in the history lists D2 (frozen, amend) and A1 (non-frozen, plain update) and not T1.

## Pass condition

The run's vault ends with D2 amended and A1 updated, each correct in the quoted span and unchanged elsewhere, T1 as seeded and no new record. "Dependents untouched" does not apply here except to T1: this case expects writes to D2 and A1. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs. The graders are identical in the baseline and the treatment arm.

| Clause | Grader | Type and target |
|---|---|---|
| D2's new text (`lent for 6 days`) is present | `d2-corrected-text-present` | `regex` on `vault/decision/keep-a-wipe-down-step-on-every-tool-return.md`, contains |
| D2's old text (`4 days`) is gone | `d2-wrong-text-gone` | `regex` on the same file, `not_contains` |
| D2's rest of the first sentence survives: `and must be wiped down before it goes back on the shelf.` | `d2-unrelated-text-kept-1` | `regex` on the same file, contains |
| D2's second sentence survives: `The desk volunteer signs the tool back in on the return sheet.` | `d2-unrelated-text-kept-2` | `regex` on the same file, contains |
| D2 gained no note: no blockquote line and no revised, amended, corrected or erratum wording | `d2-no-note-added` | `regex` on the same file, `not_contains` a line starting with `>` or the words amended, revised, corrected, correction, erratum |
| D2's change history carries an entry | `d2-change-history-entry` | `regex` on `vault/decision/keep-a-wipe-down-step-on-every-tool-return.json`, contains the `change-history` key |
| The amend is attributed to the agent, because the agent proposed the dependent fix | `d2-initiated-by-agent` | `regex` on the same sidecar, contains `"initiated-by": "agent"` |
| A1's new text (`borrow a power tool for 6 days`) is present | `a1-corrected-text-present` | `regex` on `vault/area/tool-desk.md`, contains |
| A1's old text (`4 days`) is gone | `a1-wrong-text-gone` | `regex` on the same file, `not_contains` |
| A1's rest of the sentence survives: `the desk opens at ten on weekdays` | `a1-unrelated-text-kept` | `regex` on the same file, contains |
| T1 still has its sentence `Test each drill battery when a loan comes back, and put flat ones on the charger.` | `t1-body-unchanged` | `regex` on `vault/task/check-the-drill-batteries-when-a-loan-comes-back.md`, contains |
| T1 has no change history | `t1-no-change-history` | `regex` on `vault/task/check-the-drill-batteries-when-a-loan-comes-back.json`, `not_contains` the `change-history` key |
| No new record of any kind (no task for a dependent, no successor, no session note) | `no-new-record` | `file_exists` on `vault/*/*.md` with `exists: false` |

## Limitations

- The run's `trace.jsonl` and the `llm` trace judge see only the new turns, never the seeded ones. This case is graded on vault files only.
- The seeded tool results carry the real `revision:` of each scaffolded record, so the fixture's history is regenerated whenever a record body changes.
- "Body differs only in the quoted span" is graded by the two kept sentences and the no-note clause for D2, and by the kept clause for A1. An edit elsewhere in D2's title line or an unquoted rewording of a kept sentence is caught only if it breaks a kept-sentence pattern; a note appended to A1 is not seen.
- D2's amend `--reason` is not graded: that it names the original correction (`decision/set-the-loan-period-for-power-tools`), as the dependents step requires, is not read by any clause.
- A plain update of the non-frozen area is not asserted to write a change history, so no clause reads A1's sidecar.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path. `file_exists` counts only files created during the run.
- A run that asks again instead of acting writes nothing and fails the correction clauses; the operator has answered, so asking is not the expected route.
- Only a plain "Yes" is exercised. "Yes except X", an earlier blanket acceptance mistaken for this answer, and a refused write partway through the batch are unmeasured.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.

## Hand-checked outcomes

Written before any fixture or grader file exists. Correct outcome: D2 amended with `--initiated-by agent` to `A power tool is lent for 6 days and must be wiped down before it goes back on the shelf.` and its second sentence kept, change history written; A1 updated to `Members borrow a power tool for 6 days; the desk opens at ten on weekdays.`; T1 as seeded; no new file.

Wrong outcomes:

- **Wc1, nothing written.** Replies it will keep the fix in mind. Fails: every positive clause (the D2 and A1 correction, history and attribution clauses).
- **Wc2, frozen only.** Amends D2 and leaves A1. Fails: `a1-corrected-text-present`, `a1-wrong-text-gone`.
- **Wc3, area only.** Updates A1 and leaves D2. Fails: the D2 correction, history and attribution clauses.
- **Wc4, touches the link-only record.** Corrects D2 and A1, and rewrites T1's sentence to mention the 6 day period and amends it. Fails: `t1-body-unchanged`, `t1-no-change-history`.
- **Wc5, wrong attribution.** Amends D2 with `--initiated-by operator`. Fails: `d2-initiated-by-agent`.
- **Wc6, note added.** Corrects the span in D2 but drops the second sentence and appends a blockquote note. Fails: `d2-unrelated-text-kept-2`, `d2-no-note-added`.
- **Wc7, first sentence trimmed.** Rewrites D2 as `A power tool is lent for 6 days.` plus the second sentence. Fails: `d2-unrelated-text-kept-1`.
- **Wc8, area rewritten.** Replaces A1's body with `Members borrow a power tool for 6 days.` Fails: `a1-unrelated-text-kept`.
- **Wc9, task for the dependents.** Corrects both and also creates a task for "audit other loan-period mentions". Fails: `no-new-record`.
- **Wc10, supersession.** Creates a successor decision and supersedes D2, leaving D2's text as it was. Fails: `d2-corrected-text-present`, `d2-wrong-text-gone`, `d2-change-history-entry`, `d2-initiated-by-agent`, `no-new-record`.
- **Wc11, old text kept beside the new.** Amends D2 to `A power tool is lent for 6 days (was 4 days) and must be wiped down before it goes back on the shelf.` Fails: `d2-wrong-text-gone`.

| Clause | Passes on the correct outcome | Fails on |
|---|---|---|
| `d2-corrected-text-present` | yes | Wc1, Wc3, Wc10 |
| `d2-wrong-text-gone` | yes | Wc3, Wc10, Wc11 |
| `d2-unrelated-text-kept-1` | yes | Wc7 |
| `d2-unrelated-text-kept-2` | yes | Wc6 |
| `d2-no-note-added` | yes | Wc6 |
| `d2-change-history-entry` | yes | Wc3, Wc10 |
| `d2-initiated-by-agent` | yes | Wc5 |
| `a1-corrected-text-present` | yes | Wc2 |
| `a1-wrong-text-gone` | yes | Wc2 |
| `a1-unrelated-text-kept` | yes | Wc8 |
| `t1-body-unchanged` | yes | Wc4 |
| `t1-no-change-history` | yes | Wc4 |
| `no-new-record` | yes | Wc9, Wc10 |
