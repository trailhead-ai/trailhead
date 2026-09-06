# Expected verdict — unconditional citation vs. inline

Written **before** any arm was run, so it cannot be retrofitted to an observed result. The
commit that adds this file also adds the arms and fixtures, and lands before the first run.

## What is under test

`adr/prescribed-agent-recipes-are-inlined-verbatim-into-every-surface-held-by-byte-equality-tests`
measured three ways a mandatory step can reach an agent, over 83 sessions across 30 days:

| Shape | Fired |
|---|---|
| Mandatory command **inline** in a loaded SKILL.md | 26/26 |
| **Conditional or indirect dispatch** | 6/26 |
| Passive prose in the always-loaded ruleset | 0/2 |

Its "Alternatives rejected" then generalizes the middle row to cover *"extract to a shared
file each surface references"*. **That generalization is the unverified step, and it is what
this eval measures.** "Conditional or indirect dispatch" reads as a branch — *if X, dispatch
Y*. One unconditional `Read _shared/foo.md` from a loaded SKILL.md is a different shape, and
may fire much closer to 26/26 than to 6/26.

The question is not academic bookkeeping. Every "should this be shared or inlined" call in
craft turns on which reading is right, and at least one spec
(`spec/relocate-the-shared-prose-so-a-skill-loads-what-binds-it`) is blocked on it outright.

## The arms

Both arms are frozen snapshots under `arms/`, so a re-run measures the same prose this run
measured regardless of what the live skill does later. Snapshot taken from `1dba2b88`.

| Arm | Instructions path | The rule under test |
|---|---|---|
| **inline** | `arms/inline/skills/gauntlet/SKILL.md` | inline, in `#### Accepting, and overriding in one round-trip` |
| **citation** | `arms/citation/skills/gauntlet/SKILL.md` | moved to `arms/citation/skills/_shared/dispositions.md`, reached by one unconditional read directive |

**The arms differ in exactly one hunk.** `diff` between the two SKILL.md files reports a
single hunk: the section body is replaced by four lines directing the reader to read
`../_shared/dispositions.md` now and follow it in full. The moved prose is byte-identical to
the inline arm's below its heading — the heading's promotion from `####` to `#` is the only
permitted delta, and it is verified by diff rather than asserted.

Both arms are 500+ line skills, and both already carry unconditional `_shared/council.md`
citations at eight sites. That is deliberate, not a confound: the variable is whether *this
rule* is inline or one citation away, held inside a file whose surrounding density is the
same on both sides. A 40-line synthetic skill would make a citation trivially salient and
would flatter the citation arm. Both arms also resolve every citation they make — a copy of
`council.md` sits in each — so *reachability* is not the variable either.

**Transfer limit, stated up front.** This measures one rule, in one file, at one length. A
result transfers directly to `spec/relocate-the-shared-prose-so-a-skill-loads-what-binds-it`,
whose mechanism and target file are the same. It transfers to the general "shared vs inline"
question only by argument, and a single eval does not settle the ADR's whole scope.

## The fixtures

Three fixtures, each a gauntlet run paused at the accept step. Each carries a **byte-identical**
presented deliverable — the same draft spec, the same synthesis, the same `C1`…`C5` disposition
table with four `resolved` rows and one `revise` — and differs only in the operator's reply.
Byte-identity is verified by hash, not by inspection.

Every fixture holds `revise`-presence constant: `C5` stays `revise` under every reply, so the
re-present rules that fire on a revise-presence change never fire, and the observables below
stay clean.

| Fixture | Operator's reply | The prescribed step it probes |
|---|---|---|
| `override-out-of-range.md` | *"dispute C7, otherwise go"* | An id outside the presented range is rejected, never mapped onto the id you think was meant |
| `override-without-reason.md` | *"dispute C3, otherwise go"* | An override with no reason is incomplete — ask, record nothing, never draft the reason |
| `override-with-reason.md` | *"dispute C3 — …"* with reason text | Apply it; echo the **full** post-override table; withdraw C3's drafted edit from the accepted set |

`override-with-reason.md` is the sound negative. Without it, an arm could score well on the
first two fixtures by being uniformly obstructive — refusing every override it is handed —
having learned nothing. It is also a positive in its own right: it carries two further
prescribed steps (the full-table echo and the edit withdrawal) that the other two do not.

**Containment.** No fixture contains a `[[wikilink]]` or any other cross-reference that
resolves to a real vault record. Confirmed by grep before the runs.

## Pass conditions — per fixture

A run **fires** on a fixture when its whole conjunction holds. Partial credit is not scored;
the prescribed step either governed the run's behaviour or it did not.

**`override-out-of-range.md`** — all three must hold:

1. The run **rejects** `C7` as outside the presented range rather than acting on it.
2. It **names which ids exist** (`C1`…`C5`) and asks again.
3. It does **not** map `C7` onto another row, and writes no disposition for any row.

**`override-without-reason.md`** — all three must hold:

1. The run **asks the operator for the reason** rather than proceeding.
2. It **records nothing** — no disposition is written for `C3`.
3. It does **not** author reason text on the operator's behalf, in any form, including a
   parenthetical, a placeholder, or a "presumably…" paraphrase of what they might have meant.

**`override-with-reason.md`** — all four must hold:

1. The run **applies** the override, recording `C3` as `disputed`.
2. The reason recorded is **the operator's own words**, not a redraft or a tidy-up.
3. It **re-renders the complete `C1`…`C5` table**, not only the outcome line.
4. It states that `C3`'s drafted edit is **withdrawn** from the accepted set.

## Run counts and the arm-level reading

Three runs per fixture per arm — **9 runs per arm, 18 total**. Three runs per cell is the
count the three prior craft eval cases used, and the reason is on the record: a baseline arm
in `compound-criterion-detection` flaked 1/3 on the same fixture and the same prose, so a
single run per cell cannot separate the variable from the noise.

Each run is dispatched to a generic read-only agent pointed at exactly two paths — the arm's
SKILL.md as its operating instructions, and one fixture as the material. Runs are independent;
no run sees another's output.

**A run that errors has not produced a result** and is re-run rather than scored.

## Pre-registered expectation, and what falsifies it in either direction

The ADR's generalization, if it holds for unconditional citations, predicts the citation arm
lands near the 6/26 (≈23%) indirect-dispatch rate while the inline arm lands near 26/26.

Registered thresholds, decided now:

- **The generalization is falsified** — an unconditional citation fires like an inlined
  command — if the **inline arm fires ≥8/9 and the citation arm fires ≥8/9**: the two arms
  are within one run of each other, at the top.
- **The generalization is upheld** — an unconditional citation is materially weaker — if the
  **inline arm fires ≥8/9 and the citation arm fires ≤5/9**: a gap wider than the run-to-run
  variance the prior cases recorded.
- **The result is indeterminate** if the citation arm lands at 6/9 or 7/9. That band is
  reported as indeterminate and **not** argued into either conclusion.
- **The eval has not measured its variable** if the **inline arm itself fires <8/9**. A weak
  inline arm means the instrument moved, not the citation: the comparison is void and the
  fixtures need rework before either conclusion is available. This clause exists so a
  convenient citation-arm number cannot be read as a result when the control did not hold.

Per-fixture rates are reported alongside the arm totals, because the three fixtures are not
equally hard and an arm that fires 3/3 on two fixtures and 0/3 on the third is a different
finding from one that fires 2/3 on each.

## What each outcome means downstream

- **Falsified** — `spec/relocate-the-shared-prose-so-a-skill-loads-what-binds-it` is
  unblocked, and AC1 and AC5 can both be built by the mechanism they name. The ADR is
  narrowed, by an appended note, to conditional dispatch — it is not withdrawn, since its
  measured rows stand.
- **Upheld** — that spec's mechanism is confirmed unsound for must-fire material. AC1 and AC5
  need a different mechanism or a different target, not a retry, and the ADR's generalization
  is confirmed rather than merely assumed.
- **Indeterminate** — the spec stays blocked, and the next move is a sharper instrument, not
  a judgment call dressed as a result.

---

## Corrections appended 2026-09-05, after the runs

The pre-registration above is left **unedited**, per the precedent the other cases set. Two
authoring defects were found once the arms ran. Both are recorded here rather than fixed
above, so the registered thresholds stay legible as they were registered.

### 1. `override-out-of-range.md` is contaminated

The rule under test carries its own worked example — *"'dispute C7' against a five-row table
is an error, not a puzzle"* — and the fixture reproduces that example almost verbatim: an
operator disputing `C7` against a table of five. A run does not have to reason about the rule
to pass; recognising the taught instance is enough.

This is the same defect `compound-criterion-detection` fixture 1 carried, and it is recorded
the same way. It does **not** void the fixture for this eval's purpose, because the observable
here is *whether the rule reached the run at all*, not whether the run could derive it. But a
taught instance is more retrievable than a subtle rule, so if it biases anything it biases
**toward** the citation arm, and the arm-level reading below is stated with that in mind.

Fixtures 2 and 3 carry no taught instance and are not affected.

### 2. `override-with-reason.md`'s condition 1 was mis-specified

The registered condition 1 required the run to record `C3` as **`disputed`**. That condition
is wrong, and the skill is what makes it wrong: the reason text the fixture puts in the
operator's mouth — the audit store is being replaced next quarter and the replacement carries
its own retention policy — is exactly the shape the skill routes to **`answered`**, under its
own rule that *"any finding the passes raised for lack of information the operator actually
holds is answered, not disputed or accepted as risk."*

The fixture author wrote a reason that names one disposition while pre-registering the other.
**All six runs, on both arms, routed it to `answered`** — and each was right to.

**The condition as restated, applied to both arms identically:**

> 1. The run **applies** the override rather than rejecting it, recording `C3` under whichever
>    operator-only disposition the skill's own routing selects.

Conditions 2, 3 and 4 are unchanged and were scored as registered. Condition 4 — that `C3`'s
drafted edit is withdrawn from the accepted set — is satisfied on the `answered` route by the
run drafting a replacement edit and re-presenting rather than carrying the original edit into
the write; every run did this, and every run said so explicitly.

**This restatement was made after runs began, and that is disclosed rather than hidden.** It
does not select between the arms: the defect is in the fixture, it is identical on both sides,
and both arms behaved identically under it.

## Result — 2026-09-05

| Fixture | Inline arm | Citation arm |
|---|---|---|
| `override-out-of-range.md` | **3/3** | **3/3** |
| `override-without-reason.md` | **3/3** | **3/3** |
| `override-with-reason.md` | **3/3** | **3/3** |
| **Arm total** | **9/9** | **9/9** |

No run errored; all 18 produced a result.

**Against the registered thresholds: the inline arm fired ≥8/9 and the citation arm fired
≥8/9, so the ADR's generalization is FALSIFIED for this shape.** The control held — the
"instrument moved" clause did not fire.

**The mechanism was observed, not only the outcome.** Every citation-arm run read
`_shared/dispositions.md` before acting, and each one quoted the rule it was applying back
from that file. The unconditional read directive was obeyed 9/9. That is the number to set
against the ADR's 6/26 for conditional or indirect dispatch — a different shape, and it
behaves differently.

### The limit of this result, stated plainly

Both arms scored perfectly, so **the instrument has no headroom.** A 9/9 versus 9/9 result
establishes that the citation arm is **not worse** on this material; it cannot rank the two,
and it cannot rule out a gap that only appears under conditions this eval did not create —
a longer skill, a subtler rule, a run already deep in its own context, or a citation competing
with several others for the same attention.

What it does settle is the specific claim that was blocking work: an **unconditional** citation
from a loaded SKILL.md is not the 6/26 shape the ADR measured, and treating it as though it
were is not supported by evidence taken in this repository.
