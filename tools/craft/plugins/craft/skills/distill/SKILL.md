---
name: distill
description: >
  Run the backward-distillation ritual — condense completed spec/task work into ADRs, re-synthesize
  the area profiles they touch, and flip the member specs `complete`. Clusters related specs into
  logical design changes first, drafts one ADR per design change, and writes nothing until a human
  has dispositioned every draft.
  TRIGGER when: the user says "distill", "run a distill sweep", "distill this spec", "turn this into
  an ADR", "what did we decide across these specs", "close out the spec lifecycle", or invokes
  /craft:distill explicitly; also as the handoff tidy-followups prints once a spec's follow-ups are
  dealt with.
  DO NOT TRIGGER when: the work is still in flight (distillation is a post-hoc reading of finished
  work — the deferral rule would defer it anyway), the user wants to author a design decision BEFORE
  the work, or wants to record a single in-flight finding (both are lore's `decision` / `lesson`
  capture, deliberately cheap and liberal — craft authors ADRs only backward, from work already
  finished, so there is no forward path to route a pre-work decision into).
---

# Distill

**Distillation is the backward path.** Some decisions are authored up front; most are *discovered* —
they crystallize across several specs and only become legible once the work is done. Distill reads
finished work and condenses it into the ADR log, then brings the `area` profiles that work touched
back into agreement with it.

It is the final stage of the pipeline: brainstorm → gauntlet → (slice → plan → execute → review)* →
tidy-followups → distill. The bracketed loop repeats once per slice; distill attaches once, after
the loop reports the spec closed out. It is also the **sole writer of a spec's completion edge**
(`planned → complete` for pre-loop records, `ready → complete` for a spec the slice loop closed out)
— `complete` *means* distilled, including the zero-ADR outcome.

## The bar: what is worth an ADR

Ask one question of every candidate decision:

> **would a future implementer working in this area do something wrong or wasteful without knowing
> this?**

If the answer is no, it is not an ADR. Content **re-derivable from the code does not qualify** — an
ADR that restates what a reader would learn by opening the file costs upkeep and returns nothing. A
sweep that yields three ADRs from twenty specs is a normal sweep, not a failed one.

The four-section body contract in `${CLAUDE_PLUGIN_ROOT}/templates/adr.md` is exhaustive: Context,
Decision, Consequences, Alternatives rejected, budgeted at roughly one screenful. Provenance never
goes in prose — it goes in `related:` edges.

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
`lore record create`, `lore record update`, `lore task graph`. **Never read, glob, or edit a vault
file directly.** A direct write bypasses the index and the sidecar and silently corrupts the record;
a direct read sees bytes on disk without the sidecar that gives them meaning. This ritual touches
more records in one sitting than any other in craft, so a shortcut here costs the most.

**Every vault-sourced value is shape-checked before it enters a command line.** Record ids, record
names, and vault names all arrive from a git-synced vault a teammate can write, and this ritual
substitutes them into `lore search`, `lore record show`, and `lore record update` invocations
throughout. Validate each one against the safe-value shape `^[A-Za-z0-9._/-]+$` **before ANY
substitution** — the same rule `_shared/security.md` codifies for any vault-sourced value entering a
command, and the same one `plan/SKILL.md` and `slice/SKILL.md` already apply. This validation
**governs every substitution site** in this document, not a fixed count of them: a site added later
is covered by it without amending this rule. A value that fails the check is **never substituted,
quoted, or escaped in** — refuse loudly and stop. Silently omitting it would turn a refusal into a
query that returns zero hits and reads as "nothing found", which is exactly the wrong report for a
record whose id could not be trusted.

**Every batch update passes `--vault <name>` explicitly.** `lore record update` locates a record by
scanning the configured vaults in **config order**, so an unscoped update in a multi-vault install
lands wherever the scan happens to hit first — which is not necessarily where the record lives.
`--vault` disambiguates the record's CURRENT location; it is not the same flag as
`--product`/`--team` on a `create`, which instead selects the DESTINATION vault (see step 1). A
record's sidecar carries no vault field. For a spec the sweep queued, the vault is the part of its
queue line before `/spec/`; carry it from there and pass it as `--vault` on every read and update
against that spec.

## Sweep mode — building the queue

### 1. Ask lore for the queue

```
lore distill-queue
```

Lore holds the rule for which specs are waiting to be distilled — the candidate statuses, the
`distilled=` annotation and `related: adr` exclusions, the resumed-after-stop exception, the `draft`
ADR carve-out and the zero-task-edge deferral. **Do not re-apply that rule by hand:** no
`lore search "kind:spec ..."` candidate queries, no per-candidate exclusion check. A hand-built
queue and lore's drift apart, and the sweep would then queue what `lore health` does not report, or
skip what it does.

**Any failure is a hard stop, never an empty queue.** A run succeeded only when it exited 0 **and**
its first line starts `lore distill-queue — `. Anything else — a non-zero exit, a first line that
starts differently, no output (an older lore without the command, a crash) — stops the sweep before
anything is queued, clustered or drafted:

- a `lore:` refusal line (exit 1, a vault or a spec or task record that could not be read): relay it
  exactly as printed. It already names the way on, `/craft:distill spec/<name>` for one spec by
  name.
- the command is missing (an unknown-command error, an unrecognised-command usage message): say that
  this lore predates `lore distill-queue` and to update it (`trailhead update`), then stop.
- anything else: relay what was printed and stop.

Never read a failure as "nothing waiting", and never fall back to deriving the queue from
`lore search`.

On success the first line is `lore distill-queue — nothing waiting` (report that and stop) or
`lore distill-queue — N waiting, M deferred`; `0 waiting, M deferred` is a valid header, and the
`waiting:` list is then absent. Otherwise a `waiting:` list follows, then
`deferred, no linked task (distill each by target):`. Each line reads
`<vault>/spec/<name>  <date>  <status>  <reason>`, the reason sometimes carrying an ADR note after a
`;`.

- **The `waiting:` specs are the queue.**
- **The `deferred:` specs are deferred, not queued.** Report each as:

  > no task edges — link tasks or distill by target

  Both remedies are named on purpose: the spec may be genuinely unlinked (link the tasks and
  re-sweep) or simply distillable from its own text (use targeted mode). Guessing at the tree from
  prose alone is how a cluster comes out wrong.
- A line `<vault>/spec/ (a record whose name is not a valid record id)` names nothing the sweep can
  act on: report it as unusable and go on. This fixed-phrase line is exempt from the stop below: it
  is reported and skipped, and nothing from it is substituted anywhere.

**Shape-check every printed name before it enters a command.** The vault and spec name on each line
come from a git-synced vault a teammate can write. Check the id against the safe-value shape
`^[A-Za-z0-9._/-]+$` (the rule under "Rules that bind every step") before substituting it anywhere;
one that fails is never substituted, quoted or escaped in — refuse it loudly and stop.

### 2. Read each queued spec's sidecar

Split each queue line's `<vault>/spec/<name>` (after the shape check above) into the vault and
`spec/<name>`, then read the spec with the vault passed explicitly:

```
lore record show spec/<name> --json --vault <vault>
```

The printed `<vault>/spec/<name>` is not a record id and `lore record show` refuses it as not found;
only `spec/<name>` is. Lore decided the queue; it does not return the scope fields the
explicit-scope rule needs later, so read them here for each waiting spec and keep the output. Carry
the `<vault>` from the queue line as `--vault` on every later read and update of that spec.

### 3. Resolve each queued spec's task tree

```
lore search "kind:task related-spec:<name>"
lore task graph <task-name>
```

The first query rides the **forward** `related-spec` facet, which is maintained on every write — a
freshly-linked task shows up with no reindex in between. The second expands each matched task into
its tree, because the facet returns the task that carries the edge, not its parent plan or its
sibling tasks.

**In a sweep, a spec lore listed as waiting for which this search finds no task is a disagreement,
not a deferral.** Lore only lists a spec as waiting when a task links it, so one of the two reads is
wrong (a vault the search did not cover, a stale index, or a task the index does not hold but lore
counts). Report the spec and the disagreement, and leave it out of the clustering rather than
dropping it silently.

**In targeted mode, finding no task is not a disagreement.** Lore listed nothing, and a spec with no
linked task is exactly what the deferred list sends an operator here for. Say the tree is empty,
cluster the spec from its own text, its `related` edges and shared areas, and let the disposition
gate stand as the backstop.

**Stated thin spot:** nothing in the record model marks *which* task of a tree is the plan task.
Task trees resolve through `related: spec=` edges plus judgment, and the human disposition gate is
the backstop. Do not invent a discriminator and present the guess as resolution — if the tree is
ambiguous, say so when you present the cluster.

## Step 1 — Cluster before drafting

Clustering runs **before any ADR is drafted**. Drafting one ADR per spec produces a log shaped like
the execution schedule rather than like the design, which is exactly the failure the ADR tier exists
to fix.

Reconstitute the candidates into **logical design changes — M specs ↔ N ADRs**:

- **`related` edges** between specs, and between specs and tasks.
- **Shared areas** — specs whose work landed in the same area are usually one design change.
- **Superseded chains** — a spec the operator superseded and the one that replaced it are one
  thread, read from the `supersedes:` and `superseded-by:` fields. Superseded specs are never
  distilled individually; they enter **only as chain context** for the surviving spec.
- **Judgment**, where the graph is thin. Name the judgment when you present the cluster.

Execution-convenience splits (one design change cut into three specs so it could ship in pieces)
consolidate into **one** ADR. A spec carrying two independent decisions separates into **two**.

**Lingering `draft` ADRs are surfaced as candidate material two ways: an ADR any cluster member
carries a `related: adr=` edge to, and a `draft` ADR touching the cluster's areas** — a distillation
may fold either's content into the cluster ADR rather than starting from a blank record. The edge
check is not optional: an ADR the cluster's own members descend from must never be left to area
overlap alone to surface it. When either check hits, the draft is retired (`--status dropped`) with
a `related: adr=` edge to the ADR that absorbed it, so the abandoned number is traceable rather than
merely a gap.

**This surfacing excludes a `draft` ADR while some spec *other than the cluster's own members*
carries a `related: adr=` edge to it and has not yet reached a terminal status.** The carve-out is
required for the edge check above to ever fire: every cluster member is, by construction,
non-terminal at clustering time — its own `complete` flip is write-order step 6, which has not run
yet — so an exclusion keyed on *any* edged spec, the cluster's own members included, would always be
true exactly when the edge-based surfacing rule fires, and that path could never absorb anything.
Scoping the exclusion to specs other than the cluster's own members makes both rules hold at once:
surfacing fires on the cluster member's edge, and the exclusion asks only whether every *other*
edged spec has already landed. When the cluster being distilled holds the last outstanding edge,
every other edged spec is already terminal — which is exactly the moment absorption becomes correct.
Resolve those other specs spec-side, off the forward facet —
`lore search "kind:spec related-adr:<adr-id>"`, filtered to exclude the cluster's own membership —
then read `TERMINAL_SPEC_STATUSES = {"complete", "superseded", "dropped"}`
(`pipeline/derive.py:97`). **The edge is spec-side, never ADR-side**: a spec carries
`related: adr=<adr-id>` when it descends from that decision, and distill writes
`--related spec=<member>` on the ADR only on the backward path — so an ADR still waiting on its
derived specs carries no `related: spec=` edge of its own, and an exclusion keyed on one would
exclude nothing and retire the very ADRs it exists to protect. Such an ADR is still mid-decision,
waiting on its other derived specs to finish, not lingering. This exclusion protects decision
context those unlanded specs are still relying on: absorbing the ADR here would erase it, retiring
the ADR out from under every sibling spec still pointing at it as its provenance before the rest
have landed.

### The deferral rule

**A cluster with any member still in flight is deferred whole** — report "waiting on spec X" and
move to the next cluster. There are **no partial ADRs**: a half-distilled cluster writes a frozen
record of half a decision, and completing a frozen record means superseding it. The sweep
re-surfaces the cluster once the last member lands.

## Step 2 — Draft the proposal

For each cluster, draft:

- the proposed ADR(s), body-complete against `${CLAUDE_PLUGIN_ROOT}/templates/adr.md` — or the
  explicit verdict **zero ADRs, because …**, which is a real outcome and not a failure;
- the **absorption list** — the `decision` records this ADR subsumes, and any lingering `draft` ADR
  Step 1 surfaced as candidate material for this cluster, presented for disposition the same as an
  absorbed decision — retiring one is now the only terminal path it has;
- any **existing `active` ADR this supersedes**, and why;
- the **area profiles** the cluster touched, and what changes in each.

Nothing is written yet.

## Step 3 — Disposition (human-gated)

**No write happens before disposition.** The operator dispositions **each** draft ADR:

- `approve` — the draft goes to the write list as-is.
- `edit` — the draft is revised, then re-enters the write list (see below).
- `reject` — the cluster is not distilled. See Terminal outcomes.

Present **the actual write list** — every record that will be created or changed, with its record id
and the exact change — not just the proposal prose. The two diverge precisely where it matters: the
proposal argues, the write list is what lands.

After an `edit`, the final write-list review **renders the post-edit ADR body verbatim, never
proposal prose**, and that render is **the last thing the operator confirms before any write**. An
edit that does not re-enter the reviewed list would land the pre-edit text into a frozen record,
where a later fix is an amend only inside the ADR's scope (`/lore:correct`) and otherwise a whole
superseding ADR.

## Step 4 — Write, in a fixed order

The order is the contract. Each step has a different failure mode, and the sequence is what makes an
interrupted run recoverable: the ADR exists early enough for a re-run to detect it, and no spec
claims `complete` until everything behind it landed.

1. **ADR records are created first.** One
   `lore record create --kind adr --status active --product <scope>` (or `--team <scope>`) per
   approved draft, carrying its provenance as edges: `--related spec=<member>` for each member spec,
   and `--related decision=<absorbed>` for each absorbed decision. The scope flag is not optional
   here either — on create it selects the destination vault, so an unscoped create can land the ADR
   in the wrong vault. No ADR routes through `/craft:gauntlet`; this disposition owns every ADR's
   flip. This create is distill's only route to `active`: no other write in this document sets that
   flag on an ADR, and it is reachable at authorship — no condition on the status of the specs the
   ADR derives from gates it. When the new ADR supersedes an earlier one, run
   `lore record supersede adr/<old> --by adr/<new>` **immediately after that create** — the new ADR
   is already `active`, so the verb's pairing check passes. The successor's `supersedes` edge is
   then the durable pointer to `adr/<old>`: a resume that knows only the new ADR reads it from
   there.

2. Then **absorbed `draft` ADRs are retired** — each `draft` ADR Step 1 surfaced as candidate
   material and Step 2's proposal listed for disposition is flipped `--status dropped`, carrying a
   `related: adr=` edge back to the ADR that absorbed it, so the abandoned number is traceable
   rather than merely a gap. With forward activation gone, this write is the only terminal path a
   lingering draft ADR has: an absorbed draft that never gets this write simply lingers `draft`
   forever, undispositioned by anyone.

3. Then **absorbed `decision` records are retired** with
   `lore record supersede decision/<d> --by adr/<new>` — once per absorbed decision, so no competing
   owner of the same decision is left `active`.

4. Then **superseded ADRs are confirmed retired**: run step 1's verb again, once per ADR the new ADR
   supersedes — `lore record supersede adr/<old> --by adr/<new>`. Step 1's call already did the
   work, so this writes nothing when it landed and finishes it when it was interrupted. The verb
   writes the successor's `supersedes` edge and flips the predecessor to `superseded` in one call.
   That is the **one status change distill makes to an `active` ADR**: a reader landing on a
   superseded ADR finds its successor in `superseded-by:` once `lore reindex` has run, and in the
   successor's `supersedes` list at once.

Each verb call is idempotent and completes a torn pair, so an interrupted run is healed by running
the same verb again: *the re-run finds the successor ADR by its provenance edges, reads the
predecessor ADRs from the successor's `supersedes` list, and repeats step 1's verb and steps 3 and
4.* Re-running a call that already landed changes nothing, and the repair is **licensed as part of
that supersession** — it is the same write, finished late, not a new edit to a frozen record.

5. Then **touched `area` profiles are re-synthesized** to the sections of
   `${CLAUDE_PLUGIN_ROOT}/templates/area.md`, citing the new ADRs inline as `[[wikilinks]]`. Areas
   describe how things work **now**, so a new ADR that changes the answer makes the profile wrong
   until this step runs. Use `lore record update --diff` **scoped to the affected sections**,
   preserving the frontmatter and the `## Overview` lead line — the area map's one-liner extraction
   reads them, and a full-body replace discards everything the distillation never looked at. Then
   **verify each diff applied by re-reading the body**: a diff that fails to apply does not announce
   itself, and an unverified failure leaves the profile quietly disagreeing with the ADR you just
   wrote.

6. Finally, **member-spec `complete` flips land last** — one per member:

   ```
   lore record update <spec-id> --status complete --vault <name>
   ```

Write this **only if the spec is closed out and its cluster is dispositioned**. `complete` means
distilled; a cluster no human dispositioned has not been distilled no matter what else ran. Two spec
shapes count as closed out, and no others:

   - the spec is already `planned` — the pre-loop route; or
   - the spec is `ready` and carries `craft/slice-loop=complete` — the slice loop reached its
     terminating condition with every acceptance criterion covered.

**A spec carrying `craft/slice-loop=stopped` is distilled but is not flipped `complete`.** It
stopped with acceptance criteria still unmet, and `complete` is irreversible in practice:
`/craft:slice` refuses to select against a `complete` spec, and its stated remedy is to start a new
spec entirely. So an early-stopped spec keeps its `ready` status and its marker. The annotation-only
write in **Terminal outcomes** below (`--annotation distilled=<value>` with no `--status complete`)
is what keeps it out of later sweeps, so it neither re-queues forever nor claims a completeness it
never reached.

That distinction has to be **visible at disposition, not just here**: a `stopped` spec is rendered
in the write list with its marker value and its recorded stop-reason note shown, so closing one out
is a choice someone made rather than a side effect of approving the cluster.

**Guard this write with a fresh read.** Re-read the spec and re-check the marker immediately before
it lands — never trust the read from queue-build. An arbitrarily long human disposition sits between
the two, and `/craft:slice` clears and re-asserts `craft/slice-loop` on every re-entry. Without the
re-check, an operator who adds a slice while this cluster awaits disposition ends up with a
`complete` spec and an `in-progress` slice orphaned beneath it. This is the same "read fresh
immediately before the write" discipline `/craft:slice` applies to its own spec writes; it shrinks
the lost-update window without closing it.

Last position is deliberate: an interruption anywhere above leaves the spec still queued, and the
sweep picks it up again.

## Terminal outcomes

Every cluster ends in exactly one of these, and every one of them is terminal — nothing re-queues
forever.

**The `--status complete` half of every command below is conditional; the `--annotation` half is
not.** Each write is spelled here for a member that is closed out *complete* — already `planned`, or
`ready` carrying `craft/slice-loop=complete` (step 6's gate). For a member carrying
`craft/slice-loop=stopped`, drop `--status complete` and write the annotation alone:

```
lore record update <spec-id> --annotation distilled=<value> --vault <name>
```

That is the annotation-only form step 6 relies on: it is what keeps an early-stopped spec out of
later sweeps without claiming a completeness it never reached. The annotation value is chosen from
the outcomes below exactly as it would be for any other member — the cluster's outcome does not
change because one of its members stopped early.

- **ADRs written.** The ADR(s) carry `related: spec=<member>` edges from step 1, and the members
  reach `complete` in step 6, stamped so the exclusion check can tell this outcome from "not yet
  distilled":

  ```
  lore record update <spec-id> --status complete --annotation distilled=adr --vault <name>
  ```

- **Zero ADRs.** The cluster produced nothing worth recording. The members still reach `complete`,
  annotated so the sweep can tell "distilled, yielded nothing" from "not yet distilled":

  ```
  lore record update <spec-id> --status complete --annotation distilled=zero-adr --vault <name>
  ```

- **Rejected.** The operator rejected the cluster's drafts. The stamp goes on, and it **leaves their
  status untouched** — a rejection is the opposite of distilled, so `complete` would be a lie:

  ```
  lore record update <spec-id> --annotation distilled=rejected --vault <name>
  ```

Re-open it later with `/craft:distill spec/<id>`; the sweep will not.

## Resuming an interrupted run

A run that stops mid-write order leaves a cluster whose ADR exists and whose specs have not reached
their terminal state — still `planned` for pre-loop records, still `ready` with the
`craft/slice-loop` marker and no `distilled=` annotation for a loop-closed spec. Either shape is
exactly what the next sweep re-surfaces, since both stay in the queue `lore distill-queue` builds
and neither has picked up the annotation that would exclude it.

Before drafting anything for a re-surfaced cluster, **detect the existing cluster ADR via the
forward `related-spec` facet** — `lore search "kind:adr related-spec:<spec-name>"`. `related:` edges
are forward-only, and step 1 writes the edge on the ADR (`--related spec=<member>`), never on the
spec, so the member specs' sidecars carry nothing to detect from; the query above reads the side
that actually carries it. A run that finds one resumes rather than restarts: it **completes the
remaining writes** from wherever the order stopped, taking each predecessor ADR from the successor's
`supersedes` list. Both the found ADR and its `supersedes` list are vault-sourced, so a shared-vault
writer can plant them: before any entry is substituted into a command it must match the full-string
pattern `^adr/[A-Za-z0-9._-]+$` (`^decision/[A-Za-z0-9._-]+$` for an absorbed decision read back),
with no whitespace or newline allowed, and an entry that fails is refused loudly and never
substituted, quoted or escaped in. Then show the operator each resumed retirement — the
predecessor's id, title and current status — and run its verb only on an explicit yes for that
entry; a resume never retires a record on its own authority. Do not draft a second ADR — it would
split one decision across two records that readers will find independently, and there is no clean
way to undo either.

## Outcome report

Report, per cluster: the members, the ADRs written (with their record ids), the decisions absorbed,
the ADRs superseded, the area profiles re-synthesized, and the terminal outcome. Report deferrals
separately with their reason — an in-flight member, or the zero-task-edges message.

End with the read command for each ADR written, **fully formed** — the real record id, never a
placeholder — so the reader can paste it into a fresh session as-is:

```
lore record show adr/record-ops-locate-by-config-order-scan
```
