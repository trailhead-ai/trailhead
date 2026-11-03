# camp env ls reads hosts concurrently and can name one host

The rendered surface for two changes to `camp env ls`: every machine is read at the same time, and
the listing can be pointed at one host.

```
camp env ls          # this machine, then every opted-in host, all read at once
camp env ls HOST     # that one host, opted in or not
```

`HOST` is a host declared in the hosts file, this machine's declared `self_name`, or `local`: the
same host grammar `camp env attach HOST/NAME` already accepts, refused with the same words when it
is none of those. At most one host is named. `--host` keeps its existing refusal (`--host has no
meaning here`), because naming a host is a positional argument to this verb, not camp's
run-on-another-host flag.

## What already holds, and is not re-litigated here

How one machine is read is unchanged: the contract a container follows, the inventory command, the
30-second bound on each read, the closed set of outcomes (environments, none, unreadable with a
reason, timed out, Docker not installed, Docker not running), and the rule that far-side text
reaches the terminal only with control characters removed. The listing's layout is unchanged too:
the `attached:` lines first, a heading per machine, one aligned and sorted line per environment,
and exit status 0 whenever the listing renders. See the design doc for
`camp env ls lists environments on this machine and on opted-in hosts`.

## How the machines are read

Every machine the listing will show is read at the same time: this machine's local Docker read and
each opted-in host's ssh read all start together. A listing across several hosts therefore takes
about as long as the slowest single read, not the sum of them. Each read keeps its own 30-second
bound, so the whole listing is bounded by 30 seconds plus the connect time, however many hosts are
opted in.

What is printed does not depend on which read finished first. Blocks always appear in the same
order — this machine first, then the hosts in the order `env_hosts` names them — and each block is
printed as soon as its own read and every read before it in that order have finished. A fast host
listed after a slow one waits for the slow one; a slow host listed last never holds back the blocks
above it.

A bare-name `camp env attach NAME` reads the same set of machines the same way, so it waits for the
slowest of them rather than all of them in turn. What it decides from those reads is unchanged.

## Interrupting a listing

Ctrl-C during a listing ends it promptly. Blocks already printed stay on the screen, nothing more is
printed to stdout, and stderr carries one line, `camp env: interrupted`. The exit status is 130.
Every local `ssh` and `docker` the listing started is ended before it exits, whether the interrupt
reached them too (a terminal's Ctrl-C) or only reached camp (a signal sent to camp's process
alone). A remote Docker command already running on a host may run on until it finishes, as it does
when a read times out.

## Naming one host

`camp env ls HOST` reads that host alone. It reads it whether or not `env_hosts` names it, and
reads no other machine. When anything is attached, the `attached:` lines still come first, because
they describe this machine and cost no read. Then comes one block, headed as the full listing would
head it, and nothing after it: the closing `not opted in:` line belongs to the full listing, which
is the only place a host can be silently left out.

The exit status is 0 whenever that block renders, whatever the host said, as for the full listing.
It is 1 only when the command refuses to list at all: the host is not one camp knows, the text is
not a host name, more than one host is named, or the hosts file is unusable. A refusal prints
nothing on stdout.

## State — named host with no environments

The host answered, and no environment follows the contract there. Its heading is followed by
`  no environments`, exactly as in the full listing.

```
perseus
  no environments
```

## State — named host with one environment

The heading is followed by one line: group, name and state.

```
perseus
  levr  main  running
```

## State — named host with many environments

The heading is followed by one line per environment, sorted by group then name, the group and name
columns aligned within the block.

```
perseus
  levr  ben-feature  stopped
  levr  main         running
```

## State — named host unreadable

The read did not produce a believable answer. The heading is followed by `  unreadable: <reason>`,
the same reasons in the same words as the full listing, and the exit status is still 0.

```
perseus
  unreadable: unreachable (Could not resolve hostname)
```

## State — named host timed out

Nothing came back within 30 seconds. The heading is followed by
`  unreadable: no answer within 30s`.

## State — named host without Docker running

The host answered, but Docker did not. The heading is followed by `  Docker is not running`, or
`  Docker is not installed` when there is no `docker` to run. Neither is shown as unreadable.

## State — named host not declared

The name is well formed but is not a `[hosts.<name>]` table, this machine's `self_name`, or
`local`. Nothing is read and nothing is printed on stdout. stderr carries the same refusal attach
gives for that name, naming the hosts file and the hosts it declares, and the exit status is 1:

```
camp env: no host named orion in /home/tom/.config/camp/hosts.toml; declared: perseus, andromeda
```

Text that is not a host name at all (`Perseus`, `perseus/levr`, an empty string) is refused with
attach's host-name rule instead, also exit 1.

## State — named host is this machine

`local`, or the name this machine declares as `self_name`, reads this machine through Docker
locally, never over ssh, even when the hosts file also declares a `[hosts.<name>]` table under that
name. The block is headed `this machine (<self_name>)`, or `this machine` when no `self_name` is
declared.

```
this machine (andromeda)
  levr  main  running
```

## State — hosts answering out of declared order

With `env_hosts = ["perseus", "orion"]`, orion answers in one second and perseus in ten. The
listing still prints this machine, then perseus, then orion. Orion's block appears right after
perseus's, once perseus has answered, never above it. If this machine's own Docker read is the
slowest, nothing is printed until it finishes, and then every block that is ready follows at once.

```
this machine (andromeda)
  levr  main  running
perseus
  levr  main  running
orion
  no environments
```
