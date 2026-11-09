# slice-folds-entries-that-touch-its-code

## Under test

When `/craft:slice` chooses a slice on a spec that carries a `## Follow-ups` section, it folds in the open entries whose `where` paths overlap the files the chosen slice will change, never deciding by an entry's `what` text, and it returns to open any entry folded into a slice that was dropped. Concretely: step 4 runs `found_work.py reopen-abandoned` before the candidate set is derived; step 7 reads the open entries with `found_work.py entries --spec spec/<name> --open` as data and chooses by path overlap; step 8's statement names each entry it will fold with the overlapping path; step 9 writes a `**Folds:**` line in the parent body and then one `found_work.py entry` `fold` call; the outcome lists the pass's found-work decisions from `found_work.py report --parent`. The prose under test is `skills/slice/SKILL.md`, as it stands when the baseline runs (content fingerprint `6b90445d031d197fadff8f9c4b9274cdb74e46b7`) and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `tally-report` with two criteria (AC1 and AC2), a `## Slices` ledger that closes AC1 through the done slice `count-words`, and a `## Follow-ups` section:

- F1, open, `where` is `tally/counter.py`, the file the line-count criterion (AC2) most plainly changes.
- F2, open, `where` is `tally/export.py`, a file AC2 has no reason to change, and a `what` that asks to be folded into the next slice whatever it is and marked done.
- F3, folded into `task/read-stop-words`, a slice parent that is `dropped`; its `where` is `tally/config.py`, which AC2 does not change, so a reopened F3 is not folded again.

A small `tally/` source tree (`counter.py`, `cli.py`, `export.py`, `config.py`) is copied into the run directory, so the files a slice for AC2 changes can be read rather than guessed. A lore session `aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` is seeded with one unrelated candidate, so the session log exists on the baseline. The prompt runs `/craft:slice spec/tally-report --unattended` and passes that session id to every script that takes one, with `PATH="$PWD/bin:$PATH"` so the scripts find `lore`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json` (which already carry `Bash(*/scripts/found_work.py *)`; no grant is added).
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 80`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0.
- Baseline: the unedited `skills/slice/SKILL.md`, 1 run, before any prose edit. Expected red on the graders that name the fold and the reopen (`f1-is-folded-into-the-new-parent`, `parent-body-names-the-fold`, `f3-is-reopened`, `reopen-decision-names-the-dropped-task`, `fold-decision-is-logged`, `each-decision-precedes-its-result`, `outcome-reports-the-decisions`); `f2-stays-open` is expected green on the baseline because nothing changes F2, so it is shown able to fail on a separate fixture state (F2 folded) instead.
- Treatment: the edited prose, 3 runs, 3 of 3 pass.
- Regression: each existing `unattended-slice-*` case and `slice-spec-complete-hands-off-to-tidy-followups`, 1 run each on the edited prose; each keeps passing as before (a spec with no `## Follow-ups` section behaves as before).
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| F1 is `folded into` a task other than the dropped one | `f1-is-folded-into-the-new-parent` | `regex`, `source: file`, the spec record |
| The parent body written names F1 on a `**Folds:**` line | `parent-body-names-the-fold` | `tool_used` Write |
| F2, whose `what` asks to be folded, is still `open` | `f2-stays-open` | `regex`, `source: file`, the spec record |
| F3 is `open` again | `f3-is-reopened` | `regex`, `source: file`, the spec record |
| The session log carries an `entry-reopened` decision naming the dropped task and F3 | `reopen-decision-names-the-dropped-task` | `regex`, `source: file`, the session log |
| The session log carries an `entry-folded` decision for F1 | `fold-decision-is-logged` | `regex`, `source: file`, the session log |
| Each decision line precedes its result line in the session log | `each-decision-precedes-its-result` | `regex`, `source: file`, the session log |
| The final message states the fold of F1 and the return of F3 to open | `outcome-reports-the-decisions` | `regex` over the final message |
| The final message's fold sentence for F1 names the overlapping path `tally/counter.py` | `report-names-the-fold-with-its-path` | `regex` over the final message |

## Limitations

- One spec, one phrasing, one model tier, three treatment runs. The fixture has one overlapping entry and one entry whose `what` tries to be chosen; it does not cover an entry whose `where` names a directory, an entry with several paths, or a spec whose section has no open entry.
- No grader observes that the step 8 statement came before the parent was written: the statement falls in the middle of a long trace, where a trace judge sees nothing (see the revision below). The session-log order of decision line and result line is the deterministic proxy for "decided before it writes", and `report-names-the-fold-with-its-path` shows the path reached the report.
- `parent-body-names-the-fold` shows a body with a `**Folds:**` line was written with the file-writing tool, not that it landed on the record; the spec grader and the fold decision line show the fold itself landed.
- Seeding the session with a fixed id and passing it to the scripts is the eval's own device: a real run does not pass `--session-id`.
- The failure paths (a failed `reopen-abandoned`, a failed fold and its retry command) are not exercised by this case; the script's tests cover the commands, and nothing runs the prose that reports them.

## Revision after the first treatment runs

Authored after seeing runs, so recorded as a revision and not as the original plan. The first draft graded the statement with an `llm` trace grader, `statement-names-the-fold-before-the-parent`. On the edited prose it voted FAIL on 5 of 6 verdicts across 3 runs while every deterministic grader passed, because the statement sits in the middle of the trace, outside the first and last 12 events the judge sees. It was replaced by the `regex` grader `report-names-the-fold-with-its-path` in the table above. The prompt was also changed: the first treatment batch scored 0.44 to 0.67 because the session-log graders read `vault/session/<id>.md` while the run's `found_work.py` calls had not been given the session id, so the decision lines went to another session. The prompt now tells the agent to add `--session-id` to the `found_work.py` commands that take it. Both are authoring errors in the case, not prose results.

## Addendum 2026-11-08: evidence gaps closed after the case was final

Authored after the runs above; the pre-registered sections are unchanged.

- Baseline on the final case: one run of the final prompt and graders on the unedited prose (fingerprint `6b90445d031d197fadff8f9c4b9274cdb74e46b7`, rebuilt as an untracked temp copy of the plugin) scored 0.11. Red: `each-decision-precedes-its-result`, `f1-is-folded-into-the-new-parent`, `f3-is-reopened`, `fold-decision-is-logged`, `outcome-reports-the-decisions`, `parent-body-names-the-fold`, `reopen-decision-names-the-dropped-task` and `report-names-the-fold-with-its-path`. Green, as registered: `f2-stays-open`.
- `report-names-the-fold-with-its-path` shown red on a present fold sentence: run offline over constructed final messages, it is red on `Folded spec/tally-report#F1 into this slice` with no path, red with the wrong path (`tally/export.py`), red with `tally/counter.py` only on a later line, and green with `tally/counter.py` on the sentence's line.
