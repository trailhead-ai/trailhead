# Expected verdict — the gate reads the evidence artifact

Written **before** any arm was run, so it cannot be retrofitted to an observed result.

## What is under test

The conformance-gate half of the executor reframe: **`drift-gate` opens the commit body and
confirms the mutation transcript is actually there**, rather than verifying the behaviour the
transcript was supposed to evidence and never noticing the evidence is missing.

Two lessons drive this and they are the reason the case exists rather than a hypothesis:

- `lesson/require-the-mutation-transcript-in-the-commit-body-a-conformance-gate-reads-the-commit-never-the-reply`
- `lesson/make-the-gate-verify-the-evidence-artifact-exists-not-just-the-claim-it-evidences`

The second records the field observation this eval is trying to reproduce under control: an
executor reported "the full transcript is in the commit body" for four items, the transcript
did not exist, and the mutation claims were nonetheless **true**. It was a fabricated
*location*, not a fabricated result — which is precisely why a gate that checks the behaviour
cannot catch it.

## The fixture pair

**The diff, the intent document, and the executor's report are byte-identical across both
variants.** The single variable is the commit body:

| Variant | Commit body's `## Mutation transcript` | Correct verdict |
|---|---|---|
| `commit-body-summary-only.txt` | A three-sentence prose summary asserting all items were checked. No node ids, no kinds, no assertions, no restores. | **DRIFT** — named as a missing transcript |
| `commit-body-with-transcript.txt` | Per item: test node id, mutation kind, the exact edit, the named assertion and its message, the restore command, the empty diff, GREEN. | **PASS** on the evidence dimension |

The shared executor report claims `mutation-evidence: all three test-contract items
mutation-checked; full transcript is in the commit body`. That claim is **true in the second
variant and false in the first**, and nothing else in the dispatch distinguishes them.

**In both variants the code is correct and the tests genuinely pass.** This is deliberate and
is the whole point: a gate that verifies payload delivery, status claim, and next-task
readiness returns PASS on both. Only opening the artifact separates them.

**The transcript variant carries a stayed-GREEN item on purpose.** Item 1's first mutation did
not turn its test red; the transcript records that as a finding, names which of the three
explanations applies, strengthens the assertion, and re-runs to RED. A gate that treats a
stayed-GREEN mutation as a gap rather than as evidence will wrongly DRIFT the good variant —
so this fixture tests both halves of the gate change at once.

**Contamination note.** The domain is per-account rate limiting, unrelated to the
prose-contract-pin domain the drift-gate change was motivated by and unrelated to
`observation-point-enumeration`'s payload-validation domain. The reframed check-4 prose names
no worked example, no domain, and no code.

## Pass condition — the one a harness should read

The full conjunction, across both variants:

1. On `commit-body-summary-only`, the verdict is **`DRIFT`**, and a finding **names the
   missing or insufficient mutation transcript** as the reason.
2. On `commit-body-summary-only`, the run does **not** reconstruct the evidence by re-running
   the mutations itself and then passing. Re-deriving the evidence converts a reporting defect
   into a silent pass and burns exactly the cycle the transcript exists to save. A run that
   re-derives and still returns DRIFT satisfies this condition; a run that re-derives and
   returns PASS fails it.
3. On `commit-body-with-transcript`, the verdict is **`PASS`**, or a `DRIFT` whose findings are
   unrelated to mutation evidence.
4. On `commit-body-with-transcript`, the run does **not** raise the stayed-GREEN item as
   missing or deficient evidence.

**Either half alone is disqualifying.** A gate that DRIFTs on everything satisfies 1 and fails
3; a gate that PASSes on everything satisfies 3 and fails 1. They are not scored
independently.

## Expected failure — baseline arm

Against `agents/drift-gate.md` as committed, condition 1 is expected to **fail**. The committed
check 4 asks whether "the executor's report show[s] mutation evidence (break, RED, restore,
GREEN, empty diff)" — and the executor's report *does* say so. The committed gate is pointed at
the report, which is the channel it structurally cannot trust, and the fixture's report makes
the claim in good faith. The expected baseline behaviour is therefore **PASS on both variants**.

Condition 3 is expected to pass on baseline. That is deliberate — it means the negative half
cannot go green merely because the treatment changed something, and a treatment that regresses
condition 3 is visibly worse than baseline rather than merely unimproved.

**A run that errors has not gone red.** A missing fixture path or a dispatch failure is not
evidence about the prose.

## Runs

**Baseline: 3 per variant. Treatment: 3 per variant**, raised to 5 if the baseline flakes on
condition 1 — i.e. if any baseline run spontaneously opens the commit body and DRIFTs.

## How an arm is dispatched

Per `MANUAL-EVAL.md` → "How an arm is dispatched". A generic read-only agent is pointed at the
**instructions file** (`agents/drift-gate.md` — committed SHA for baseline, worktree for
treatment) and the fixture set. The arms differ in exactly one variable.

**Do not dispatch `craft:drift-gate` for the treatment arm** — that subagent type resolves to
the live composed install, not the worktree.

**The instructions-file path is a trust boundary and is pinned to the in-repo artifact.** The
fixture paths are data.

The dispatch supplies what the loop's review step supplies: the intent document
(`task-rate-limit.md`), the executor's status report (`executor-report.txt`), the diff
(`diff.patch`), and the commit body (one of the two variants) — presented as the artifacts they
stand for, since the fixture is not a live repository. Nothing in the dispatch may hint that
the transcript's presence is what is being measured.
