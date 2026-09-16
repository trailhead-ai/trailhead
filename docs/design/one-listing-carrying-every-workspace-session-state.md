# One listing carrying every workspace session state

The rendered surface for `camp list` once each row carries the state of its workspace's
tmux session, and once tmux sessions camp no longer models are visible beside the
workspaces.

This is the read path of the consolidated session model and nothing more. It creates no
session, records no window, attaches to nothing, and kills nothing. Everything below is
produced by enumerating tmux once and comparing that enumeration against the group's
workspaces.

## The session name is derived from group and slug

A workspace's tmux session is named `camp-<group>-<slug>`, both components folded to the
characters a tmux target can address. The name is derived, never stored: camp recomputes it
from the group and the slug whenever it needs to ask about a workspace, so there is nothing
to keep in sync and nothing to go stale.

Deriving from both components is what keeps two groups apart. `trailhead/camp-cli` and
`levr/camp-cli` are ordinary, independent workspaces that happen to share a slug, and a
name derived from the slug alone would have them resolve to one tmux session — so attaching
to one would hand over the other, and stopping one would kill the other.

The retired form, which named a session after the single Claude conversation inside it,
was `camp-<component>-<8 hex>`:

```
camp-audio-assebly-f4919a6f
camp-ets-workshop-e2026480
```

Both forms carry the `camp-` prefix, and a slug can end in something that reads like a hex
tail, so the two are told apart by precedence rather than by pattern alone: a name that is
the derived name of a workspace in the group is that workspace's session, and only what is
left over is tested against the retired pattern. Precedence, not pattern, is what makes the
answer stable.

## How the state is answered

Liveness is answered by asking tmux, every time, and never by reading anything camp wrote.
There is no window record yet, and once there is one, it still will not be consulted for
this question. tmux is enumerated once per listing:

```
tmux list-sessions -F '#{session_windows}|#{session_name}'
```

The window count is emitted first, because it is always digits and a foreign session name
may legitimately contain the delimiter. The name is the remainder after the first `|`, so
no session name can be misparsed however it is spelled. This is not theoretical: tmux
accepts a `|` in a session name, and `tmux rename-session -t x 'camp-pipe|9|evil-1a2b3c4d'`
succeeds, which a name-first field order would read as a session called `camp-pipe` holding
nine windows.

A session name is a string from outside camp, so it is printed through the same escaper
camp already applies to paths it did not author (`printable_path`,
`camp/launch/recovery.py:263`), which folds C0, DEL, C1, and the bidi overrides. tmux 3.7c
does reject a name carrying control characters, at both `new-session` and `rename-session`,
so this is defence in depth rather than a live hole — but the escaper is one call, camp
supports whatever tmux the operator has, and the rule in this codebase is that camp does not
assume an external string is printable.

Three outcomes, kept distinct:

- exit 0 — the listed sessions are the answer.
- non-zero exit, and tmux said the server is not running — zero sessions. A well-formed
  answer, not a failure.
- anything else — the binary is absent, the call timed out, or tmux exited non-zero for
  some other reason. camp did not get an answer and says so, rather than reporting every
  workspace as having no session.

The second case is recognised narrowly, because a non-zero exit on its own does not mean
what it appears to. Measured on tmux 3.7c, three unrelated causes all exit 1 and differ
only in what they print:

```
$ tmux -L absent list-sessions
error connecting to /private/tmp/tmux-501/absent (No such file or directory)   # no server

$ tmux -L stale list-sessions
directory /private/tmp/bad/tmux-501 has unsafe permissions                      # an outage

$ tmux -L far list-sessions
error connecting to /private/tmp/.../far (File name too long)                   # an outage
```

Only the first is zero sessions. Reading the other two as zero would render every
workspace `none` during a live outage, with no notice and exit 0 — a wrong answer wearing
a right answer's clothes, and the single worst thing this column could do. So the empty
case is matched on the no-server condition specifically, and every other non-zero exit
falls through to unanswered, carrying tmux's own stderr line as the notice.

Defaulting the unrecognised case to unanswered is deliberate: a tmux that has changed its
wording produces `unknown`, which is conservative and visible, rather than `none`, which is
confident and wrong.

This is the same discipline `camp stop` applies to `tmux has-session`, sharpened by what
that seam's own note warns about — a scoped existence query has a documented contract for
its non-zero exit, and a general listing command does not. The two must not be reasoned
about interchangeably.

## The row

Three fields, space-separated, no header, one line per row:

```
<slug>  <state>  <workspace-path>
```

The state field sits in the middle so that the workspace path remains the last field on
the line, which is what lets a path be read as the remainder of the row rather than as a
field that must be delimited.

The state vocabulary is:

- `none` — the workspace exists and its tmux session does not.
- `running:<n>` — the session is up, holding `n` windows.
- `unmanaged` — a tmux session in the retired form, belonging to no workspace.
- `unknown` — tmux could not be asked.
- `-` — no group in play, so no session name is derivable (the legacy registry fallback).

In `--json`, every row gains `state`, `window_count`, and `tmux_session`. Rows keep the
`ok` discriminator they already carry, because an unmanaged session is a successful
observation rather than a failure to observe something.

## State — Zero workspaces in the group

A configured group holding no workspaces, with no camp tmux sessions anywhere. The listing
prints nothing and exits 0 — the existing silence, unchanged.

```
$ camp list
$ echo $status
0
```

```
$ camp list --json
[]
```

Silence stays the honest answer: the question was well-formed and the answer is empty.
Adding a state column gives camp nothing new to say here, and a header row announcing
columns over zero rows would be noise. This is distinct from `unknown` below, where camp
could not determine the answer at all.

An empty group with an unmanaged session still on the machine is not this state — see
*An unmanaged session in the retired one-session-per-conversation form*, which prints that
row against no workspaces at all.

## State — One workspace, no session

The ordinary resting state of a workspace that has been created but not opened.

```
$ camp list
camp-cli  none  /Users/tduffield/.local/state/camp/trailhead/worktrees/camp-cli
```

```
$ camp list --json
[{"ok": true, "slug": "camp-cli", "branch": "worktree-camp-cli",
  "workspace_path": "/Users/tduffield/.local/state/camp/trailhead/worktrees/camp-cli",
  "group": "trailhead", "state": "none", "window_count": 0,
  "tmux_session": "camp-trailhead-camp-cli"}]
```

`tmux_session` is carried even though nothing is running, because it is the name a caller
would use to ask about or open this workspace, and it is derived rather than observed. A
caller reading the row does not have to know the derivation rule to act on it.

`window_count` is `0` rather than `null`: there is a running session with no windows in no
circumstance, so zero is the true count and a null would only invite a caller to special-case
it.

## State — Many workspaces, mixed session states

The state this listing exists for. One command, one row per workspace, every state legible
side by side, with no second verb and no `tmux ls` to cross-reference.

```
$ camp list
camp-cli        running:3  /Users/tduffield/.local/state/camp/trailhead/worktrees/camp-cli
docs-refresh    none       /Users/tduffield/.local/state/camp/trailhead/worktrees/docs-refresh
lookout-spike   running:1  /Users/tduffield/.local/state/camp/trailhead/worktrees/lookout-spike
```

Rows stay in the slug order the listing already used; the state column reorders nothing.
A workspace whose session is not running is not sorted to the bottom, because the operator
is scanning for a slug they already have in mind, and moving rows between invocations would
work against that.

The window count is the one piece of size information in the row, and it answers the
question the old session inventory was reached for: how much is open in here. It is a count
of windows, not of conversations — camp does not look inside a window on this path, and a
window may hold a shell.

## State — An unmanaged session in the retired one-session-per-conversation form

A tmux session left over from the retired model, matching `camp-<component>-<8 hex>` and
belonging to no workspace in the group. It appears in the listing, in the same stream,
with the tmux session name where a slug would be and no path.

A leftover session belongs to no group — its name predates the group-qualified scheme and
cannot be attributed to one. So a group-scoped listing, which was asked about one group,
reports that they exist without naming them, and the widened question names them:

```
$ camp list
camp-cli  running:3  /Users/tduffield/.local/state/camp/trailhead/worktrees/camp-cli
2 unmanaged camp sessions — `camp list -g` to name them
```

```
$ camp list -g
camp-cli                    running:3  /Users/tduffield/.local/state/camp/trailhead/worktrees/camp-cli
camp-levr-client-a1b2c3d4   unmanaged  -
camp-ets-workshop-e2026480  unmanaged  -
```

The split is a disclosure boundary, not tidiness. A retired-form name carries the workspace
component it was derived from, so naming every leftover in a group-scoped listing would put
one group's project names into an answer about another — and this output is routinely pasted
into bug reports. camp already narrowed group-scoped session queries for exactly this reason;
the unmanaged rows would have been the one path that re-opened it.

The count still appears in the narrow form because the operator's real question is what is
holding memory on this machine, and a count answers it. Naming them is a widening the
operator asks for.

```
$ camp list -g --json
[{"ok": true, "slug": "camp-cli", ..., "state": "running", "window_count": 3,
  "tmux_session": "camp-trailhead-camp-cli"},
 {"ok": true, "slug": null, "branch": "", "workspace_path": null, "group": null,
  "state": "unmanaged", "window_count": 2,
  "tmux_session": "camp-ets-workshop-e2026480"}]
```

The group-scoped `--json` carries the same restraint: an `unmanaged_count` integer, and no
unmanaged rows. A caller that wants them asks the widened question, exactly as the human
reader does.

These rows exist so the operator can see what is holding memory on the machine, which is
the honest reason the retired session inventory was ever reached for. They are reported and
nothing more. camp adopts none of them into a workspace: the row carries no slug and no
path, and no amount of resemblance between the session's name component and a workspace's
slug changes that — a workspace's session is the one at the workspace's own derived name,
and nothing else is a candidate.

No camp command kills one of these, either. This listing issues no `kill-session` at all,
and the retired verbs that could address such a session by name are removed rather than
repointed. An operator who wants one gone uses `tmux kill-session` directly, which is the
correct tool for a session camp does not own.

`slug` is `null` rather than the tmux name repeated, so a caller filtering the JSON for
workspaces gets workspaces. The human row puts the tmux name in the first column because a
blank leading field would be worse to read and worse to parse, and the state field
immediately after it says what the first column means.

## State — A tmux session that was never camp's

A tmux session with no `camp-` prefix, or with the prefix but no hex tail — an editor
session, a long-running server, anything the operator started themselves.

```
$ tmux ls
camp-trailhead-camp-cli: 3 windows
camp-ets-workshop-e2026480: 2 windows
dotfiles: 1 windows
work: 4 windows

$ camp list -g
camp-cli                    running:3  /Users/tduffield/.local/state/camp/trailhead/worktrees/camp-cli
camp-ets-workshop-e2026480  unmanaged  -
```

`dotfiles` and `work` do not appear, in any form. camp has no claim on them and nothing to
say about them, and listing them would turn a workspace listing into a tmux browser — which
is the thing this design set out to stop being. The operator already has `tmux ls` for that
question and it answers it better.

The line is drawn at the name, which is the only evidence available without inspecting the
session, and it is drawn deliberately conservatively: a session camp did not create but
which happens to match the retired form is reported as unmanaged, which is exactly what it
is treated as — something to look at, never something to act on.

## State — tmux unreachable or not running

Two cases that must not be confused, because one is an answer and the other is the absence
of one.

**No server running.** tmux is installed and no session exists anywhere. This is zero
sessions, and every workspace correctly reads as `none`:

```
$ camp list
camp-cli        none  /Users/tduffield/.local/state/camp/trailhead/worktrees/camp-cli
docs-refresh    none  /Users/tduffield/.local/state/camp/trailhead/worktrees/docs-refresh
$ echo $status
0
```

**Not answerable.** tmux is not on `PATH`, or the call timed out. camp does not know, and
says so rather than reporting the same rows it would print for an empty server:

```
$ camp list
camp list: tmux did not answer — session state is unknown
camp-cli        unknown  /Users/tduffield/.local/state/camp/trailhead/worktrees/camp-cli
docs-refresh    unknown  /Users/tduffield/.local/state/camp/trailhead/worktrees/docs-refresh
$ echo $status
0
```

The notice goes to stderr and the rows still go to stdout, because the workspace half of
the answer is still correct and still worth having — a listing that refuses outright would
take away the slugs and paths over a question the operator may not have been asking.

The exit status stays 0. An unanswerable tmux is not a failure of `camp list`, whose
subject is the group's workspaces; it is one column of the row being unknown, and the row
says so in the column itself. A caller that cares reads `state`, which is the same place it
reads every other state from — it does not need a second signal.

Distinguishing these two cases is the whole point. Collapsing them would make a broken
tmux indistinguishable from an idle one, and an operator who reads `none` for a workspace
they know is open will stop trusting the column entirely.
