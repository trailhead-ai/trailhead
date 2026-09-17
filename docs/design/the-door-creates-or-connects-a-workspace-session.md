# The door creates or connects a workspace session

The rendered surface for `camp attach` once a workspace slug addresses that workspace's tmux
session — creating the session when there is none, connecting to it when there is one, and
never asking the operator which of the two is happening.

This is the create phase of the consolidated session model. It creates and it connects. It
records no window, resurrects nothing, kills nothing, and opens no window inside the session
it brings up. The door decides between two outcomes here, not three.

## The verb is `camp attach`, and a slug wins

`camp attach` today resolves a *session* reference — a prefix of a harness session's name or
id — and hands the terminal to it. That verb is what this spec retires, and retirement is a
later slice, so both meanings live under one name for now. They are told apart by precedence
rather than by pattern:

```
camp attach camp-cli       # names a workspace in the resolved group -> the door
camp attach f4919a6f       # names no workspace -> the retired ref path, unchanged
camp attach                # the workspace picker (new meaning)
camp attach <ref> --host x # unchanged: the cross-host session path
camp attach -a             # unchanged: the widened session picker
```

Precedence, not pattern, for the reason the listing slice already settled: a workspace slug
and a session ref are drawn from overlapping alphabets, and any pattern that tries to
separate them is a guess that goes wrong on the day someone names a workspace `a1b2c3d4`. A
slug that names a workspace in the resolved group *is* that workspace, and only what is left
over is offered to the ref resolver. The retirement slice deletes the fallback; nothing has
to be renamed.

The bare form is the one place precedence cannot help. `camp attach` with no argument meant
"pick a running session"; it now means "pick a workspace". That is a change of meaning, not
an extension of one, and it is stated here because no rule hides it. The session picker is
still reachable at `camp attach -a`, which this slice does not touch.

**The machine probes are outside the door entirely.** `camp attach <ref> --resolve --json` and
`camp attach --list --json` answer questions; they never attach and they must never create.
`camp attach -a` fires them in parallel at every declared host, so a probe that fell through to
the door would create a tmux session on another machine as a side effect of being asked whether
a reference resolves. Precedence is gated off for both flags before it is consulted at all.

That gate is not a formality. `--list --json` and the bare reference-less form share one code
branch today, and the bare form is exactly what becomes the workspace picker — so the two
meanings have to be split apart before either is changed, or the probe inherits the picker.

`camp open` was not available: `open` is already a legacy redirect to `new`
(`camp/workspace/verb_taxonomy.py:81`) and sits in the reserved set that stops a slug
shadowing a verb.

## Creating a workspace goes through the same door

`camp new <slug>` creates a workspace and, today, prints its path. Starting a session in it
is opt-in (`--launch`) and detached — it never hands over a terminal. Under the consolidated
model, creating a workspace and entering it are one act:

```
camp new <slug>              # create the workspace, create its session, attach
camp new <slug> --no-attach  # create both, leave this terminal alone
camp new <slug> --no-session # create the workspace only — the pre-flip behaviour
camp new <slug> --launch     # accepted; prints that it is now the default
```

`--launch` keeps working and does nothing new, because camp's own concierge skill passes it
today and the retirement slice has not told anyone yet. It prints one notice and takes the
default path. `--json` no longer requires it: the object is the machine answer for the command,
not for the flag.

`--no-session` is the way back out. Creating a session is now unconditional, and a default that
cannot be turned off is a change you can only undo by reverting it; this flag reproduces
exactly what `camp new` does today, so backing out is a flag rather than a release.

## The `cd` wrapper goes, because the door replaces it

`trailhead shellenv` installs a `camp()` shell function that intercepts `camp new`, captures
its stdout in `$( … )`, and `cd`s the parent shell to the printed path. It exists because camp
cannot change its caller's working directory — it can only answer where to go.

The door answers differently. It does not tell the operator where the workspace is; it puts
them inside a tmux session already rooted there. There is nothing left to `cd`.

The wrapper is also actively incompatible with that. A captured stdout is a pipe, not a
terminal, so the door's own interactivity test reads false and `camp new` takes the
report-only arm — the attach silently not happening, with no error to see. And the function's
contract is that the command prints a cd target as its *only* stdout line, which an outcome
line appended to stdout breaks.

So the `new` arm is removed. `camp new` prints the workspace path on stdout and everything
else on stderr, and hands over the terminal directly.

The `remove` arm stays. `camp remove` returns the operator to the group repo from inside a
workspace being torn down, which is a real thing the shell cannot do for itself and has
nothing to do with this design. The function keeps existing with one arm.

## What the created session holds

One window, at the workspace root, running the operator's login shell. Nothing else.

```
tmux new-session -d -s camp-trailhead-camp-cli -c /…/worktrees/camp-cli
```

No harness argv, no environment scrub, no account binding. A session window holding a
composed Claude conversation is what the workspace's own window-creation binding produces,
and that binding needs the window record to write the conversation id into — neither exists
yet. Composing one here would create exactly the window the next slice has to learn to
record, and it would be the one window in the workspace that no record describes.

This is a real reduction against `camp launch`, which starts a conversation. `camp launch` is
untouched by this slice and still does. The door's job is to put the operator in the
workspace; what they run there is theirs until the binding ships.

## Creation cannot race into a second session

`tmux new-session -d -s <name>` refuses an existing name rather than making a second session:
measured on tmux 3.7c, a repeat call exits 1 and prints `duplicate session: <name>`. So AC3's
"creates no second tmux session" is guaranteed by tmux itself, not by camp's probe winning a
race against another camp. camp still probes, because it has to *report* which of created and
connected happened, and `new-session -A` — the idempotent attach-or-create form — is precisely
the thing that collapses that distinction. The probe is for the report, not for the safety.

A probe that says "no session" and a creation that then says `duplicate session` is therefore
not an error condition. Another camp won the race; the outcome is `connected`.

## `=` is a target prefix, never a name

Every tmux *target* camp passes is written `=<name>`, so tmux matches the name exactly rather
than by prefix. Without it, a workspace named `api` addresses a session named `api-staging`,
and for a kill that is a mis-targeted destruction.

The prefix belongs to `-t` and to nothing else. `new-session -s` takes a *name*, and measured
on tmux 3.7c, `tmux new-session -d -s '=weird'` succeeds and creates a session literally
called `=weird`. Normalizing `-s` the way `-t` is normalized would silently name every camp
session with a leading `=`, and every subsequent `=<name>` target would then miss. The rule is
per-flag, not per-invocation.

## Handing over the terminal

Two calls, chosen by whether camp is already running inside tmux — read from `TMUX`, the
variable tmux sets in every pane it starts:

```
TMUX unset     ->  tmux attach-session -t =camp-trailhead-camp-cli
TMUX set       ->  tmux switch-client  -t =camp-trailhead-camp-cli
```

Both go through the existing exec seam (`camp/host/handoff.py`), which flushes stdout and
stderr and then replaces camp's process image. Camp prints its outcome line *before* the
handover, and the flush is what makes that line survive an exec that discards process memory
rather than draining it.

`switch-client` is not a nicety. From outside tmux, `attach-session` takes over the terminal,
which is right. From inside one it nests a second tmux in the first, and the nested session's
prefix key fights the outer one's — which camp currently responds to with a stderr warning
(`camp/attach/prefix_warning.py`) and attaches anyway. Inside the multiplexer the operator's
intent is to *move this client to that session*, and `switch-client` is the call that does it.
The warning becomes unreachable on the door's path, because the condition it warns about no
longer happens there.

`switch-client` needs a current client on the same tmux server and exits 1 with
`no current client` when it has none. That is not a case the door reaches: `TMUX` being set is
exactly the evidence that a client exists, and camp's sessions and the operator's own live on
the same default socket. It is named here because a future path that sets `TMUX` artificially
would hit it.

## What the door reports

One line on stdout on the human path, naming the outcome after the fact:

```
$ camp attach camp-cli
created camp-trailhead-camp-cli
```

Under `--json`, one object:

```json
{"ok": true, "outcome": "created", "slug": "camp-cli", "group": "trailhead",
 "workspace_path": "/…/worktrees/camp-cli",
 "tmux_session": "camp-trailhead-camp-cli", "attached": true}
```

`outcome` is an open vocabulary with two members today, `created` and `connected`.
`resurrected` joins it when the window record exists; a caller reads the field rather than
inferring the answer from whether `window_count` moved, which is what an agent attaching to a
half-restored workspace would otherwise have to do.

`attached` is separate from `outcome` because the two vary independently: a non-interactive
invocation creates a session and attaches nothing, and that is a success, not a degraded
create. A caller deciding whether it now owns a terminal reads `attached`; a caller deciding
whether the workspace was already open reads `outcome`.

`tmux_session` and `workspace_path` carry the same values, spelled the same way, that
`camp list` already puts in its rows. The door and the listing answer about the same objects
and a caller should not have to reconcile two spellings of them.

**Exit status.** `0` for created and for connected; `1` for every refusal below. AC16 also
asks that partial resurrection be distinguishable, and this slice defines no code for it: a
partial resurrection cannot occur until the window record exists, and reserving a number for
an outcome nothing can produce puts a value in the contract with no producer to hold it
honest. The resurrection slice adds the third code alongside the outcome that earns it.

## State — A slug naming a workspace with no session

The ordinary first open of the day.

```
$ camp attach camp-cli
created camp-trailhead-camp-cli
<terminal is now the session>
```

The line prints before the exec, so it is on screen behind the attached session rather than
lost with the process image.

## State — A slug naming a workspace whose session is running

Indistinguishable to the operator from the previous state except for one word, which is the
point: they typed the same thing and did not have to know.

```
$ camp attach camp-cli
connected camp-trailhead-camp-cli
<terminal is now the session>
```

## State — Created, terminal deliberately not handed over

Only reachable through `camp new --no-attach`; the door itself has no non-attaching flag,
because a door that does not open is a different verb and `camp list` already answers the
question it would be reached for.

```
$ camp new docs-refresh --no-attach
/Users/…/worktrees/docs-refresh          # stdout
created camp-trailhead-docs-refresh      # stderr
$ echo $status
0
```

Stdout carries the workspace path and nothing else; the outcome goes to stderr beside any
notice. Nothing captures this command's stdout any more, but the split is worth keeping on its
own terms — a path is what a caller substitutes into something, an outcome is what a human
reads, and one line each is what makes both usable without a parser.

## State — No interactive terminal

An agent, a CI step, a hook. The session is made to exist and reported; nothing is handed
over, and the non-attaching flag makes no difference because there was never a terminal to
leave alone.

```
$ camp attach camp-cli --json < /dev/null
{"ok": true, "outcome": "created", …, "attached": false}
$ echo $status
0
```

Interactivity is `stdin` *and* `stdout` both being terminals — the same expression camp's
existing picker call sites compute — and it is passed down as data, never re-probed inside
the logic that acts on it. A door that probed `isatty` where it decides would be untestable
except by owning a pty.

This is the state camp's own concierge skill runs in. Its `camp new <slug> --launch --no-wait
--json` keeps working: no terminal, so no attach, and the flag is accepted.

## State — A slug naming no workspace in the group

The slug is not a workspace, so the door does not own it. It falls through to the retired ref
resolver, which refuses in its own words when the ref matches nothing:

```
$ camp attach no-such-thing
camp attach: no session matches "no-such-thing"
$ echo $status
1
```

camp creates no workspace here. `camp attach` is a door onto something that exists; the verb
that brings a workspace into being is `camp new`, and guessing that a typo meant "create"
would make a typo expensive.

Once the retirement slice removes the ref path, this refusal becomes the door's own and names
the slug directly.

## State — No slug, an interactive terminal, several workspaces

```
$ camp attach
Workspaces:
  1) camp-cli        running:3
  2) docs-refresh    none
  3) lookout-spike   none
> 2
created camp-trailhead-docs-refresh
```

The rows carry the same state field `camp list` prints, from the same classifier, because the
operator choosing a workspace is choosing partly on whether it is already open. Rows stay in
the listing's slug order for the same reason the listing does not reorder: the operator is
scanning for a name they already have in mind.

Blank input, a non-numeric line, and an out-of-range number re-prompt rather than exit —
the behaviour the existing session picker already has, which this reuses rather than
reimplements.

## State — No slug, an interactive terminal, exactly one workspace

The picker still prompts. It does not act on the only row.

```
$ camp attach
Workspaces:
  1) camp-cli  running:3
> 1
connected camp-trailhead-camp-cli
```

Auto-selecting would make `camp attach` mean something different on the day a second
workspace appears, and the operator would discover that by landing somewhere they did not
choose. One keystroke is the price of the verb meaning one thing.

## State — No slug, an interactive terminal, no workspaces at all

```
$ camp attach
camp attach: no workspaces in group "trailhead" — `camp new <slug>` creates one
$ echo $status
1
```

Stated, not prompted. An empty numbered list is a prompt with no answer.

## State — No slug and no interactive terminal

```
$ camp attach --json < /dev/null
camp attach: no workspace named — pass a slug
$ echo $status
1
```

The refusal names the missing slug and presents no picker, because there is nobody to answer
it. A picker written to a pipe would hang until its reader gave up, and the caller would see a
timeout where it should have seen a refusal.

The terminal is checked before the workspaces are enumerated, so this refusal does not depend
on how many workspaces exist and reads the same in an empty group as in a full one.

## State — tmux cannot be reached

camp cannot tell whether the session exists, so it does neither thing.

```
$ camp attach camp-cli
camp attach: tmux did not answer — <tmux's own stderr line> — `camp list` still shows the workspaces
$ echo $status
1
```

The listing degrades to an `unknown` column here and still prints its rows, because the
workspace half of its answer is still correct. The door has no half to keep: every outcome it
can report is a claim about the session. So it refuses, carrying tmux's own stderr line, and
the operator learns tmux is the problem rather than watching a create fail for a reason camp
does not explain.

The no-server condition is not this state. A tmux with no server running is a well-formed
answer meaning zero sessions, recognised on its own stderr shape, and the door reads it as
"no session exists" and creates one — which starts the server as a side effect, exactly as
`tmux new-session` does from any shell.

## State — The session could not be created

tmux answered, camp asked for a session, and it did not appear.

```
$ camp attach camp-cli
camp attach: could not create camp-trailhead-camp-cli — <tmux's own stderr>
$ echo $status
1
```

camp reports tmux's own words rather than a summary of them. The failures here are the
operator's to fix — a workspace directory that has been removed under camp, a server refusing
to start, a socket directory with unsafe permissions — and every one of them is diagnosable
from the line tmux printed and none of them from a line camp composed.

The one non-failure in this shape is `duplicate session`, which means another camp created it
between the probe and the create. That is the `connected` outcome, reported as such, and never
an error.

camp does not decide that on the string alone. `duplicate session: <name>` is pinned against
tmux 3.7c, and a reworded or localised message would turn a benign race into a hard refusal. So
an unrecognised create failure re-asks `has-session` before refusing: a session that is there
is `connected` whatever tmux called the collision, and only a create that failed with nothing
behind it is a refusal.
