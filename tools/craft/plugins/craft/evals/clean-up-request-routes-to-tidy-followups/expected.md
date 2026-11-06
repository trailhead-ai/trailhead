# clean-up-request-routes-to-tidy-followups

## Under test

A request to clean up a spec's recorded follow-ups reaches tidy-followups and not polish (AC46). "Clean up" collides with polish's trigger "clean these up". The prose under test is `skills/tidy-followups/SKILL.md` and `skills/polish/SKILL.md` (frontmatter descriptions), as it stands when the baseline runs and after the distill description edit in the treatment arm. The prompt carries no slash command; the claim is that a plain-language request makes the agent invoke the right skill on its first turn.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, 3 runs, `-j 1`, no scaffold.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 4`, `timeout_seconds: 180`; enough to invoke a skill without doing its work.
- A run scores the fraction of graders that pass and passes only at 1.0. The bar is 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such.
- The baseline runs on the current, unedited prose before any prose edit. A green baseline is recorded as green.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The expected skill was invoked | `tidy-followups-used` | `tool_used`, `Skill`, input matching `craft:tidy-followups`, min 1 |
| The confusable skill was not invoked | `polish-not-used` | `tool_used`, `Skill`, input matching `craft:polish`, min 0 and max 0 |

## Limitations

- This case may be green on the baseline. A green baseline is recorded as green and the case is a guard, not a detector. The polish description edit is made only if the baseline shows the two confused.
- One prompt phrasing per case, one model tier, three runs. The signal is which skill the agent invoked, not what it did afterwards.
- The fixtures directory is a placeholder; no seeded spec exists, so the case does not measure how the ritual behaves once invoked.
