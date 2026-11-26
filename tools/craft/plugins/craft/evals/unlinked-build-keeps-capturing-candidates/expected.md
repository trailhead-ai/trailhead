# unlinked-build-keeps-capturing-candidates

## Under test

A build whose parent has no spec link keeps today's behaviour: the same review is captured as session candidates and found_work.py is never called. A guard, expected green on the baseline and in the treatment arm. The prose under test is `skills/_shared/execute.md`, section "Found work" and the places it is called from, as it stands when the baseline runs (the unedited file, swapped in for the run and restored) and after the edit in the treatment arm.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants exactly as in craft's `eval-gate.json` `runner` block.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 40`, `timeout_seconds: 420`.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. A green baseline is recorded as green.

## Pass condition

Every clause holds.

| Clause | Grader |
|---|---|
| found_work.py is never called | `found-work-script-never-called` |
| the findings are session candidates | `findings-captured-as-session-candidates` |
| no found-work line is in the log | `no-found-work-lines-in-the-log` |

## Limitations

One fixture, one phrasing, one model tier, 3 runs per arm. The agent does not dispatch an executor in the sandbox (no git, no code), so a fold is judged by the decision it logs and the line it says it would dispatch, not by a code change. `./lore` and `bin/lore` are the seeded wrappers; the child puts `bin` on PATH for the script, as the prompt says.

## Deviation from pre-registration (2026-11-25): the run directory supplies the session

The prompt told the model to add `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to its lore and script commands. Across the cases whose prompts carried that instruction, `claude-sonnet-5-5` runs sometimes left the flag off, so their entries went to the runner's own session, where no grader reads, and some placed it after a stdin redirect, which the run's permission grants refused: the prompt's extra argument, not the procedure under test, decided those runs. The case's lore wrappers now set the case's session themselves, as Claude Code sets a real session's, and the prompt no longer names the flag, so the model runs each command as the procedure writes it. No grader, fixture record or bar changed.
