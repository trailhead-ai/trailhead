# lore health reports specs waiting to be distilled

The rendered surface of `lore health`'s fifth check, `distill-queue`, and of `lore distill-queue`,
the read-only command `/craft:distill`'s sweep builds its queue from. Both decide through one
definition held in lore, so a spec is in the report exactly when the sweep would queue it. The
report stays read-only, with the same commands, flags and exit contract as before
(`docs/design/lore-health-reports-dangling-links-across-every-vault.md` and the three documents
after it). This document fixes only what changes: a fifth check, and the command distill reads.

## What the definition decides

A spec is a **candidate** when any of these holds:

- its status is `planned`;
- its status is `complete`;
- its status is `ready` and it carries the label `craft/slice-loop`, whatever its value.

A candidate is **excluded** when either holds:

- it carries a `distilled` annotation, whatever its value — except a `ready` spec whose
  `craft/slice-loop` label is `complete`, which stays in: its annotation records an earlier
  distillation, and the slices shipped since the loop resumed have never been distilled;
- it carries a `related adr` edge whose ADR is `active`, `superseded` or `dropped`. An edge whose
  ADR is `draft`, cannot be found, or cannot be read does not exclude it: no decision is on record
  yet. The ADR is looked up in the spec's own vault first. When that vault does not hold it, every
  other configured vault is looked in, and the edge excludes only when every copy found is
  `active`, `superseded` or `dropped`; copies that disagree keep the spec waiting, so a same-named
  ADR elsewhere cannot silently take a spec out of the queue.

A candidate that is not excluded is **waiting to be distilled**. One with no task linked to it
(no task in any configured vault carries a `related spec` entry naming it) is **deferred** instead:
the sweep cannot cluster it without a task tree, so it is distilled by target.

Session records are never judged. When any vault was not fully read, no spec can be judged — a
task edge or an ADR's status could sit in the unread part — so the check is skipped for every
vault and `lore distill-queue` refuses.

## Layout changes

1. **The summary** gains a fifth row per vault, `distill-queue`, after `missing-paths`. "Records
   checked" counts the vault's specs.
2. **The groups** gain a fifth group, `distill-queue`, last. Its findings are the specs waiting to be
   distilled, oldest first by last update, at most 20 unless more were asked for. Deferred specs
   are not findings; the heading counts them.
3. **Its action line** names `/craft:distill`.

A finding line names the spec, its last update, its status, and why it is waiting:

| The spec | The line says |
|---|---|
| `planned` | `planned, not yet distilled` |
| `complete`, no annotation | `complete, never distilled` |
| `ready`, slice loop `complete` | `slice loop complete, not yet distilled` |
| `ready`, slice loop `stopped` | `slice loop stopped, not yet distilled` |
| `ready`, slice loop any other value | `slice loop closed, not yet distilled` |
| `ready`, slice loop `complete`, annotated | `slice loop complete, previously distilled` |

The label's value is printed only when it is `complete` or `stopped`; any other value is never
echoed. A spec kept waiting despite a `related adr` edge says why, after the reason: `; its ADR is
still draft`, `; its ADR could not be found`, or `; its ADR could not be read`.

Every vault name, record id and reason is printed through the same control-character escaping and
record-id shape rule the rest of `lore health` uses, in both the check and `lore distill-queue`.

## `lore distill-queue`

Read-only, over every configured vault; it takes no flags but `--help`. One line per spec waiting,
oldest first, then one line per deferred spec, each as `<vault>/spec/<name>` with the same record-id
shape rule every health finding uses:

```
lore distill-queue — 3 waiting, 1 deferred

waiting:
  trailhead/spec/streaming-export  2026-09-02  ready  slice loop complete, not yet distilled
  trailhead/spec/notes-are-records  2026-10-11  planned  planned, not yet distilled
  levr/spec/audio-on-demand  2026-11-01  ready  slice loop complete, previously distilled

deferred, no linked task (distill each by target):
  default/spec/sketch-the-sync-design  2026-06-30  complete  complete, never distilled
```

With nothing waiting and nothing deferred it prints `lore distill-queue — nothing waiting` and exits
0; with nothing waiting but some specs deferred it prints the counted heading
(`lore distill-queue — 0 waiting, N deferred`) and the deferred list, so a deferred spec is never
hidden. When a vault was
not fully read it prints one `lore:` line and exits 1, because a partial queue read as whole would
let the sweep skip a spec. The line names the vault and the way on:

```
lore: vault scratch could not be read, so the distill queue is refused rather than built partial; repair or remove the vault to sweep again, or distill one spec by name with /craft:distill spec/<name>
```

Every successful run's first line starts with `lore distill-queue — `. A caller treats any non-zero
exit, and any output whose first line does not start that way (an older lore without this command,
a crash), as a refusal — never as an empty queue. A record name that is not a valid record id is printed as
`<vault>/spec/ (a record whose name is not a valid record id)` and is never echoed; the sweep
cannot act on it, and the line says so.

## State — no spec waiting to be distilled

```
distill-queue — no findings.
```

When some specs are deferred, the heading says so even with no findings:

```
distill-queue — no findings (1 deferred: no linked task; list them with lore distill-queue and distill each with /craft:distill spec/<name>).
```

## State — one spec waiting to be distilled

```
distill-queue — 1 finding, oldest first
  trailhead/spec/streaming-export  2026-09-02  ready  slice loop complete, not yet distilled
Act on these with /craft:distill: a sweep takes every waiting spec, including any not shown; /craft:distill spec/<name> takes one. The id is spec/<name>, without the vault prefix.
```

## State — more specs waiting than the per-check cap

```
distill-queue — 31 findings, oldest first (4 deferred: no linked task; list them with lore distill-queue and distill each with /craft:distill spec/<name>)
  default/spec/import-notes-from-old-wiki  2025-01-14  complete  complete, never distilled
  …  (20 lines)
  11 more. Run `lore health --all` to see every finding, or `--limit <n>` for more.
Act on these with /craft:distill: a sweep takes every waiting spec, including any not shown; /craft:distill spec/<name> takes one. The id is spec/<name>, without the vault prefix.
```

## State — the check fails, such as an anchoring ADR that cannot be read

An ADR that cannot be read does not fail the check: it confirms no decision, so the spec stays
waiting, and the line says why it was kept:

```
  trailhead/spec/notes-are-records  2026-10-11  planned  planned, not yet distilled; its ADR could not be read
```

A vault that was not fully read skips the check on every row, as the first three checks do:

```
default     distill-queue   —        —         yes: vault scratch could not be read, so no spec's ADR or tasks can be confirmed
scratch     distill-queue   —        —         yes: its directory could not be read

distill-queue — skipped.
```

A check that raises is reported as `errored: <error type>` on its rows and in its group, and every
other check still renders, as for every check.
