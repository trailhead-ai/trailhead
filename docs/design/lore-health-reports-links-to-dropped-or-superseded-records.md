# lore health reports links to dropped or superseded records

The rendered surface of `lore health`'s second check, `retired-links`, and of the report's
multi-check layout. The report stays read-only, with the same commands, flags and exit contract as
its first check, `dangling-links` (`docs/design/lore-health-reports-dangling-links-across-every-vault.md`).
This document fixes only what changes: a second check, one summary row per vault per check, one
findings group per check, where a malformed reference sits, and what the index line counts.

## What the check judges

A **linking record** is judged when it is still live:

- a decision, ADR or area profile whose status is `active`;
- a task whose status is `open`, `ready`, `in-progress` or `blocked`.

Every other record is not judged, because its links record history: a done, dropped or superseded
task, a superseded or dropped decision or ADR, a draft ADR, a lesson, a spec, a session. "Records
checked" in the summary counts the linking records judged.

A linking record's references are the ones `dangling-links` reads except `supersedes`: `related`
entries, `depends-on`, a task's `parent`, and body `[[wikilinks]]` outside code. A `supersedes`
entry is how a successor records what it replaced, so it always points at a superseded record and
is history, never a finding. So is a `related` entry or body wikilink from a record to a target that
the same record supersedes: the link restates the supersession, and the record is the successor the
finding would name. When a bare `[[name]]` resolves to several retired records, superseding any one
of them makes the link history. A reference is reported when every record it can resolve to, in any
configured vault, is `dropped` or `superseded`. A bare `[[name]]` resolves to every record with that
name; a kinded reference to every record with that kind and name. When a reference resolves to more
than one retired record, the finding names each, with its own status. A reference that resolves to
nothing is `dangling-links`' finding, not this one, and a malformed one is not judged here. A target
whose status is missing or not a string is neither live nor retired, and is not reported.

A task's link to a spec is never reported by this check: a task tied to a retired spec is a
stale-task concern, which the spec requires be reported once, as stale, not as a retired link.

A superseded record's **successor** is the record whose `supersedes` names it. Lore stores that
edge only on the successor, so the check finds it by reading every record of every vault. The
successor list is unverified: any record in any vault can claim to supersede a target. So a
successor is named only when its kind and name have the shape of a record id; any other successor
is counted under the fixed phrase `a successor whose name is not a valid record id`, never echoed.
At most three successors are named, sorted, then `and N more`. A successor that is itself dropped
or superseded is marked `(itself dropped)` or `(itself superseded)`, so the finding never presents
a dead record as the replacement without saying so. When two successors share a kind and name and
differ only by vault, each such entry is printed as `<vault>/<kind>/<name>`; unambiguous entries
stay `<kind>/<name>`. A finding naming several retired records qualifies its entries the same way.

A bare `supersedes` entry (a name with no kind) is treated as claiming that name for every kind.
That is deliberately broader than the search index, which derives `superseded-by` only from
`kind/name` entries, so the report can name a successor that search does not show.

A check that raises unexpectedly does not take the report down: its rows read
`errored: <error type>` and its group says so in one line, every other check still renders, and
the report still exits successfully. The same holds when the raise is in that check's sorting or
in rendering any of its findings.

The check is skipped for every vault when any vault was not fully read, on the same rule and with
the same reason as `dangling-links`: a target's status cannot be confirmed in a vault that was not
read.

## Layout, top to bottom

1. **The summary.** One row per vault per check, vault by vault, checks in a fixed order
   (`dangling-links`, then `retired-links`).
2. **Unread records and the index line**, unchanged in place.
3. **One group per check**, in the same order. Each group lists its findings oldest first by the
   linking record's last update, at most 20 unless more were asked for, then a line counting the
   rest and a line naming what acts on them. The cap applies to each check on its own.

The index line names the same quantity on every line it can print: the records the index would
hold, sessions included. Its could-not-confirm form counts those records too, never the records
parsed.

A malformed reference in `dangling-links` names where it sits, never its text: a body wikilink by
its line in the body, a sidecar entry by its field and its position in that field. One finding per
field, listing each place:

```
  default/task/import-notes-from-old-wiki  2024-11-02  wikilink → malformed reference on body lines 12, 40 and 41: not shaped like a record id
  default/decision/use-a-pooled-client  2025-02-10  related spec → malformed reference at entry 3: not shaped like a record id
```

## State — No findings

Every vault was read and no live record links to a retired one. The check's rows show zero findings,
and its group says so in one line:

```
lore health — 2 vaults

vault       check           records  findings  skipped
default     dangling-links  1,204    0         no
default     retired-links   212      0         no
trailhead   dangling-links  4,511    0         no
trailhead   retired-links   1,630    0         no

Records read match the index (sessions included): 6,402 read, 6,402 indexed.

dangling-links — no findings.

retired-links — no findings.
```

## State — One finding

A finding names the linking record with its vault, its last update, the field the link sits in,
the retired target, and what became of it:

```
retired-links — 1 finding, oldest first
  trailhead/decision/use-a-pooled-client  2026-03-14  related decision → decision/pool-size-limits: dropped
Act on these with /lore:correct: point a superseded link at a named live successor; remove a link to a dropped record, or to one with no live successor, unless it still applies.
```

## State — Findings beyond the cap

Past 20 findings, the group stops and counts the rest. Each check's cap stands alone, so a long
`dangling-links` group never hides `retired-links`:

```
retired-links — 63 findings, oldest first
  default/task/migrate-the-billing-cron  2025-01-08  related decision → decision/run-billing-nightly: superseded by decision/run-billing-hourly
  …  (20 lines)
  43 more. Run `lore health --all` to see every finding, or `--limit <n>` for more.
Act on these with /lore:correct: point a superseded link at a named live successor; remove a link to a dropped record, or to one with no live successor, unless it still applies.
```

## State — Superseded target with its successor named

A link to a superseded record names the record that superseded it, so the fix is visible in the
finding. When more than one record supersedes it, up to three are named, sorted, then a count of
the rest:

```
  trailhead/adr/session-notes-are-markdown  2026-05-02  wikilink → decision/store-sessions-as-json: superseded by adr/sessions-are-json-records
  trailhead/area/lore-search  2026-06-11  related decision → decision/rank-by-recency: superseded by decision/rank-by-relevance and decision/rank-by-relevance-v2
  trailhead/adr/search-ranking  2026-06-12  related decision → decision/rank-by-recency: superseded by decision/a, decision/b, decision/c and 2 more
```

## State — Superseded target whose successor cannot be found

A record can be marked superseded with no record naming it in `supersedes`: the successor was
removed, or the status was set by hand. The finding says so rather than leaving the successor
blank:

```
  trailhead/decision/cache-search-results  2026-04-20  related decision → decision/use-an-lru-cache: superseded, and no record supersedes it
```

A successor the check cannot name safely, or one that is itself retired, is reported as such rather
than offered as the replacement:

```
  trailhead/task/tune-the-cache  2026-05-01  related decision → decision/cache-ttl: superseded by a successor whose name is not a valid record id
  trailhead/area/lore-search  2026-06-11  wikilink → decision/rank-by-recency: superseded by decision/rank-by-bm25 (itself superseded)
```

A link that resolves to two retired records names each:

```
  trailhead/task/clean-old-notes  2026-02-09  wikilink → notes-policy: adr/notes-policy superseded by adr/notes-are-records; decision/notes-policy dropped
```

## State — Check skipped because a vault could not be read

When any vault could not be fully read, both checks are skipped on every row, each with the reason
naming that vault, and each group is one line:

```
vault       check           records  findings  skipped
default     dangling-links  —        —         yes: vault scratch could not be read, so no target can be confirmed missing
default     retired-links   —        —         yes: vault scratch could not be read, so no target's status can be confirmed
scratch     dangling-links  —        —         yes: its directory could not be read
scratch     retired-links   —        —         yes: its directory could not be read

dangling-links — skipped.

retired-links — skipped.
```
