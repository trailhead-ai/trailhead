# an-add-to-a-docket-with-many-finished-entries-gives-no-over-limit-warning

## Under test

The claim: finished entries do not count toward the 25-live-entry warning: an add that leaves a vault with 25 or fewer live entries gets no over-limit warning even when the list as a whole holds more than 25 entries. The prose under test is `skills/docket/SKILL.md`, loaded from the plain request the prompt gives. The fixture is the shared docket scaffold (`evals/docket-scaffold.sh`) with `FILLER_LIVE=14` and `FILLER_DONE=6`: before the add the list holds 20 live and 8 finished entries (28 in all); the add of one live task leaves 21 live and 29 in all. A change result's `live_count` is the list's entry count (29 here), which is above 25 although only 21 are live, so a session that warns from it warns wrongly. Scaffold settings: camp answering, sync working.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/docket/plugins/docket`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`.
- Limits: `allowed_tools: [Skill, Bash, Write, Read]`, `max_turns` 25, `timeout_seconds` 360.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such and not as stable. A scaffold failure or timeout is an error, logged apart from graded fails.
- The red baseline runs against `skills/docket/SKILL.md` as it stands before the change that has the session read the live count from the bare `docket` report. It is expected red: the old text warns from the list's entry count.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| The list file ends with the new task (the add landed) | `list-ends-with-the-task` | `regex` |
| The docket skill was loaded from the plain request | `skill-triggered-by-plain-words` | `tool_used` |
| The reply gives no over-the-limit warning | `reply-gives-no-over-limit-warning` | `regex` |

## Limitations

- One fixture, one model tier, three runs. The reply grader reads wording; a warning in unexpected words can slip past the no-warning pattern.
- The grader does not show that the session ran the bare `docket` report after the add; it shows only the outcome the report is for.
- `file_exists` counts only files created during the run; the seeded list is graded by `regex` over its exact path.
