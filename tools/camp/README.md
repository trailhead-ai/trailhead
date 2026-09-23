# camp — group worktree orchestration

camp is an agent-native workflow plugin that gives Claude structured primitives for
managing git worktrees across a configured group of repositories. It handles the
"where is the work happening" question so agents don't have to.

**Standalone use:** camp stands alone — adopt it without lore or craft if you only
want the worktree orchestration.

## PATH setup

`trailhead install` builds a shim for the `camp` CLI. To put it on your PATH, add
the brew-style `shellenv` line to your shell profile (fish/zsh/bash all handled):

```sh
eval "$(/path/to/trailhead/bin/trailhead shellenv)"
```

Then `camp new <slug>` works from a plain shell. See the [root README](../../README.md)
for the full install flow.

## Quick start

```
camp groups          # list every configured group (any cwd)
camp new <slug>      # create or enter a workspace, then attach to its tmux session
camp new <slug> --no-attach  # create or enter + its session, leave this terminal alone
camp new <slug> --no-session # create or enter the workspace only — no tmux session
camp pwd <slug>      # print workspace path
camp list            # list all worktrees (alias: ls)
camp status          # show git + drift status
camp sessions        # list the live harness sessions camp can see
camp sessions --recoverable  # list the dead ones that could be brought back
camp kill <ref>      # stop one session and reclaim its memory
camp attach <ref>    # hand your terminal to a running session
camp stop <slug>     # preview, then kill a workspace's whole tmux session
camp remove          # tear down a worktree (alias: rm)
camp --help          # full command reference
camp --version       # show version + resolved binary path
```

A conversation starts in exactly one way: reach the workspace's tmux session
with `camp attach <slug>` or `camp new <slug>`, then press the ordinary
window-creation key (prefix+`c`) inside it. Resuming a dead conversation works
the same way — reaching the workspace through the door brings its windows
back, and each window that held a conversation says how to resume it.

## Shell integration

`camp pwd <slug>` prints the resolved workspace path on stdout (exactly one line).
Use it directly to change directory:

```sh
cd "$(camp pwd <slug>)"
```

Wrap it in your own alias or shell function if you use it frequently. For example, in fish:

```fish
function camp_cd
    cd (camp pwd $argv)
end
```

### `camp remove` changes your shell's directory

camp never starts, stops, or replaces a process, and it cannot change your shell's
working directory on its own — it only *answers* where to go, as exactly one line
on stdout. Acting on that answer takes a shell function, which is what the
`shellenv` line installs:

```sh
eval "$(/path/to/trailhead/bin/trailhead shellenv)"
```

The wrapper it defines intercepts `camp remove` (and `camp rm`) and does the `cd`
for you, back to the group's first-member repo. Without it, the printed path is
inert — use `cd "$(camp pwd <slug>)"`.

`camp new` needs no such wrapper: it hands your terminal to a tmux session already
rooted at the new workspace, so there is nowhere left to `cd`.

## Sessions

```
camp sessions [<slug>] [--dir <path>] [--all-groups|-g] [--json]  # what is live
camp sessions --recoverable [<slug>] [--dir <path>]         # what is dead
                          [--limit <n>|--all] [--json]
```

`camp new <slug>` creates the workspace's tmux session (a bare shell, rooted at
the workspace) and attaches to it, described above under "Quick start" and in
"`camp remove` changes your shell's directory". Starting a harness conversation
in a workspace is the ordinary window-creation key pressed inside that
session, run from inside the workspace `camp new` or `camp attach` just opened.

`camp list` and `camp sessions` both take `--all-groups` (short: `-g`), which
answers for every configured group in one invocation instead of one — every
group's worktrees for `list`, every group's stores for `sessions` — ordered by
group. It refuses alongside a named group rather than picking one; naming a
group with plain `camp sessions --group <name>` (or from inside a workspace)
does the opposite, narrowing the live listing to that group's own rows rather
than the ordinary cross-store answer.

The pane a new window opens in drops the session's ambient credential
variables and binds the group's declared account, so a window never inherits
a different account's credentials — see
[Declaring a group's account](#declaring-a-groups-account) below.

### Declaring a group's account

`[launch]` also accepts an optional `account` key:

```toml
[launch]
account = "~/.claude-levr"
```

The value is stored exactly as written and passed through to the harness seam,
which owns its interpretation: camp does not expand `~` and does not know which
environment variable the harness turns the value into. The only thing camp
checks at load is that it is a non-empty string carrying no control characters,
since the value becomes a path something resolves and an operand of a process
spawn. A group with no `account` declared leaves the harness to resolve its own
default.

The **deny list** below is the one place camp does read the value as a path.
Every group's `account` is denied as a launch root — including to groups that
declared none — but only when it is absolute or `~`-anchored. A relative
`account` names no fixed location, so it contributes no deny entry at all: it is
skipped rather than resolved against whatever directory camp happens to be
invoked from. The harness refuses to bind a session to a relative account in the
first place, so such a value is a misconfiguration to fix, not a protection to
rely on.

**A credential deny list applies unconditionally.** `~/.ssh`,
`~/.gnupg`, `~/.aws`, `~/.azure`, `~/.kube`, `~/.docker`, `~/.config/gcloud`,
`~/.netrc`, `~/.config/gh`, `~/.npmrc`, `~/.pypirc`, `~/.git-credentials`, and
the harness's own credential stores `~/.claude` and `~/.claude.json` are fixed
in camp's code as a floor. The rule denies a target that is at, under, **or
above** any entry — so rooting a window at the home directory itself cannot
launder every credential store inside it past the gate. The refusal names the
credential rule and no group configuration can permit it.

**Every `account` any group declares is added to that deny list**, so a second
account's credential directory is protected exactly like the first — including
from a *different* group. The derivation is additive only: a group config can
extend the floor and can never remove, narrow, or shadow an entry, so no group
config can permit a denied path.

### Bringing a dead session back

Resuming a dead conversation happens by reaching the workspace through the
door — `camp attach <slug>` or `camp new <slug>`. If the workspace's tmux
session itself is gone, reaching it that way brings its windows back: each
window that held a conversation prints what it held and how to resume it —
`camp: this window held conversation <id>` and `camp: resume it with: <the
harness's own resume command>` — and the operator decides which conversations
to resume, and when, from inside that shell. camp starts no conversation on
the way back.

`camp sessions --recoverable` is how those conversations can be surveyed
without reaching the workspace first — every session the harness kept a
transcript for, minus the ones running now, newest first and capped at the
newest 20 with the total named (`--limit <n>` or `--all` widen it). A row
whose directory no longer exists is listed and marked rather than hidden.

`camp sessions` degrades rather than failing: an enumeration error, an unknown
harness, or an absent tmux prints a notice on stderr and an empty list on
stdout, exit 0. `--recoverable` degrades the same way when the live set cannot
be determined — it reports none rather than printing an unsubtracted pool that
might include sessions running right now. It has two refusals instead, because
neither has an empty listing as an honest answer: camp cannot name a harness for
any configured group, or the harnesses it can name keep no transcripts it can
read. An unusable `--limit` refuses too.

## Attach

`camp attach` hands your terminal to a running session, on this machine or a
declared one, without working out which machine it's on first:

```
camp attach
camp attach -a
camp attach <ref>
camp attach <ref> --host <name>
camp attach <ref> -a
```

With no reference, it offers a numbered picker over this machine's running,
camp-owned sessions — most recently active first — and reads one choice;
`-a` widens that picker across every declared machine too. `<ref>` is the
same unambiguous prefix `camp kill` already accepts, resolved by the
identical rule so the two verbs never drift into two grammars.

From inside a configured group (or with `--group`), `<ref>` is checked against that
group's own workspace slugs first: a match creates or connects that workspace's
tmux session — creating it if nothing is running yet, joining it if something
already is — and hands you the terminal the same way. A `<ref>` matching no
workspace falls through to the session-reference form below, unchanged. Outside
tmux this attaches directly; from inside an existing tmux session it moves your
client there instead of nesting, so hopping between workspaces never tears down
the one you came from.

`<ref> --host <name>` carries the reference across untouched: the named
machine's own camp resolves it and refuses in its own words, exactly as if
you had run `camp attach <ref>` there yourself. `<ref> -a` instead asks
every declared machine to resolve the reference and counts how many did:
none is no match, more than one refuses and names every machine that
matched, and a machine that did not answer refuses the whole attempt rather
than guessing — the one place this surface departs from a plain listing,
which can report an unreachable machine as a row and still succeed. Attach
cannot, because the silent machine might have held the only match.

Only a session camp itself started is offered — the same ownership check
`camp kill` applies. A resolved session that is not currently running
refuses, rather than reporting "not found" for a session that does exist:

```
camp attach: session <id> is not running — find its workspace with `camp
sessions` and reattach with `camp attach <slug>`
```

Exit status is the attaching multiplexer's own once the handoff happens, and
camp's own before it: `1` for a refusal, `2` for a reference matching more
than one session (one machine, or more than one under `-a`), matching `camp
kill`.

`camp attach <ref> --resolve --json` is machine-readable and never attaches
anything — it just answers whether `<ref>` resolves here, as JSON. Its only
consumer is `camp attach <ref> -a` itself, probing every declared machine
this same way before deciding where to hand the terminal.

```
camp attach <ref> --resolve --json
```

`camp attach --list --json` is `--resolve --json`'s reference-less sibling: it
dumps this machine's own picker pool as JSON instead of prompting. Its only
consumer is `camp attach -a` itself (the bare, no-reference form), merging
every declared machine's own pool before presenting one combined numbered
list.

```
camp attach --list --json
```

## Remote hosts

Declare a remote machine once, in `~/.config/camp/hosts.toml`:

```toml
[hosts.andromeda]
ssh = "andromeda.lan"
camp_bin = "/home/tom/.local/state/trailhead/bin/camp"
```

`ssh` defaults to the table key itself — the destination a plain `ssh <name>`
would use — and `camp_bin` defaults to the bare command name `camp`; declare
it explicitly whenever `camp` is not on that host's non-interactive PATH,
which is the common case over a plain SSH invocation.

`hosts.toml` also carries this machine's own name, as a top-level
`self_name` — reserved separately from `[hosts.<name>]` so it can never
collide with a declared peer:

```toml
self_name = "sunrise"
```

Every machine `camp transfer` runs between must declare its **own**,
**different** `self_name` — nothing here checks that for you. A machine
that never declares one is a supported "not yet participating" state, not
an error; `camp transfer` itself refuses on that host and names this file
and the requirement (see [Transferring a workspace](#transferring-a-workspace)
below — the refusal, not this section, is the primary way an operator
learns the file exists). Two machines sharing a name silently pass every
ownership check on *both* sides at once, since a peer's declared name is
only ever compared for equality — pick names that cannot collide, the way
`andromeda`/`sunrise` do above, not `host1`/`host2`.

```
camp list --host <name>
camp list --host <name> --json
camp sessions --host <name>
camp sessions --host <name> --json
```

Both connect over `BatchMode=yes` SSH and answer for every configured group
on that machine in one call. `--host` refuses alongside `--group` and
`--all-groups`; `camp sessions --host <name>` additionally refuses
`--recoverable`, `--all`, `--limit`, `--dir`, and a positional workspace
slug, since each of those narrows or reshapes a single machine's own local
question rather than "every group on that host". A host name not declared
in `hosts.toml` is refused the same way.

Stopping a session on a declared machine is the one state-changing form of
this shape:

```
camp kill <ref> --host <name>
camp kill <ref> --host <name> --json
```

The reference alone names the session, so no `--group` is needed — the far
side resolves it against its own pool exactly as it would locally, and
refuses in its own words. There is no `--all-hosts` form: a stop acts on the
one machine you name. Its exit status is its own four-value contract, so a
script can branch without reading prose — `0` stopped (or already down,
told apart by the answer's `outcome` field), `2` the reference matched more
than one session and the candidates are on stdout, `3` the outcome could not
be determined, `1` every certain failure including a far-side refusal. `2`
and `3` are reserved: a remote exiting on either collapses to `1` rather
than impersonating camp's own signal.

A failure to connect, authenticate, or run camp on the far side is its own
rendered outcome rather than a crash: unreachable, connected but stalled
before answering, an unrecognized host key, a changed host key, camp not
resolvable on that host (declare `camp_bin`), every credential offered
refused, or the remote camp's own refusal relayed verbatim.

`--host <name>` names one machine and answers for every group on it.
`camp list`/`camp sessions` also take `--all-hosts` (short: `-a`), which is
the opposite shape: **this resolved group, on every declared machine plus
the one you're typing on** — the asymmetry an operator otherwise has to
learn the hard way, so it is stated here rather than left implicit. `-ag`
(the bundled short form of `-a -g`) widens the group axis too, for every
group on every machine; `-a --group <name>` composes to ask for one named
group on machines generally, without needing a resolvable cwd.

```
camp list -a --group <name>
camp list --all-hosts --group <name> --json
camp list -ag --json
camp sessions -a --group <name>
camp sessions --all-hosts --group <name> --json
camp sessions -ag --json
```

(`-a`/`--all-hosts` also resolve the group from cwd, the same as plain
`camp list`/`camp sessions` — `--group <name>` is shown explicitly above only
so each form is runnable from any cwd.)

Declared hosts are contacted concurrently, and the merged answer is grouped
by machine on the human path — the local machine first (named by
`self_name` in `hosts.toml`, or `this machine` when undeclared), then every
declared host in the order `hosts.toml` declares it — with a failed
machine's line printed under its own header rather than aborting the rest.
The `--json` path is one flat array in the same order, every row carrying
`host`. `-a` refuses alongside `--host` (they name a machine's worth of
groups and a group's worth of machines at once) and, with no group resolved
from cwd or `--group`, refuses naming `-ag` or `--group <name>` as the ways
forward — it never falls back to the legacy standalone-worktree source.

## Transferring a workspace

```
camp transfer <slug> --to <peer> [--dry-run] [--overwrite] [--json]
```

Moves a workspace's committed history, working-tree content, and every
conversation rooted in it directly to a declared peer host (see [Remote
hosts](#remote-hosts) for declaring one) — and, as the transfer's last step,
ownership itself: the peer becomes the recorded owner, and this host stops
offering the conversations that just crossed. `--dry-run` previews the same
checks without moving anything. A workspace already present on the peer and
owned by this host needs `--overwrite` to be replaced.

Each member's regenerable state — build output, installed dependencies,
anything a transfer should recreate on the peer rather than copy — is
declared per member as `excluded`, a list of paths relative to that
member's worktree inside the workspace (`<workspace>/<member>`) — the tree a
transfer would walk, not the original clone:

```toml
[[members]]
name = "trailhead-ai.github.io"
repo_root = "/path/to/trailhead-ai.github.io"
excluded = ["node_modules"]
```

Absence of the key is its own state, distinct from declaring it empty: a
member that has never declared `excluded` reads as "never declared" (not
"declares nothing regenerable"), and `camp transfer --dry-run` refuses by
name, listing the member(s), until every member states one or the other —
an empty list is a legitimate answer, silence is not.

Exit codes:

```
0  every check passed — a clean verdict with --dry-run, or (without it) the
   transfer completed
1  an unexpected/local error (bad flags, malformed config)
3  not clean, for a reason with no more specific code below
4  this host does not own the workspace
5  the peer could not be reached
6  no workspace is recorded here for that slug
7  the named peer is not declared in hosts.toml
8  the workspace already exists on the peer, owned by this host — pass
   --overwrite to replace it
9  a move phase failed before ownership moved — nothing after it ran;
   re-running the transfer is safe
10 a move phase failed after ownership had already moved to the peer — a
   re-run is NOT safe; continue the work on the peer and compare the two
   hosts' records to confirm they disagree
11 ownership moved cleanly but at least one crossed conversation's release
   failed — this host may still hold a resumable copy of it; see the
   FAILED line(s) printed for which conversation(s) still need cleaning up
   by hand
12 whether ownership moved is genuinely unknown, even after re-probing the
   peer — check the peer directly with `camp transfer-probe` before doing
   anything else
```

`transfer` and `transfer-probe` (the wire-level answer `--dry-run` reads
from the peer) are reserved verb names, and so is `transfer-receive` (the
peer side of a move), so none of the three can be dispatched as a bare slug
(`camp transfer` alone always means the verb). `transfer-probe` and
`transfer-receive` are each intercepted before a group is ever resolved, and
that interception — not membership in the reserved set — is what stops a
same-named workspace slug shadowing them today; the reserved set is the
backstop if that interception is ever reordered or removed. A workspace is
still free to *be named* `transfer`, `transfer-probe`, or `transfer-receive`,
and stays fully reachable through its group's own named verbs:

```
camp transfer transfer --to <peer> --dry-run --group <name>
camp transfer-probe --group <name> --slug transfer-probe
camp transfer transfer-receive --to <peer> --dry-run --group <name>
```

The first previews a workspace literally named `transfer`; the second
answers for one named `transfer-probe`; the third previews one named
`transfer-receive`.

## Group setup

```
camp group <name> --member NAME=PATH [--member NAME=PATH ...]
```
Authors a group config TOML and wires SessionStart hooks into each member repo.

Everything else in a group config is optional and off until you add it — including
the `[launch] account` binding described under
[Declaring a group's account](#declaring-a-groups-account). The full schema
is documented on `camp.group.config`.
