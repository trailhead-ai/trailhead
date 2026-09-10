---
name: consolidate
description: >
  Run the consolidation ritual over a labelled lesson corpus — walk it whole, cluster by the
  failure each lesson prevents, fold each duplicate cluster into one coherent lesson, repoint
  inbound links, and delete what was folded away. Predicts its own merge count before reading
  the corpus, and stops merging at the point where a cluster's members name genuinely distinct
  triggers.
  TRIGGER when: the user says "consolidate the lessons", "the lesson corpus has bloated",
  "dedupe the lessons", "run a consolidation pass", "these lessons keep getting re-learned", or
  invokes /craft:consolidate explicitly.
  DO NOT TRIGGER when: the user wants to capture a single new lesson (that is lore's `lesson`
  capture — deliberately cheap), wants to condense finished spec work into ADRs (that is
  distill, a different backward ritual over a different kind), or wants to reword or retitle
  one record (a plain `lore record update`).
---

# Consolidate

Lessons accrete and nothing removes them. Every run that learns something writes a record; no run
ever checks whether the thing was already written down. Meanwhile the retrieval that surfaces
lessons at dispatch time loads a fixed *number of records*, ordered by recency. **The corpus grows
without bound and the window does not**, so a lesson's odds of ever being read decay as the corpus
grows around it — and the only thing that raises them back is a smaller count.

That is not a filing problem. It is the mechanism by which the same lesson gets re-learned: writing
a lesson is cheap and feels productive, and nobody checks for an existing one because checking means
reading a corpus no one can hold.

Consolidation returns a corpus **smaller and sharper** without losing anything real. Smaller alone
is easy and worthless — see the stop condition, which is the part of this ritual that actually
decides its value.

## The unit of judgment is the prevention, not the title

Two lessons are duplicates when **a dispatch author who had applied one would also have been
protected from the other's failure.** That is the whole test. Ask it of every pair in a candidate
cluster, in that direction, out loud.

Title similarity is a **weak** signal and is used only to build candidate clusters cheaply. Two
records can share almost every word and carry different preventions; two records can share no words
and carry one. Never merge on titles.

Bodies are the evidence. Read both bodies before judging any pair.

## The stop condition

> **A merge that has to drop a concrete trigger in order to read coherently is not a merge. It is
> two lessons, and they stay two.**

Three lessons naming three distinct triggers for one broad family are **not** duplicates. Collapsing
them yields an umbrella lesson that names no concrete trigger — and a lesson that names no trigger
prevents nothing, because a reader cannot tell whether they are in it.

This is the failure mode to fear, ahead of leaving the corpus long. Over-merging destroys the
specificity that makes a lesson actionable, and it does so invisibly: the count goes down and the
ritual looks like it worked.

Applied as a check on every proposed merge:

- Name each input's trigger — the concrete, recognizable situation the reader is in.
- If the merged record can name **every** one of those triggers and still read as one lesson with
  one prevention, merge.
- If naming them all makes the record read as a list of unrelated situations, **do not merge**.
  Different triggers with one remedy are still different lessons.
- If the merged record could only stay coherent by generalizing the triggers away, **do not merge**.
  That is the umbrella.

A cluster that survives this check unmerged is a **successful** outcome of the pass, not a missed
one. Record it as examined.

## Predict before you read

**Before opening a single body, state a predicted merge count and write it down.** The prediction is
part of the work product.

The prediction is what makes the result interpretable:

- Merge a handful out of a large corpus and the ritual is not earning its cost — that is a finding,
  and it says the corpus was healthier than believed.
- Merge a large fraction and retrieval's bounded window has stopped being the binding constraint —
  also a finding, and a different one.

Either result is worth having. An unstated expectation is worth nothing, because any outcome can be
narrated as the expected one after the fact.

## Rules that bind every step

**All vault access goes through the `lore` CLI.** `lore search`, `lore record show`,
`lore record update`, `lore record delete`. Never read, glob, or edit a vault file directly: a
direct write bypasses the index and the sidecar and silently corrupts the record, and a direct read
sees bytes without the sidecar that gives them meaning. This ritual deletes records, so a shortcut
here is the most expensive one available.

**Every vault-sourced value is shape-checked before it enters a command line.** Record ids and names
arrive from a git-synced vault a teammate can write, and this ritual substitutes them into commands
throughout. Validate each against `^[A-Za-z0-9._/-]+$` **before any substitution**, per
`../_shared/security.md`. A value that fails is never substituted, quoted, or escaped in — refuse
loudly and stop.

**Every update and delete passes `--vault <name>` explicitly.** `lore record update` and
`lore record delete` locate a record by scanning the configured vaults in config order, so an
unscoped write in a multi-vault install lands wherever the scan hits first, which is not necessarily
where the record lives. Read each record's vault from the sidecar that `lore record show --json`
already returned and pass it back.

**One corpus per pass.** A corpus is one label in one vault. Two vaults holding the same label are
two passes: they are different products with different subsystems, and a lesson that duplicates
within one may be the only copy in the other.

## Step 1 — Walk the corpus whole

Read `total` first, then page until `truncated` is false:

```bash
lore search 'kind:lesson and label.craft.dispatch-lesson:executor' --json --limit 1
lore search 'kind:lesson and label.craft.dispatch-lesson:executor' --json --limit 50 --offset 0
lore search 'kind:lesson and label.craft.dispatch-lesson:executor' --json --limit 50 --offset 50
```

Results come back in a total order, so paging neither skips nor repeats a record.

**A page is not the corpus.** `--limit` alone returns the top N under the ranking, and a process
that must *examine* every record cannot start from a sample. An audit of this very corpus once
reported it as 20 records and concluded it was not bloated; 20 was the default `--limit`, and the
conclusion was confidently wrong about the other 89% of the data.

**Assert the walk before trusting it.** The count of records collected must equal `total`, and their
ids must be unique. Say both numbers in the report. If they disagree, stop — every judgment
downstream is being made against an unknown fraction of the corpus.

## Step 2 — Cluster by prevention

Build candidate clusters cheaply from titles and one-line summaries, then **read the bodies** of
every candidate and re-form the clusters on the prevention test.

Expect the cheap pass to be wrong in both directions, and expect to fix both:

- Records whose titles look alike but prevent different failures — split them.
- Records whose titles share nothing but prevent one failure — the cheap pass will not have put them
  together, so look for them deliberately once the bodies are read.

Carry every record into exactly one cluster, including the singletons. A record no cluster claims is
a record the pass did not examine, and the report must not imply otherwise.

## Step 3 — Judge each cluster

For each cluster of two or more, apply the prevention test pairwise and the stop condition to the
cluster as a whole. Produce one of:

- **merge** — one survivor, one or more records folded into it.
- **keep separate** — distinct triggers; the cluster dissolves back into singletons.
- **partial merge** — a subset merges, the rest stay. Common and correct; do not force a cluster to
  be all-or-nothing.

Choose the survivor on **which record best states the prevention**, not on age, length, or link
count. A long record that narrates four incidents is usually the *worst* survivor candidate in its
cluster, not the best — see the next step.

## Step 4 — Merge without flattening

**The merged record is one coherent lesson. It is never an append-log of recurrences.**

The observed failure this guards against: a record that had accumulated four bolted-on sections,
each narrating another time the same thing happened, with no single statement of what to do. It was
four times the length and no more useful than any one of its sections.

The shape that works:

- **One claim.** The prevention, stated once, at the top, in the imperative.
- **Incidents are evidence for that claim, not a chronology.** Cite them compactly and only where a
  concrete detail makes the trigger recognizable. Drop the dates, the run ids, and the order they
  happened in — none of that helps a reader decide whether they are in it.
- **Every distinct trigger the inputs named survives, named.** This is the load-bearing one. A merge
  that loses a trigger is a regression, not a consolidation, and it is the thing to check for
  explicitly before writing.
- **Length is a reading, not a limit.** The retrieval window is bounded by *record count* — the
  dispatch read is a `--limit`ed query — so what a merge buys is the slot it frees, and the
  survivor's word count competes with nothing. Never trim to hit a number, and never drop a trigger
  to make a record shorter.

What length *tells* you is which kind of cluster you had. Fold several genuinely redundant records
and the survivor lands far below their combined length, because most of what you dropped was
restatement. Fold two rich records that each named a real trigger and it lands just under their sum,
because there was little restatement to drop — that is a correct merge, not a failed one. A survivor
at nearly the full combined length is worth one more look, not because it is too long but because it
suggests the inputs shared nothing: that is the stop condition telling you these were two lessons.

### What has to be carried across

- **Cross-links.** Every `[[wikilink]]` a folded record's body carried moves to the survivor, unless
  the survivor already says the same thing.
- **`supersedes` edges.** A folded record's `supersedes` targets become the survivor's.
- **Label provenance.** Re-read the labels on every folded record. **A single-valued label is the
  trap**: where a folded record carried a different value for one — a second subsystem, say — the
  survivor can hold only one, so the fact has to be carried in the **prose** or it is silently
  dropped at the moment of the merge. Check every single-valued label before writing, not after.

Write the survivor with `lore record update <id> --vault <name>`, piping the full merged body.

## Step 5 — Repoint inbound links, then delete

Order matters: **repoint first, delete second.** A delete that runs first leaves every inbound link
pointing at nothing, and nothing will tell you.

Find what points at the folded record and rewrite each referrer's body to point at the survivor:

```bash
lore search 'related-lesson:<folded-name>' --json --limit 50
```

Then `lore record update` each referrer with the link rewritten. Re-run the search afterwards and
confirm it returns nothing.

### Why the loser is deleted rather than marked

This is a deliberate decision, and the ritual states it rather than assuming it:

The `lesson` kind has **no `superseded` status** in its vocabulary — only the live ones — and the
`superseded-by` reverse edge does not populate for it. So a record that has been folded away but
left in place stays **fully live in retrieval**. It keeps competing for the same bounded window the
merge existed to free up, which defeats the merge entirely: the corpus count drops on paper while
the retrieval pressure that motivated the pass stays exactly where it was.

Deleting after folding the content forward is therefore the only disposition that actually shrinks
the corpus. It is safe **only** because the content moved first and the links were repointed first —
which is why those are steps of their own, and why the delete is last.

```bash
lore record delete lesson/<folded-name> --vault <name>
```

## Step 6 — Verify, and report against the prediction

Verification is the deliverable. Report every one of these:

- **Corpus count before and after**, next to the **prediction** from the start.
- **Inbound links that still resolve** — the count before and after must match. A drop is a
  regression: something was deleted with a live referrer.
- **Trigger preservation, spot-checked.** For a sample of merged records, re-read the folded inputs'
  triggers and confirm the survivor still names each one. A merge that lost a trigger is reverted,
  not explained.
- **Each merge's length against its inputs' combined length**, as a reading on the cluster, not a
  bar it had to clear. Ratios spread wide is the healthy result: the low ones found real redundancy,
  the high ones merged records that shared a prevention but little prose.
- **Clusters examined and deliberately kept separate**, with the distinct triggers that kept them
  apart. This is evidence the stop condition was applied, and without it a small merge count is
  indistinguishable from a pass that did not look.

Then sync the vault.

## What this ritual does not fix

Consolidation buys **headroom**, not a cure. The window closes again within a few runs, because the
corpus resumes growing the moment the pass ends and the retrieval window is still the same size and
still bounded by recency.

Say this in the report rather than letting a good merge count imply the problem is solved. The
durable fix is a change to **what retrieval selects on** — recency is what makes an old lesson
unreachable no matter how good it is — and that is a separate piece of work this ritual should name
every time it runs.
