# camp env attach, status and detach for an environment on this machine

The rendered surface for attaching an environment that runs on the machine camp is run on, so its
services answer on this machine's localhost at their standard ports, exactly as a remote
environment's do. The verbs are unchanged; what changes is that this machine is now a place an
environment can be attached from.

```
camp env attach NAME              # a bare name; this machine's environments now resolve too
camp env attach local/NAME        # this machine only
camp env attach <self_name>/NAME  # the same, by this machine's declared name
camp env attach local/GROUP/NAME  # this machine, that group
camp env status                   # names a local attachment as it names a remote one
camp env detach                   # ends it and frees its ports
```

## What already holds, and is not re-litigated here

Listing, the contract (labels, the `<group>-env-<name>` rule, loopback-only TCP published ports, the
standard port being the container port), target parsing, the order attach checks things in, the
port probe, the one-attachment-per-machine rule, and every remote state are as
`camp-env-attach-status-and-detach-for-a-remote-environment.md` describes. This machine is read
locally, never over ssh, as `camp-env-ls-lists-environments-on-this-machine-and-on-opted-in-hosts.md`
describes. A local environment's published ports are on this machine's own loopback already; the
attachment only moves them to their standard numbers.

## How a local attachment is made: a relay

A remote environment is attached through a background `ssh` forward. A local one is attached
through a **relay**: a background process camp starts itself, standard library only, that holds
each standard port on 127.0.0.1 and carries every connection it accepts to the environment's
published port on 127.0.0.1. It never dials anywhere but this machine's loopback. It needs no sshd
on this machine and no third-party tool (socat and ssh-to-localhost were both considered and not
adopted: see the two `local-tcp-relay-for-same-machine-attach-*` decisions).

The relay binds every standard port before it reports ready. If any one cannot be bound, it
releases every port it had already bound and exits, naming the port, so an attach never leaves
some ports held and others not.

Once ready, the relay runs in its own session with its input and output pointed at `/dev/null`
and its working directory at `/`, so it outlives the camp command that started it and never reads
a file from wherever that command was run. It ends on SIGTERM or SIGINT, closing its listeners and
every open connection.

## How a local attachment is found again

Nothing is written to disk, as for a forward. The relay's process-table entry is shaped so it
carries nothing from camp's own install:

```
camp-env-relay -m camp.env.relay -N -o Tag=camp-env:<id>:local/<group>/<name> -L 127.0.0.1:<standard>:127.0.0.1:<published> ...
```

Its first word is the fixed name `camp-env-relay`, not the interpreter's path, and where camp lives
is passed to it in its environment, never in its arguments. So a relay started by one camp checkout
or install, including one whose path contains whitespace, is recognised by `status`, `detach`,
`ls` and the already-attached refusal run from any directory through any other.

A process is recognised as a relay when it is owned by this user, its first word is
`camp-env-relay`, its next two are `-m camp.env.relay`, and the words after them read, exactly as a
forward's options do, as `-N`, at least one `-L` of the same shape and exactly one tag. The tag's
host is `local`. A process that only mentions the tag (a `grep`), names another module, lacks `-N`,
or belongs to another user is never counted, reported or ended. Ending a relay is the same as ending
a forward: the table is read again before each signal, and only a process still carrying the same
attach id is signalled.

A local attachment is named `local/<group>/<name>` everywhere, the form that attaches it again.

## How a name is resolved

- **`local/NAME`**, **`<self_name>/NAME`**, **`local/GROUP/NAME`** read this machine alone, locally,
  and take one match exactly as a host form does. A name in two groups here is ambiguous.
- **`NAME`** reads this machine and every host in `env_hosts`, as before. One match on this machine
  alone is now taken, not refused. A name found here and on a host is ambiguous, and the refusal
  offers `local/NAME` beside the host forms.

## State — attached on this machine

```
$ camp env attach main
attached local/levr/main on ports 5173 8080 8443; its health was not checked
```

One line on stdout, exit 0, in the same shape as a remote attach. The ports are the standard ports,
ascending. Each now answers on `localhost`, relayed to the environment's published port.

## State — environment on this machine stopped

```
camp env: local/levr/main is stopped; start it with its project's own tooling, then attach again
```

On stderr, exit 1. Nothing is started. A running local environment that publishes no ports is
refused as a remote one is: `camp env: local/levr/main publishes no ports, so there is nothing to
attach`.

## State — Docker not running on this machine

```
camp env: Docker is not running on this machine
camp env: Docker is not installed on this machine
```

On stderr, exit 1, for a target naming this machine. For a bare name, Docker missing or not running
here blocks nothing and is noted in a not-found refusal as before
(`; Docker is not running on this machine`), because nothing runs here to be missed. A read of this
machine that answers but does not validate is unreadable, and blocks a bare name as any unreadable
machine does: `camp env: this machine could not be read: <why>` for `local/NAME`.

## State — standard port already held

```
camp env: port 8080 is held by node (pid 4242); stop it, then attach again
```

On stderr, exit 1. Nothing is started, nothing is held. The probe is the one a remote attach runs.
When the port is taken in the moment between the probe and the relay's bind, the relay releases
every port it had bound and exits, and attach names the port and what now holds it:

```
camp env: attach failed: port 8080 is held by node (pid 4242); stop it, then attach again
```

No relay is left running and no port is held.

## State — already attached

```
camp env: local/levr/main is attached; run camp env detach before attaching another
camp env: perseus/levr/main is attached; run camp env detach before attaching another (a forward whose link has died ends on its own within about 45s)
```

On stderr, exit 1, before any machine is read. A relay blocks a remote attach and a forward blocks a
local one: there is one attachment per machine, of either kind. The note about a dead link ending
on its own belongs to a forward only, and is not said of a relay, which has no link to lose.

## State — name found both here and on a remote host

```
camp env: main matches more than one environment: local/main, perseus/main; attach one of those
camp env: main matches more than one environment on this machine: local/levr/main, local/acme/main; attach one of those
```

On stderr, exit 1. This machine's match is offered as `local/NAME` (or `local/GROUP/NAME` where it
has the name in two groups), and no longer marked as unattachable. A bare name refused while a
machine is unreadable now also offers `local/NAME` when this machine has the name.

## State — environment gone before the relay starts

```
$ camp env attach main
attached local/levr/main on ports 5173 8080 8443; its health was not checked
```

An environment that stops or is removed between being read and the relay starting is not noticed
by attach: the relay binds the standard ports, which are free, and reports ready. The success line
already says health was not checked. A connection to a standard port is then accepted and closed at
once, because the relay cannot reach the published port behind it, and the browser shows the
connection closed. `status` keeps naming the attachment until `detach`, as it does for a forward
whose environment has stopped; flagging that is the spec's deferred stale-attachment follow-up.

## State — status: attached on this machine

```
$ camp env status
attached: local/levr/main on ports 5173 8080 8443; its health was not checked
```

On stdout, exit 0, read from the live relay in the process table. `camp env ls` opens with the same
`attached:` line.

## State — detached on this machine

```
$ camp env detach
detached local/levr/main; ports 5173 8080 8443 are free
```

On stdout, exit 0. The relay is asked to end, forced after two seconds if it has not, and its ports
are confirmed free, exactly as for a forward. Every failure detach reports for a forward it reports
for a relay, in the same words.

## State — attachment made by a different camp checkout or a path with whitespace

```
$ cd /tmp && camp env status
attached: local/levr/main on ports 5173 8080 8443; its health was not checked
$ camp env detach
detached local/levr/main; ports 5173 8080 8443 are free
```

The relay was started by a camp at `/Users/tom/My Projects/trailhead`; status and detach here are
run by a different camp checkout, from another directory. Both find it, because nothing in the
relay's arguments depends on where camp lives. The same holds the other way round, and for a
forward.
