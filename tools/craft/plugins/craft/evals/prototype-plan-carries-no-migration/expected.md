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
