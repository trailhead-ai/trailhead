# The window record — camp writes down what is inside a workspace session

The rendered surface for the windows inside a workspace's tmux session: how one comes to exist,
what camp writes down about it at the moment it does, and what the operator sees when any part of
that fails.

This is the first mutate phase of the consolidated session model. It writes the record and never
corrects it. Nothing here reconciles the record against tmux, resurrects a window, or kills one —
a window closed in tmux stays in the record until the reconciliation slice removes it, and that is
the expected end state of this work rather than a defect to patch inside it.

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
has three call sites today; recording a window is the fourth. The floor this slice applies is the
conjunction of two checks (AC21): the directory must be inside the workspace root, and it must
clear the credential store. A directory failing either one is refused and **no window is
recorded** — the refusal is not a warning attached to a recorded window.

## camp chooses the conversation id, because it can never learn it later

A conversation's id cannot be re-derived once its conversation stops running, and this model
deliberately removes the enumeration that would otherwise answer the question. An id not captured
when the window was created is not recoverable.

So camp mints the id and hands it to the harness rather than discovering it afterwards — the
pattern `camp/launch/session.py:737` already follows, generating a UUID and passing it to
`harness.session_launch`. Recording happens at creation for the same reason: there is no later
moment at which the answer is still available.

**The composed command carries neither the remote-control flag nor the visible-name flag.** The
existing composition at `trailhead/harness/claude_code.py:1414-1460` adds both. AC56 forbids both,
and although retiring them everywhere belongs to a later slice, a *new* composition site built to
add them would be building work that slice then has to undo. This slice composes without them.

**The environment scrub is part of the command the window executes, never part of the tmux request
that creates it** (AC60). This is not a stylistic choice: a tmux pane inherits the tmux *server's*
environment, fixed when that server started, so a scrub applied to the request that creates the
window does nothing at all. camp has already been burned by exactly this — a stale
`CLAUDE_CONFIG_DIR` in a long-running tmux server's global environment sent every launched session
to the wrong account's config while the caller's own shell looked correct.

## The recorded window id is the one tmux reports

camp does not predict a window id, it reads back the one tmux assigned (AC18), by asking for it on
the same call that creates the window. A predicted id is a guess that silently diverges, and every
later slice matches recorded windows to live ones on this id alone.

## The binding takes over the operator's window-creation key

camp installs no tmux configuration today — no binding, no hook, no session option anywhere in the
tree. This slice adds the first.

A tmux key binding is **server-global**, not per-session: there is no way to bind a key for one
session and leave every other session on the server untouched. So the binding camp installs is
conditional in its own body — inside a camp workspace session it runs camp's window composition,
and anywhere else it does what the key has always done.

**What counts as "a camp workspace session" is a mark camp sets, never a name it recognizes.**
A session-local option camp writes when it creates the session is unforgeable by an unrelated
session; a session-name pattern is not, and a personal session whose name happens to match would
run camp's composition against a context its owner never intended. The spec accepts that the key
behaves differently *inside* a camp workspace session. It does not accept changing the behaviour
of the operator's own unrelated sessions, and those are the ones a name heuristic puts at risk.

The binding is installed when camp creates a workspace session, and installing it is idempotent —
re-running it over an existing binding is how a session created by an older camp comes into line
rather than a special case. The first install in a server prints one line saying the key now means
something new here, matching how camp already warns about a nested session's prefix conflict
(`camp/attach/prefix_warning.py:34-37`) rather than inventing a second convention for the same
surprise. It is not printed on every keypress.

**The binding is installed through a stable dispatcher, never wired directly to a verb's argv.**
A tmux server outlives camp's own versioning: a server started this morning keeps whatever binding
it was given, so a binding naming today's argv shape keeps naming it after that shape changes or
after this slice is rolled back. Binding to a stable entry point means the indirection absorbs
those changes instead of the operator's window key breaking.

## Taking the binding back

Installing into state camp does not own creates an obligation to be able to undo it. The removal
path is camp's, not a tmux incantation the operator has to be told: it restores the key's default
behaviour without killing the server, because killing the server takes down every session on it —
camp's and the operator's alike — which is the outcome the whole consolidated model exists to
avoid.

Removal is also how a broken binding is recovered. A dispatch that misfires takes the
window-creation key with it, so the way back cannot itself be reached by opening a window.

## State — A window opens and is recorded

The ordinary case, and the only one the operator sees nothing for: the window simply opens.

```
<operator presses the prefix key, then c, inside camp-trailhead-camp-cli>
<a new window opens, rooted at the workspace, running a fresh conversation>
```

camp prints nothing on this path. The window is the feedback, and a line of output would land
inside the new window rather than anywhere the operator was looking.

What is written is a new entry carrying the id tmux reported, the window's name, the working
directory relative to the workspace root, and the conversation id camp chose.

## State — A window holding no Claude conversation

A window opened to run something other than a conversation records the full command line it was
created with instead of a conversation id (AC20).

```
<operator opens a window running a build, inside camp-trailhead-camp-cli>
<the window opens and runs it>
```

The two forms are exclusive: an entry carries a conversation id or a command line, never both and
never neither. A later slice brings such a window back as what it was, which it can only do from
the command line recorded here.

**A recorded command line is scrubbed of credential-shaped material before it is written.** A
command line is exactly where a token, a password flag, or a bearer header shows up, and recording
one verbatim moves a secret out of a shell's volatile history and into a file that outlives the
session. The file is `0o600`, which bounds who can read it but does nothing about how long it
lasts or what else later copies it. Recording the shape of the command without its secrets is
enough for a later slice to bring the window back.

## State — A working directory outside the workspace root

```
$ camp window new --cwd /etc
camp: /etc is outside the workspace
```

No window opens and no entry is written. The refusal names the directory, because the operator
supplied it and needs to know which one was rejected.

## State — A working directory at or under a credential store

Distinct from the state above, and deliberately so: that one is a containment failure, this one is
a policy refusal that would apply even to a directory inside the workspace.

```
$ camp window new --cwd <a path under a declared credential store>
camp: refusing to root a window at or under a credential store
```

No window opens and no entry is written. The message does not echo the offending path back: the
path is the thing the deny-list exists to keep out of output.

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

The directory is unwritable, the disk is full, or the rename fails.

```
$ camp window new
camp: could not write the window record: <the reason>
```

The window is **not** opened. The alternative — open the window and fail to record it — produces
exactly the failure this whole slice exists to prevent: a live conversation whose id is now
unrecoverable, because the only moment it could have been captured has passed.

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

## State — The binding is removed and the key returns to its default

```
$ camp window unbind
camp: the window-creation key is back to its tmux default
```

The key behaves as tmux ships it again, in every session on the server, immediately and without
restarting anything. Running it when no binding is installed reports the same end state rather
than an error: the operator asked for the key to be default, and it is.
