# The session inventory retires: a workspace is addressed only by its slug

The rendered surface for retiring camp's per-conversation inventory and addressing family:
`camp sessions` in every form, and conversation-reference addressing on `camp kill` and
`camp attach`. Afterwards camp addresses exactly one kind of thing, a workspace, and it
addresses it by slug. `camp list` shows the workspaces, `camp attach <slug>` reaches one, and
`camp stop <slug>` puts one down.

This is the last cleanup slice of the consolidated session model. After it, every retired verb
points at a verb camp still dispatches.

## What goes and what stays

| Goes | Stays |
|---|---|
| `camp sessions`, with `--recoverable`, `--all`, `--dir`, `--limit`, `--name`, `--host`, `-a`, `-g` | `camp list`, with `-g`, `-a`, and `--host`, one row per workspace |
| `camp kill <ref>`, with `--host` | `camp stop <slug>`, which previews, reconciles, kills, and checks |
| `camp attach <ref>`: a conversation id or retired session-name prefix | `camp attach <slug>`, the door, and bare `camp attach`, its numbered picker over workspaces |
| `camp attach -a`, `camp attach -a <ref>`, and the `--resolve --json` / `--list --json` probes behind them | `camp attach <slug> --host <name>`, with the group resolved locally and forwarded to that machine's door |
| the session picker that bare `camp attach` fell back to outside a group | — |
| the ambiguous-reference exit status, 2 | exit 0 for success, 1 for refusal, and the door's partial-resurrection status |
| the conversation-reference resolver, the pane-ownership match, and the enumeration glue behind them | the harness's session enumeration, which the teardown guard and `camp transfer` still read |

## One kind of address

A conversation reference was a prefix of a conversation id, or of the tmux session name camp
used to derive per conversation. Nothing mints that session name any more, and a conversation id
is something only the harness can list. Both were ways of finding a conversation, and a
conversation is not a thing camp addresses now. It lives in a window, inside a workspace's
session, and the workspace brings it back.

So `camp attach` resolves its argument as a slug in one group, and nothing else. The group is
resolved the way every other group-scoped verb resolves it: `--group`, else the directory you are
standing in. Where no group resolves, `camp attach` refuses in the same words `camp stop` and
`camp pwd` use. It no longer falls back to a picker over every conversation on the machine.

## Two replacements, not one

`camp sessions` answered two questions. Which workspaces have a session, and how large is it?
`camp list` answers that. Which conversations can I get back? The door answers that: reaching a
workspace brings every window back, and a window that held a conversation shows how to resume it.
`sessions` points at `list`.

`camp kill <ref>` stopped one conversation's tmux session. A workspace now has one session, and
`camp stop <slug>` takes it down, showing first what it is about to take down. `kill` points at
`stop`. `stop` needs no ownership match: the only session it can name is `camp-<group>-<slug>`,
which camp composed itself, and it never names a session `camp list` reports as unmanaged. The
name is the ownership check. A session someone creates by hand under exactly that name is
treated as the workspace's own. This is accepted: the only way to create one is as the same
user, and no privilege boundary is crossed.

## Across machines

`camp attach <slug> --host <name>` resolves its group locally — `--group` if given, else the
group the operator's own cwd resolves to, the same rule a local attach uses — and forwards the
slug plus that resolved group to the named machine. What changes is how the far side reads it: as
a slug in the forwarded group, through that machine's own door. There is no probe of the far side
beforehand, so the forwarded command's resolution is the only one, and there is no second answer
for it to disagree with. No group resolving locally refuses locally, with the same needs-group
line every other group-resolved verb prints, and no machine is contacted.

`camp attach -a` goes. Both of its forms read every machine's list of conversations, and that
list is what is retiring. To find a workspace on another machine, `camp list -ag` shows every
group on every machine, and `camp attach <slug> --host <name>` reaches it.

Cross-machine `stop` is not added here. `camp kill --host` retires with `kill`, and a
cross-machine stop is a new capability that belongs to the cross-host work the spec defers. To
stop a workspace on another machine, attach to it with `--host` and run `camp stop` there, or
run `ssh <host> camp stop <slug> --group <group>` — `camp stop` also needs a group from that
machine's own `$HOME`, which `ssh` never resolves on its own.

## Machine output

`camp sessions --json` goes with `sessions`. Its replacement is `camp list --json`, whose rows are
workspaces rather than conversations. A consumer that parsed conversation rows has no drop-in
replacement, and none is offered. No caller in this repository, its sibling repositories, or the
operator's dotfiles reads it. A caller outside them that still runs it gets the redirect line on
stderr, empty stdout, and exit 1, so it fails at its JSON parse rather than reading stale data.

## State — camp sessions in any form prints its replacement and lists nothing

```
$ camp sessions
camp sessions: this command has been replaced — use 'camp list' instead.
$ echo $?
1
```

The same line and exit status for `camp sessions --recoverable`, `camp sessions <slug> --json`,
`camp sessions -g`, and `camp sessions --dir <path> --limit 5`. No harness enumeration runs, and
no transcript store is read.

## State — camp kill naming a session ref prints its replacement and kills nothing

```
$ camp kill 8f2c
camp kill: this command has been replaced — use 'camp stop' instead.
$ echo $?
1
```

The same for a full conversation id, a retired session-name prefix, and a bare `camp kill`. No
tmux session is signalled, and the ambiguous-reference status 2 is never returned.

## State — camp attach naming a session ref rather than a slug refuses it as an unknown slug and attaches nothing

```
$ camp attach 8f2c --group trailhead
camp attach: no workspace named 8f2c in group trailhead — 'camp list' shows its workspaces
$ echo $?
1
```

A conversation id, a retired session-name prefix, and a mistyped slug are all read as slugs and
refused in the same words `camp stop` uses, plus a pointer to `camp list`. An operator who typed a
conversation reference from habit is sent to the listing that replaced it. No tmux session is
created or joined.

With no group resolvable, the refusal names what is missing:

```
$ cd /tmp && camp attach 8f2c
camp attach: no group resolved from cwd — pass --group <name> or run from inside a group member directory
```

The all-machines form refuses and names what replaced it:

```
$ camp attach -a 8f2c
camp attach: -a is retired — find the workspace with 'camp list -ag', then 'camp attach <slug> --host <name>'
$ echo $?
1
```

Bare `camp attach -a`, and `--resolve` or `--list` in any combination, refuse the same way, and
no machine is contacted.

## State — A retired verb given with --host prints its replacement locally and forwards nothing

```
$ camp sessions --host andromeda
camp sessions: this command has been replaced — use 'camp list' instead.
$ camp kill 8f2c --host andromeda
camp kill: this command has been replaced — use 'camp stop' instead.
```

Retired verbs are answered before host routing, so no ssh connection is opened, and a peer
running older camp is never asked to run the retired verb. The same holds for `-a` and `-g`.

## State — A redirect whose target is not a live verb fails the suite

Every retired verb's replacement is a verb camp dispatches. `sessions` points at `list` and
`kill` at `stop`. Both are live, and neither is itself retired. A redirect that points at a
retired verb, or at a token camp does not know, fails camp's own test suite rather than reaching
an operator.
