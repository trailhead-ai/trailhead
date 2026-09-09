# Expected verdict — prototype plan carries no migration

Written **before** any arm is run, so it cannot be retrofitted to an observed result. The
commit that adds this file also adds the arms and fixtures, and is committed before any arm
has run — the ordering rests on the authoring record, exactly as the sibling case
(`waived-concern-stands-down`) states of its own `expected.md`.

## What is under test

`scripts/migration_bar.py` already renders a directive block naming whether migration and
backfill tasks are suppressed for a plan built against a given target repository's resolved
maturity level. Whether planning's step 7 ("Define Tasks"), reading that block, actually
changes what tasks a planning session decomposes — rather than merely printing the block and
decomposing a migration task anyway — is prompt behaviour, and no contract test can show it:
producing a plan body needs a planning session, not an assertion. This case measures exactly
that, on three fixtures that hold the feature constant and vary only the maturity stamp and
(on the negative fixture) one acceptance criterion.

## The arms

Two prose files under `arms/`, differing in the migration-bar-reading paragraphs step 2 of
this plan's parent task added to `skills/plan/SKILL.md`'s step 7:

| Arm | Instructions path | The rule under test |
|---|---|---|
| **baseline** | `arms/baseline.md` | step 7 as it stood before this run's parent plan touched it (`afd96268`) |
| **treatment** | `arms/treatment.md` | step 7 at the revision this task commits (`HEAD`) |

**The arms differ in the migration-bar addition, verified by diff rather than asserted.**
`diff` between the two files shows exactly: the reproduced step 7 section gaining the five
paragraphs that read the renderer's block and route on it, and the two framing lines naming
which revision each arm reproduces. No other content changes. Both arms carry an identical
wrapper explaining that the renderer has already been run against the fixture and its output
is embedded — so neither arm needs to invoke a script itself — and an identical footer asking
for a task list plus a `Given Axioms` section. Both arms are dispatched to a generic
read-only agent, pointed at the arm's prose (its operating instructions) and a fixture (the
material), per `MANUAL-EVAL.md`.

## The fixtures

Three fixtures under `fixtures/`, sharing a byte-identical feature (replace a widget's
`category` field with a `tags` list) and byte-identical `AC1`/`AC2`, differing only in `AC3`
and the `## Maturity` stamp — which changes what `scripts/migration_bar.py` renders into each
fixture's own "Migration bar (rendered)" section.

| Fixture | Maturity stamp | AC3 | Rendered block |
|---|---|---|---|
| `positive.md` | `trailhead: prototype` | Explicitly does not require preserving existing state | `— suppressed: migration and backfill` |
| `negative.md` | `trailhead: prototype` | Explicitly **requires** preserving existing state (the carve-out) | `— suppressed: migration and backfill` (identical block; the carve-out is a planner judgment over the criteria, not something the renderer itself detects) |
| `control.md` | `trailhead: production` | Same as `positive.md` | `— not-suppressed: migration and backfill` |

**Each fixture's embedded block is the renderer's real output, verified by direct run.**
`python3 plugins/craft/scripts/migration_bar.py --target-repo trailhead` was run against
each fixture's own `## Maturity` section content before this file was written, and its
stdout was embedded verbatim. Without this, the case could pass against hand-authored
suppression wording the renderer never emits.

**`negative.md` is the load-bearing fixture.** Without it, an arm could score well on
`positive.md` alone by learning "prototype means drop the migration task unconditionally"
rather than by actually applying the carve-out — the direction that would silently drop a
migration a production consumer needed. `negative.md` proves the carve-out survives.

**Containment.** No fixture or arm contains a `[[wikilink]]` or any other cross-reference
resolving to a real vault record, and neither arm's prose nor any fixture names or reaches
this file. Confirmed by grep before any run.

## Pass conditions — per fixture

A run **fires** (routes correctly) when its whole conjunction holds. Partial credit is not
scored.

**`positive.md`** — all three must hold:

1. The presented task list carries **no** task shaped as migration or backfill work (no
   task whose delivery is "backfill/migrate/populate existing widgets' tags from their
   category").
2. The run states, in the task list or its `Given Axioms` section, that migration/backfill
   was suppressed — naming the resolved level (`prototype`), the basis (`stamp`), and the
   target repository (`trailhead`).
3. The presented task list still carries the non-migration tasks the feature needs (at
   minimum, something covering AC1's write path and AC2's list-view grouping) — a run that
   goes silent on the whole feature has not shown suppression, it has shown non-performance.

**`negative.md`** — both must hold:

1. The presented task list **carries** a task shaped as migration or backfill work (backfill
   every existing widget's `category` into its initial `tags` entry), decomposed normally —
   the suppression must not silently drop AC3's requirement.
2. The run **names AC3** (or paraphrases its preserve-existing-state requirement) as the
   reason the migration task survived despite the suppressed block — a run that keeps the
   task without saying why has not shown the carve-out was applied, only that it happened to
   get the right answer.

**`control.md`** — both must hold:

1. The presented task list carries a migration/backfill task, decomposed normally.
2. The run does not state that anything was suppressed (there is nothing to suppress at
   `production`).

## Run counts and the arm-level reading

Three runs per fixture per arm — 9 runs per arm, 18 total — matching the run count the prior
craft eval cases use, for the same reason: a single run per cell cannot separate the variable
from run-to-run noise. If a fixture's result does not separate the arms within these 9+9
runs, that is recorded as a collapsed differential rather than re-run with a re-tuned
fixture, per `35d0616b test(craft): record both eval results, including a collapsed
differential`.

Each run is dispatched to a generic read-only agent, pointed at exactly two paths — the
arm's prose as its operating instructions, and one fixture as the material. Runs are
independent; no run sees another's output. `craft:planner` or any `craft:` subagent is never
dispatched for the treatment arm, since that subagent type resolves to the live composed
install rather than this worktree and would silently re-run unedited prose.

**A run that errors has not produced a result** and is re-run rather than scored.

## Pre-registered expectation, and what falsifies it in either direction

Baseline step 7 never mentions the renderer or the maturity stamp at all, so it is expected
to decompose a migration/backfill task on **every** fixture, including `positive.md` where
none is needed — the exact cost the parent spec names as its first-named problem (plans that
carry tasks nobody needed). Treatment is expected to suppress on `positive.md`, keep and name
the carve-out on `negative.md`, and behave identically to baseline on `control.md`.

Registered thresholds, decided now:

- **`positive.md` — the rule is upheld** if baseline fires condition 1 (no migration task) on
  **≤1/3** runs and treatment fires the full conjunction on **≥8/9**... adjusted for this
  case's 3-per-fixture-per-arm count: baseline **≤1/3** and treatment **≥3/3** (allowing one
  treatment miss would still be a clear separation against a baseline that essentially never
  suppresses unprompted).
- **`negative.md` — the carve-out is upheld** if treatment fires the full conjunction
  (migration task present **and** AC3 named) on **≥2/3** runs. This fixture has no baseline
  comparison to make — baseline is expected to keep the migration task on every fixture
  regardless, so `negative.md`'s baseline result is recorded for completeness but is not part
  of the pass/fail bar.
- **`control.md` — the sanity control holds** if **both** arms keep the migration task on
  **3/3** runs each. If either arm drops it, the instrument itself is broken and no other
  fixture's result can be trusted until that is fixed.
- **Falsified on `positive.md`** if both arms suppress on **≥2/3** runs each (documenting the
  rule made no measurable difference).
- **Indeterminate** if neither band above is reached on the fixture in question.
- **A collapsed differential is a result, not a failure to re-run** — recorded verbatim per
  the precedent above, with a plain statement of which fixtures separated and which did not.

## What each outcome means downstream

- **Upheld on `positive.md` and `negative.md`, control holds** — the durable trace and the
  suppression itself are both load-bearing prose changes, and AC10's behavioural half (which
  no contract test can pin) is closed in observed behaviour.
- **Falsified on `positive.md`** — the renderer's own block text is legible enough that a
  bare step 7 with no migration-bar awareness already suppresses unprompted; worth recording,
  not a reason to revert the change, since the explicit routing is still what turns the
  behaviour into a stated contract rather than an emergent property of one prose reading.
- **`negative.md` below its bar** — the carve-out does not reliably survive; this is the
  direction with the worse cost (a real migration silently dropped), so a below-bar result
  here is escalated rather than filed as a nice-to-have follow-up.
- **Collapsed differential** — recorded plainly, per fixture, with no re-tuning of the
  fixture to manufacture separation.

---

## Result — 2026-09-09

All 18 runs (3 per fixture per arm) produced a result; none errored.

| Fixture | Baseline arm (full conjunction) | Treatment arm (full conjunction) |
|---|---|---|
| `positive.md` | **0/3** — condition 1 (no migration task) held 3/3, but condition 2 (naming level, basis, *and* target-repo) held 0/3 | **3/3** |
| `negative.md` | **3/3** | **3/3** |
| `control.md` | **3/3** | **3/3** |

**`positive.md` — corrected, 2026-09-09: scored against the registered rule, not the full
conjunction.** The registered bar names **condition 1 alone** (no migration/backfill task) as
the suppression-firing metric: upheld if baseline fires it on ≤1/3 runs and treatment fires
the full conjunction on ≥3/3; **falsified if both arms fire condition 1 on ≥2/3 runs each**.
Measured: baseline fired condition 1 on **3/3** runs, not ≤1/3, and treatment's full-conjunction
3/3 necessarily includes condition 1, so both arms suppress condition 1 on ≥2/3 runs each. That
is the registered falsification condition. The table above's earlier verdict instead scored the
full three-condition conjunction (0/3 for baseline, since baseline never recorded the durable
trace) as if it were the pre-registered metric — substituting a metric the pre-registration did
not name after the runs had already produced a result, and reading a null result on the
registered suppression claim as a pass. That was wrong and is corrected here rather than left
standing:

**The suppression claim — FALSIFIED.** Every baseline run independently declined to add a
migration/backfill task, reading AC3's own "no automated preservation ... required" and the
fixture's embedded "Migration bar (rendered)" section as informative even though baseline's own
instructions never mention the renderer — so the block's own legible text, not the routing this
task adds, is what suppressed the task on this fixture. Registering the bar against condition 1
alone (rather than the full conjunction) exists exactly to catch this: a baseline that reaches
the right task-decomposition answer by reading the fixture's embedded block does not show the
prose change did anything.

**The durable-trace claim — UPHELD.** This is a separate, independently observed dimension, not
a substitute for the falsified one, and it carries no pre-registered numbered bar of its own —
the registered thresholds cover condition 1's firing rate only, so "upheld" here is a plain
description of a clean, measured separation rather than a claim scored against a threshold. No
baseline run recorded the durable trace this task's contract requires: one run said nothing about
suppression, one named only "the migration bar's suppression" with no specifics, and the closest
run named the level (`prototype`) and target repo (`trailhead`) but never the basis (`stamp`) or
the literal word "suppressed" tied to those fields. Treatment named all four — target-repo,
level, basis, and the suppression — in a dedicated `Given Axioms` section on 3/3 runs.

**Read together: two distinct outcomes, not one blended verdict.** The change did not prove out
on whether a migration task gets added on this fixture (falsified — the fixture's own embedded
block already carries enough signal for a migration-bar-unaware baseline to suppress) — but it
did prove out on whether the suppression decision leaves a durable, checkable trace an operator
or a later slice-loop pass can read back (upheld, cleanly, 0/3 vs 3/3). Finding 2 below identifies
why the suppression half could not be measured cleanly on this fixture — the same embedded block
that lets treatment route correctly also hands baseline the answer — and registers a second
measurement round that removes it from baseline's material.

**`negative.md` — carve-out UPHELD against its own bar (treatment ≥2/3): 3/3.** But this
fixture's differential **collapsed rather than separated**: baseline also fired the full
conjunction 3/3, keeping the migration/backfill task and naming AC3 as the reason in every run
("per the rendered migration bar, AC3's requirement ... is the carve-out that reopens migration/
backfill work"; "this task exists specifically because AC3 requires preserving existing state").
This is recorded plainly rather than re-run or re-tuned, per
`35d0616b test(craft): record both eval results, including a collapsed differential`. It is not a
surprising result on reflection: AC3 on this fixture states the preservation requirement in plain
prose ("no widget may end up with an empty `tags` list"), which any competent task-decomposition
reads and acts on with no migration-bar concept at all — the same effect the pre-registration
already anticipated when it declined to make `negative.md`'s comparison part of the pass/fail bar.
**What this fixture does establish, and what it does not:** it establishes that the carve-out,
once suppression is being applied, reliably survives under the treatment prose (3/3, matching the
bar) — it does not establish that the treatment's explicit carve-out routing is what produces
that survival on this material, since a bare step 7 with no migration-bar awareness reaches the
same answer for an unrelated reason (plain-English AC3 reading). A fixture isolating the carve-out
from AC3's own legibility would need AC3 to require preservation less explicitly.

**`control.md` — sanity control holds, comparison not void.** Both arms kept the migration/
backfill task 3/3 and neither claimed a suppression that does not exist at `production`. One
baseline run explicitly noted the tension between AC3's "disposable-state prototype" framing and
the `production` stamp, and correctly deferred to the stamp/rendered bar rather than AC3's own
language — which is the behavior AC10 requires regardless of arm.

### What this settles

Planning's step 7 change is behaviourally load-bearing on the one half no contract test can pin:
the durable trace. `positive.md` shows the treatment arm reliably records the resolved
target-repo/level/basis/suppression where the baseline arm — even when it reaches the same
task-decomposition answer — does not. `negative.md` shows the carve-out survives reliably under
treatment, though the comparison against baseline is not this fixture's evidence for *why* — that
credit is shared with AC3's own plain-language legibility on this fixture. `control.md` confirms
neither arm regresses at `production`. Read together: AC10's behavioural half is closed for the
durable-trace claim, and closed-but-not-isolated for the carve-out-survives claim. It is **not**
closed for the suppression claim itself — `positive.md`'s corrected scoring above shows the
registered suppression bar was falsified on this instrument, because the fixture's own embedded
block hands baseline enough signal to suppress unprompted. Round Two, registered below, exists
to answer the suppression question on an instrument that does not make that mistake.

### The limit of this result, stated plainly

Three synthetic fixtures, one feature shape, one model tier, 18 runs total. The fixtures embed the
renderer's real output as material available to both arms — necessary so neither arm has to
invoke a script itself — but this means a capable reader can reach the "right" migration-task
decision from the embedded block's own text even under baseline's unmodified prose, as the
`waived-concern-stands-down` case's own re-measurement found for its calibration block. This
result is strongest on the durable-trace claim, where the fixture's material cannot substitute for
an instruction the reader was never given, and weakest on `negative.md`'s carve-out-survives
claim, where it cannot yet.

---

## Round Two — pre-registration, written and committed before any Round Two arm is run

Round One's own limits section named the defect this round exists to close: every fixture
embeds `migration_bar.py`'s real output as material available to **both** arms, so a capable
reader can reach the "right" migration-task decision from the embedded block's own text even
under baseline's unmodified prose — and `positive.md`'s corrected scoring above shows this is
not hypothetical: baseline suppressed condition 1 on 3/3 runs while its own instructions never
mention the renderer at all. That is not evidence the prose change does nothing; it is evidence
the fixture handed baseline the answer through a channel neither arm's instructions describe as
data. A real baseline planning session never runs the renderer and never sees a block — so
Round Two removes the block from the baseline arm's material entirely, and fixes a second,
independent defect in `control.md`.

### What changed in the instrument, and what did not

- **The rendered block now reaches only the treatment arm's material.** Three new fixtures —
  `fixtures/round2-baseline-positive.md`, `fixtures/round2-baseline-negative.md`,
  `fixtures/round2-baseline-control.md` — carry the same spec, the same Acceptance Criteria, and
  the same `## Maturity` stamp as `positive.md` / `negative.md` / `control.md` respectively,
  verified byte-identical over that shared content by diff, but omit the "## Migration bar
  (rendered)" section outright. The treatment arm keeps reading the original three fixtures
  unchanged — they still carry the block, which treatment's own instructions still direct it to
  read.
- **`arms/baseline.md` and `arms/treatment.md`'s shared wrapper paragraph was reworded to be
  conditional** ("If the fixture also carries a … section … If the fixture carries no such
  section, proceed using only the spec and its acceptance criteria"), applied identically to
  both arm files so the two still differ only in the step 7 body — verified by `diff
  arms/baseline.md arms/treatment.md`, pasted in the executor's report. This is the
  "mechanically necessary framing" the diff check already tolerated in Round One, extended to
  cover a fixture that may or may not carry the section, rather than a per-arm asymmetry.
- **`control.md`'s AC3 was reworded** to drop "This is a disposable-state prototype with no
  deployed consumers", which contradicted the fixture's own `production` stamp, while keeping
  the same waived-preservation requirement the sentence also stated. `round2-baseline-control.md`
  carries the corrected wording too. `positive.md` and `negative.md` needed no such fix — their
  AC3 prototype framing agrees with their `prototype` stamp.
- **Both arms' step 7 body were refreshed** to the current revisions: `arms/baseline.md`
  reproduces `skills/plan/SKILL.md`'s step 7 at `afd96268` (unchanged from Round One — verified
  identical by diff); `arms/treatment.md` reproduces step 7 at this task's `HEAD`, picking up the
  durable-trace clause's current wording ("write the decision into the plan's `Given Axioms` in
  step 8, **as an axiom citing the spec's own `## Maturity` section**"), which a sibling pass
  changed after Round One ran.
- **Round One's fixtures, arms, results, and scoring are left exactly as they now stand** (Round
  One's own scoring corrected against its registered rule, above) — nothing in this section
  edits them further.

### Dispatch for Round Two

Six cells, three runs each (18 runs total), matching Round One's run count:

| Cell | Instructions path | Material path |
|---|---|---|
| baseline × positive | `arms/baseline.md` | `fixtures/round2-baseline-positive.md` |
| baseline × negative | `arms/baseline.md` | `fixtures/round2-baseline-negative.md` |
| baseline × control | `arms/baseline.md` | `fixtures/round2-baseline-control.md` |
| treatment × positive | `arms/treatment.md` | `fixtures/positive.md` |
| treatment × negative | `arms/treatment.md` | `fixtures/negative.md` |
| treatment × control | `arms/treatment.md` | `fixtures/control.md` |

Each run is dispatched to a generic read-only agent, pointed at exactly the two paths in its
row. Runs are independent; no run sees another's output. `craft:planner` or any `craft:`
subagent is never dispatched for either cell, for the same reason as Round One.

### Pass conditions, scored as two separate dimensions this time

Round One's authoring error was scoring the full three-condition conjunction against a bar the
pre-registration wrote for condition 1 alone. Round Two registers both dimensions explicitly and
separately, so no future reading can re-conflate them:

- **Suppression (condition 1 alone) — the pre-registered pass/fail bar.** On `positive.md`,
  upheld if baseline fires condition 1 (no migration task) on **≤1/3** runs and treatment fires
  it on **≥3/3**; falsified if **both** arms fire condition 1 on **≥2/3** runs each; indeterminate
  otherwise.
- **Durable trace (condition 2 alone) — reported, not scored against a numbered bar**, exactly as
  in the corrected Round One reading: whichever way suppression falls, record separately whether
  each arm's runs name the resolved target-repo, level, and basis (and the literal word
  "suppressed") together. This is descriptive, per the corrected Round One reading, not a second
  pass/fail bar — Round One already showed this dimension can be cleanly measured without one.
- **`negative.md` carve-out — same bar as Round One.** Upheld if treatment fires the full
  conjunction (migration task present **and** AC3 named) on **≥2/3** runs. No baseline pass/fail
  bar — Round One's own reading already explains why (AC3's plain-English legibility on this
  fixture, independent of any migration-bar routing).
- **`control.md` sanity control — same bar as Round One.** Holds if **both** arms keep the
  migration task on **3/3** runs each.
- **A collapsed differential is a result, not a failure to re-run** — recorded verbatim, per
  fixture, exactly as Round One's `negative.md` result was, and per
  `35d0616b test(craft): record both eval results, including a collapsed differential`. This
  round is not re-tuned a third time if it also fails to separate; that would itself be a finding
  about AC10, reported plainly.

### What this round settles either way

An upheld suppression bar on `positive.md` closes the one claim Round One's instrument could not
measure: that the treatment prose's explicit migration-bar routing, not the fixture's own legible
block text, is what suppresses the task. A falsified or indeterminate result here — with the
durable trace still separating as it did in Round One — means AC10's behavioural closure rests
entirely on the durable-trace claim, and the suppression claim itself remains open pending a
harder instrument than this one.
