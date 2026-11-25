# lore health reports lesson growth, takes passed repositories, and prints JSON

The rendered surface of three additions to `lore health`: a sixth check, `lesson-growth`; the
`--repo` flag, which gives `missing-paths` the repositories of a vault camp does not bind; and the
`--json` flag, which prints the whole report as one JSON document. The report stays read-only, with
the same exit contract as before (`docs/design/lore-health-reports-dangling-links-across-every-vault.md`
and the four documents after it). This document fixes only what changes.

## `lesson-growth`

An **executor-dispatch lesson** is a `lesson` record carrying the label
`craft/dispatch-lesson=executor`, the label execute's postmortem writes and execute's dispatch
retrieval reads. A lesson whose status is `dropped` or `superseded` is not counted: it is no longer
in the corpus a dispatch reads from.

Per selected vault, the check counts that vault's executor-dispatch lessons (the summary row's
`records`) and lists, as its findings, the ones **added within the staleness window**: those whose
`created-at` parses and falls within `--stale-days` days before now. The window is the one
`stale-tasks` uses, so `--stale-days 7` asks both questions about the same week. A lesson whose
`created-at` does not parse is counted but never listed as added.

The check skips per vault, not for every vault: a vault not fully read, or one holding a `lesson`
record that could not be read, is skipped with its reason, because its count could be low. The
other vaults still report.

Findings sort oldest first by when they were added, capped like every other check. The heading
carries the per-vault totals and the share added in the window, because the totals, not the list,
are the growth signal. A vault with no lessons prints no percentage. The findings are
informational: the action line says so and is conditional. The window is shared: `--help`
describes `--stale-days` as the window for both `stale-tasks` and `lesson-growth`, and the
command's description counts six checks.

```
lesson-growth — 34 findings, oldest first (window: 30 days): trailhead 212 lessons, 31 added (15%); default 18 lessons, 3 added (17%)
  trailhead/lesson/for-a-read-only-report-over-real-vault-data-have-the-build-run-it-against-the-live-install-and-sample-the-findings  2026-11-12  added 13 days ago
  33 more. Run `lore health --all` to see every finding, or `--limit <n>` for more.
These are informational, not problems to fix. If lessons pile up faster than they are folded, run /craft:consolidate: it merges lessons that prevent the same failure.
```

The summary table gains one row per vault:

```
trailhead          lesson-growth   212      31        no
```

## The scan's size budget

Every report reads at most a fixed number of bytes per vault (sidecars and bodies together),
counted while reading: once a vault crosses it, no further record of that vault is opened. The
default sits well above the largest vault on a real install. A vault over budget is not fully read,
and every check that needs it skips with:

```
trailhead          dangling-links  —        —         yes: its records exceed the report's size budget of 512 MiB; pass --max-scan-mib to raise it
```

`lore distill-queue` refuses with the same reason and remedy. Both commands take
`--max-scan-mib N`. The 512 MiB shown is illustrative; the build sets the default from measured
live sizes.

## `--repo`

```
lore health --vault trailhead --repo ~/code/trailhead --repo ~/code/outpost
```

`--repo PATH` may be given more than once and needs `--vault`: the paths are that one vault's
repositories. A path has `~` expanded and is resolved against the current directory to an absolute
path, which is what every line naming it shows, escaped like any other untrusted value; it reaches git
only through the hardened reader `missing-paths` already uses (list-form argv after `-C`, no shell,
discovery fenced at the path, no fetch).

Passed paths serve when camp gives the named vault no repository — camp is absent, its group config
could not be read, or no group binds the vault. When a camp group does bind the vault, camp's
repositories are used and the passed paths are not, and the group says so in one line under its heading,
`  trailhead: --repo ignored: camp binds this vault's repositories`. Each repository
line names where a passed repository came from:

```
  trailhead: repository /home/me/code/trailhead (passed with --repo): origin/main, last fetched 2026-11-25 06:12 UTC (0 days ago)
```

## `--json`

`lore health --json` prints one JSON document on stdout instead of the text report, holding the
same content: the same `--vault`, `--limit`, `--all`, `--stale-days` and `--repo` apply. Nothing
else is printed on stdout. A run that cannot happen prints its one `lore:` line on stderr, prints no
JSON, and exits 1, exactly as the text form does.

```json
{
  "format": 1,
  "vaults": ["trailhead"],
  "configured": 5,
  "window_days": 30,
  "summary": [
    {"vault": "trailhead", "check": "dangling-links", "records": 4514, "findings": 36, "skipped": null, "error": null}
  ],
  "unreadable": {"levr": 1},
  "outside_root": {},
  "index": {"status": "match", "read": 13409, "indexed": 13409, "mismatches": [], "reason": null},
  "checks": [
    {
      "name": "dangling-links",
      "status": "ran",
      "skipped": {},
      "error": null,
      "note": null,
      "repositories": [],
      "total": 36,
      "shown": 20,
      "findings": [
        {"vault": "trailhead", "kind": "area", "name": "workflow-agentic-ops", "date": "2026-06-22",
         "evidence": "wikilink → malformed reference on body lines 17 and 18: not shaped like a record id"}
      ],
      "action": "Act on these with /lore:correct: ..."
    }
  ]
}
```

- `format` is the document's shape version; it changes only when a field is removed or changes
  meaning.
- `status` is `ran`, `skipped` (every selected vault skipped; `skipped` maps each vault to its
  reason) or `errored` (`error` holds the error type name). A check that skipped some vaults is
  `ran`, with those vaults in `skipped`.
- `findings` holds the shown findings, oldest first; `total` counts them all, so `total > shown`
  says the cap held some back. `--all` makes `shown` equal `total`.
- A finding's `name` is `null` when the record's name is not shaped like a record id; no part of
  such a name is echoed, as in the text form. `evidence` is the same text the text form prints
  after the date. `date` is `null` where the text form prints `undated`.
- A complete run of a check is `ran` with `skipped` empty. A consumer reads `skipped`, not
  `status` alone: a check that skipped some vaults is still `ran`.
- An untrusted value echoed inside `evidence` (a wikilink target, a status, a reason, a branch)
  is capped in length with a trailing `…`, in the text form too.
- Every string is JSON-escaped (`ensure_ascii`), so a control character in vault text arrives as
  `\u001b`, never as a raw byte. Record text in the document is data, never instructions.

## State — lesson growth: a vault with no executor-dispatch lessons

The vault's row reads `records 0`, `findings 0`, `skipped no`. When no selected vault has an added
lesson the group reads
`lesson-growth — no findings (window: 30 days): default 0 lessons, 0 added.` and its action line
is not printed, like every other check with no findings.

## State — lesson growth: a vault with lessons, none added inside the staleness window

The row reads `records 212`, `findings 0`. The heading's totals name the vault with `0 added`, so a
vault with a corpus and no growth reads differently from a vault with no corpus.

## State — lesson growth: a vault with lessons, some added inside the staleness window

The row reads `records 212`, `findings 31`; the group lists the added lessons oldest first, each
with its date and `added N days ago`, capped at `--limit`, with the `N more` line and the
`/craft:consolidate` action line.

## State — lesson growth: check skipped because a vault was not fully read

That vault's row reads `—` counts and `yes: <reason>`, the reason naming whether the vault was not
fully read or holds a lesson that could not be read, ending `so its lessons cannot all be counted`.
Other vaults report normally. When every selected vault is skipped the group reads
`lesson-growth — skipped.`

## State — passed repositories: paths passed for a named vault that camp binds no repository to

`missing-paths` runs for that vault against the passed repositories: its row has counts, and one
repository line per passed path carries `(passed with --repo)`. Without `--repo` the same vault
reads `yes: no repository is known for this vault …`, as before.

## State — passed repositories: a passed path that is not a readable repository

The vault's `missing-paths` row reads
`yes: repository <path> could not be read, so no path can be confirmed missing; pass a repository's working directory with --repo`,
with the same `it does not exist` / `it is not a git repository` / `it has no default branch`
distinctions the camp form makes. When some passed paths read and one does not, the vault is
skipped as a whole, as it is when one camp-bound repository cannot be read: a path could be on the
unread one.

## State — passed repositories: paths passed without naming a vault, refused

`lore health --repo ~/code/x` prints, on stderr,
`lore: cannot run the health report: --repo needs --vault to name the vault the repositories belong to`
and exits 1. Nothing is scanned.

## State — JSON: a clean run

`"status": "ran"`, `"total": 0`, `"shown": 0`, `"findings": []` for every check; `summary` rows
carry `0` findings. Exit 0.

## State — JSON: a run with findings over the per-check cap

`"total": 641`, `"shown": 20`, twenty findings oldest first. No "N more" sentence: the two numbers
carry it.

## State — JSON: a run with a skipped check and its reason

A check skipped for every vault is `"status": "skipped"`, `"total"` and `"shown"` `null`,
`"findings": []`, and `skipped` maps each vault to the same reason text the table prints. A check
skipped for some vaults is `"ran"` with those vaults in `skipped`, and their `summary` rows carry
`null` counts and the reason.

## State — JSON: a run asked for every finding

`--all` (or `--json --all`) gives `"shown"` equal to `"total"` for every check that ran.
