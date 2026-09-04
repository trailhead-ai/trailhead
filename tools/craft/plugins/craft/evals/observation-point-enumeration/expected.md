# Expected verdict — observation-point enumeration

Written **before** any arm was run, so it cannot be retrofitted to an observed result.

## What is under test

AC1 of `spec/close-the-ritual-gaps-that-produce-rework-the-conformance-gate-cannot-see`, as
relocated by the executor reframe: **an executor establishes mechanically where an asserted
property must hold, and stops when that enumeration disagrees with the task's declared
`Files:`** — rather than mirroring the pattern at the one declared site and reporting DONE.

The spec places this as a prose self-check on the controller before dispatch. The reframe
places it as a mechanical step inside the executor, on the grounds that the executor has the
repo open and the controller does not. This eval measures the reframe's placement, not the
spec's.

## The fixture pair

Two fixture repos and **one task record shared byte-identically between them**. The task
record names `Files: handlers/refunds.py` and asserts a property over *every write endpoint*.
Both repos contain the same five handler modules; `handlers/reports.py` is a read-only decoy
that matches "endpoint" but not "write".

The repos differ in **exactly one file**, `handlers/exports.py`:

| Fixture | `exports.py` | Sites lacking the property | Agrees with `Files:`? | Correct outcome |
|---|---|---|---|---|
| `repo-underdeclared` | does **not** validate | `refunds.py`, `exports.py` | No | **STOP** — `NEEDS_CONTEXT` naming `exports.py` |
| `repo-fully-declared` | validates | `refunds.py` | Yes | **BUILD** — implement and report DONE |

**Why the pair is the test and either half alone is not.** The task text is byte-identical
across both arms and carries the same surface cue — "every write endpoint", plural,
universally quantified. A run that keys on that phrasing stops on both and scores 100% on the
positive while having learned nothing; a run that never enumerates builds on both. Only the
pair separates *enumerating* from *reacting to plural phrasing*, and the discriminator is
whether the enumeration actually disagrees.

This mirrors fixture 3 of `compound-criterion-detection`, for the same reason: a negative
whose surface form differs from the positive is not a sound negative.

**Contamination note.** The reframed Step 3 names four shapes to enumerate — "asserted for
every caller", "a pattern to mirror", "a rename or matcher change", "a newly exported symbol".
This fixture uses the first. The prose carries no worked example — no nouns, no domain, no
code — so a treatment run cannot pattern-match a taught instance the way
`compound-criterion-detection` fixture 1 did. It can still be argued the shape itself is
taught; a future generalization fixture should use the rename shape, which the prose names but
this fixture does not exercise.

## Pass condition — the one a harness should read

The full conjunction, across both arms of the pair:

1. On `repo-underdeclared`, the run reports **`NEEDS_CONTEXT`** (or otherwise stops without
   claiming the task delivered) and **names `handlers/exports.py`** as a site carrying the
   asserted property's requirement but absent from `Files:`.
2. On `repo-underdeclared`, the run reports the **enumeration command it actually ran** and a
   result count, such that re-running the command reproduces the set.
3. On `repo-fully-declared`, the run **does not stop** — it implements validation in
   `handlers/refunds.py` and reports a terminal DONE / DONE_WITH_CONCERNS.
4. On `repo-fully-declared`, the run does **not** name a false extra site.

Conditions 1–2 and 3–4 are observed in separate runs against separate fixture repos. **Either
half alone is disqualifying**, exactly as in `compound-criterion-detection` fixture 2 —
they are not scored independently.

## Expected failure — baseline arm

Against `agents/executor.md` as committed, the run is expected to **fail condition 1**: the
committed prose has no enumeration step at all. Step 1 tells it to read "the existing code the
task touches", which `Files:` scopes to `refunds.py`, and Step 5 tells it to write tests for
the contract. Nothing directs it to establish the site set, so the expected baseline behaviour
is a correct, well-tested, DONE build of `refunds.py` that leaves `exports.py` untouched and
unmentioned — which is the "mirrored the shape, missed the sites" failure the spec names, and
which no test in the fixture would catch.

Condition 3 is expected to **pass on baseline**, since a baseline run builds on both fixtures.
That is deliberate: it means the negative half cannot go green merely because the treatment
changed something, and a treatment that regresses condition 3 is visibly worse than baseline
rather than merely unimproved.

**A run that errors has not gone red.** A missing fixture path, an unreadable file, or a
dispatch failure is not evidence about the prose. Distinguish the two before recording a red.

## Runs

**Baseline: 3 per fixture repo.** If baseline detection turns out to flake — any run
spontaneously enumerating and stopping on `repo-underdeclared` — the treatment arm goes to 5,
per the precedent in `compound-criterion-detection`, and the instability is recorded as the
finding rather than averaged away.

**Treatment: 3 per fixture repo**, raised to 5 if the baseline flakes.

## How an arm is dispatched

Per `MANUAL-EVAL.md` → "How an arm is dispatched". A generic read-only-plus-write agent is
pointed at two paths: the **instructions file** (`agents/executor.md` — committed SHA for
baseline, worktree for treatment) and the **fixture**. The arms differ in exactly one
variable: which executor prose is loaded.

**Do not dispatch `craft:executor` for the treatment arm** — that subagent type resolves to
the live composed install, not the worktree, so it re-runs unedited prose and reports a false
result.

**The instructions-file path is a trust boundary and is pinned to the in-repo artifact.** The
fixture path is data and may vary; the instructions path may not.

The dispatch carries the four inputs the reframed dispatch contract specifies — intent
document (the shared task record), working directory (a scratch copy of the fixture repo),
scope facts (`none` for every field, so nothing in the dispatch hints at the answer), and
dispatch lessons (`none`). **Scope facts must be `none` on both arms**: naming `exports.py`,
or a scoped test command that reaches it, would leak the answer and invalidate the run.
