# a-notes-temp-file-is-private-and-removed-before-it-is-listed

## Under test

The claim: the temp file a note is written through is created readable only by its owner (mode 0600) and is gone by the time the note is added to the docket. The prose under test is `skills/docket/SKILL.md`, loaded from the plain request the prompt gives. The fixture is the shared docket scaffold (`evals/docket-scaffold.sh`): a throwaway lore vault named `eval` with a bare origin, a docket list of eight entries (two of them finished, one attached to a workspace running on a host that answers), `./docket` as the real docket package reached through a test-only runner, and a stand-in `./camp`. Scaffold settings for this case: the defaults (standard list, camp answering, sync working), then `./lore` and `./docket` are wrapped so each records into `tempfile-trace.log`: `./lore record create` the mode and inode of the file on its standard input, and `./docket add` whether that inode is still present in `$TMPDIR`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/docket/plugins/docket`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`.
- Limits: `allowed_tools: [Skill, Bash, Write, Read]`, `max_turns` 25, `timeout_seconds` 360.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such and not as stable. A scaffold failure or timeout is an error, logged apart from graded fails.
- The red baseline runs against `skills/docket/SKILL.md` as it stood before this change. A green baseline is recorded as green, and the case is then a guard rather than a detector.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| The file lore read the note from was a regular file of mode 600, and it was gone when docket add ran | `temp-file-private-and-gone` | `regex` |
| One lore record create made a blob with the note keyword in vault eval | `note-created-in-the-group-vault` | `tool_used` |
| docket add named the new note by its eval/blob ref | `note-added-by-ref` | `tool_used` |

## Limitations

- One fixture, one model tier, three runs. The graders read the list file, the tool calls and files the run leaves; a right-looking run can hide a wrong detail they do not read.
- `file_exists` counts only files created during the run; the seeded list is graded by `regex` over its exact path.
- The agent's writes stay uncommitted or committed in the throwaway vault only; nothing leaves the run directory.
