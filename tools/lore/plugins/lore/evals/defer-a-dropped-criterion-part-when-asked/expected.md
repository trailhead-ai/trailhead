# defer-a-dropped-criterion-part-when-asked

## Under test

When the operator narrows a `ready` spec's criterion and asks for the dropped part to be tracked for later, the agent amends the criterion in place and creates exactly one task that names the dropped part and links to the spec, and no other record. The criterion is AC32 (the part a narrowed criterion drops produces no new record unless the operator asks for it to be deferred, and then it is one task naming the dropped part and linked to the spec), in `spec/frozen-records-are-corrected-in-place-amend-within-scope-supersede-only-on-a-material-change`, planned in `task/correcting-one-record-fixes-the-dependents-that-repeat-its-claim-and-nothing-else` (its narrowing rule). This is the defer branch of the rule that `drop-part-of-a-criterion-with-no-new-record` measures from the other side. The prose under test is whatever the agent reads about correcting a frozen record and about capturing work: in the baseline arm, the current lore and craft prose, which has no narrowing rule for the dropped part; in the treatment arm, the same plus the added rule. Whether the baseline already passes is Unknown U3 in the plan; a green baseline is reported as the measurement.

The prompt is an operator who says the text-message part is not being built this round, dictates the narrowed criterion and asks for a task for that part, linked to the spec. Wording cue: the explicit "track it for later" points at a task, and the answer is the task: the cue and the answer agree, so the case measures whether the agent writes exactly one task, links it, and names the dropped part, not whether it guesses the operator's wish. The fixture (a library hold-pickup spec) shares no wording with any skill example.

Seeded spec `spec/let-members-collect-holds`, `ready`, objectives and criteria:

- Objectives: `Members can pick up a book the library is holding for them.` and `Members find out when a hold is ready.`
- `**AC1.** A member can collect a held book from the front desk by showing a library card.`
- `**AC2.** A member is told by text message and by email when a hold is ready.`
- `**AC3.** A held book is kept at the front desk for seven days.`

The spec says nothing else about text messages. Dropping that part leaves the second objective delivered by the email in AC2, so the change is an amend.

The prompt: in `spec/let-members-collect-holds` the text message part is not being built this round; change AC2 to read `A member is told by email when a hold is ready.`, and write a task for the text message part, linked to the spec.

## Pass condition

The run's vault ends with the spec amended in place, one new task, and no other new record. "Dependents untouched" does not apply: this case expects writes, and the spec has no dependents in the vault. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs. The graders are identical in the baseline and the treatment arm.

| Clause | Grader | Type and target |
|---|---|---|
| The new AC2 text (`A member is told by email when a hold is ready.`) is present | `corrected-text-present` | `regex` on `vault/spec/let-members-collect-holds.md`, contains |
| The dropped part (`text message`) is gone from the spec | `wrong-text-gone` | `regex` on the same file, `not_contains` |
| The narrowed criterion keeps its number, edited in place | `criterion-kept-in-place` | `regex` on the same file, contains `**AC2.**` |
| Untouched text survives: AC1 (`by showing a library card`) | `unrelated-text-kept-1` | `regex` on the same file, contains |
| Untouched text survives: AC3 (`A held book is kept at the front desk for seven days.`) | `unrelated-text-kept-2` | `regex` on the same file, contains |
| The sidecar's change history carries an entry (seeded records have none) | `change-history-entry` | `regex` on `vault/spec/let-members-collect-holds.json`, contains the `change-history` key |
| Status unchanged (`ready`) | `status-unchanged` | `regex` on the same sidecar, contains `"status": "ready"` |
| A task exists | `task-created` | `file_exists` on `vault/task/*.md` |
| Exactly one `./lore record create` call made a task, and that call names the dropped part and links to the spec | `deferral-task-call-names-dropped-part-and-spec` | `tool_used`: `tool: Bash`, `min: 1`, `max: 1`, `input_match` a JS-regex lookahead over the JSON of the whole tool input requiring `record create`, `--kind task`, `spec=let-members-collect-holds` and `[Tt]ext[ -]message`, with no quote characters in the pattern |
| No session note (no follow-up candidate) was written | `no-session-note` | `file_exists` on `vault/session/*.md` with `exists: false` |
| No successor spec was created | `no-successor-spec` | `file_exists` on `vault/spec/*.md` with `exists: false` |

## Limitations

- The task is graded with the runner's `tool_used` grader, not an `llm` trace grader: the `llm` judge sees only the first 12 and last 12 events of a run past 24, so a mid-run create call could fall outside it (plan Unknown U2). `tool_used` scans every call with no window. It is read from the runner binary and has no precedent in this repo (the evals README lists only `regex`, `file_exists` and `llm`); it is proven at the fixtures task's smoke run (Unknown U4), by flipping it against a correct and a wrong scripted outcome, before the baseline. Its meaning is fixed here, and only the pattern spelling may follow the proof.
- The pattern carries no quote characters because the tool input is JSON, which escapes a quote as `\"` in the text the pattern runs over.
- `max: 1` counts matching create calls, so a second create call that carries the same terms fails it; a second task made by a call that omits one of the terms is not counted, and `task-created` cannot count files. "Exactly one task" is therefore the pair of `max: 1` and existence, and a second, differently worded task would pass both.
- Only a session note and a successor spec are checked for "no other new record"; a stray decision, lesson or ADR would pass. `file_exists` counts only files created during the run.
- The task's own link is read from the create call's text, not from the created sidecar, whose name the agent picks and a `regex` grader cannot glob.
- `wrong-text-gone` keys on the words `text message`, which appear only in the dropped clause.
- A run that asks the operator instead of acting writes nothing and fails the correction and task clauses; the operator has ruled, so asking is not the expected route.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path. The agent's writes stay uncommitted in the vault's git, so every clause is graded on files and calls, never on commits.

## Hand-checked outcomes

Written before any fixture or grader file exists. Correct outcome: the spec amended in place so AC2 reads `**AC2.** A member is told by email when a hold is ready. *Verified by: manual check.*`, AC1, AC3 and objectives as seeded, a change-history entry, status `ready`; one Bash call `./lore record create --kind task --title "Send a text message when a hold is ready" --related spec=let-members-collect-holds ...` with a body describing the text message part; one task file; no session note, no successor.

Wrong outcomes:

- **We1, amend only.** Amends the spec and writes nothing else (the other case's behaviour). Fails: `task-created`, `deferral-task-call-names-dropped-part-and-spec` (no matching call).
- **We2, task not linked.** Creates a task about the text message part with no `--related spec=...`. Fails: `deferral-task-call-names-dropped-part-and-spec`.
- **We3, task does not name the part.** Creates a task titled "Follow up on holds" whose body has no mention of text messages, linked to the spec. Fails: `deferral-task-call-names-dropped-part-and-spec`.
- **We4, two tasks.** Creates two tasks, each by a call carrying the terms. Fails: `deferral-task-call-names-dropped-part-and-spec` (`max: 1`).
- **We5, task but no amend.** Creates the linked task and leaves AC2 as seeded. Fails: `corrected-text-present`, `wrong-text-gone`, `change-history-entry`.
- **We6, extra candidate.** Amends, creates the task, and also writes a session candidate. Fails: `no-session-note`.
- **We7, supersession.** Creates a successor draft spec and the task, and supersedes the original; the original's text is kept. Fails: `corrected-text-present`, `wrong-text-gone`, `change-history-entry`, `status-unchanged`, `no-successor-spec`.
- **We8, criterion removed.** Deletes the AC2 line and leaves AC1 and AC3 numbered as they were, plus the task. Fails: `corrected-text-present`, `criterion-kept-in-place`.
- **We9, body replaced.** Amends by replacing the body with only the AC2 line, plus the task. Fails: `unrelated-text-kept-1`, `unrelated-text-kept-2`.
- **We10, status moved.** Amends, creates the task, and sets the spec back to `draft`. Fails: `status-unchanged`.
- **We11, dropped part kept.** Writes AC2 as `A member is told by email when a hold is ready (text message deferred).` plus the task. Fails: `corrected-text-present` (the sentence no longer ends at `ready.`) and `wrong-text-gone`.

| Clause | Passes on the correct outcome | Fails on |
|---|---|---|
| `corrected-text-present` | yes | We5, We7, We8, We11 |
| `wrong-text-gone` | yes | We5, We7, We11 |
| `criterion-kept-in-place` | yes | We8 |
| `unrelated-text-kept-1` | yes | We9 |
| `unrelated-text-kept-2` | yes | We9 |
| `change-history-entry` | yes | We5, We7 |
| `status-unchanged` | yes | We7, We10 |
| `task-created` | yes | We1 |
| `deferral-task-call-names-dropped-part-and-spec` | yes | We1, We2, We3, We4 |
| `no-session-note` | yes | We6 |
| `no-successor-spec` | yes | We7 |
