# slice-takes-an-all-reads-surface-as-one-ordinary-slice

## Under test

Given `/craft:slice spec/shelf-notes --unattended` on a `ready` spec whose one surface has only read criteria (AC1 `notes list`, AC2 `notes show <id>`, AC3 `notes search <text>`), nothing shipped, the chooser takes one ordinary slice covering every read, with a value claim and no enabler justification (AC8). The prose under test is `skills/_shared/slice.md` (fingerprint `6b1c75d0c50634a5af9e56aff9cb44663e5d47eb` before the first-slice rule) and `skills/slice/SKILL.md` (fingerprint `6444508502a5691cd5c2e94aacd1e3dca632606e` before it), as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds the spec `shelf-notes` (`ready`) with three plain read criteria and nothing else: no Open Questions, no Notes, no slicing wording. No ledger, no slice record. The scaffold is otherwise the same as the sibling cases.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`. No grant was added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500` (inside the 540 s merge-only budget). The prompt asks for single-command Bash calls.
- Baseline arm: the prose as the whole-surface rule left it (`skills/_shared/slice.md` `6b1c75d0c50634a5af9e56aff9cb44663e5d47eb`, `skills/slice/SKILL.md` `6444508502a5691cd5c2e94aacd1e3dca632606e`). **Honest expectation: probably green.** The whole-surface rule already takes AC1 to AC3 and the covers graders pass. The one route to red is the phase section's read description ("the plumbing phase ... a list that renders its zero state ... there is no feature yet"), which could lead a run to call an all-reads slice an enabler: it would then write no `**Covers:**` line, make no covers-gate call, and fail the covers graders and the ordinary-slice judge. If the baseline comes out green, the case guards the AC8 rule against a regression and does not discriminate; the controller decides whether that is acceptable.
- Treatment arm: the edited prose, bar 3 of 3 runs, shim stops at 2 once decided (disclosed deviation).
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A judge that disagrees across runs is tightened, never rerun away. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`, measuring `skills/_shared/slice.md` and `skills/slice/SKILL.md`.
- Graders read the written `**Covers:**` line and `**Value claim:**` section, the covers-gate call's `--covers` list, and the statement from the trace.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A written body has a `**Covers:**` line naming AC1, AC2 and AC3 and a `**Value claim:**` section | `the-written-parent-covers-every-read` | `tool_used` Write, `min: 1` |
| A `covers_gate.py` call's `--covers` list names AC1, AC2 and AC3 | `the-covers-gate-is-given-every-read` | `tool_used` Bash, `min: 1` |
| The statement presents an ordinary slice ("You will be able to ...", a "Try it ..." sentence, AC1 to AC3 made green); calling it an enabler, writing a justification in place of a value claim, or saying there is nothing to try fails | `the-slice-is-an-ordinary-one-with-no-enabler-justification` | `llm`, `focus: trace` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

## Limitations

- The absence of an enabler justification is judged by the llm grader; the covers graders only show an ordinary slice's shape, since an enabler writes no `**Covers:**` line.
- One fixture, one phrasing, one model tier, two runs as run.
- A baseline that comes out green is reported as non-discriminating (plan unknown U2), not strengthened silently.

## Deviation from pre-registration (2026-11-25): the run directory supplies the session

The prompt told the model to add `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to its lore and script commands. Across the cases whose prompts carried that instruction, `claude-sonnet-5-5` runs sometimes left the flag off, so their entries went to the runner's own session, where no grader reads, and some placed it after a stdin redirect, which the run's permission grants refused: the prompt's extra argument, not the procedure under test, decided those runs. The case's lore wrappers now set the case's session themselves, as Claude Code sets a real session's, and the prompt no longer names the flag, so the model runs each command as the procedure writes it. No grader, fixture record or bar changed.
