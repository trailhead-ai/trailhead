# camp env attach, status and detach for a remote environment

The rendered surface for three questions: put a remote environment on this machine's localhost,
say what is on it, and take it off again. There are three new verbs beside `camp env ls`, and one
new field the environment contract carries.

```
camp env attach NAME              # a bare name, resolved across this machine and every opted-in host
camp env attach HOST/NAME         # that host only, opted in or not
camp env attach HOST/GROUP/NAME   # that host, that group
camp env status                   # what is attached on this machine
camp env detach                   # end it and free its ports
```

Like `ls`, all three ignore the current camp group: an environment is identified by its host, its
group and its name, wherever the command is run from. `HOST` is a host declared in the hosts file.
This machine's environments, named as `local/NAME` or by this machine's `self_name`, cannot be
attached yet; that is refused, and says so (see "this machine" below).

A bare `camp env`, or an unknown subcommand, prints one usage line naming `ls`, `attach`, `status`
and `detach`, and exits 1.

## What already holds, and is not re-litigated here

`camp env ls`, its contract labels (`camp.env.group`, `camp.env.name`), the `<group>-env-<name>`
naming rule, the `env_hosts` opt-in, and every way a machine's read can fail are as
`camp-env-ls-lists-environments-on-this-machine-and-on-opted-in-hosts.md` describes. Attach reads
machines exactly as `ls` does, and a read that `ls` would show as unreadable is unreadable here for
the same reason, with the same words.

## What the contract adds: published ports

An environment publishes each of its ports on the loopback address 127.0.0.1 only, over TCP. Each
published port maps to a container port, and that container port is the **standard port**: the
port an attachment binds on this machine. An environment publishing `127.0.0.1:18080->8080/tcp` is
reached on this machine at `localhost:8080`, whatever host port its own machine used to keep it
apart from its neighbours.

The inventory read now asks Docker for each container's published ports too. The read is believed
only when every record's ports validate: each entry loopback, TCP, and both ports in 1024 to 65535,
written in ASCII digits. A record publishing anything else (a wildcard or IPv6 address, UDP, a
privileged port) makes the whole machine unreadable, naming the line, as any other bad record
does. A record publishing the same container port twice
(`127.0.0.1:18080->8080/tcp, 127.0.0.1:18081->8080/tcp`) is a contract defect too, since one
standard port is one port to hold: the machine is unreadable, naming the line and not the value.
A stopped container publishes nothing, and that is valid.

`camp env ls` does not show ports. The listing reads them only so that one read serves both verbs.

## How a name is resolved

- **`HOST/GROUP/NAME`** reads `HOST` alone and takes that exact environment.
- **`HOST/NAME`** reads `HOST` alone, whether or not it is in `env_hosts`. One match is taken. A
  name found in two groups on that host is ambiguous (below).
- **`NAME`** reads this machine and every host in `env_hosts`, and never any other host. It is
  taken only when exactly one environment anywhere has that name, and every machine that could have
  had one answered.

A machine where Docker is not installed or not running blocks nothing: nothing runs there, so
nothing can be missed there. A machine whose read is unreadable, this one included, blocks a bare
name, because the name might be there.

`HOST` must be a `[hosts.<name>]` table in the hosts file. `HOST`, `GROUP` and `NAME` are each
lowercase letters, digits and `-`, starting with a letter or digit; any other target is a usage
error, and one whose host part breaks the rule says that host names must be lowercase letters,
digits and `-`. The hosts file does not hold the keys of its `[hosts.<name>]` tables to that rule
(it checks `self_name` and the `env_hosts` entries, not the keys, and loading does not change
that), so a declared host whose key falls outside it cannot be attached in any form, and
`env_hosts` could not name it either.

## How an attachment is made, and found again

An attachment is one background `ssh` forward from this machine to the environment's host, holding
each standard port on 127.0.0.1 and carrying it to the published port on the host's own loopback.
It is reached through the same hosts-file destination, pinned host key and non-interactive
settings as every other camp call over ssh, with connection sharing turned off
(`ControlMaster=no`, `ControlPath=none`) so a shared connection the user's own ssh config sets up
can never take the forward over where camp cannot see it. Forwards in the user's own ssh config
for that host (`LocalForward`, `RemoteForward`, `DynamicForward`) are not cancelled, because
`ClearAllForwardings` would cancel camp's `-L` too: they ride along with the attachment. The
success line and `status` name only the environment's ports, and if a port of a forward from the
user's own config is busy, the attach fails on that port. It is kept alive with ssh's own
keep-alives, so a forward whose link has died ends on its own within about 45 seconds.

Nothing about an attachment is written to disk. camp finds it again by reading the process table:
a process owned by this user, run as `ssh`, whose options (read as OpenSSH reads them: up to the
destination or a `--`, an option's argument consumed with it) include `-N`, at least one `-L`, and
exactly one `-o Tag=camp-env:<id>:<host>/<group>/<name>`. `<id>` is chosen fresh for each attach.
The ports it holds are read from its own `-L` arguments, in ASCII digits only. A process that only
mentions the tag elsewhere in its arguments (a `grep`, an interactive ssh running a remote
command, `ssh box pgrep -N -o Tag=...`), or that belongs to another user, is never counted, reported or ended. Because the forward
carries nothing from camp's own install, `status` and `detach` find it from any directory and from
any camp checkout or install, including one whose path contains whitespace.

There is one attachment per machine, across every group, because a machine has one set of standard
ports.

Which ports an attachment binds is decided by the environment's host: the standard ports come from
the container side of its published ports. That is the trust camp already places in a host the
user opted in or named: whoever can start a contract-labelled container there can choose ports on
this machine's loopback in 1024 to 65535. A port already held is refused rather than taken, and the
success line names every port bound.

## The order attach checks things in

1. Is anything already attached? The process table answers this without reading any host, so the
   refusal is immediate.
2. Resolve the name, reading only the machines resolution needs.
3. Is the environment running, and does it publish at least one port?
4. Is every standard port free? camp tries to bind each one itself, with `SO_REUSEADDR` set as ssh
   sets it, on 127.0.0.1, ::1, 0.0.0.0 and ::, and releases it at once. A port that fails any of
   the four binds as in use is held. A port whose only trace is a closed connection in TIME_WAIT is
   free, because ssh binds it too: detach after real traffic frees its ports, and attaching again
   at once works. A machine with no IPv6 (some WSL setups) cannot bind the IPv6 addresses at all,
   and that counts as free on them. Any other bind failure (a permission error, say) is a refusal,
   not a crash: the port is reported held by a process that could not be checked, with the error.
   The rule was measured on Linux and macOS: with `SO_REUSEADDR` on all four addresses, every
   active listener, however it is bound, answers "in use" on at least one, and a TIME_WAIT-only
   port answers free on all four.
5. Start the forward, wait for ssh to confirm every port is bound before it goes to the background,
   and find the started forward in the process table by its `<id>`. ssh is started with its output
   discarded and its error text sent to a temporary file, never to a pipe camp reads: a backgrounded
   ssh keeps the descriptors it was given, so a pipe would never reach its end.

An attach that fails at step 5, or is interrupted at any point after it, ends the forward it
started, found by its own `<id>`, and only that one. It leaves no port held.

## State — attached

```
$ camp env attach main
attached perseus/levr/main on ports 5173 8080 8443; its health was not checked
```

One line on stdout, exit 0. The environment is named in its fully qualified form, whatever form
was typed, and the ports are its standard ports in ascending order. camp says it did not check the
environment's health, because a running container with a forward in front of it can still be
failing behind it.

## State — environment stopped

```
camp env: perseus/levr/main is stopped; start it with its project's own tooling, then attach again
```

On stderr, exit 1. Nothing is started. camp does not start environments.

A running environment that publishes no ports is refused the same way, with its own reason:
`camp env: perseus/levr/main publishes no ports, so there is nothing to attach`.

## State — environment not found

```
camp env: no environment named main on this machine, andromeda or perseus
camp env: no environment named main on perseus
```

On stderr, exit 1. The first form is a bare name: it names every machine that was read. Where a
machine could not be asked because Docker is not installed or not running there, it says so
(`; Docker is not running on perseus`), since that is the likeliest reason a name is missing. Where
declared hosts were left out by `env_hosts`, it names them and the form that reads one anyway:
`(not read: toms-macbook-pro; name one as <host>/main)`. Either form ends with
`; run camp env ls to see what is running`. The second form is a named host: it names only that
host. A host form naming a host the hosts file does not declare is refused as
`camp env: no host named orion in <hosts file>; declared: andromeda, perseus`.

## State — name ambiguous

```
camp env: main matches more than one environment: andromeda/main, perseus/main; attach one of those
camp env: main matches more than one environment on perseus: perseus/levr/main, perseus/acme/main; attach one of those
```

On stderr, exit 1. Each match is given in the shortest form that would name it alone: `HOST/NAME`,
or `HOST/GROUP/NAME` where one host has that name in more than one group. A match on this machine
is listed as `local/main (this machine; cannot be attached yet)`.

## State — opted-in host unreadable during resolution

```
camp env: main cannot be resolved while perseus could not be read (unreachable (Could not resolve hostname)); name the host, as perseus/main or andromeda/main
```

On stderr, exit 1. A bare name is refused while any machine it would read is unreadable, even when
another machine has it, because the unread one might too. The refusal names each unreadable machine
with its reason, and offers the qualified form for each machine that has the name and each machine
that could not be read.

## State — named host unreadable

```
camp env: perseus could not be read: the host key is not pinned; run ssh tduffield@perseus once to pin it
camp env: Docker is not running on perseus
```

On stderr, exit 1. A host named in the target is read alone, and every way `ls` can fail to read
it is a refusal here, worded as `ls` words it. Docker missing or not running is said plainly, not
as unreadable.

## State — standard port already held

```
camp env: port 8080 is held by node (pid 4242); stop it, then attach again
camp env: port 8080 is held by a process camp cannot see (another user's, or one lsof cannot name); stop it, then attach again
```

On stderr, exit 1. Nothing is started and nothing is held. The port is the standard port. camp
names the holder through `lsof` when `lsof` is installed and can see it, and otherwise says it
cannot. The holder's command name is chosen by whatever process holds the port, so it reaches the
terminal reduced to printable characters, as far-side text does. Being held on ::1 alone counts: a browser asking for `localhost` may try ::1 first and reach
the other process instead of the attachment. So does a wildcard holder (0.0.0.0 or ::).

When ssh itself cannot bind a port (taken in the moment between camp's check and ssh's start), the
attach fails with ssh's own reason, and nothing is left behind:
`camp env: attach failed: ssh could not start the forward: <ssh's line>`.

## State — already attached

```
camp env: perseus/levr/main is attached; run camp env detach before attaching another (a forward whose link has died ends on its own within about 45s)
```

On stderr, exit 1, before any host is read. It is the same whichever group, host or camp checkout
made the existing attachment.

## State — status: attached

```
$ camp env status
attached: perseus/levr/main on ports 5173 8080 8443; its health was not checked
```

On stdout, exit 0. Read from the live forward in the process table, never from a record of the
last attach. When more than one forward is recognised, each gets its own line. The forward's
health is not checked, and the line says so: an attachment whose environment has since stopped or
been rebuilt still reads attached until it is detached, and a forward whose link has died ends on
its own within about 45 seconds. A local connection test cannot tell either case apart, because
ssh accepts the local connection whatever the far side does.

`camp env ls` opens with this same `attached:` line when something is attached, then lists as
before. When the process table cannot be read, `ls` says
`camp env: could not tell whether anything is attached: <why>` on stderr and lists anyway.

## State — status: none attached

```
$ camp env status
nothing attached
```

On stdout, exit 0. When the process table itself cannot be read, `status` says
`camp env: cannot tell what is attached: <why>` on stderr and exits 1, rather than claiming nothing
is attached.

## State — detached

```
$ camp env detach
detached perseus/levr/main; ports 5173 8080 8443 are free
```

On stdout, exit 0. camp ends every recognised forward, asking first and then forcing one that does
not end within two seconds, and confirms its ports are free again. When a forward cannot be ended,
or a port is still held afterwards, detach says which, and by what, and exits 1. When the process
table cannot be read before anything was signalled, it says nothing was signalled; when that
happens after the first signal, it says the forward may already have ended and to run
`camp env status`.

## State — detach: none attached

```
$ camp env detach
nothing attached
```

On stdout, exit 0. With nothing to end, detach has succeeded.

## This machine

`local/NAME`, `<self_name>/NAME`, and a bare name whose only match is on this machine are refused
with `camp env: attaching an environment on this machine is not supported yet`, exit 1. This
machine is still read for a bare name, so a name on this machine and on a host is ambiguous, not
quietly resolved to the host.
