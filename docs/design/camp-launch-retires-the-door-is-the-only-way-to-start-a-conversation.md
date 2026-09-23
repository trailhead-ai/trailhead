# camp launch retires: the door is the only way to start a conversation

The rendered surface for retiring the launch verb family: `camp launch` in every flavor, the
directory allowlist that fenced `--dir`, and the harness composition behind them. After this, a
Claude conversation starts in exactly one way. The operator reaches the workspace's tmux session
through the door, and presses the ordinary new-window key inside it.

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
assigned the declared one. The window key composed the first half of that and not the second,
so a window opened in a group with a declared account ran on the default account instead.

With `camp launch` gone the window key is the only way in, so it composes both halves. It asks the
same resolver that workspace provisioning already asks when it seeds the account's trust entry,
so the account a window runs on and the account its workspace was trusted in cannot disagree.
The pane command is `env`, then one `-u` per scrubbed name, then one `NAME=value` per binding,
then the conversation's argv. A group that declares no account gets no assignment, which is how
the harness states its default.

## The allowlist goes, the floor stays

`[launch] roots` answered one question: which directories an operator may name to `--dir`. With
`--dir` gone nothing asks it. A composed window is rooted inside the workspace or refused, and no
config value widens that.

The credential floor answers a different question, whether a directory is a credential store, and
it stays on every path that roots a window. The window key already applies it directly.

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
camp: [launch] roots in ~/.config/camp/groups/trailhead.toml is no longer used and grants nothing — remove it
```

A window rooted at `~/code/elsewhere`, a directory the allowlist names but outside the workspace,
is refused exactly as it would be with no `roots` at all:

```
camp: cannot open window — directory ~/code/elsewhere is outside the workspace root ~/.local/state/camp/trailhead/worktrees/camp-cli; choose a directory inside the workspace
```

## State — A flag naming a directory outside the workspace tree roots no session

```
$ camp launch --dir ~/code/elsewhere
camp launch: this command has been replaced — use 'camp attach' instead.
$ camp new scratch --launch
camp new: error: unrecognized arguments: --launch
```

No flag remaining on any camp verb names a directory for a conversation to be rooted at. The
window key's directory is the pane's own, and the floor above bounds it.

## State — A window composed by the workspace key carries no remote-control or visible-name flag

The window's start command, as tmux reports it:

```
env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CONFIG_DIR … CLAUDE_CONFIG_DIR=/Users/tduffield/.claude-levr claude --session-id 3b0e…
```

There is no `--remote-control` and no `--name`, in this command or in any command camp's
harness seam can compose. For a group with no declared account there is no assignment, and the
`-u` scrub is unchanged.

## State — A composed conversation starts with no enumeration poll behind it

The window opens and the record gains its entry, with the conversation id camp chose, in the same
step. camp never asks the harness to list its sessions to learn or confirm that id. A harness
whose enumeration would fail or hang does not delay or fail the window.

## State — A composed window's environment scrub rides the command the window runs

The scrub and the account assignment are both tokens of the pane's own command, shown above.
The tmux request that creates the window carries no `-e` and no `set-environment`, because a
pane inherits the tmux server's environment and a scrub applied to the request would do
nothing.
