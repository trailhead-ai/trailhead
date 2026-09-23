# The window record — camp writes down what is inside a workspace session

The rendered surface for the windows inside a workspace's tmux session: how a conversation in one
comes to be recorded, what camp writes down about it at the moment it starts, and what the operator
sees when any part of that fails.

This document covers writing the record. Reconciling it against tmux, resurrecting a workspace from
it, and stopping one are described in their own documents.

## The record is a second file beside the manifest

A workspace already owns one state file: its provisioning manifest at
`central_state_dir(group)/worktrees/<slug>/manifest.json`, whose schema, reader, and sole writer
live in `camp/group/manifest.py:79-206`. The window record is a *separate* file in that same
directory (AC30), for the reason the spec's own interface list gives: the manifest describes how a
workspace was built, and the window record describes what is running inside it. They change on
completely different occasions and have different failure consequences — a workspace whose
manifest is unreadable cannot be provisioned, while a workspace whose window record is unreadable
is merely a workspace camp cannot say the contents of.

The record is written through the same discipline the manifest already uses, not a second one
invented beside it: a temporary file in the destination directory, then `os.replace` over the
previous file, then `0o600` (`camp/group/manifest.py:159-176`). That sequence is what makes a
concurrent reader see either the whole old file or the whole new one and never a half-written one.

## The lock is the one camp already has, and it is not reentrant

Mutations serialize on `reconcile_lock(ws_dir)` (`camp/group/manifest.py:247-297`) — the same
per-workspace `flock` every provisioning writer already takes, held on a lockfile that lives
*beside* the workspace directory rather than inside it, because teardown removes the directory
whole and a lock inside it would lose its inode mid-critical-section.

That lock is **not reentrant**: re-acquiring it on a second descriptor in the same process
deadlocks, which `camp/group/manifest.py:390-396` already states and already designs around by
exposing an `_unlocked` variant for callers that hold it. The window record follows that same
split rather than inventing a second convention — a locking entry point, and an unlocked one for
code already inside the critical section. This is the single likeliest way to introduce a hang,
so the split is structural rather than advisory.

## Every recorded directory is workspace-relative

The record stores each window's working directory as a path relative to the workspace root, and
writes no absolute path at all (AC29). Two reasons, and only the second is about tidiness: a
workspace's absolute location is a function of the machine it is on, so an absolute path is wrong
the moment the record is read anywhere else; and a relative path cannot silently carry a home
directory or an account name into a file that is later read, printed, or transferred.

## The directory floor applies here too

`assert_not_a_credential_store` (`camp/launch/eligibility.py:268-294`) refuses a directory at,
under, or above any credential-store entry, unconditionally and independent of any allowlist. It
applies to recording a window too. The floor is the conjunction of two checks (AC21): the
directory must be inside the workspace root, and it must clear the credential store. A conversation
whose directory fails either one is **not recorded** — never recorded with a warning attached.

## camp records the conversation id when the harness starts it

A conversation's id cannot be re-derived once its conversation stops running, and this model
deliberately removes the enumeration that would otherwise answer the question. An id not captured
while the conversation was starting is not recoverable.

So camp learns the id at the one moment it is certain: when the harness starts the session. The
camp plugin ships a session-start hook (`hooks/hooks.json`, declared in camp's
`capabilities.toml`), so it fires for every session wherever the plugin is enabled, however the
operator started it — a plain `claude` in any pane, a resume, a cleared context. The harness hands
the hook a payload naming the session; only the harness knows that payload's shape, so camp asks
the harness seam for the id (`session_start_hook_session_id`) and never parses it itself.

**The hook finds its window through tmux, never through a name.** tmux exports `TMUX_PANE` to every
process in a pane, and the hook inherits it from the session that ran it. camp asks tmux where that
pane sits, then reads the `@camp_*` session-local options workspace-session creation writes. Only a
session carrying `@camp_workspace` is camp's; a pane in any other session, or no tmux at all,
records nothing and costs one environment lookup.

**The hook is silent.** It runs inside the start of every session on the machine, most of which have
nothing to do with camp, so it never prints, never fails the session, and bounds every wait — a
workspace lock it cannot take within a few seconds is given up on rather than waited for.

## The session carries the account, not a composed command

A tmux pane inherits the tmux *server's* environment, fixed by whichever process started the server.
camp has already been burned by exactly this — a stale `CLAUDE_CONFIG_DIR` in a long-running tmux
server's global environment sent every launched session to the wrong account's config while the
caller's own shell looked correct — and a server started from inside an agent session hands every
pane that session's markers as well.

So the workspace session states its own environment when camp creates it: the harness's scrub as
removals (`tmux set-environment -r`) and the group's declared account as assignments. A session's
environment overrides the server's for every pane the session starts, so every pane — the first one,
and any the operator opens by hand — starts on the group's account with the parent session's markers
gone, whoever started the server. The first pane started before the session could carry anything,
so camp restarts it once the environment is stated. A declared account camp cannot bind refuses the
create; a session tmux will not give its whole environment to is killed rather than left running on
the wrong account.

## The recorded window id is the one tmux reports

camp does not predict a window id; it records the one tmux reports for the pane the conversation
started in (AC18). A predicted id is a guess that silently diverges, and every later slice matches
recorded windows to live ones on this id alone.

## One conversation per window

A window holds one conversation at a time. A later conversation started in the same window — a
resume, a cleared context, a fresh launch after the last one exited — replaces the window's entry
rather than adding a second one: resurrecting both would bring back a window the operator had
already moved on from.

## State — A conversation starts and is recorded

The ordinary case, and the only one the operator sees nothing for: the conversation simply starts.

```
<operator opens a window inside camp-trailhead-camp-cli and runs claude>
<the conversation starts; nothing else happens>
```

camp prints nothing on this path — the hook is silent by construction (see above).

What is written is an entry carrying the window id tmux reported, the window's name, the working
directory relative to the workspace root, and the conversation id the harness reported.

## State — A window holding no Claude conversation

The record's other entry form carries a full command line instead of a conversation id (AC20), for
a window that runs something other than a conversation. The two forms are exclusive: an entry
carries a conversation id or a command line, never both and never neither. Resurrection brings such
a window back as what it was, from the command line recorded.

The session-start hook only ever writes the conversation form; nothing camp ships writes the
command-line form today.

**A recorded command line is scrubbed of credential-shaped material before it is written.** A
command line is exactly where a token, a password flag, or a bearer header shows up, and recording
one verbatim moves a secret out of a shell's volatile history and into a file that outlives the
session. The file is `0o600`, which bounds who can read it but does nothing about how long it
lasts or what else later copies it.

## State — A conversation started outside the workspace root

```
<operator runs claude from /etc in a pane of camp-trailhead-camp-cli>
<the conversation starts; nothing is recorded>
```

The window floor is the conjunction of two checks (AC21): the directory must be inside the workspace
root, and it must clear the credential store. A conversation whose pane directory fails the first is
not recorded. The conversation itself is untouched — camp has no business refusing a session the
operator started, only declining to promise it can bring it back.

## State — A conversation started at or under a credential store

Distinct from the state above, and deliberately so: that one is a containment failure, this one is
a policy refusal that would apply even to a directory inside the workspace.

```
<operator runs claude from ~/.ssh inside the workspace>
<the conversation starts; nothing is recorded>
```

Nothing is written, and nothing names the path.

## State — Two windows recorded at once

Two windows opened close enough together that both writes are in flight.

```
<two windows open; both appear in the record>
```

Both entries survive. This is the state the `os.replace` discipline and the workspace lock exist
for: without the lock, the second write is a read-modify-write over a snapshot taken before the
first landed, and the first window silently disappears from the record while remaining perfectly
alive in tmux. The record would then disagree with tmux in the one direction nothing later
corrects — reconciliation removes windows tmux no longer has, and never restores one it never saw.

## State — The record cannot be written

The directory is unwritable, the disk is full, the rename fails, or the workspace lock is held past
the hook's bound (the background provisioner holds it across every member's `git worktree add`).

```
<the conversation starts; nothing is recorded>
```

The conversation is not stopped — the hook runs after the harness has already started it, and a
hook that failed the session would charge camp's problem to the operator's work. The id is lost for
that conversation; the next conversation started in the window records normally.

The partial temporary file is removed rather than left beside the record, matching
`camp/group/manifest.py:159-176`.

## State — The record is missing

A workspace whose session has never had a window opened in it has no record file at all. This is
not an error and not a migration: the spec settles that no backfill step is required, because a
workspace with no record reads correctly as one whose session has not been created, which is the
state every pre-existing workspace is in.

```
$ camp status camp-cli
<the usual status output, unchanged>
```

A missing record reads as "no windows recorded", never as a failure.

## State — The record is unparseable or partially written

The file exists and is not valid — truncated by a full disk, corrupted, or hand-edited.

```
$ camp status camp-cli
<the usual status output, unchanged>
```

A workspace's status, path, activation, and removal commands keep returning their usual exit
status and all of the output that does not come from the record (AC32). They do that by not
consulting the record at all where they do not need it — `camp path` already resolves a workspace
without reading its manifest, and that is the pattern followed here.

**An unreadable record is not silent.** The commands above keep working, but a workspace whose
record cannot be read is a workspace camp has quietly stopped being able to describe, and nothing
would say so until a later slice refuses to resurrect it — by which point the operator has lost
the context of what was running. camp surfaces it where a workspace's health is already reported,
without changing any command's exit status.

**An unreadable record is never read as an empty one.** The distinction matters at the next
slice's boundary, where resurrection must refuse against a record it cannot parse rather than
bring up a session with no windows (AC33), and it matters as a matter of standing discipline:
camp has repeatedly shipped bugs where a probe that could not answer returned the same value as a
probe that answered "no", and the caller read that as permission. "Cannot tell" and "nothing
there" are different answers and stay different values.
