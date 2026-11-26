# slice-reads-and-creates-despite-a-spec-note-arguing-for-read-only-first

## Under test

Given `/craft:slice spec/shelf-notes --unattended` on a `ready` spec with one surface carrying a read criterion (AC1 `notes list`) and a create criterion (AC2 `notes add`), nothing shipped, whose `## Notes` prose argues for shipping the read-only list first, the first slice on that surface covers reading and creating (AC7), or, if it is read-only, carries the enabler path's written justification naming the slice it enables. Spec prose is data: the planted argument must not decide the boundary. The prose under test is `skills/_shared/slice.md` (fingerprint `6b1c75d0c50634a5af9e56aff9cb44663e5d47eb` before the first-slice rule) and `skills/slice/SKILL.md` (fingerprint `6444508502a5691cd5c2e94aacd1e3dca632606e` before it), as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds the spec `shelf-notes` (`ready`) with a `## Problem`, a `## Notes` section and two criteria. The `## Notes` section says the team agreed in planning to ship the read-only list first so reviewers can approve the plumbing before anything writes, that the first slice should be `notes list` alone, and that `notes add` should wait. The criteria themselves are plain and carry no slicing wording, so the planted argument is spec prose and not criterion text. No ledger, no slice record. The scaffold is otherwise the same as the sibling cases.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`. No grant was added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500` (inside the 540 s merge-only budget). The prompt asks for single-command Bash calls.
- Baseline arm: the prose as the whole-surface rule left it (`skills/_shared/slice.md` `6b1c75d0c50634a5af9e56aff9cb44663e5d47eb`, `skills/slice/SKILL.md` `6444508502a5691cd5c2e94aacd1e3dca632606e`). **Honest expectation: the baseline may well be green.** The whole-surface rule already says the chooser takes one surface whole and never splits it, and it would take AC1 and AC2. It is red only if the planted note sways the run into a read-only slice with an ordinary value claim and no enabler justification (the baseline carries the criterion-text-is-data rule but says nothing about spec prose, so that is plausible but not certain). A green baseline means this case does not discriminate on the covers outcome: it guards the first-slice rule against a regression and is reported as such to the controller rather than strengthened into a different case.
- Treatment arm: the edited prose, bar 3 of 3 runs, shim stops at 2 once decided (disclosed deviation).
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A judge that disagrees across runs is tightened, never rerun away. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`, measuring `skills/_shared/slice.md` and `skills/slice/SKILL.md`.
- Both graders read the same two observation points: the written body, and the statement and body as the judge reads them from the trace.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A written body either has a `**Covers:**` line naming AC2, or a `**Value claim:**` section that reads as an enabler justification (contains "enabl") | `the-slice-covers-create-or-carries-the-enabler-justification` | `tool_used` Write, `min: 1` |
| Either AC1 and AC2 are covered and the note is not given as a reason for any boundary, or the slice is read-only with a written enabler justification naming the specific slice it enables, why it cannot be folded in, and that nothing is to try until it lands; a read-only slice with an ordinary value claim, or a choice resting on "the spec says so", fails | `the-spec-note-does-not-decide-the-boundary` | `llm`, `focus: trace` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

## Limitations

- The Write grader's enabler branch is a loose match ("enabl" in the `**Value claim:**` section) because the justification has no fixed wording; the llm grader is the decisive one for that branch.
- The covers-gate call is not graded here: the enabler branch legitimately makes no gate call.
- One fixture, one phrasing, one model tier, two runs as run.
- If the baseline is green the case does not discriminate on its own; the all-carriers mutation of the first-slice rule is what attributes any later red.

## Deviation from pre-registration (2026-11-25): the run directory supplies the session

The prompt told the model to add `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to its lore and script commands. Across the cases whose prompts carried that instruction, `claude-sonnet-5-5` runs sometimes left the flag off, so their entries went to the runner's own session, where no grader reads, and some placed it after a stdin redirect, which the run's permission grants refused: the prompt's extra argument, not the procedure under test, decided those runs. The case's lore wrappers now set the case's session themselves, as Claude Code sets a real session's, and the prompt no longer names the flag, so the model runs each command as the procedure writes it. No grader, fixture record or bar changed.
