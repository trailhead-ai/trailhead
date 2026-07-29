---
name: distill
description: >
  Run the backward-distillation ritual — condense completed spec/task work into ADRs, re-synthesize
  the area profiles they touch, and flip the member specs `complete`. Clusters related specs into
  logical design changes first, drafts one ADR per design change, and writes nothing until a human
  has dispositioned every draft.
  TRIGGER when: the user says "distill", "run a distill sweep", "distill this spec", "turn this into
  an ADR", "what did we decide across these specs", "close out the spec lifecycle", or invokes
  /craft:distill explicitly; also as the handoff after a plan's work has landed and been reviewed.
  DO NOT TRIGGER when: the work is still in flight (distillation is a post-hoc reading of finished
  work — the deferral rule would defer it anyway), the user wants to author a design decision BEFORE
  the work (that is the forward path — brainstorm's altitude gate writes a draft adr and /craft:gauntlet
  reviews it), or the user wants to record a single in-flight finding (that is lore's `decision` /
  `lesson` capture, deliberately cheap and liberal).
---

# Distill

**Distillation is the backward path.** Some decisions are authored up front; most are *discovered* —
they crystallize across several specs and only become legible once the work is done. Distill reads
finished work and condenses it into the ADR log, then brings the `area` profiles that work touched
back into agreement with it.

It is the final stage of the pipeline: brainstorm → gauntlet → plan → execute → review → distill.
It is also the **sole writer of a spec's `planned → complete` edge** — `complete` *means* distilled,
including the zero-ADR outcome.

## The bar: what is worth an ADR

Ask one question of every candidate decision:

> **would a future implementer working in this area do something wrong or wasteful without knowing this?**

If the answer is no, it is not an ADR. Content **re-derivable from the code does not qualify** — an
ADR that restates what a reader would learn by opening the file costs upkeep and returns nothing. A
sweep that yields three ADRs from twenty specs is a normal sweep, not a failed one.

The four-section body contract in `${CLAUDE_PLUGIN_ROOT}/templates/adr.md` is exhaustive: Context,
Decision, Consequences, Alternatives rejected, budgeted at roughly one screenful. Provenance never
goes in prose — it goes in `related:` edges and annotations.

## Two modes

| Invocation | Mode |
|---|---|
| `/craft:distill` | **sweep** — build the queue, work it cluster by cluster |
| `/craft:distill spec/<id>` | **targeted** — one entry point, expanded to its whole cluster |

Targeted mode **expands to the spec's full cluster** before anything else: the argument names an
entry point, not the unit of work. The deferral rule and the no-partial-ADR rule apply identically,
and it **never distills a lone member of a larger cluster**.

Targeted mode is also the **only way to re-open** a cluster stamped `distilled=rejected` — the sweep
excludes those by design, so a rejection stays rejected until someone deliberately points at it.

## Rules that bind every step

**All vault access goes through the `lore` CLI** — `lore search`, `lore record show`,
`lore record create`, `lore record update`, `lore task graph`.
**Never read, glob, or edit a vault file directly.**
A direct write bypasses the index and the sidecar and silently corrupts the record;
a direct read sees bytes on disk without the sidecar that gives them meaning. This ritual touches
more records in one sitting than any other in craft, so a shortcut here costs the most.

**Every batch write passes its scope explicitly (`--product` / `--team`).** Record operations locate
a record by scanning the configured vaults in **config order**, so an unscoped write in a multi-vault
install lands wherever the scan happens to hit first — which is not necessarily where the record
lives. Read each record's scope from the sidecar that `lore record show <record-id> --json` already
returned during the queue pass, and pass it back on every write against that record.

## Sweep mode — building the queue

### 1. Enumerate the candidates

```
lore search "kind:spec status:planned"
```

Plus, **one-time migration**: specs that reached `complete` before this ritual existed and were
therefore never distilled. Enumerate them the same way —

```
lore search "kind:spec status:complete"
```

— and let the exclusion check below drop the ones that were genuinely distilled. Once the migration
cohort is worked through, this second query returns only already-excluded records.

### 2. Apply the exclusion per candidate, never in the query

A spec is out of the queue if it already carries **a `distilled=` annotation** —
`distilled=adr` (ADRs written), `distilled=zero-adr`, or `distilled=rejected`. This is the sole
exclusion key: `related:` edges are forward-only, and step 1 writes the ADR's provenance edge as
`--related spec=<member>` **on the ADR**, never on the spec, so a spec's own sidecar never carries
a `related: adr` edge to check. `distilled=` cannot ride the KQL query either, because
annotations are never indexed, so this cannot be a KQL filter. Check per candidate:

```
lore record show <spec-id> --json
```

Read the sidecar's annotations and drop the candidate on a hit. This is an extra call per
candidate; at spec volume that is fine, and it is the only correct way to apply an exclusion key
the index does not carry.

Keep the `--json` output — its scope fields are what the explicit-scope rule above needs later.

### 3. Resolve each surviving spec's task tree

```
lore search "kind:task related-spec:<name>"
lore task graph <task-name>
```

The first query rides the **forward** `related-spec` facet, which is maintained on every write — a
freshly-linked task shows up with no reindex in between. The second expands each matched task into
its tree, because the facet returns the task that carries the edge, not its parent plan or its
sibling slices.

**A spec with zero task edges is deferred, not queued.** Report it as:

> no task edges — link tasks or distill by target

Both remedies are named on purpose: the spec may be genuinely unlinked (link the tasks and re-sweep)
or simply distillable from its own text (use targeted mode). Guessing at the tree from prose alone
is how a cluster comes out wrong.

**Stated thin spot:** nothing in the record model marks *which* task of a tree is the plan task. Task
trees resolve through `related: spec=` edges plus judgment, and the human disposition gate is the
backstop. Do not invent a discriminator and present the guess as resolution — if the tree is
ambiguous, say so when you present the cluster.

## Step 1 — Cluster before drafting

Clustering runs **before any ADR is drafted**. Drafting one ADR per spec produces a log shaped like
the execution schedule rather than like the design, which is exactly the failure the ADR tier exists
to fix.

Reconstitute the candidates into **logical design changes — M specs ↔ N ADRs**:

- **`related` edges** between specs, and between specs and tasks.
- **Shared areas** — specs whose work landed in the same area are usually one design change.
- **Superseded chains** — a reframed spec and its successor are one thread. Superseded specs are
  never distilled individually; they enter **only as chain context** for the surviving spec.
- **Judgment**, where the graph is thin. Name the judgment when you present the cluster.

Execution-convenience splits (one design change cut into three specs so it could ship in pieces)
consolidate into **one** ADR. A spec carrying two independent decisions separates into **two**.

**Lingering `draft` ADRs touching the cluster's areas are surfaced as candidate material** — a
distillation may fold a draft's content into the cluster ADR rather than starting from a blank
record. When it does, the draft is retired (`--status dropped`) with a `related: adr=` edge to the
ADR that absorbed it, so the abandoned number is traceable rather than merely a gap.

### The deferral rule

**A cluster with any member still in flight is deferred whole** — report "waiting on spec X" and
move to the next cluster. There are **no partial ADRs**: a half-distilled cluster writes an
immutable record of half a decision, and immutable is exactly the wrong property for that. The sweep
re-surfaces the cluster once the last member lands.

## Step 2 — Draft the proposal

For each cluster, draft:

- the proposed ADR(s), body-complete against `${CLAUDE_PLUGIN_ROOT}/templates/adr.md` — or the
  explicit verdict **zero ADRs, because …**, which is a real outcome and not a failure;
- the **absorption list** — the `decision` records this ADR subsumes;
- any **existing `active` ADR this supersedes**, and why;
- the **area profiles** the cluster touched, and what changes in each.

Nothing is written yet.

## Step 3 — Disposition (human-gated)

**No write happens before disposition.** The operator dispositions **each** draft ADR:

- `approve` — the draft goes to the write list as-is.
- `edit` — the draft is revised, then re-enters the write list (see below).
- `reject` — the cluster is not distilled. See Terminal outcomes.

Present **the actual write list** — every record that will be created or changed, with its record
id and the exact change — not just the proposal prose. The two diverge precisely where it matters:
the proposal argues, the write list is what lands.

After an `edit`, the final write-list review
**renders the post-edit ADR body verbatim, never proposal prose**,
and that render is **the last thing the operator confirms before any write**. An
edit that does not re-enter the reviewed list would land the pre-edit text into a record that
convention then forbids fixing — the correction would have to be a whole superseding ADR.

## Step 4 — Write, in a fixed order

The order is the contract. Each step has a different failure mode, and the sequence is what makes an
interrupted run recoverable: the ADR exists early enough for a re-run to detect it, and no spec
claims `complete` until everything behind it landed.

1. **ADR records are created first.** One
   `lore record create --kind adr --status active --product <scope>` (or `--team <scope>`) per
   approved draft, carrying its provenance as edges: `--related spec=<member>` for each member spec,
   `--related decision=<absorbed>` for each absorbed decision, and — when this ADR supersedes an
   existing one — `--related adr=<predecessor>`. The scope flag is not optional here either — on
   create it selects the destination vault, which is the per-vault `ADR-NNN` numbering namespace,
   so an unscoped create can mint a number in the wrong vault. The create path assigns the `ADR-NNN`
   number itself. Distilled ADRs do not route through `/craft:gauntlet`; this disposition owns their flip.

2. Then **absorbed `decision` records are flipped `superseded` with a `related: adr=` edge** back to
   the ADR that subsumed them, so no competing owner of the same decision is left `active`.

3. Then **superseded ADRs are flipped, both edge directions written** — the predecessor gains
   `--status superseded` and `--related adr=<successor>`. This bidirectional metadata write is the
   **sole metadata exception to active-ADR immutability**: a reader landing on a superseded ADR must
   see its successor without waiting for a reindex.

   The two directions are **two CLI writes, not a transaction**, so their internal order is fixed:
   the successor's `related: adr=<predecessor>` edge is written at ADR creation; the predecessor's `superseded` flip and its `related: adr=<successor>` back-edge are written second.
   That ordering is the recoverable one — the surviving artifact of a crash is an `active` successor
   pointing at a still-`active` predecessor, which is detectable.
   *An interruption between the two is healed by the resume rule: the re-run detects the successor's edge and completes the predecessor's write.*
   This metadata-only repair is **licensed as part of the supersession exception** — it is the same
   write, finished late, not a new edit to an immutable record.

4. Then **touched `area` profiles are re-synthesized** to the sections of
   `${CLAUDE_PLUGIN_ROOT}/templates/area.md`, citing the new ADRs inline as `[[wikilinks]]`. Areas
   describe how things work **now**, so a new ADR that changes the answer makes the profile wrong
   until this step runs. Use `lore record update --diff` **scoped to the affected sections**,
   preserving the frontmatter and the `## Overview` lead line — the area map's one-liner extraction
   reads them, and a full-body replace discards everything the distillation never looked at. Then
   **verify each diff applied by re-reading the body**: a diff that fails to apply does not announce
   itself, and an unverified failure leaves the profile quietly disagreeing with the ADR you just
   wrote.

5. Finally, **member-spec `complete` flips land last** — one per member:

   ```
   lore record update <spec-id> --status complete --product <scope>
   ```

   Write this **only if the spec is already `planned` and its cluster is dispositioned**. `complete`
   means distilled; a spec that never reached `planned`, or whose cluster no human dispositioned,
   has not been distilled no matter what else ran. Last position is deliberate: an interruption
   anywhere above leaves the spec still queued, and the sweep picks it up again.

## Terminal outcomes

Every cluster ends in exactly one of these, and every one of them is terminal — nothing re-queues
forever.

- **ADRs written.** The ADR(s) carry `related: spec=<member>` edges from step 1, and the members
  reach `complete` in step 5, stamped so the exclusion check can tell this outcome from "not yet
  distilled":

  ```
  lore record update <spec-id> --status complete --annotation distilled=adr --product <scope>
  ```

- **Zero ADRs.** The cluster produced nothing worth recording. The members still reach `complete`,
  annotated so the sweep can tell "distilled, yielded nothing" from "not yet distilled":

  ```
  lore record update <spec-id> --status complete --annotation distilled=zero-adr --product <scope>
  ```

- **Rejected.** The operator rejected the cluster's drafts. The stamp goes on, and it
  **leaves their status untouched** — a rejection is the opposite of distilled, so `complete` would
  be a lie:

  ```
  lore record update <spec-id> --annotation distilled=rejected --product <scope>
  ```

  Re-open it later with `/craft:distill spec/<id>`; the sweep will not.

## Resuming an interrupted run

A run that stops mid-write order leaves a cluster whose ADR exists and whose specs are still
`planned` — which is exactly what the next sweep re-surfaces.

Before drafting anything for a re-surfaced cluster,
**detect the existing cluster ADR via the forward `related-spec` facet** —
`lore search "kind:adr related-spec:<spec-name>"`. `related:` edges are forward-only, and step 1
writes the edge on the ADR (`--related spec=<member>`), never on the spec, so the member specs'
sidecars carry nothing to detect from; the query above reads the side that actually carries it. A
run that finds one resumes rather than restarts: it **completes the remaining writes** from
wherever the order stopped. Do not draft a second ADR — it would burn a second sequence number and
split one decision across two immutable records, and there is no clean way to undo either.

## Outcome report

Report, per cluster: the members, the ADRs written (with their record ids), the decisions absorbed,
the ADRs superseded, the area profiles re-synthesized, and the terminal outcome. Report deferrals
separately with their reason — an in-flight member, or the zero-task-edges message.

End with the read command for each ADR written, **fully formed** — the real record id, never a
placeholder — so the reader can paste it into a fresh session as-is:

```
lore record show adr/adr-014-record-ops-locate-by-config-order-scan
```
