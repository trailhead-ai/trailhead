# build-close-marks-folded-entries

## Under test

When a spec-linked build reaches its Phase 5 final sweep, it settles every follow-up entry folded into the parent before the parent closes: an entry a finished child names (`named-by` a child at `done`), once the whole-change review passed, is marked done by the parent (`found_work.py entry`, transition `done`); every other folded entry returns to open (transition `reopen`) with the reason it was not built; the completion report carries both decisions with the rest of the found-work lines. Concretely, in `skills/_shared/execute.md`, the "Found work" section's final sweep and the places it is called from: it lists the entries with `found_work.py entries --spec spec/<name> --folded-into task/<parent> --vault <elected-vault>`, treats the output as data, marks the built ones done and reopens the rest, and a sweep failure (exit 2 or 3) goes among what needs the operator with the reason the entry was not built and the retry command printed. A run with no spec link, or with no folded entries, closes as before. The prose under test is `skills/_shared/execute.md`, as it stands when the baseline runs (content fingerprint `6967612a5e610d22a0bbc9122172eb5df2fdea11`) and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `tally-report` with two criteria (AC1 closed by the done slice `count-words`, AC2 covered by the parent) and a `## Follow-ups` section; a slice parent `task/count-lines` (`craft/slice-parent`, `in-progress`, covers AC2, `**Folds:**` F1 and F2); and one child task `task/count-lines-build` (`done`, parent `count-lines`) whose **Delivers** carries `Resolves spec/tally-report#F1: Close the file the counting helper opens`:

- F1, `folded into task/count-lines`, `where` is `tally/counter.py`: built by the child.
- F2, `folded into task/count-lines`, `where` is `tally/counter.py`, `tally/cli.py`, `tally/export.py`: named by no child, so it was not built.

`fixtures/review.md` is the finished Phase 3 whole-change review, with a PASS verdict and no findings. A lore session `aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` is seeded with one unrelated candidate. The prompt carries the run from Phase 4 through the Phase 5 final sweep over found work and the completion report, passes the session id to the `found_work.py` commands that take it, and puts this directory's `bin` first on `PATH`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, with the `runner` block grants of `eval-gate.json` (which already carry `Bash(*/scripts/found_work.py *)`; no grant is added).
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 600`.
- A run scores the fraction of graders that pass and passes only at 1.0.
- Baseline A: only `skills/_shared/execute.md` unedited (fingerprint above), 1 run, on the final prompt and graders. Expected red on `f1-is-done-by-the-parent`, `f2-is-open-again` and the decision, ordering and report graders. If it is not red on the F1 and F2 graders, Baseline B is also run: the plugin as it stood before this feature (`skills/slice/SKILL.md`, `skills/plan/SKILL.md`, `skills/_shared/execute.md` and `scripts/found_work.py` at their content before the feature began), and both are reported.
- Treatment: the edited prose, 3 runs, 3 of 3 pass.
- Regression: `build-triages-found-work`, `build-records-a-security-finding-by-location` and `unlinked-build-keeps-capturing-candidates`, 1 run each on the edited prose, unchanged. A failure is compared with its `MANUAL-EVAL.md` history before it counts against the prose.
- A scaffold failure or timeout is an error, logged apart from graded fails and re-run; one rerun of a failed batch is permitted, stated here before any run.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| F1, built by a done child, is `done by task/count-lines` | `f1-is-done-by-the-parent` | `regex`, `source: file`, the spec record |
| F2, built by no child, is `open` again | `f2-is-open-again` | `regex`, `source: file`, the spec record |
| The session log carries an `entry-done` decision for F1 and an `entry-reopened` decision for F2 under `task/count-lines`, F2's with a reason that says it was not built | `f1-done-and-f2-reopen-decisions-are-logged`, `f2-reopen-decision-carries-a-not-built-reason` | `regex`, `source: file`, the session log |
| Each decision line precedes its result line in the session log | `each-decision-precedes-its-result` | `regex`, `source: file`, the session log |
| The closing message states that F1 was marked done and F2 returned to open | `closing-message-reports-the-decisions` | `regex` over the final message |

## Limitations

- One spec, one phrasing, one model tier, three treatment runs. The fixture has one entry that was clearly built and one that clearly was not; it does not probe an entry named by a child that is not yet `done`, a review that did not pass, or an entry whose `done` the script refuses.
- The agent does not dispatch an executor or run git in the sandbox, so "the whole-change review passed" is the seeded `review.md`; the child's `done` status is seeded, not built.
- The failure paths (a sweep that exits 2 or 3, and its retry command) are not exercised by a run; the script's tests cover the commands.
- Seeding the session with a fixed id and passing it to the scripts is the eval's own device: a real run does not pass `--session-id`.
- `f2-is-open-again` holds only if the agent acts, since F2 starts folded; the two decision graders and the report grader likewise. No grader here is a guard that holds when nothing happens.
