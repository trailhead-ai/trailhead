# lore health reports stale tasks

The rendered surface of `lore health`'s third check, `stale-tasks`, and of the changes this check
brings to the rest of the report. The report stays read-only, with the same commands, flags and
exit contract as before (`docs/design/lore-health-reports-dangling-links-across-every-vault.md`,
`docs/design/lore-health-reports-links-to-dropped-or-superseded-records.md`). This document fixes
only what changes: a third check and its window, how a finding names its record and its date, a
blank line between groups, and the advice for a superseded record that links back to its linker.

## What the check judges

An **unfinished task** is judged: a task whose status is `open`, `ready`, `in-progress` or
`blocked`. No other record is judged, and a session never is. "Records checked" in the summary
counts the unfinished tasks judged.

A judged task is reported, once, when either holds:

- **It has gone stale.** Its last-updated time (`updated-at`) is earlier than the start of the
  staleness window: the run's current time minus the window. A task updated inside the window is
  not reported on age. A last-updated time in the future is inside the window.
- **It is tied to a retired spec.** One of its references (a `related spec` entry, or a body
  `[[wikilink]]` outside code) can resolve to a spec, every record it can resolve to, in any
  configured vault, is `dropped` or `superseded`, and no record it could resolve to is unreadable.
  This is the rule `retired-links` applies to every other link, so a task's link that `retired-links`
  leaves to this check is judged the same way: a bare name that also matches a live record, or an
  unreadable one, is not reported. Such a task is reported whatever its last update.

A task that meets both is still one finding, naming both reasons. A task whose last-updated time is
missing or does not parse as a timestamp is reported as having no readable last-updated time: the
check cannot show it is fresh, and a task nobody can date is the kind of task the report exists to
surface.

The **staleness window** is 30 days unless the run sets another with `--stale-days N`, a whole
number of days from 1 to 36,500. The window in force is printed in the check's group heading, so a
run with a different window cannot be read as the default.

The group heading also breaks the findings down by reason, counting each task once, under the
first of: undated, tied to a retired spec, stale on age. A reason with no tasks is left out. On a first run, most findings are
stale on age and sort first. A recent task tied to a retired spec sorts last and falls under the
cap, so the breakdown is the only place the first page shows it exists.

Each finding line carries the task's status after its date, so a `blocked` task waiting on
something outside the vault reads differently from an `open` one nobody touched.

The check is skipped for every vault when any vault was not fully read, on the same rule and with
the same reason as the other checks: a spec's status cannot be confirmed in a vault that was not
read.

## Layout changes

1. **The summary** gains a third row per vault, `stale-tasks`, after `retired-links`.
2. **The groups** gain a third group, `stale-tasks`, last. Its findings are listed oldest first by
   last update, a task with no readable last-updated time first of all, at most 20 unless more were
   asked for. Its action line names what acts on the findings.
3. **A blank line separates each group from the next**, as in every example below. The report ends
   on the last group's action line, with no trailing blank line.

The order is the same in every check: oldest first by the date printed on the line. A last-updated
time is compared as a point in time, so two timestamps written with different offsets sort by when
they happened, not by their text.

## How a finding names its record and its date

Every finding line, in every check, opens with the record and a date. Both are read from files any
synced vault can write, so both are printed only when they have the expected shape:

- The record is printed as `<vault>/<kind>/<name>` when its name has the shape of a record id.
  A record whose name does not is printed as `<vault>/<kind>/` followed by the fixed phrase
  `(a record whose name is not a valid record id)`; its name is never echoed.
- The date is the record's last update as a UTC `YYYY-MM-DD` when its `updated-at` parses as a
  timestamp carrying an offset, and `undated` otherwise. A timestamp with no offset, a date with no
  time, and a value whose conversion to UTC overflows are all `undated`. One parser decides this
  for printing, sorting and the window alike, so no record's date can make a check fail. No part of
  an unparseable value is echoed.
- Every other record id a finding prints (a retired target, a successor, a retired spec, the ids
  in a printed command) goes through the same shape check.

## Advice for a superseded record that links back

A link to a superseded record that no record supersedes is usually a supersession that was never
recorded: the linking record replaced the target, and the target's own `related` list points back
at it. When the target relates back to the linking record, the finding says so and names the
command that records the supersession, instead of leaving only the advice to remove the link:

```
  trailhead/adr/notes-are-records  2026-06-02  related decision → decision/notes-policy: superseded, and no record supersedes it; it links back to this record, so if this record replaced it, record that: `lore record supersede decision/notes-policy --by adr/notes-are-records`
```

The command is printed only when both ids pass the record-id shape check and the guards of
`lore record supersede` would accept the linking record as the successor: an adr, spec or decision
target needs a successor in a frozen status (so an open task or an area cannot be one), and the
pair must not form a cycle through `supersedes` edges. Otherwise the finding keeps only the words,
with no command, because pasting it would be refused; the words then say the link back exists but
this pairing cannot be recorded as a supersession. The verdict comes from the verb's own pairing
guard (`evaluate_supersede_pairing`), and its cycle walk reads chain members the way the verb will
once the printed command is pasted: from the named vault alone when the command carries `--vault`,
otherwise from the first configured vault holding the record, with an unparseable copy ending the
chain. The verb also runs `evaluate_graph_guards` on each record it writes, which includes
`evaluate_supersedes_guard`; the report does not run those, so a pasted command can still be refused
by them even when the pairing guard accepts. Each id is quoted for the shell. When the target's kind and
name exist in more than one vault, the command adds `--vault <the target's vault>`, so pasting it
changes the record the finding describes and no other.

The `retired-links` action line names that remedy beside the existing ones.

## State — No stale tasks

Every vault was read and no unfinished task is stale or tied to a retired spec:

```
lore health — 2 vaults

vault       check           records  findings  skipped
default     dangling-links  1,204    0         no
default     retired-links   212      0         no
default     stale-tasks     18       0         no
trailhead   dangling-links  4,511    0         no
trailhead   retired-links   1,630    0         no
trailhead   stale-tasks     402      0         no

Records read match the index (sessions included): 6,402 read, 6,402 indexed.

dangling-links — no findings.

retired-links — no findings.

stale-tasks — no findings (window: 30 days).
```

## State — One stale task

A finding names the task, its last update, and how long ago that was in whole days:

```
stale-tasks — 1 finding, oldest first (window: 30 days): 1 stale on age
  trailhead/task/migrate-the-billing-cron  2026-08-02  open  last updated 103 days ago
Act on these: if the work already landed, mark the task done; if it still applies, resume it with /craft:execute; if it was abandoned, drop it with a note saying why (lore record update task/<name> --status dropped --diff). The id is task/<name>, without the vault prefix.
```

## State — Stale tasks beyond the cap

Past 20 findings, the group stops and counts the rest, as every check does:

```
stale-tasks — 412 findings, oldest first (window: 30 days): 380 stale on age, 30 tied to a retired spec, 2 undated
  default/task/import-notes-from-old-wiki  2024-11-02  open  last updated 741 days ago
  …  (20 lines)
  392 more. Run `lore health --all` to see every finding, or `--limit <n>` for more.
Act on these: if the work already landed, mark the task done; if it still applies, resume it with /craft:execute; if it was abandoned, drop it with a note saying why (lore record update task/<name> --status dropped --diff). The id is task/<name>, without the vault prefix.
```

## State — Task linked to a dropped or superseded spec

The finding names the retired spec and its status, whatever the task's age. A recent task tied to a
retired spec, and an old one that is also stale on age, each print one line:

```
  trailhead/task/export-as-csv  2026-11-10  ready  linked to spec/streaming-export, which is dropped
  trailhead/task/export-as-xlsx  2026-07-01  blocked  last updated 135 days ago; linked to spec/streaming-export, which is superseded
```

A task tied to several retired specs names up to three, then `and N more`.

## State — Staleness window set by the operator

`--stale-days 90` changes which tasks are stale on age, and the heading says which window was used.
A task tied to a retired spec is reported under any window:

```
stale-tasks — 37 findings, oldest first (window: 90 days): 31 stale on age, 6 tied to a retired spec
```

`--stale-days` takes a whole number from 1 to 36,500; anything else is refused before the report
runs, as `--limit` is.

## State — Task with no readable last-updated time

A task whose `updated-at` is missing, not a timestamp, or a timestamp with no offset is listed
first, undated:

```
  default/task/sketch-the-sync-design  undated  in-progress  no readable last-updated time
```

## State — Check skipped because a vault could not be read

When any vault could not be fully read, every check is skipped on every row, each with the reason
naming that vault, and each group is one line:

```
vault       check           records  findings  skipped
default     dangling-links  —        —         yes: vault scratch could not be read, so no target can be confirmed missing
default     retired-links   —        —         yes: vault scratch could not be read, so no target's status can be confirmed
default     stale-tasks     —        —         yes: vault scratch could not be read, so no spec's status can be confirmed
scratch     dangling-links  —        —         yes: its directory could not be read
scratch     retired-links   —        —         yes: its directory could not be read
scratch     stale-tasks     —        —         yes: its directory could not be read

dangling-links — skipped.

retired-links — skipped.

stale-tasks — skipped.
```
