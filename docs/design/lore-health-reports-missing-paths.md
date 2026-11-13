# lore health reports missing paths

The rendered surface of `lore health`'s fourth check, `missing-paths`, and of the one change it
brings to the report's frame: a check can now be skipped for one vault and run for another. The
report stays read-only, with the same commands, flags and exit contract as before
(`docs/design/lore-health-reports-stale-tasks.md` and the two documents it cites). This document
fixes only what changes.

## What the check judges

A **live decision, ADR or area profile** is judged: a `decision`, `adr` or `area` record whose
status is `active`. No other kind is judged; specs, tasks and sessions never are. "Records checked"
in the summary counts the records judged.

A judged record's **paths** are taken from two places:

- **Inline code in its body.** Each single-backtick span outside a fenced code block. A fenced
  block is example text, not a claim about the repository.
- **Its related files.** Each entry of the sidecar's `related-files-or-folders` list.

A candidate is treated as a path only when all of these hold:

- It is one token: no whitespace, no control character, at most 300 characters.
- It does not start with a home, root or variable marker (`~`, `/`, `$`, `%`), and is not a URL
  (anything carrying `://`, or starting `mailto:`).
- It is not a glob pattern: no `*`, `?`, `[` or `{`.
- Its first segment is a top-level entry of one of the vault's repositories, read from the same
  branch the check reads (below). `lore health` in a decision's prose is not a path; `tools/lore`
  is, in a vault whose repository has a top-level `tools`.

Before the lookup, a trailing location is taken off: a line suffix (`:198`, `:12:4`), a line anchor
(`#L40`, `#L40-L52`), a pytest node suffix (`::TestThing::test_case`) and a trailing `/`. The
printed path is the candidate as written, so the operator can find it in the record.

A path is **reported** when it is absent from the default branch of **every** one of the vault's
repositories, as a file or as a directory. A record naming the same missing path twice is one
finding for that path, located at its first appearance.

## Which repositories a vault has

A vault's repositories are every member repository (`repo_root`) of every camp group whose
`[[lore_scopes]]` binding names that vault. A group that binds two vaults lends its repositories
to both; a vault bound by two groups gets the repositories of both.

When camp is not installed, no group binds the vault, or the groups cannot be read, the vault has
no repositories and the check is skipped for it, with the reason. It is never reported as zero
findings.

## Which branch is read, and when it was fetched

For each repository the check reads one branch, without fetching and without touching the working
tree:

1. The remote-tracking default branch: `origin/HEAD` when set, otherwise `origin/main`, otherwise
   `origin/master`.
2. When none exists, the local default branch: `main`, otherwise `master`, and the report says the
   local branch was used.

The check names each repository it read, under the group heading, with the branch and the time of
the last fetch (the modification time of the repository's `FETCH_HEAD`, in UTC to the minute), or
`no fetch recorded` when the repository has never fetched. A stale fetch is visible, not hidden.

A repository whose directory is missing, is not a git repository, or has none of the branches
above **cannot be read**. Absence can then not be confirmed across every repository, so the check
is skipped for that vault, naming the repository.

No value from a record reaches git. The check lists each repository's tree once
(`git ls-tree -r -t --name-only`) and compares paths in memory.

## Skipping one vault, running another

Until now a check was skipped for every vault or none. `missing-paths` can be skipped for one vault
(no repositories, an unreadable repository, or the vault not fully read) and run for the rest. The
summary row of a skipped vault reads `yes: <reason>`, with `—` for records and findings; a vault
the check ran on reads `no` with its counts, so a clean run reads differently from a skipped one.
The other three checks keep their all-or-nothing rule.

The group lists findings from the vaults it ran on. When it ran on no vault at all, the group is
the one line `missing-paths — skipped.`, as for any skipped check.

## Layout changes

1. **The summary** gains a fourth row per vault, `missing-paths`, after `stale-tasks`.
2. **The groups** gain a fourth group, `missing-paths`, last. Under its heading, one line per
   repository read, then the findings, oldest first by the record's last update, at most 20 unless
   more were asked for, then its action line.

Each finding line opens with the record and date, exactly as in every other check, then says where
the path sits and the path:

```
  trailhead/decision/camp-pane-ownership  2026-08-21  body line 14: `tools/camp/plugins/camp/camp/launch/panes.py` is on no repository's default branch
  trailhead/adr/notes-are-records  2026-06-02  related file 2: `tools/lore/lore/notes.py` is on no repository's default branch
```

The path is printed between backticks, through the same control-character escaper every line goes
through.

The action line: `Act on these with /lore:correct: point the record at where the code now lives,
or, if the feature it describes was removed, drop or supersede the record.`

## State — No missing paths

Every path named by every live decision, ADR and area profile exists:

```
vault      check           records  findings  skipped
trailhead  dangling-links  4,511    0         no
trailhead  retired-links   1,630    0         no
trailhead  stale-tasks     402      0         no
trailhead  missing-paths   590      0         no

…

missing-paths — no findings.
  repository trailhead: origin/main, last fetched 2026-11-13 14:53 UTC
  repository outpost: origin/main, last fetched 2026-11-13 14:53 UTC
```

## State — One missing path

```
missing-paths — 1 finding, oldest first
  repository trailhead: origin/main, last fetched 2026-11-13 14:53 UTC
  trailhead/decision/camp-pane-ownership  2026-08-21  body line 14: `tools/camp/plugins/camp/camp/launch/panes.py` is on no repository's default branch
Act on these with /lore:correct: point the record at where the code now lives, or, if the feature it describes was removed, drop or supersede the record.
```

## State — Many missing paths, capped

Past 20 findings, the group stops and counts the rest, as every check does:

```
missing-paths — 143 findings, oldest first
  repository trailhead: origin/main, last fetched 2026-11-13 14:53 UTC
  …  (20 lines)
  123 more. Run `lore health --all` to see every finding, or `--limit <n>` for more.
Act on these with /lore:correct: point the record at where the code now lives, or, if the feature it describes was removed, drop or supersede the record.
```

## State — Skipped, no repository for the vault

A vault no camp group binds is skipped on its own row; the others still run:

```
vault      check           records  findings  skipped
default    missing-paths   —        —         yes: no repository is known for this vault (no camp group binds it)
trailhead  missing-paths   590      3         no
```

When camp is not installed, or its group config cannot be read, the reason says that instead:
`yes: no repository is known for this vault (camp is not installed)` or
`yes: no repository is known for this vault (camp's group config could not be read)`.

## State — Local default branch used, no remote-tracking branch

A repository with no remote-tracking default branch is read at its local one, and the line says so:

```
  repository lookout: local main (no remote-tracking default branch), last fetched 2026-11-12 11:26 UTC
```

## State — Repository unreadable

A repository that cannot be read skips the check for every vault that has it, naming the
repository; its line in the group says why:

```
vault      check           records  findings  skipped
trailhead  missing-paths   —        —         yes: repository outpost could not be read, so no path can be confirmed missing

missing-paths — skipped.
```

The reasons are `its directory does not exist`, `it is not a git repository` and `it has no
default branch`. A repository path from camp's config is printed through the escaper, like every
other value.
