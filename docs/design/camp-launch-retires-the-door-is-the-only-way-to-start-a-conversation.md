# camp launch retires: the door is the only way into a workspace

The rendered surface for retiring the launch verb family: `camp launch` in every flavor, the
directory allowlist that fenced `--dir`, and the harness composition behind them. camp composes no
conversation command. The operator reaches the workspace's tmux session through the door and starts
a conversation in any pane of it; camp records it when it starts (see
`the-window-record-camp-writes-down-what-is-inside-a-workspace-session.md`).

This is the first cleanup slice of the consolidated session model. It retires the launch family.
It does not retire `camp sessions`, its recoverable listing, or ref-prefix `camp kill` and
`camp attach`; those remain exactly as they are.

## What goes and what stays

| Goes | Stays |
|---|---|
| `camp launch`, `camp launch --resume`, `camp launch --dir`, `camp launch --host` | the door, `camp attach <slug>` and `camp new` |
| `camp new --launch` | `camp new --no-session`, which creates a workspace with no session |
| the launch engine and its enumeration poll | session enumeration, which `camp sessions`, `camp kill` and the teardown guard still read |
| `[launch] roots`, the directory allowlist | `[launch] account`, and the credential floor |
| the harness's session-launch composition, with its remote-control and visible-name flags | the harness's resume command and its launch environment |
| the concierge skill, its conformance test, and its eval | — |

The concierge skill goes whole. Every flow it drives either starts a conversation through
`camp launch` or resumes one through `camp launch --resume`, and it exists to serve the
phone-driven sessions the spec gives up. Nothing is left for it to do.

## The account a window runs on

A group can declare the account its conversations run under, as `[launch] account`. `camp
launch` honored it: the pane's environment dropped every ambient account variable and then
assigned the declared one.

With `camp launch` gone, the workspace session itself carries both halves: the scrub as removals
and the declared account as assignments, stated on the session when the door creates it, so every
pane the session starts inherits them. The door asks the same resolver that workspace provisioning
already asks when it seeds the account's trust entry, so the account a pane runs on and the account
its workspace was trusted in cannot disagree. A group that declares no account gets no assignment,
which is how the harness states its default.

## The allowlist goes, the floor stays

`[launch] roots` answered one question: which directories an operator may name to `--dir`. With
`--dir` gone nothing asks it. A conversation is recorded only when its directory is inside the
workspace, and no config value widens that.

The credential floor answers a different question, whether a directory is a credential store, and
it stays on every path that roots or records a window.

Existing group configs name `roots`, and `[launch]` refuses keys it does not know. Refusing
`roots` outright would make every camp command fail for those groups until someone edited the
file. So `roots` stays a known key that grants nothing, and camp names it once per invocation.
Any other unknown key under `[launch]` is still refused.

## Retired verbs point at a verb that exists

A retired verb prints the verb that replaced it and exits 1. `launch` points at `attach`.
`resume` and `bookmark` pointed at `launch --resume`, which is now retired itself, so they
point at `attach` too. Reaching a workspace through the door brings its windows back, and each
window that held a conversation says how to resume it.

The message says *replaced* rather than *renamed*, because it is shared by verbs that were
renamed and verbs whose job moved. The check that every redirect target is a live verb reads
camp's real verb registry, not merely "not itself retired".

## What `camp kill` still recognizes

`camp kill <ref>` decides whether a pane is camp's by matching the command it was started with.
It used to accept two shapes: the old launch composition and the resume composition. The launch
composition no longer exists, so a pane started by the old `camp launch` is no longer recognized.
`camp kill` refuses it as not camp's, and `tmux kill-session` still ends it. No running session
on this machine has that shape. The resume shape is still recognized until `camp kill` itself
retires.

## After a transfer

A completed `camp transfer` lists each conversation it moved and how to resume it. That line
named `camp launch --resume`. It now shows the harness's own resume command, the same line a
resurrected window shows.

```
    8f2c1a3e-… @ trailhead
      resume with: claude --resume 8f2c1a3e-…
```

## State — camp launch in any flavor prints its replacement and starts nothing

```
$ camp launch camp-cli
camp launch: this command has been replaced — use 'camp attach' instead.
$ echo $?
1
```

The same line, the same exit status, for `camp launch --resume <ref>`, `camp launch --dir
<path>`, and `camp launch <slug> --host <name>`. No tmux session or window is created, no
enumeration runs, and nothing is forwarded to another machine.

## State — A retired redirect that pointed at launch now names a verb that still exists

```
$ camp resume 8f2c
camp resume: this command has been replaced — use 'camp attach' instead.
$ camp bookmark
camp bookmark: this command has been replaced — use 'camp attach' instead.
```

Every retired verb's replacement is a verb camp dispatches. A redirect that points at another
retired verb, or at a token camp does not know, fails camp's own test suite rather than
reaching an operator.

## State — A group config carrying the launch directory allowlist roots no session outside the workspace tree

The group config still reads `[launch] roots = ["~/code"]`. Every camp command for that group
works and says once, on stderr:

```
camp: [launch] roots in /Users/you/.config/camp/groups/trailhead.toml is no longer used and grants nothing — remove it
```

A conversation started in `~/code/elsewhere`, a directory the allowlist names but outside the
workspace, is not recorded, exactly as it would not be with no `roots` at all.

## State — A flag naming a directory outside the workspace tree roots no session

```
$ camp launch --dir ~/code/elsewhere
camp launch: this command has been replaced — use 'camp attach' instead.
$ camp new scratch --launch
camp new: unknown flag '--launch'
```

No flag remaining on any camp verb names a directory for a conversation to be rooted at. A
conversation's directory is its pane's own, and the floor above bounds what is recorded.

## State — camp composes no conversation command, so no remote-control or visible-name flag

The operator runs the harness themselves, in a pane of the workspace session. There is no
`--remote-control` and no `--name` in any command camp's harness seam can compose, because camp
composes no command to start a conversation at all.

## State — A recorded conversation has no enumeration poll behind it

The record gains its entry when the harness starts the session and tells camp's session-start hook
its id. camp never asks the harness to list its sessions to learn or confirm that id. A harness
whose enumeration would fail or hang does not delay or fail the conversation.

## State — A pane's environment scrub and account come from the session

A pane in a group with a declared account, as the pane itself sees its environment:

```
CLAUDE_CONFIG_DIR=/Users/tduffield/.claude-levr
```

with none of the harness's scrubbed names present, whatever the tmux server's own global
environment carries. For a group with no declared account there is no assignment, and the scrub
is unchanged. The removals and the assignment are the workspace session's own environment
(`tmux set-environment -r NAME` and `tmux set-environment NAME value` against that session), which
overrides the server's for every pane the session starts.
