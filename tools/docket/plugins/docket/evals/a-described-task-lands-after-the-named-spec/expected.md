# a-described-task-lands-after-the-named-spec

## Under test

The claim: a plain request to put a named task after a named spec lands the entry directly after that spec and leaves every other entry where it was. The prose under test is `skills/docket/SKILL.md`, loaded from the plain request the prompt gives. The fixture is the shared docket scaffold (`evals/docket-scaffold.sh`): a throwaway lore vault named `eval` with a bare origin, a docket list of eight entries (two of them finished, one attached to a workspace running on a host that answers), `./docket` as the real docket package reached through a test-only runner, and a stand-in `./camp`. Scaffold settings for this case: the defaults (standard list, camp answering, sync working).

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/docket/plugins/docket`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`.
- Limits: `allowed_tools: [Skill, Bash, Write, Read]`, `max_turns` 25, `timeout_seconds` 360.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such and not as stable. A scaffold failure or timeout is an error, logged apart from graded fails.
- The red baseline runs before `skills/docket/SKILL.md` exists, so the agent has the `./docket` tool and the prompt only. A green baseline is recorded as green.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| The list file holds the nine entries, the new task directly after the Tide table import spec and every other entry in its old place | `list-has-the-task-right-after-the-spec` | `regex` |
| The add named the after anchor, the spec ref and the task ref | `add-used-the-after-anchor` | `tool_used` |
| The docket skill was loaded from the plain request | `skill-triggered-by-plain-words` | `tool_used` |

## Limitations

- One fixture, one model tier, three runs. The graders read the list file, the tool calls and the reply's wording; a reply that is right in different words can fail a wording grader, and a right-looking reply can hide a wrong detail the wording graders do not read.
- `file_exists` counts only files created during the run; the seeded list is graded by `regex` over its exact path.
- The agent's writes stay uncommitted or committed in the throwaway vault only; nothing leaves the run directory.
