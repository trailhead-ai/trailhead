# a-description-that-derives-an-unsafe-slug-is-found-by-search

## Under test

The claim: when the operator's description would derive a slug that does not fit the slug shape, no lore or docket command carries it, and the record is found by search and added by its real ref. The prose under test is `skills/docket/SKILL.md`, loaded from the plain request the prompt gives. The fixture is the shared docket scaffold (`evals/docket-scaffold.sh`): a throwaway lore vault named `eval` with a bare origin, a docket list of eight entries (two of them finished, one attached to a workspace running on a host that answers), `./docket` as the real docket package reached through a test-only runner, and a stand-in `./camp`. Scaffold settings for this case: the defaults (standard list, camp answering, sync working).

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/docket/plugins/docket`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`.
- Limits: `allowed_tools: [Skill, Bash, Write, Read]`, `max_turns` 25, `timeout_seconds` 360.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such and not as stable. A scaffold failure or timeout is an error, logged apart from graded fails.
- The red baseline runs against `skills/docket/SKILL.md` as it stood before this change. A green baseline is recorded as green, and the case is then a guard rather than a detector.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| A lore search was run to find the record | `record-found-by-search` | `tool_used` |
| No Bash command carries the unsafe words from the description | `no-command-carries-the-unsafe-words` | `tool_used` |
| docket add named the found task by its eval/task ref | `task-added-by-ref` | `tool_used` |
| The docket list file holds the task | `list-holds-the-task` | `regex` |

## Limitations

- One fixture, one model tier, three runs. The graders read the list file, the tool calls and files the run leaves; a right-looking run can hide a wrong detail they do not read.
- `file_exists` counts only files created during the run; the seeded list is graded by `regex` over its exact path.
- The agent's writes stay uncommitted or committed in the throwaway vault only; nothing leaves the run directory.
