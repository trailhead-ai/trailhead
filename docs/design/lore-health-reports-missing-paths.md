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
- It is not a glob pattern or a placeholder: no `*`, `?`, `[`, `{`, `<` or `>`.
- Its first segment is a top-level entry of one of the vault's repositories, read from the same
  branch the check reads (below). `lore health` in a decision's prose is not a path; `tools/lore`
  is, in a vault whose repository has a top-level `tools`.

Before the lookup, a trailing location is taken off: a line suffix (`:198`, `:12:4`, `:42-50`,
`:17,35`), an anchor (`#L40`, `#L40-L52`, `#symbol`), a pytest node suffix (`::TestThing::test_case`) and a trailing `/`. The
printed path is the candidate as written, so the operator can find it in the record.

A path is **reported** when it is absent from the default branch of **every** one of the vault's
repositories, as a file or as a directory, and is not the tail of a path that is there. A record
often writes a path relative to a package inside the repository (`src/step-up.ts` for
`services/auth/src/step-up.ts`); such a path counts as present when it matches the end of a real
path from a directory boundary. The cost is that a deleted path is missed when another package
still holds the same tail. A path beneath a submodule in any of them counts as
present, because the submodule's own contents are not read. A record naming the same missing path twice is one
finding for that path, located at its first appearance.

## Which repositories a vault has

A vault's repositories are every member repository (`repo_root`) of every camp group whose
`[[lore_scopes]]` binding names that vault. A `repo_root` written with `~` or as a relative path is
resolved against the home directory or the group file's own directory, never the current directory.
A repository shared by several vaults is read once per run. A group that binds two vaults lends its repositories
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

The check names each repository it read by its full path (two repositories can share a directory name), under the group heading and grouped by vault, with the
branch, the time of the last fetch in UTC to the minute and how many days ago that was, or
`no fetch recorded` when the repository has never fetched. The last fetch is the newest
`FETCH_HEAD` across the repository and all its linked worktrees, since a fetch made from a camp
worktree writes that worktree's own `FETCH_HEAD`. The age is whole days, rounded down, and never
negative (a fetch time ahead of the clock reads `0 days ago`); one day reads `1 day ago`. This time is a convenience for judging
staleness, not a guarantee: a stale fetch is visible, not hidden, and the action line says to fetch
first when a repository line is old.

A repository whose directory is missing, is not a git repository, has none of the branches above,
or whose git call fails (git missing, a timeout, an over-large tree, an ownership refusal)
**cannot be read**. Absence can then not be confirmed across every repository, so the check
is skipped for that vault, naming the repository.

No value from a record reaches git. The check lists each repository's tree once
(`git ls-tree -r -t -z <ref>`, without `--name-only` so each entry's type shows a submodule
gitlink) and compares paths in memory. Git runs with its inherited
`GIT_*` environment removed, with no prompts, no optional locks and no lazy fetch, and under a
timeout.

## Skipping one vault, running another

Until now a check was skipped for every vault or none. `missing-paths` can be skipped for one vault
(no repositories, an unreadable repository, or the vault not fully read) and run for the rest. The
summary row of a skipped vault reads `yes: <reason>`, with `—` for records and findings; a vault
the check ran on reads `no` with its counts, so a clean run reads differently from a skipped one.
The other three checks keep their all-or-nothing rule.

The group lists findings from the vaults it ran on. When it ran on no vault at all, the group is
the one line `missing-paths — skipped.`, as for any skipped check, followed by the repository lines
(below). Only the repositories of the vaults the report covers are read: `--vault` narrows what is
read as well as what is judged.

If the check itself fails, or the repositories cannot be read at all, its rows read
`errored: <error type>` for every vault and its group is the one line
`missing-paths — errored: <error type>.`; the other three checks render and the exit status stays 0.

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
or, if the feature it describes was removed, drop or supersede the record. If a repository line
above is old, fetch it first; a path added on a branch not yet merged clears once it merges.`

`body line N` counts lines of the record's body, the text `lore record show` prints.

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
  trailhead: repository /home/dev/camp/trailhead: origin/main, last fetched 2026-11-13 14:53 UTC (0 days ago)
  trailhead: repository /home/dev/camp/outpost: origin/main, last fetched 2026-11-13 14:53 UTC (0 days ago)
```

## State — One missing path

```
missing-paths — 1 finding, oldest first
  trailhead: repository /home/dev/camp/trailhead: origin/main, last fetched 2026-11-13 14:53 UTC (0 days ago)
  trailhead/decision/camp-pane-ownership  2026-08-21  body line 14: `tools/camp/plugins/camp/camp/launch/panes.py` is on no repository's default branch
Act on these with /lore:correct: point the record at where the code now lives, or, if the feature it describes was removed, drop or supersede the record. If a repository line above is old, fetch it first; a path added on a branch not yet merged clears once it merges.
```

## State — Many missing paths, capped

Past 20 findings, the group stops and counts the rest, as every check does:

```
missing-paths — 143 findings, oldest first
  trailhead: repository /home/dev/camp/trailhead: origin/main, last fetched 2026-11-13 14:53 UTC (0 days ago)
  …  (20 lines)
  123 more. Run `lore health --all` to see every finding, or `--limit <n>` for more.
Act on these with /lore:correct: point the record at where the code now lives, or, if the feature it describes was removed, drop or supersede the record. If a repository line above is old, fetch it first; a path added on a branch not yet merged clears once it merges.
```

## State — Skipped, no repository for the vault

A vault no camp group binds is skipped on its own row; the others still run:

```
vault      check           records  findings  skipped
default    missing-paths   —        —         yes: no repository is known for this vault (no camp group binds it); bind it with [[lore_scopes]] in a camp group
trailhead  missing-paths   590      3         no
```

When camp is not installed, or its group config cannot be read, the reason says that instead:
`yes: no repository is known for this vault (camp is not installed)` or
`yes: no repository is known for this vault (camp's group config could not be read); run camp
status to see why`.

## State — Local default branch used, no remote-tracking branch

A repository with no remote-tracking default branch is read at its local one, and the line says so:

```
  trailhead: repository /home/dev/camp/lookout: local main (no remote-tracking default branch), last fetched 2026-11-12 11:26 UTC (1 day ago)
```

## State — Repository unreadable

A repository that cannot be read skips the check for every vault that has it, naming the
repository; its line in the group says why:

```
vault      check           records  findings  skipped
trailhead  missing-paths   —        —         yes: repository /home/dev/camp/outpost could not be read, so no path can be confirmed missing; clone or repair it, or remove it from the camp group

missing-paths — skipped.
  trailhead: repository /home/dev/camp/outpost: could not be read (its directory does not exist)
```

The repository lines stay in a group that skipped every vault, so the reason is visible beside the
check, not only in the summary.

The reasons are `its directory does not exist`, `it is not a git repository`, `it has no
default branch` and `git could not read it`. A vault that was not fully read is skipped with the
same reason the other checks give for it. A repository path from camp's config is printed through the escaper, like every
other value.
