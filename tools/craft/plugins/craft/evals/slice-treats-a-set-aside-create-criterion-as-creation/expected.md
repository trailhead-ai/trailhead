# slice-treats-a-set-aside-create-criterion-as-creation

## Under test

Given `/craft:slice spec/wallet --unattended` on a `ready` spec with one surface (the `wallet` command group) whose only create criterion (AC3 `wallet add`) needs a real merchant account with the card network that the company does not have and no stand-in replaces, plus two read criteria (AC1 `wallet list`, AC2 `wallet show <id>`) that read only what `wallet add` saves, the chooser does not write the reads as an ordinary all-reads slice. A create criterion set aside for something outside still counts as creation for the first-slice and all-reads rules (spec follow-up F7, the operator's ruling of 2026-11-20): the surface is not all-reads, and its reads ship only as an enabler whose written justification names the set-aside create criterion, or the chooser takes another surface. The fixture has one surface, so the enabler is the available branch. The prose under test is `skills/_shared/slice.md` (fingerprint `1cdf4bf2e21e2d9308ac80dcb35f936fab641dd9`) and `skills/slice/SKILL.md` (fingerprint `516322e39e3b008a1a1f21f28c9902dbb881949a`) as they stand when the baseline runs; this dispatch edits neither, the prose half does.

## Why the two arms differ

Unedited prose: step 7's set-aside rule sets AC3 aside, which leaves the candidate set rather than being cut from the surface. The surface's remaining criteria are AC1 and AC2, both reads, so the all-reads paragraph applies: an ordinary slice with a value claim, covering every core read, never an enabler. The written body carries a `**Covers:**` line over AC1 and AC2 and the covers gate is called. **Expected red.**

Edited prose: AC3 still counts as creation, so the surface is not all-reads; the first-slice rule keeps a read-only first slice to the enabler path. The written body carries a `**Value claim:**` justification naming AC3 and no `**Covers:**` line (an enabler certifies nothing), and the covers gate is never called. **Expected green.**

## Fixture

`fixtures/scaffold.sh` runs the unchanged `seed.sh`, makes `./lore` self-contained, writes the `bin/lore` shim and a `CLAUDE.md` with `## Project Maturity` / `early`, then seeds the spec `wallet` (`ready`): AC1 prints the last four digits and expiry of every saved card, AC2 prints one saved card's details, AC3 saves a new card by registering it with the live tokenization service of the card network under the company merchant account. A `## Notes` section says the reads read only what `wallet add` saves, that the company has no merchant account, that the network opens one after a weeks-long review, and that it has no test version. It does not say "set aside", name the writer, or say how to slice. No ledger, no slice record. The scripts run natively from `${CLAUDE_PLUGIN_ROOT}`; `lore` is reached through the prompt's `PATH` prefix.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json` (it already holds `Bash(*/scripts/set_aside.py *)`). No grant was added.
- Limits: `allowed_tools: [Skill, Read, Write, Glob, Grep]`, `max_turns: 60`, `timeout_seconds: 500` (inside the 540 s merge-only budget).
- Baseline (unedited prose) expected **red**, treatment (edited prose) expected **green**, bar 3 of 3 runs each. A run scores the fraction of graders that pass and passes only at 1.0. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`, measuring `skills/_shared/slice.md` and `skills/slice/SKILL.md`.

## Pass condition

Every clause holds. Observation point: the written parent's body (a `Write` whose input holds a `**Value claim:**` section) and the covers-gate call; there is no judge.

| Clause | Grader | Type and target |
|---|---|---|
| A written body has a `**Value claim:**` section and no `**Covers:**` line | `the-written-parent-is-an-enabler-with-no-covers-line` | `tool_used` Write, `min: 1` |
| That body names AC3 after the `**Value claim:**` label and has no `**Covers:**` line | `the-written-justification-names-the-set-aside-create-criterion` | `tool_used` Write, `min: 1` |
| No `covers_gate.py` call lists AC1 or AC2 | `the-covers-gate-is-never-given-the-reads` | `tool_used` Bash, `min: 0`, `max: 0` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

### Each grader against each outcome

| Outcome | Enabler shape | Names AC3 | No gate on reads | Final line | Parent created | Run |
|---|---|---|---|---|---|---|
| Unedited prose: ordinary slice over AC1 and AC2 | **fail** | **fail** | **fail** (gate called) | pass | pass | fails |
| Takes AC1, AC2 and AC3 together | **fail** | **fail** | **fail** | pass | pass | fails |
| Edited prose: enabler justification naming AC3 | pass | pass | pass | pass | pass | passes |
| Enabler that never names the set-aside criterion | pass | **fail** | pass | pass | pass | fails |
| Stops with no slice (early stop or waiting) | fail | fail | pass | fail | fail | fails: reported as a finding for the prose half |

Checked offline against hand-built JSON-escaped inputs: an ordinary body (`**Covers:** AC1, AC2`) fails the first two; an enabler body naming AC3 passes both; an enabler body that never says AC3 fails the second; a set-aside entries `Write` (no `**Value claim:**`) matches neither Write grader; `covers_gate.py --covers "AC1, AC2"`, `"AC1"` and `"AC2"` each match the third grader's pattern (a violation under `max: 0`) while `"AC3"` alone does not.

## Limitations

- The fixture has one surface, so the "chooser takes another surface" branch cannot be produced; that branch is not graded. A run that stops with no slice at all (nothing left to enable the next slice) is red and is reported as a finding for the prose half, not as a grader defect.
- The written body's shape is the observation point; the stated claim is not judged (a judge of the statement over the trace window was fragile in the earlier slice).
- The set-aside write itself is pinned by `slice-sets-aside-what-needs-an-outside-account`; this case does not grade it.
- One fixture, one phrasing, one model tier.
