# camp env ls lists environments on this machine and on opted-in hosts

The rendered surface for one question: which dev environments are running where. There is one new
verb, one new hosts-file setting, and one contract a project's containers follow to be seen.

```
camp env ls          # this machine, then every opted-in host, in the order the setting names them
```

`camp env ls` takes no arguments in this slice. A bare `camp env`, or a subcommand other than `ls`,
prints one usage line naming `ls` and exits 1. It ignores the current camp group: an environment
belongs to whichever group its labels name, and the listing spans every group on every machine it
reads. Naming one host explicitly, and reading hosts concurrently, come later; until then hosts are
read one after another, each bounded on its own.

## What already holds, and is not re-litigated here

camp reaches a declared host over a single non-interactive `ssh` with a pinned host key, no
prompting, and a bounded connect time, and sorts what comes back into a closed set of outcomes
(unreachable, host key unknown, host key changed, credentials refused, stopped responding, refused).
This slice reuses that transport for a command that is not camp. It adds no new transport failure
vocabulary. What it adds is a reading of what Docker printed.

## The contract a project's containers follow

A container counts as an environment when all three of these hold:

- it carries the label `camp.env.group`, whose value is the group, such as `levr`;
- it carries the label `camp.env.name`, whose value is the environment's name, such as `main`;
- its container name is the group, then `-env-`, then the name: `levr-env-main`.

Group and name are each lowercase letters, digits and hyphens, starting with a letter or digit:
camp's own identifier rule. A container that carries both labels under any other name is skipped
without comment. That is the contract: the name rule is how a project says which of its labelled
containers is the environment and which are helpers beside it.

camp owns these names. A project adopts them by adding the two labels beside its own; it does not
configure them.

## Which machines are read

```toml
# ~/.config/camp/hosts.toml
self_name = "andromeda"
env_hosts = ["perseus"]

[hosts.perseus]
ssh = "tduffield@perseus"

[hosts.toms-macbook-pro]
ssh = "tduffield@toms-macbook-pro"
```

`env_hosts` is a top-level list of declared host names, empty when absent. Only those hosts are ever
contacted by `camp env ls`. Every name in it must be a `[hosts.<name>]` table in the same file, or
this machine's own `self_name`. A name that is neither is refused, and so is a name listed twice.
The refusal names the file and the offending entry and lists nothing. The setting is read only by
`camp env`, so a mistake in it never breaks another camp verb, and an older camp ignores it as it
ignores any top-level setting it does not know.

This machine is always read, and always locally, never over ssh. A `[hosts.<name>]` table or an
`env_hosts` entry whose name equals `self_name` is this machine under another heading. It is not
read a second time. That case is common, because one hosts file is often synced to every machine
its owner uses.

## How a machine is read

Every machine is read with the same Docker command: the local one run as a subprocess, a remote one
sent over the transport as one plain command that a fish login shell runs unchanged. The command
asks for every container carrying both contract labels. Its table-format output always starts with
a header line, and each container's fields are JSON-encoded, so a label value carrying a `|` or a
newline cannot break a record apart or forge a second one.

A remote reply is untrusted text, and the read stops taking it after 1 MiB; a larger reply makes the
host unreadable (`reply too large`). The ssh server on a Mac may report exit 0 whatever the remote
command returned, so camp never decides from the exit code. The reply is believed only when stdout
starts with the header and every line after it parses as one record, each field a JSON string.
Anything else, including any field that is not a string and any parse error at all, makes the host
unreadable; a partial list is never shown. One gap is named, not closed: the reply carries no end
marker, so a stream cut cleanly between two records would read as a shorter list. A connection that
drops is ssh's own failure and is caught; the gap is a far side that truncates its own output. Only ssh's stdout is parsed. Stderr is consulted only to
name the reason a read failed. Before any of it reaches the terminal it is reduced to printable characters on one line: control and
format characters (newline, tab, bidirectional overrides and zero-width characters included) and any
byte that did not decode are removed, and the result is cut to 200 characters.

Each read is bounded: a host that has not answered within 30 seconds is abandoned and shown as
timed out. The bound covers the connection and Docker's answer together.

## The listing

```
this machine (andromeda)
  levr  main          running
  levr  ben-feature   stopped
perseus
  levr  main          running
not opted in: toms-macbook-pro
```

This machine comes first, headed `this machine` with its `self_name` in parentheses when one is
declared. The opted-in hosts follow, in the order `env_hosts` names them. Under each heading is one
line per environment: group, name, and `running` or `stopped`. The group and name columns are
aligned within each machine's block. Every Docker state other than running reads as stopped.
Environments are sorted by group, then name, so two runs can be compared line by line.
Each block is printed as soon as that machine's read finishes, this machine's first, so a slow or
dead host at the end of the list never hides the answers already in hand.

The exit status is 0 whenever the listing renders, whatever any host said. A host that could not be
read is a line in the answer, not a failure of the command, which is how camp's other all-hosts
answers behave. The command exits 1 only when it refuses to list at all: the hosts file is
malformed, `env_hosts` is invalid, or an argument was given.

## State — no environments on a host

The read succeeded: the header came back and no record followed it, or every labelled container
broke the name rule.

The host's heading is followed by `  no environments`. It never collapses to a bare heading, and
never to silence, because "this machine answered and runs nothing" is a different next move from
"this machine did not answer".

## State — one environment

The heading is followed by one line giving the group, the name and the state. Nothing else is said;
there is no count and no summary line.

## State — many environments across hosts

Every machine gets its own block, this machine first, then the hosts in `env_hosts` order, whatever
order they answered in. Within a block, lines are sorted by group, then name, with the group and name columns aligned. Each
block appears as its read finishes, so the listing builds down the page rather than arriving all at
once after the slowest host.

## State — host unreadable

The read did not produce a believable answer. Either the transport classified a failure
(unreachable, host key unknown or changed, credentials refused, refused), or the reply had no
header, a record that did not parse, or a field outside the identifier rule.

The heading is followed by `  unreadable: <reason>`. The reason names the cause in the operator's
terms and, where there is one, the next step: for example `unreachable (Could not resolve
hostname)`, `the host key is not pinned; run ssh <destination> once to pin it`, `ssh credentials
were refused`, or `the reply does not start with the inventory header`. Text from the far side, such as ssh's or
Docker's own error line, appears only with control characters removed and cut to one line. The host
is never shown as having no environments, and no environment from a reply that failed validation is
shown.

## State — host timed out

Nothing came back within 30 seconds. The heading is followed by
`  unreadable: no answer within 30s`. It is the same unreadable shape as above, with the timeout
named as the reason, so a slow machine is not mistaken for a broken one. The local ssh is ended; the
remote Docker command may run on until it finishes.

## State — Docker missing or not running

The machine answered, but Docker did not. Either the `docker` command was not found, or the daemon
could not be reached. Docker Desktop on a Mac whose user is not logged in is the expected case for
the second.

The heading is followed by `  Docker is not installed` or `  Docker is not running`. Neither is
shown as unreadable: nothing runs there, so there is nothing to be uncertain about. On a Mac,
`Docker is not installed` can also mean `docker` is not on the PATH a non-interactive ssh command
gets; the README says so. A Docker failure
that is neither of these, such as a permission error on the socket, is unreadable with Docker's own
line as the reason.

## State — declared hosts not opted in

Hosts declared in the hosts file but absent from `env_hosts` (other than this machine) are named on
one closing line, `not opted in: <name>, <name> (add to env_hosts in <hosts file path>)`, in
declaration order. They were not contacted. The
line makes a host left out by configuration visibly left out, rather than silently absent. With no
such hosts the line is not printed.
