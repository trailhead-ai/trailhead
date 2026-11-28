# a-loose-thought-becomes-a-note-and-is-listed

## Under test

The claim: a loose thought is saved as a note record in the vault named like the camp group, then added to the docket, with the thought's body kept off every command line. The prose under test is `skills/docket/SKILL.md`, loaded from the plain request the prompt gives. The fixture is the shared docket scaffold (`evals/docket-scaffold.sh`): a throwaway lore vault named `eval` with a bare origin, a docket list of eight entries (two of them finished, one attached to a workspace running on a host that answers), `./docket` as the real docket package reached through a test-only runner, and a stand-in `./camp`. Scaffold settings for this case: the defaults (standard list, camp answering, sync working).

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/docket/plugins/docket`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`.
- Limits: `allowed_tools: [Skill, Bash, Write, Read]`, `max_turns` 25, `timeout_seconds` 360.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such and not as stable. A scaffold failure or timeout is an error, logged apart from graded fails.
- The red baseline runs before `skills/docket/SKILL.md` exists, so the agent has the `./docket` tool and the prompt only. A green baseline is recorded as green.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| One lore record create made a blob with the note keyword in vault eval | `note-created-in-the-group-vault` | `tool_used` |
| A blob record file was created in the vault | `a-blob-record-was-created` | `file_exists` |
| The docket list file holds a blob entry | `list-holds-the-note` | `regex` |
| docket add named the new note by its eval/blob ref | `note-added-by-ref` | `tool_used` |
| The title passed to lore is a short plain label (opens with a letter or digit, at most six words, one quoted argument), so the thought's body is not on a command line | `thought-body-not-on-a-command-line` | `tool_used` |

## Limitations

- One fixture, one model tier, three runs. The graders read the list file, the tool calls and the reply's wording; a reply that is right in different words can fail a wording grader, and a right-looking reply can hide a wrong detail the wording graders do not read.
- `file_exists` counts only files created during the run; the seeded list is graded by `regex` over its exact path.
- The agent's writes stay uncommitted or committed in the throwaway vault only; nothing leaves the run directory.
