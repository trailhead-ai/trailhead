# slice-first-slice-reads-and-creates-when-an-unknown-governs-the-create

## Under test

Given `/craft:slice spec/shelf-notes --unattended` on a `ready` spec with one surface (the `notes` command group) carrying a read criterion (AC1 `notes list`, which also prints the notes the old `jot` tool already saved) and a create criterion (AC2 `notes add <text>`), nothing shipped, and an `## Open Questions` entry declaring an unknown that governs the create criterion, the chooser still makes the first slice cover reading and creating: the parent it writes covers AC1 and AC2. The prose under test is `skills/_shared/slice.md` (fingerprint `1cdf4bf2e21e2d9308ac80dcb35f936fab641dd9`) and `skills/slice/SKILL.md` (fingerprint `516322e39e3b008a1a1f21f28c9902dbb881949a`) as they stand when the arms run; this dispatch edits neither.

This is the case in which the first-slice rule decides the answer on its own (spec follow-up F1). The earlier unknown case put the unknown on edit and delete, so the unknown reason's "the cut leaves out only the criteria that unknown governs" kept list and add in the slice without any help from the first-slice rule, and removing that rule left the case green. Here the unknown governs the create criterion itself.

## Why the two arms differ (rule by rule, worked out before any run)

With the first-slice rule: the surface is `notes`, one surface, nothing shipped, remaining criteria include creation. The whole-surface rule takes AC1 and AC2. The unknown reason (the spec declares the unknown in `## Open Questions`, and it governs AC2) would cut AC2. The first-slice rule says "none of them moves the surface's own create criterion out of a first slice", so the cut is refused and the slice covers AC1 and AC2. The read-only alternative needs the enabler path, which needs a slice with no consumer value; AC1 has value on its own (it prints the legacy notes), so no enabler justification is available and the slice is the ordinary one over reading and creating. **Deciding branch: covers both read and create.**

Without the first-slice rule (every carrier removed): the same unknown reason applies and nothing refuses the cut. The unknown is declared in Open Questions, it governs AC2 and only AC2, and AC1 is complete and useful on its own, so the stop-here test passes at the boundary. The chooser ends the slice before AC2 and the written `**Covers:**` line and the covers-gate list name AC1 only. The all-reads rule is not in play (AC2 remains), and the zero-state plumbing sentence in the phase section does not apply (AC1 reads real notes). So the two arms give different covers lists.

## Fixture

`fixtures/scaffold.sh` runs the unchanged `seed.sh`, makes `./lore` self-contained, writes the `bin/lore` shim and a `CLAUDE.md` with `## Project Maturity` / `early`, then seeds the spec `shelf-notes` (`ready`): AC1 and AC2 as above, and one `## Open Questions` entry asking whether the notes file keeps both notes when two terminals run `notes add` at the same moment, stating it must be learned before anything that writes to the notes file is built, that `notes add` is the only criterion that writes, and that `notes list` does not depend on it. The unknown is declared in Open Questions, not in a criterion's wording. No ledger, no slice record.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`. No grant was added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500` (inside the 540 s merge-only budget). The prompt asks for single-command Bash calls.
- Current-prose arm, expected **green**, bar 3 of 3 runs. A run scores the fraction of graders that pass and passes only at 1.0.
- First-slice-rule-removed arm (the probe patch removes every carrier of the rule: the rule paragraph in `skills/_shared/slice.md` and step 7's pointer to it in `skills/slice/SKILL.md`), expected **red**, bar 3 of 3 runs red on the covers graders.
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`, measuring `skills/_shared/slice.md` and `skills/slice/SKILL.md` (core cases for both files already exist).

## Pass condition

Every clause holds. Observation points are the written parent's body (a `Write`) and the covers-gate call (a `Bash` input); there is no judge.

| Clause | Grader | Type and target |
|---|---|---|
| A written body's `**Covers:**` line names AC1 and AC2 | `the-written-parent-covers-reading-and-creating` | `tool_used` Write, `min: 1` |
| A `covers_gate.py` call's `--covers` list names AC1 and AC2 | `the-covers-gate-is-given-reading-and-creating` | `tool_used` Bash, `min: 1` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

### Each grader against each outcome

| Outcome | Covers line | Gate list | Final line | Parent created | Run |
|---|---|---|---|---|---|
| Current prose: AC1 and AC2 | pass | pass | pass | pass | passes |
| Rule removed: AC1 only, unknown named | **fail** | **fail** | pass | pass | fails |
| Read-only slice carrying an enabler justification (allowed by the rule, not the deciding branch) | **fail** (no Covers line) | **fail** (no gate call) | pass | pass | fails: reported, not graded as pass |
| No slice created | fail | fail | fail | fail | fails |

Checked offline against hand-built JSON-escaped inputs: a body with `**Covers:** AC1, AC2` matches; `**Covers:** AC1` does not, nor does a body that names AC2 only outside the Covers line; `covers_gate.py --covers "AC1, AC2"` matches while `"AC1"` and `"AC2"` alone do not.

## Limitations

- The enabler branch (read-only first slice with a written justification) is permitted by the prose and would grade red. The fixture makes it unavailable by giving AC1 value on its own (the legacy notes), but a run that writes an enabler anyway is a finding, not a defect of the grader.
- No grader reads why the slice covers AC2: the covers graders show the answer, and the first-slice-rule-removed arm shows the rule is what produced it.
- The phase section's sentence calling a zero-state-only list a plumbing-only slice that is chosen only as a declared enabler also bears on read-only first slices; it does not apply to this fixture (AC1 reads real notes) and the probe leaves it in place.
- One fixture, one phrasing, one model tier.
