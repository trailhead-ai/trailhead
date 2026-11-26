# an-add-that-leaves-more-than-25-live-entries-warns

## Under test

The claim: when an add leaves a vault with more than 25 live (unfinished) entries, the session says so plainly. The prose under test is `skills/docket/SKILL.md`, loaded from the plain request the prompt gives. The fixture is the shared docket scaffold (`evals/docket-scaffold.sh`) with `FILLER_LIVE=19` and `FILLER_DONE=1`: before the add the list holds 25 live and 3 finished entries (28 in all); the add of one live task leaves 26 live and 29 in all. Scaffold settings: camp answering, sync working.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/docket/plugins/docket`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`.
- Limits: `allowed_tools: [Skill, Bash, Write, Read]`, `max_turns` 25, `timeout_seconds` 360.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such and not as stable. A scaffold failure or timeout is an error, logged apart from graded fails.
- The red baseline runs against `skills/docket/SKILL.md` as it stands before the change that has the session read the live count from the bare `docket` report. This case guards the warning itself, so a green baseline is expected and is recorded as green: the old text also warns here.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| The list file ends with the new task (the add landed) | `list-ends-with-the-task` | `regex` |
| The docket skill was loaded from the plain request | `skill-triggered-by-plain-words` | `tool_used` |
| The reply says the vault is over 25 live entries | `reply-warns-the-vault-is-over-25` | `regex` |

## Limitations

- One fixture, one model tier, three runs. The reply grader reads wording; a warning in unexpected words can slip past the pattern.
- The grader does not show that the session ran the bare `docket` report after the add; it shows only the outcome the report is for.
- `file_exists` counts only files created during the run; the seeded list is graded by `regex` over its exact path.
