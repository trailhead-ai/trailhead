# fix-up-list-routes-to-polish

## Under test

A numbered list of in-session code fix-ups framed "tidy these up" still reaches polish and not tidy-followups (AC47: a request to work through a list of in-session code fix-ups still routes to the polish ritual). The prose under test is `skills/polish/SKILL.md` and `skills/tidy-followups/SKILL.md` (frontmatter descriptions), as it stands when the baseline runs and after the distill description edit in the treatment arm. The prompt carries no slash command; the claim is that a plain-language request makes the agent invoke the right skill on its first turn.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, 3 runs, `-j 1`, no scaffold.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 4`, `timeout_seconds: 180`; enough to invoke a skill without doing its work.
- A run scores the fraction of graders that pass and passes only at 1.0. The bar is 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such.
- The baseline runs on the current, unedited prose before any prose edit. A green baseline is recorded as green.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The expected skill was invoked | `polish-used` | `tool_used`, `Skill`, input matching `craft:polish`, min 1 |
| The confusable skill was not invoked | `tidy-followups-not-used` | `tool_used`, `Skill`, input matching `craft:tidy-followups`, min 0 and max 0 |

## Limitations

- This case is expected to be green on the baseline, since the list is plainly code edits. It is a guard against the tidy-followups description pulling fix-up lists away from polish, and it is recorded as a guard if green.
- One prompt phrasing per case, one model tier, three runs. The signal is which skill the agent invoked, not what it did afterwards.
- The fixtures directory is a placeholder; no seeded spec exists, so the case does not measure how the ritual behaves once invoked.
