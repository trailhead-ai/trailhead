# drop-part-of-a-criterion-with-no-new-record

## Under test

When narrowing a `ready` spec's criterion drops one clause, and every objective is still delivered, the agent amends the criterion in place and creates no record for the dropped clause: no task, no follow-up, no session candidate, no successor. The criterion is AC32 (the part a narrowed criterion drops produces no new record unless the operator asks for it to be deferred), in `spec/frozen-records-are-corrected-in-place-amend-within-scope-supersede-only-on-a-material-change`, planned in `task/correcting-one-record-fixes-the-dependents-that-repeat-its-claim-and-nothing-else` (its narrowing rule, added beside Step 2's narrowing paragraph). The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose, which says nothing about the dropped part; in the treatment arm, the same plus the added narrowing rule. Whether the baseline already passes is Unknown U3 in the plan; a green baseline is reported as the measurement, not as a fix. This case differs from the existing `amend-narrow-one-criterion-in-ready-spec` in dropping a whole clause under a deferral cue, not in rewording a limit.

The prompt is an operator who says they are not doing the email part this round and dictates the narrowed criterion. Wording cue: "we're not doing that part this round" points at deferral, a task or a note for later, and the answer is no new record, because the operator did not ask for one. The fixture (a trail closure notice spec) shares no wording with any skill example.

Seeded spec `spec/publish-trail-closure-notices`, `ready`, objectives and criteria:

- Objectives: `Hikers can see which trails are closed before they set out.` and `Rangers can take a notice down when a trail reopens.`
- `**AC1.** A ranger can post a closure notice for a trail.`
- `**AC2.** A closure notice shows on the page for that trail and is emailed to hikers who follow that trail.`
- `**AC3.** A ranger can take a closure notice down when the trail reopens.`

The spec says nothing else about email. Dropping the email clause leaves the first objective delivered by AC1 and AC2 and the second by AC3, so the change is an amend.

The prompt: in `spec/publish-trail-closure-notices` the email part is not being done this round; change AC2 to read `A closure notice shows on the page for that trail.`

## Pass condition

The run's vault ends with the spec amended in place, the dropped clause gone, and no new record of any kind. "Dependents untouched" does not apply: the spec has no dependents in the vault. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs. The graders are identical in the baseline and the treatment arm.

| Clause | Grader | Type and target |
|---|---|---|
| The new AC2 text (`A closure notice shows on the page for that trail.`) is present | `corrected-text-present` | `regex` on `vault/spec/publish-trail-closure-notices.md`, contains |
| The dropped clause (`emailed`) is gone | `wrong-text-gone` | `regex` on the same file, `not_contains` |
| The narrowed criterion keeps its number, edited in place | `criterion-kept-in-place` | `regex` on the same file, contains `**AC2.**` |
| Untouched text survives: AC1 (`A ranger can post a closure notice for a trail.`) | `unrelated-text-kept-1` | `regex` on the same file, contains |
| Untouched text survives: AC3 (`A ranger can take a closure notice down when the trail reopens.`) | `unrelated-text-kept-2` | `regex` on the same file, contains |
| Untouched text survives: the first objective (`before they set out`) | `unrelated-text-kept-3` | `regex` on the same file, contains |
| The sidecar's change history carries an entry (seeded records have none) | `change-history-entry` | `regex` on `vault/spec/publish-trail-closure-notices.json`, contains the `change-history` key |
| Status unchanged (`ready`) | `status-unchanged` | `regex` on the same sidecar, contains `"status": "ready"` |
| No new record of any kind (task, session note, follow-up, successor spec) | `no-new-record` | `file_exists` on `vault/*/*.md` with `exists: false` |

## Limitations

- `file_exists` counts only files created during the run, so it sees a task, a session candidate or a successor and cannot see edits to seeded records, which is what this clause wants. `vault/*/*.md` covers tasks, sessions and specs.
- The agent's closing line, which the added rule says must state that the dropped part is untracked, kept in the change history and trackable on request, is not graded.
- `wrong-text-gone` keys on the single word `emailed`, which appears only in the dropped clause. A run that leaves a note such as "email deferred" in the spec passes it only if it avoids that word; the spec's text is otherwise fixed by the dictated AC2.
- A run that asks the operator instead of acting writes nothing and fails the correction clauses. The prompt is a single turn from an operator who has ruled, so asking is not the expected route.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.

## Hand-checked outcomes

Written before any fixture or grader file exists. Correct outcome: the spec amended in place so AC2 reads `**AC2.** A closure notice shows on the page for that trail. *Verified by: manual check.*`, AC1, AC3 and the objectives as seeded, a change-history entry, status `ready`; no file created; the final message says the email part is untracked.

Wrong outcomes:

- **Wd1, task for the dropped part.** Amends the spec and creates a task "Email followers of a closed trail". Fails: `no-new-record`.
- **Wd2, follow-up candidate.** Amends the spec and writes a session candidate for the email part. Fails: `no-new-record`.
- **Wd3, supersession.** Creates a successor draft spec without the email clause and supersedes the original; the original's text is kept. Fails: `corrected-text-present`, `wrong-text-gone`, `change-history-entry`, `status-unchanged`, `no-new-record`.
- **Wd4, asks and waits.** Asks whether to track the email part and writes nothing. Fails: `corrected-text-present`, `wrong-text-gone`, `change-history-entry`.
- **Wd5, criterion removed.** Deletes the AC2 line outright and leaves AC1 and AC3 numbered as they were. Fails: `corrected-text-present`, `criterion-kept-in-place`.
- **Wd6, clause kept with a note.** Leaves `emailed` in AC2 and appends a blockquote line "Email deferred". Fails: `corrected-text-present`, `wrong-text-gone`.
- **Wd7, status moved.** Amends the spec and sets it back to `draft`. Fails: `status-unchanged`.
- **Wd8, body replaced.** Amends by replacing the body with only `**AC2.** A closure notice shows on the page for that trail.`, dropping AC1, AC3 and the objectives. Fails: `unrelated-text-kept-1`, `unrelated-text-kept-2`, `unrelated-text-kept-3`.

| Clause | Passes on the correct outcome | Fails on |
|---|---|---|
| `corrected-text-present` | yes | Wd3, Wd4, Wd6 |
| `wrong-text-gone` | yes | Wd3, Wd4, Wd6 |
| `criterion-kept-in-place` | yes | Wd5 |
| `unrelated-text-kept-1` | yes | Wd8 |
| `unrelated-text-kept-2` | yes | Wd8 |
| `unrelated-text-kept-3` | yes | Wd8 |
| `change-history-entry` | yes | Wd3, Wd4 |
| `status-unchanged` | yes | Wd3, Wd7 |
| `no-new-record` | yes | Wd1, Wd2, Wd3 |
