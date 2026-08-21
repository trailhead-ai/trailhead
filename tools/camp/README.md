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
camp new <slug>      # create or enter a workspace
camp new <slug> --launch  # create or enter, then start a detached session in it
camp pwd <slug>      # print workspace path
camp list            # list all worktrees (alias: ls)
camp status          # show git + drift status
camp launch <slug>   # start a detached harness session in a workspace
camp launch --resume <ref>   # bring a dead session back where it started
camp sessions        # list the live harness sessions camp can see
camp sessions --recoverable  # list the dead ones that could be brought back
camp kill <ref>      # stop one session and reclaim its memory
camp remove          # tear down a worktree (alias: rm)
camp --help          # full command reference
camp --version       # show version + resolved binary path
```

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

### `camp new` and `camp remove` change your shell's directory

camp never starts, stops, or replaces a process, and it cannot change your shell's
working directory on its own — it only *answers* where to go, as exactly one line
on stdout. Acting on that answer takes a shell function, which is what the
`shellenv` line installs:

```sh
eval "$(/path/to/trailhead/bin/trailhead shellenv)"
```

The wrapper it defines intercepts `camp new` and `camp remove` and does the `cd`
for you. Without it, the printed path is inert — use `cd "$(camp pwd <slug>)"`.

## Detached sessions

`camp launch <slug>` starts a harness session in a tmux pane rooted at the
workspace and returns immediately — nothing is attached to your terminal. camp
mints the session id itself, so it can hand it back on stdout (one line,
`--json` for the machine shape) and report the workspace, the tmux name, and a
paste-ready `tmux attach -t <name>` on stderr.

```
camp launch <slug> [--json]                 # start one; stdout is the session id
camp launch --dir <path> --group <name>     # start one at a named directory
camp launch --resume <ref> [--group <name>] # bring a dead one back
camp sessions [<slug>] [--dir <path>] [--json]              # what is live
camp sessions --recoverable [<slug>] [--dir <path>]         # what is dead
                          [--limit <n>|--all] [--json]
camp new <slug> --launch [--no-wait] [--json]
```

`camp launch` has three addressing forms and they are mutually exclusive: a slug
roots the session at that workspace, `--dir` roots it at a directory you name,
and `--resume` re-enters a session the harness already has a transcript for,
rooted wherever that session recorded it started. One launch is rooted one way.

Its exit codes carry more than pass/fail. `0` means the session launched and
stdout is its id. `1` means camp refused, nothing was started, and stdout is
empty — the reason is on stderr. `2` means a `--resume` reference matched more
than one session: the candidates are printed on stdout to choose between, so it
is an answer to narrow, not a command that broke.

The launch is confirmed, not assumed: camp polls harness enumeration until the
new session id appears and refuses (killing the pane) if it never does — a
session stalled at an unanswered trust prompt is invisible to enumeration, so
camp pre-seeds trust for the directory it is about to root the session at. The pane also drops the
parent session's environment, so a launched session never inherits the
credentials of the session that launched it.

`camp new <slug> --launch` blocks until the workspace finishes provisioning
before launching; `--no-wait` launches immediately and leaves later provisioning
failures to surface under `camp status <slug>`. Either way stdout stays exactly
the workspace path.

### Rooting a launch at a directory

`camp launch --dir <path>` is **off by default**. A directory a launch may root
at has to be allowlisted in the group's config first:

```toml
[launch]
roots = ["~/code", "/srv/work"]
```

A target is eligible when it is one of those entries or sits under one — equal
or under, so allowlisting `~/code` never allowlists `~`. With no `[launch]`
block at all, no directory is eligible and camp says so rather than falling back
to something permissive.

`--dir` also **requires an explicit `--group`**. The allowlist is the containment
boundary, so which group supplies it must never depend on the directory camp
happened to be invoked from.

**A credential deny list overrides the allowlist unconditionally.** `~/.ssh`,
`~/.gnupg`, `~/.aws`, `~/.azure`, `~/.kube`, `~/.docker`, `~/.config/gcloud`,
`~/.netrc`, `~/.config/gh`, `~/.npmrc`, `~/.pypirc`, and `~/.git-credentials`
are fixed in camp's code. No group config can permit one, and the rule denies a
target that is at, under, **or above** any entry — so `roots = ["~"]` cannot
launder a home directory full of credential stores past the gate in one line.
The refusal names the credential rule and never mentions the allowlist, because
editing the allowlist is not the fix.

### Bringing a dead session back

`camp launch --resume <ref>` re-enters a session from the harness's own
transcript, from any directory. `<ref>` is an unambiguous prefix of either the
derived session name (`camp-<slug>-<uuid8>`) or the session uuid — camp resolves
it, and an ambiguous one lists the candidates and exits 2 rather than guessing.

A session that started inside a camp workspace needs no `--group`: camp built
that directory and reads the owning group off the path. A session rooted
anywhere else needs an explicit `--group` and is then held to that group's
allowlist as it stands today, exactly like `--dir`.

Refusals each name their own situation: the session is still running, its
directory is gone, its start directory could not be read at all, its root is not
eligible, or the reference matched nothing. camp never recreates a torn-down
directory to resume into it.

`camp sessions --recoverable` is how those references are discovered — every
session the harness kept a transcript for, minus the ones running now, newest
first and capped at the newest 20 with the total named (`--limit <n>` or `--all`
widen it). A row whose directory no longer exists is listed and marked rather
than hidden, since seeing it is how you learn why the resume will refuse.

Both listings take the same scope: a slug scopes to that workspace, `--dir
<path>` scopes to a directory and everything under it, and neither is
eligibility-gated — the allowlist fences launching, not looking.

Neither the launch flavors nor either listing writes anything camp keeps. Every
session they can name already exists in the harness's own store, so there is no
camp-side record to go stale — which is why a reference works from a plain shell
that has never seen the session before.

`camp sessions` degrades rather than failing: an enumeration error, an unknown
harness, or an absent tmux prints a notice on stderr and an empty list on
stdout, exit 0. `--recoverable` degrades the same way when the live set cannot
be determined — it reports none rather than printing an unsubtracted pool that
might include sessions running right now. It has two refusals instead, because
neither has an empty listing as an honest answer: camp cannot name a harness for
any configured group, or the harnesses it can name keep no transcripts it can
read. An unusable `--limit` refuses too.

## Group setup

```
camp group <name> --member NAME=PATH [--member NAME=PATH ...]
```
Authors a group config TOML and wires SessionStart hooks into each member repo.

Everything else in a group config is optional and off until you add it — including
the `[launch] roots` allowlist that directory-rooted launches need, described under
[Rooting a launch at a directory](#rooting-a-launch-at-a-directory). The full schema
is documented on `camp.group.config`.
