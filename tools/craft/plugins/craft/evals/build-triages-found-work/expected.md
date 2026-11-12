# build-triages-found-work

## Under test

Under a spec-linked build, a review with four findings (a one-line in-scope fix, an in-scope change that would balloon, an issue in another subsystem, and one too open for any slice) is decided one finding at a time, each decision logged before its write: the ballooning one becomes an open F1 entry in the spec, the other-subsystem one an unlinked task, the open one a \`route=brainstorm\` task, the small one is folded with no entry, and the report opens with the brainstorm command and ends with the decision sentences. Expected red on the baseline (decision-line, ordering, entry and task graders), because the unedited prose has no found-work rule. The prose under test is `skills/_shared/execute.md`, section "Found work" and the places it is called from, as it stands when the baseline runs (the unedited file, swapped in for the run and restored) and after the edit in the treatment arm. The decide-or-ask rule in `skills/_shared/escalation.md` is also under test here: the routine decisions this case makes are made and logged without a question to the operator, and the final message poses no found-work question while the routine write has landed (`no-found-work-question-was-asked`, a `regex` over the final message that fails on a choice-prompt shape and requires the final message to mention the in-scope header fix; expected green on the unedited prose and after the edit, so it is a guard against the rule over-asking, not a detector).

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants exactly as in craft's `eval-gate.json` `runner` block.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 40`, `timeout_seconds: 420`.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. A scaffold failure or timeout is an error, logged apart from graded fails and re-run. One rerun of a failed batch is permitted, stated here before any run; a rerun-green is reported as such.

## Pass condition

Every clause holds.

| Clause | Grader |
|---|---|
| a decision line of each of the four kinds is in the session log | `one-decision-line-per-finding` |
| a decision line precedes the first result line | `decision-logged-before-any-write` |
| the ballooning finding is an open `F1` entry | `deferred-finding-is-an-open-entry-F1` |
| the folded finding left no entry | `folded-finding-writes-no-entry` |
| two tasks were written, neither the parent | `separate-and-brainstorm-tasks-were-written` |
| the report opens with the operator items and ends with the decisions | `report-opens-with-the-operators-items` (llm) |

## Limitations

One fixture, one phrasing, one model tier, 3 runs per arm. The agent does not dispatch an executor in the sandbox (no git, no code), so a fold is judged by the decision it logs and the line it says it would dispatch, not by a code change. `./lore` and `bin/lore` are the seeded wrappers; the child puts `bin` on PATH for the script, as the prompt says.
