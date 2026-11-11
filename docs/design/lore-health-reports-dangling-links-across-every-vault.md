# lore health reports dangling links across every vault

The rendered surface of `lore health`: one terminal report of the records in the operator's vaults
whose references point at a record that does not exist. The report only reads. It creates, edits
and deletes no record, takes no vault lock, and opens the search index read-only. Acting on a
finding is a separate step, named in the report.

```
lore health                  # every configured vault
lore health --vault <name>   # one vault; targets are still looked up in every vault
lore health --limit <n>      # show up to n findings per check (default 20)
lore health --all            # show every finding
```

## What the report is built from

Every record in every vault `lore vault ls` lists is read from disk: its sidecar and its body. A
record's references are:

- its `related` edges, one per `<kind>: <name>` entry;
- its `supersedes` entries (`<kind>/<name>`);
- its `depends-on` entries: a task's bare names are tasks, a spec's or ADR's `<kind>/<name>[@stage]`
  entries drop the stage;
- a task's `parent`;
- the `[[wikilinks]]` in its body (`[[name]]`, `[[kind/name]]`, with any `|alias` or `#heading`
  removed). Wikilinks inside fenced code blocks and inline code are examples, not links, and are
  not read.

A reference dangles when no record with that kind and name exists in **any** configured vault. A
bare `[[name]]` carries no kind, so it dangles only when no record of any kind has that name. A run
limited to one vault reports only that vault's records, but still looks every target up in every
vault.

Session records are read, because a reference may point at one, but a session is never reported.

Every record id, field name and target shown comes from a vault and is untrusted. Before display,
each is rendered with control and invisible format characters escaped, so a finding is always one
line and can never carry a terminal escape.

## Layout, top to bottom

1. **The summary.** One row per vault and check: records checked, findings, and whether the check
   was skipped.
2. **The index line.** Whether the number of records read matches the number the search index
   holds for the vaults checked.
3. **One group per check.** Its findings, oldest first by the linking record's last update, at most
   20 unless more were asked for, then a line counting the rest and a line naming what acts on
   them.

Records checked counts the records a check judged: every record in the vault except sessions.

## State — No findings

Every vault was read and no reference dangles. The summary shows zero findings for every row, and
the check's group says so in one line, so a clean run reads differently from a skipped one:

```
lore health — 3 vaults

vault       check           records  findings  skipped
default     dangling-links  1,204    0         no
trailhead   dangling-links  4,511    0         no
levr        dangling-links  310      0         no

Records read match the index: 6,402 read, 6,402 indexed.

dangling-links — no findings.
```

## State — One finding

One finding names the linking record (with its vault), its last update, the field the reference
sits in, and the missing target:

```
lore health — 1 vault (targets looked up in all 3)

vault       check           records  findings  skipped
trailhead   dangling-links  4,511    1         no

Records read match the index: 4,890 read, 4,890 indexed.

dangling-links — 1 finding, oldest first
  trailhead/decision/use-a-pooled-client  2026-03-14  related decision → decision/pool-size-limits: not found in any vault
Act on these with /lore:correct: point the reference at the record that replaced its target, or remove it.
```

A body wikilink names `wikilink` as its field, and a bare `[[name]]` target shows as the name
alone.

## State — Findings beyond the cap

Past 20 findings in a check, the group stops and counts the rest, and says how to see them:

```
dangling-links — 145 findings, oldest first
  default/task/import-notes-from-old-wiki  2024-11-02  related lesson → lesson/retry-budget: not found in any vault
  …  (20 lines)
  125 more. Run `lore health --all` to see every finding, or `--limit <n>` for more.
Act on these with /lore:correct: point the reference at the record that replaced its target, or remove it.
```

## State — Record count differs from the index

When the records read and the index's count for the same vaults differ, the index line says so,
with both numbers and what to do. The findings are still shown, because they come from the records
on disk, not from the index:

```
Records read differ from the index: 6,402 read, 6,396 indexed. Run `lore reindex`, then run the report again.
```

When the index cannot be opened, the line says the count could not be confirmed rather than
claiming a match:

```
Could not confirm the count against the index: no index found. 6,402 records read.
```

A record whose sidecar or body cannot be read is counted as read but cannot be judged. Each vault
with any such record gets one line after the summary:

```
trailhead: 2 records could not be read and were not checked.
```

## State — Report cannot run

When the report cannot run at all, it prints one `lore:` line to stderr and exits unsuccessfully.
This happens only before any vault has been read: lore's vault config cannot be read, or `--vault`
names a vault that is not configured.

```
lore: cannot run the health report: no vault named 'scratch' is configured (configured: default, trailhead, levr)
```

A vault whose directory cannot be read once the run has started does not stop the run. Its row is
marked skipped with the reason, every other vault is still checked, and the report exits
successfully:

```
scratch     dangling-links  —        —         yes: the vault's directory could not be read
```

In every case other than these two, the report exits successfully, with or without findings.
