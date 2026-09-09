# Cross-group, cross-account listing

The rendered surface for `camp list` and `camp sessions` once they answer for every
configured group and every declared account on this machine.

Two axes are being added to questions the two verbs already answer:

- **group** — `--all-groups` (`-g`) widens the answer from one group to every configured
  group. Absent, both verbs behave exactly as they do today.
- **account** — not an option. Session enumeration always spans every credential store any
  group declares, plus the default one. There is no way to ask for one account, because an
  account is a group's declaration, not an operator's per-command choice.

Every session row gains two keys in `--json`, present on every invocation: `group` (the
group whose worktree the session's working directory falls in, or `null`) and `account`
(the credential store the row was read from, carried verbatim from the group's
declaration, or the resolved default). The human-readable line is unchanged.

Workspace rows already carry `group`; nothing about their shape changes.

The `account` value is a credential-store directory path, written verbatim as the group
declared it. That makes `--json` output a description of this machine's filesystem layout
across every configured group, not just the one the operator asked about — worth a glance
before it is pasted into a shared bug report.

## State — zero

No configured group has a workspace, and no session is running. Both verbs print nothing
and exit 0 — the existing silence, preserved under the new option.

```
$ camp list -g
$ camp sessions -g
$ echo $status
0
```

```
$ camp list -g --json
[]
```

Silence is the honest answer here: the question was well-formed and the answer is empty.
This is distinct from *collection failure* below, where camp could not determine the
answer at all, and from the case where no groups are configured, which says so:

```
$ camp list -g
camp list: no groups configured — nothing to list
```

## State — one

A single group holding a single workspace and a single running session. The widened form
renders identically to the narrow one — one row is one row.

```
$ camp list -g
audio-real-day /Users/tduffield/.local/state/camp/levr/worktrees/audio-real-day

$ camp sessions -g
09d79961-8036-4350-bd38-351ff97d9eea  interactive  ~/.local/state/camp/levr/worktrees/audio-real-day (audio-real-day-c4)
```

```
$ camp sessions -g --json
[{"ok": true, "session_id": "09d79961-8036-4350-bd38-351ff97d9eea",
  "cwd": "/Users/tduffield/.local/state/camp/levr/worktrees/audio-real-day",
  "kind": "interactive", "controllable": true, "name": "audio-real-day-c4",
  "pid": 15134, "started_at": "2026-09-09T01:25:41.077000+00:00",
  "group": "levr", "account": "~/.claude-levr"}]
```

The row is attributed to `levr` because its working directory resolves there, and to
`~/.claude-levr` because that is the store it was read from. Both are facts about the row,
not inferences about the invocation.

## State — many

Several groups across several accounts, answered in one listing. Rows are ordered by
group, then by the verb's existing within-group order, so the widened answer reads as the
narrow answers concatenated in a stable sequence rather than interleaved by discovery.

```
$ camp list -g
audio-real-day /Users/tduffield/.local/state/camp/levr/worktrees/audio-real-day
e2e-test /Users/tduffield/.local/state/camp/levr/worktrees/e2e-test
staging-to-prod /Users/tduffield/.local/state/camp/levr/worktrees/staging-to-prod
project-maturity /Users/tduffield/.local/state/camp/trailhead/worktrees/project-maturity
testing-practices /Users/tduffield/.local/state/camp/trailhead/worktrees/testing-practices
```

The session listing is the state this design exists for: two accounts, one answer.

```
$ camp sessions -g --json
[{"ok": true, "session_id": "09d79961-...", "group": "levr",      "account": "~/.claude-levr", ...},
 {"ok": true, "session_id": "177a2259-...", "group": "trailhead", "account": null, ...}]
```

`account: null` is the trailhead row's honest answer under the *group declaring no
account* state below.

A session whose working directory falls outside every configured group is still listed —
it is running, and the question was "what is running" — with `group: null`:

```
{"session_id": "1df610b2-...", "cwd": "/tmp/scratch", "group": null, "account": null, ...}
```

## State — collection failure

Enumeration cannot run at all, and the listing says so. camp asks the harness to enumerate
and the harness cannot answer for any store — the binary is missing, every invocation
fails, or the output cannot be decoded.

This is deliberately distinct from *zero*: camp does not know that nothing is running, it
knows it could not find out. Silence would be a confident wrong answer, which is the
failure this whole design exists to remove.

```
$ camp sessions -g
camp: cannot determine running sessions — the harness did not answer
$ echo $status
1
```

```
$ camp sessions -g --json
camp: cannot determine running sessions — the harness did not answer
```

Nothing is printed on stdout in the JSON form. A consumer parsing an empty array would
read "nothing is running", so no array is emitted at all; the nonzero exit and the stderr
line are the whole answer.

The workspace listing has no equivalent failure — it reads manifests off local disk — but
it has its own degradation, below.

## State — per-account partial failure

One credential store cannot be read while the others answer. The answer is the stores that
responded, plus a named notice on stderr for the one that did not. Exit status stays 0:
the rows that came back are real, and a caller that treated a partial answer as a total
failure would lose them.

```
$ camp sessions -g
09d79961-8036-4350-bd38-351ff97d9eea  interactive  ~/.local/state/camp/levr/worktrees/audio-real-day (audio-real-day-c4)
camp: could not read sessions for account ~/.claude-levr — skipping

$ echo $status
0
```

The notice names the account, not the group, because the store is what failed and several
groups can share one.

A consumer reading only stdout would otherwise see a complete-looking answer that is
quietly missing an account's sessions, and decide "nothing else is running" on it. So the
machine-readable form carries the same fact in band — the unreadable store is a row, not an
absence:

```
$ camp sessions -g --json
[{"ok": true,  "session_id": "09d79961-...", "group": "levr", "account": "~/.claude-levr", ...},
 {"ok": false, "account": "~/.claude-levr", "reason": "the harness did not answer"}]
```

Every row carries `ok`, so one field distinguishes the two and a consumer never has to
test for a key's absence — the weaker check, and the one that would not extend to the
cross-host case. A failure row carries only what it can support: the account that failed
and the reason, never session attribution it does not have. This is the same shape the
cross-host work will need for a host that does not answer, which is why it is settled here
rather than invented twice.

The same shape covers a group configuration camp cannot parse: that group is skipped, by
name, and every other group still answers.

```
$ camp list -g
audio-real-day /Users/tduffield/.local/state/camp/levr/worktrees/audio-real-day
camp: cannot read group config home-manager.toml — skipping
```

An unreadable group is a *narrower* answer than the operator asked for, so it must be
visible. A silent skip here would reproduce the defect this design closes, one level up.

## State — group declaring no account

The row states the account it resolved to, never a blank. A group with no `account` key in
its launch block runs under the harness's default credential store — the absence of the
key is how that is expressed, and it is a real answer, not missing data.

In the human form the account is not shown at all; in JSON it is `null`, which reads as
"this group declares none" rather than "unknown":

```
{"session_id": "177a2259-...", "group": "trailhead", "account": null, ...}
```

`null` is used rather than the resolved filesystem path because the path is the harness's
knowledge of where its default store lives, and camp naming it would put a
harness-specific credential location into camp's own output. What camp knows, and states,
is that the group declared nothing.

A row with `group: null` also carries `account: null` — with no group there is no
declaration to report — even though the row was, in fact, read from some store. The
declaration is what the criterion asks for.

## State — refused option combination

A single group named alongside the all-groups option. The two contradict: one narrows to a
named group, the other widens to all of them. Resolving it silently in either direction
means the operator gets an answer to a question they did not ask.

```
$ camp list --group levr --all-groups
camp list: --all-groups and --group name every group and one group at once — pass one or the other
$ echo $status
1
```

```
$ camp sessions --group levr -g
camp sessions: --all-groups and --group name every group and one group at once — pass one or the other
```

The refusal is identical for both verbs and for both spellings of the short option, and it
happens before any group is loaded or any store is read — nothing is enumerated for an
invocation that will not be answered.

Related refusal, same reasoning: with `--all-groups` present, camp never falls through to
the legacy standalone-worktree registry, whose rows carry no group at all. Where the
configured groups cannot answer, it says so rather than answering from a source that
cannot address the question:

```
$ camp list -g
camp: no groups are configured — nothing to list
```
