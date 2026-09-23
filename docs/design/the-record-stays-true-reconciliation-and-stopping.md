# The record stays true — reconciliation and stopping

The rendered surface for two things that happen to a workspace's window record after it has
been written: camp corrects it against what tmux actually holds, and the operator puts the
workspace down with `camp stop <slug>`.

This is the second mutate phase of the consolidated session model. It corrects the record and
it kills the session. It resurrects nothing, and it does not retire `camp kill`, which still
addresses a single conversation's session by reference and is a later slice's to remove.

## Reconciliation corrects the record; it never extends it

The record written when a window opens is right at that moment and drifts from then on: the
operator closes a window, renames one, opens one and never starts a conversation in it. tmux is
authoritative while it is alive, so camp corrects the record to tmux at two moments — when the
door connects to a running session, and immediately before a stop kills one — and never
between them. The listing reads tmux and prints what it sees, and leaves the record alone: a
read verb that writes is a verb whose side effects nobody expects.

A recorded window is matched to a live one on the tmux window id and nothing else. Names are
not unique and the operator changes them; indexes are reused the moment a window closes. A
window id is unique for the life of the tmux server, which is exactly the life of the record's
claim on it.

Three corrections and one deliberate non-correction:

- A recorded window whose id tmux no longer reports is dropped.
- A recorded window whose name differs from tmux's takes tmux's name.
- A recorded conversation id, working directory, and command line are never touched. camp
  cannot re-derive a conversation id once it is gone, so reconciliation is not permitted to
  clear one for any reason, including a window whose conversation has exited.
- A live window with no record entry is **not** added. camp did not compose it and knows
  nothing true to write about it; the spec accepts this as the cost of an uncomposed window.

Reconciliation is silent when it has nothing to change, and writes nothing to disk either —
a record that already agrees with tmux is not rewritten just to prove it was read.

## The seam learns to list windows

Nothing in camp's tmux seam enumerates a session's windows today; the listing counts them and
the composer creates one. Reconciliation and the stop preview both need the same answer, so the
seam gains one method that asks `list-windows` for each window's id, its active pane's current
directory and foreground command, and its name — in that order, tab-separated, name last. tmux
refuses a window name containing a tab or a newline (measured on 3.7c), so the split is
unambiguous without quoting.

The answer is tri-state like every other question the seam asks: a list, `None` when tmux
answers that no such session exists, and unanswered when tmux did not answer at all. A
reconciliation that cannot get an answer changes nothing; "could not tell" is never read as
"no windows".

## Stopping states its cost, then pays it, then checks

`camp stop <slug>` is the door's inverse and shares its front half: the same slug resolution,
the same refusals for a slug naming no workspace, the same group-qualified session name. It
takes a slug and only a slug — a workspace has one session, so there is nothing else to name.

In order:

1. Ask tmux whether the session is running. Not running is a normal end state, reported and
   exited zero; unanswered is a refusal.
2. Reconcile the record against tmux, under the workspace lock, and name what changed.
3. Print what is about to go: how many windows, which hold live conversations, which hold some
   other foreground process. This is read from tmux's own view of each window's foreground
   command, cross-referenced with the record — a recorded conversation whose process has since
   exited is not "live".
4. Kill the whole session, never one window or pane.
5. Ask tmux again, on a bounded poll, until the session is gone. Success is tmux saying it is
   gone; the kill command's own exit status proves nothing, because a stop has already been
   observed reporting success while its process survived.

The preview is printed before step 4 and is not a prompt. The operator asked to stop; the
preview is what they see on the way, and a non-interactive caller gets the same facts in the
JSON object. A stop that needs confirmation is a different verb this spec does not ask for.

The record is written in step 2 and never again. A stop that fails at step 5 leaves the
reconciled record in place, and the workspace is exactly as resurrectable as it was — the
reconciliation already happened, the session is still up, and nothing was lost.

## One outcome, three renderings

The stop reports in the same three forms the door does: one human line on stdout, a JSON
object under `--json`, and an exit status. The outcomes are closed:

| outcome | meaning | exit |
|---|---|---|
| `stopped` | the session was killed and tmux confirms it is gone | 0 |
| `not-running` | the workspace had no session; nothing to do | 0 |
| `still-present` | the kill was issued and the session is still there after the poll | 1 |
| a refusal | no such workspace, tmux unanswered | 1 |

The JSON object carries the preview too — window count, the conversation ids of windows whose
conversation is live, and the names of windows holding another foreground process — so a caller
that stopped a workspace can tell what it took down without having read the human lines.

## State — The record agrees with tmux and reconciliation prints nothing

The operator attaches to a running workspace whose record matches tmux window for window.

```
$ camp attach camp-cli
connected camp-trailhead-camp-cli
```

Nothing about the record is printed and the record file is not rewritten. Silence is the
signal that nothing was wrong, and a line saying "nothing changed" on every attach would train
the operator to ignore the line that one day says something did.

## State — A window closed in tmux is dropped from the record

The operator closed a window in tmux since the record was last written.

```
$ camp attach camp-cli
camp: window record: dropped @3 "review" (closed in tmux; conversation 41aa…)
connected camp-trailhead-camp-cli
```

The correction goes to stderr, one line per change, before the door's own outcome line.
The dropped entry's conversation id is printed in the line and goes with it: that conversation
is still resumable from the harness's own store, and the id in the operator's terminal is the
recovery path if the drop was wrong, but camp no longer claims a window for it, because there
is none. No backup copy of the record is kept; nothing would read one.

## State — A window renamed in tmux carries its new name and keeps its conversation id

```
$ camp attach camp-cli
camp: window record: renamed @1 "first" -> "planning"
connected camp-trailhead-camp-cli
```

The entry keeps its window id, its working directory, and its conversation id; only the name
moves. A rename is the operator labelling their work, not replacing it.

## State — The listing reads tmux without writing the record

```
$ camp list
camp-cli     running (3 windows)
lore-sync    no session
```

The row's window count is tmux's, not the record's, and the record file is byte-for-byte what
it was before the listing ran — whether or not it agrees with tmux. The listing is a read verb
and stays one.

## State — The record is unparseable when reconciliation runs

The file exists and cannot be read as a record.

```
$ camp attach camp-cli
camp: window record at /…/worktrees/camp-cli/windows.json could not be read; not reconciled
connected camp-trailhead-camp-cli
```

Reconciliation cannot correct what it cannot parse, and must not replace it with an empty
record either — that would turn "camp cannot say what is in this session" into "nothing is in
this session", the fail-open answer the record's reader was built to refuse. The door still
connects: an unreadable record is a workspace camp cannot describe, not one it cannot enter.
The same holds on a stop: the preview says the record could not be reconciled, and the stop
proceeds, because the record was already lost before the stop was asked for.

A readable record that camp cannot reach in time is reported the same way and treated the same
way. The workspace lock is held by the background provisioner for as long as it takes to add
every member's worktree, so reconciliation waits a bounded moment for it and then gives up
rather than hanging the door:

```
$ camp attach camp-cli
camp: window record at /…/worktrees/camp-cli/windows.json is locked by another camp process; not reconciled
connected camp-trailhead-camp-cli
```

The record is untouched, the door connects, and the next attach or stop reconciles it.

## State — Stop previews the windows, live conversations, and other foreground processes it is about to kill

```
$ camp stop camp-cli
stopping camp-trailhead-camp-cli: 3 windows
  @1 "planning"  conversation 8f2c…  live
  @3 "review"    conversation 41aa…  exited
  @5 "build"     foreground: pytest
stopped camp-trailhead-camp-cli
```

One line per window, then the outcome. "live" means a recorded conversation whose window's
foreground command is not a shell; "exited" is a recorded conversation whose window is now at a
shell; "foreground" names any other process the operator would lose. A window at an idle shell
shows neither. The shell test is the only classifier: camp never matches on the conversation
process's own name, because that name is the installed version string and changes on every
update, and a stale positive match would report a live conversation as exited. Under `--json` the same facts are fields of the
object, and the human lines are not printed.

## State — Stop on a workspace with no running session

```
$ camp stop lore-sync
not running camp-trailhead-lore-sync
```

Exit zero. The record is not touched — there is no tmux to reconcile it against, and the record
is, from this moment, the only truth about that workspace until something resurrects it. A
second `camp stop` of a stopped workspace is the same line again: stopping is idempotent, like
the door's connect.

## State — Stop kills the whole session and confirms it is gone

```
$ camp stop camp-cli
stopping camp-trailhead-camp-cli: 1 window
  @1 "camp-cli"
stopped camp-trailhead-camp-cli
```

`stopped` is printed only after tmux has answered that the session no longer exists. The kill
is `kill-session` against the exact session name; killing a window or a pane would leave a
session behind that the door would then connect to, with nothing in it the operator recognises.

## State — The session survives the kill

tmux accepted the kill and, after the poll, still reports the session.

```
$ camp stop camp-cli
stopping camp-trailhead-camp-cli: 2 windows
  …
camp stop: camp-trailhead-camp-cli is still present after kill-session; run camp stop again, or kill it in tmux with tmux kill-session -t =camp-trailhead-camp-cli
```

Exit one. The record was reconciled in step 2 and is left as reconciled; the session is still
up and the workspace is exactly as resurrectable as before the stop. The operator is told the
truth rather than a success the kill command's exit status would have implied.

## State — A window changes in tmux between the preview and the kill

The operator, or a process, opens or closes a window in the moment between the preview being
printed and the session being killed.

```
$ camp stop camp-cli
stopping camp-trailhead-camp-cli: 2 windows
  …
stopped camp-trailhead-camp-cli
```

The stop still succeeds and still reports `stopped`: the kill is of the whole session, so a
window that appeared after the preview goes with it, and one that closed was already gone.
The preview is a statement of what tmux held when camp looked, and the reconciled record is
of that same moment; neither is re-read after the kill, because there is nothing left to read.
A window opened in that gap was never recorded, exactly as an uncomposed window never is.

If tmux does not answer the preview's own listing, or lists a row camp cannot read, the
preview does not guess:

```
$ camp stop camp-cli
camp: could not list the windows of camp-trailhead-camp-cli; stopping without a preview
stopped camp-trailhead-camp-cli
```

No count is printed, because "could not tell" is never read as "no windows", and under
`--json` every window field of the object is null rather than zero or empty. The kill the
operator asked for still proceeds.
