# The door resurrects a workspace from its record

The rendered surface for the door's third arm: `camp attach <slug>` against a workspace whose
tmux session is gone but whose window record is not. The record was written when the windows
were composed and kept true by reconciliation and by stopping; this is where it is read back.

This is the last non-polish slice of the consolidated session model. It resurrects. It does not
retire `camp kill` or the `--resume` launch flavor, and it does not change what the create and
connect arms already do.

## The door decides among three arms, and the record decides the third

The door already asks tmux one question — is the workspace's session running — and answers
`connected` when it is. When it is not, the create arm used to be the only other answer. Now
the record is read first, and what it holds picks the arm:

| record | arm |
|---|---|
| missing, or present with no windows | create — one login-shell window, unchanged |
| present with windows | resurrect — one window per entry, in recorded order |
| present but unparseable | refuse — no session at all |

Liveness is still tmux's answer and only tmux's. The record never says whether a session is
running; it says what a session held. A record with windows and a running session is the connect
arm, reconciled as before, exactly as if the record had been empty.

## What a resurrected window is

A resurrected window is a shell at the recorded directory, showing on its first lines what the
window used to be and how to get it back. It is never the process itself. camp starts no Claude
conversation on the way back, because the operator put the workspace down deliberately and
bringing every conversation up at once would undo that decision and spend the operator's
sessions before they asked.

The window's command is a small shell wrapper: print the lines, then `exec` the operator's shell
under the same environment scrub the composed conversation ran under. The scrub is on the shell,
not on the tmux request, so a resume the operator types into that window inherits a clean
environment — the same reasoning that put the scrub inside a composed window's command line.
The shell that ends up in the pane is the same `$SHELL` tmux would have given a plain window, so
the stop preview's classifier reads a resurrected conversation window as `exited`, which is
what it is.

- A window whose entry carries a conversation id shows the id and one line: the exact resume
  command the harness boundary composes for that id. camp's core never spells that command; it
  asks the harness for the argv and prints it joined for a shell. A group with no harness gets
  the id and a line saying camp cannot compose a resume for it.
- A window whose entry carries a command line shows that command line, verbatim, and runs
  nothing. Re-running an arbitrary recorded command is a spec non-goal.
- A window whose recorded conversation has no transcript behind it on this machine is a shell
  that says so. The check is the harness's own transcript lookup, keyed on the window's
  recorded directory, made before the window is created. Not an error: the transcript was
  retained for as long as the harness retains transcripts, and camp reports what it found.

The lines are passed to the wrapper as arguments, never interpolated into its script, and the
window name goes to tmux through the seam's `-n` operand like every composed window. Nothing
recorded is ever pasted into shell source. Every line is also escaped whole, the way the door's
own stdout and stderr lines are, before it becomes an argument: the record is a file any process
running as the operator can write, and a control or escape sequence in a recorded command line
or conversation id must reach the terminal as its visible spelling, never as the sequence.

## The directory floor is checked again, before tmux is asked

tmux does not refuse a `-c` directory that no longer exists; measured on 3.7c it silently opens
the window at `$HOME` and exits zero. So camp decides for itself, per entry, before any tmux
call:

- The recorded directory is resolved under the workspace root. If the resolved path is not at
  or under the root, or is at, under, or above a credential store, the entry is dropped. The
  first refusal names the path; the second does not, for the reason the composed-window
  refusal already gives.
- If the resolved directory does not exist, the entry is dropped. The spec settled this over
  keeping the entry for a directory that might return.

A dropped entry leaves the record on the same write that re-stamps the survivors, and its line
carries the conversation id when it had one — the same recovery path reconciliation's dropped
line gives.

## Order, ids, and the record after

Windows are created in record order. The first surviving entry rides the session-creating call
itself, which names the window, roots it, and runs the wrapper, and reads back the window id on
that same call; every later entry is one `new-window`, id read back the same way. The order the
record held is the order tmux holds.

When every entry was dropped there is nothing to resurrect and still a session to bring up: the
door creates the plain login-shell window the create arm makes and reports `resurrected` with
zero windows restored and every entry dropped, because the record was read and acted on and
saying `created` would hide that.

Every window tmux created is then written back into the record with the id tmux assigned,
replacing the id it had, under the workspace lock, as a read-modify-write: entries that were
dropped or that failed to come back are removed, and an entry that appeared in the record while
the windows were being created — a conversation can start in a pane the moment the session
exists — is kept.
The record after resurrection is the workspace as it now is, so the reconciliation that runs on
the next connect finds every window it lists and drops none of them.

A failed write is one line on stderr naming the record, and the outcome is still what tmux
holds. The next connect's reconciliation would then drop every entry, printing each
conversation id; that is the same recovery path as any other stale record, and the session is
up either way.

## Failure stays inside the window that failed

Each window after the first is its own tmux call, and one that tmux answers with a non-zero exit
or does not answer at all is a window that did not come back. The others are created regardless,
the failed one is removed from the record on the write above, and its line on stderr carries the
conversation id when it had one. The door then reports how many did not come back, and the exit
status is the partial one.

The first window is different only because it rides the session-creating call: if that fails
there is no session, and the door reports the create failure it already reports today.

Following the printed resume instruction is the operator's action inside a shell camp handed
over; a conversation the harness refuses to re-enter fails in that shell, in that window, with
the harness's own words on the screen. camp wraps nothing around it, so nothing can hide it.

## What the door reports

`resurrected` joins `created` and `connected` as the door's third success word, and the JSON
object carries what happened to the windows:

```json
{"ok": true, "outcome": "resurrected", "slug": "camp-cli", "group": "trailhead",
 "workspace_path": "/…/worktrees/camp-cli", "tmux_session": "camp-trailhead-camp-cli",
 "attached": true, "windows": {"restored": 2, "failed": 1, "dropped": 0}}
```

Exit status tells the three apart, as the spec's AC16 asks:

| outcome | exit |
|---|---|
| `created`, `connected`, `resurrected` with nothing failed | 0 |
| a refusal, including an unparseable record | 1 |
| `resurrected` with one or more windows failed | 2 |

The terminal is handed over right after these lines, so on an interactive attach they live in the
outer terminal's scrollback, not in tmux; the conversation ids they carry are also what the
harness's own resume listing shows, so nothing on those lines is the only copy. Exit 2 means
partial here and only here; the retired ref-addressed verbs use 2 for an ambiguous reference,
and the meaning is per verb.

Dropped entries do not make the outcome partial: a dropped entry is camp declining to create a
window it has decided must not exist, reported on its own line, not a window that failed. A
caller that cares reads `windows.dropped`.

`camp new` goes through the same door and folds the new states the way it folds the others: a
fresh workspace has no record, so it never resurrects, but the fold is exhaustive and an
unparseable record on a re-run reports workspace-only, as every other door failure does there.

## State — The door resurrects a workspace whose record has windows and whose session is gone

```
$ camp attach camp-cli
resurrected camp-trailhead-camp-cli (3 windows)
```

The terminal is handed to the session, which holds three windows in the recorded order, each
named as recorded and rooted where recorded. The record on disk now carries the ids tmux
assigned. Nothing else is printed: there was nothing to correct and nothing that failed.

## State — A resurrected window with a conversation id shows the id and one resume instruction

The window's first lines, above the shell prompt:

```
camp: this window held conversation 8f2c1a3e-…
camp: resume it with: claude --resume 8f2c1a3e-…
tduffield@Mac camp-cli %
```

The second line is the harness's argv, joined for a shell, and nothing else. For a group whose
harness camp cannot resolve the second line reads `camp: no harness is configured for this
group, so camp cannot compose a resume command`, and the id is still on the first line.

## State — A resurrected window with no conversation id shows its recorded command without running it

```
camp: this window was opened with: pytest -x tests/
tduffield@Mac camp-cli %
```

The shell is at the recorded directory. Nothing was executed.

## State — A recorded conversation with no transcript comes up as a shell and says so

```
camp: this window held conversation 41aa7c02-…, but no transcript for it exists on this machine
tduffield@Mac camp-cli %
```

No resume line, because there is nothing to resume into. The entry stays in the record — the
window exists and camp composed it — and the door's outcome line counts it as restored.

## State — A conversation Claude refuses to re-enter fails inside its own window

```
tduffield@Mac camp-cli % claude --resume 8f2c1a3e-…
No conversation found with session ID: 8f2c1a3e-…
tduffield@Mac camp-cli %
```

The refusal is Claude's, printed by Claude, in the shell camp left in that window. Every other
window is untouched, and the door reported `resurrected` before any of this happened, because
the door's claim is about windows, not about conversations.

## State — Some windows fail to come back and the door reports how many

```
$ camp attach camp-cli
camp: window @- "review" did not come back — tmux: create window failed: … (conversation 41aa7c02-…)
resurrected camp-trailhead-camp-cli (2 of 3 windows; 1 did not come back)
```

Exit 2. The failure line is on stderr, before the outcome line, and carries tmux's own words and
the conversation id. The two windows that came back are there and recorded; the failed one is
not in the record. Under `--json` the object's `windows` field reads `{"restored": 2, "failed":
1, "dropped": 0}` and the human lines are not printed.

## State — A recorded working directory no longer exists and its window is dropped

```
$ camp attach camp-cli
camp: window record: dropped @3 "review" (directory review/ no longer exists; conversation 41aa7c02-…)
resurrected camp-trailhead-camp-cli (2 windows; 1 dropped)
```

Exit 0. The dropped line is on stderr in the same shape reconciliation's dropped line takes. The
entry is gone from the record. A record whose every entry is dropped brings up the one
login-shell window the create arm would have, and the line reads `resurrected
camp-trailhead-camp-cli (0 windows; 3 dropped)`.

## State — A recorded working directory has left the workspace or reached the credential floor

```
$ camp attach camp-cli
camp: window record: dropped @2 "notes" (directory ../elsewhere resolves outside the workspace)
camp: window record: dropped @4 "keys" (directory is a credential store)
resurrected camp-trailhead-camp-cli (1 window; 2 dropped)
```

Both are refusals of the same floor a composed window is held to, applied again because a
directory can be re-pointed by a symlink or a credential store can be declared after the record
was written. The first line names the recorded path; the second names nothing.

## State — The record cannot be parsed and no session is brought up

```
$ camp attach camp-cli
camp attach: window record at /…/worktrees/camp-cli/windows.json could not be read — refusing to resurrect; fix or remove the record and run camp attach again
```

Exit 1. No tmux call was made. A session with no windows would be a workspace camp cannot
describe presenting itself as an empty one, which is the fail-open answer the record's reader
was built to refuse. The remedy is in the line: the record is a file, and the operator can
inspect it, fix it, or remove it — removing it makes the next attach the create arm. Under
`--json` the object is `{"ok": false, "outcome": "record_unreadable", "reason": …}`, the shape
the other tmux-boundary refusals already take.

## State — The door's outcome, JSON, and exit status tell created, connected, resurrected, and partial apart

```
$ camp attach camp-cli --json
{"ok": true, "outcome": "resurrected", "slug": "camp-cli", "group": "trailhead", "workspace_path": "/…/worktrees/camp-cli", "tmux_session": "camp-trailhead-camp-cli", "attached": true, "windows": {"restored": 3, "failed": 0, "dropped": 0}}
```

`outcome` is one of `created`, `connected`, `resurrected`. `windows` is present only on
`resurrected`; the other two have no windows to account for. `attached` varies independently,
as before. The exit status is 0, 1, or 2 per the table above, and a non-interactive caller
reads it without parsing either rendering.

## State — The record carries tmux's new window ids after resurrection and reconciliation keeps them

```
$ camp attach camp-cli
resurrected camp-trailhead-camp-cli (3 windows)
$ # detach
$ camp attach camp-cli
connected camp-trailhead-camp-cli
```

The second attach is the connect arm, which reconciles the record against tmux and prints
nothing, because every recorded id is one tmux assigned a moment ago. A resurrection that wrote
the record with the ids it had before would be reconciled into an empty record on this connect,
dropping every conversation id it had just restored; re-stamping on the way up is what makes
the two verbs agree.
