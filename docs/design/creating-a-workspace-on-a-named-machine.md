# Creating a workspace on a named machine

The rendered surface for `camp new --host` — the verb that creates a workspace on a declared
machine the operator is not sitting at, and hands the operator's terminal to it there. It is the
sibling of [attaching](attaching-reaches-a-running-session-on-any-machine.md): the second verb that
gives the terminal to another machine, and it behaves the way that one does.

```
camp new <slug> --host andromeda                   # group from this directory; created there
camp new <slug> --host andromeda --group trailhead # group named explicitly
camp new <slug> -a                                 # refused: creation is not a broadcast
```

What this surface establishes is that **creation on another machine is a pass-through, not a
relay.** Camp decides everything its own machine can answer, refuses there if any of it fails, and
then replaces itself with an interactive `ssh -t` running that machine's own `camp new`. The far
side creates, provisions, owns, and opens the workspace exactly as it would for an operator typing
at it.

## The far side is the one creating

The invocation camp hands off to is

```
ssh -t <destination> <camp_bin> new <slug> --group <group> [--dry-run]
```

carrying the host's declared destination and camp location, and the same fixed connection options
cross-host attach uses: no interactive authentication, strict host-identity checking, the configured
connect timeout, and a requested terminal. The command is composed by the one builder both
cross-host verbs share, through the transport's own quote-and-join, so the slug, the group, and the
camp location each arrive as a single literal argument — a metacharacter in any of them is carried,
never run by the far side's shell.

Slug normalisation, the group's member list, whether the workspace already exists, provisioning,
the ownership stamp, and the door onto the new session are all the far side's. Camp does not
pre-answer any of them, because the far side's own group configuration is what gets provisioned
and a local answer could only disagree with it.

## The group is resolved here and forwarded

A command run over `ssh` lands in a home directory that belongs to no workspace, so no group
resolves on the far side. Camp resolves it on the operator's own machine — `--group` when given,
otherwise the group the current directory belongs to — and forwards the name. This is the
resolve-and-refuse step cross-host attach performs, shared rather than copied, and parameterised
only by the verb name that appears in its refusals.

That deliberately differs from the retired cross-host launch design, which never let a local
directory decide what happened on another machine. The operator chose parity with attach: the two
cross-host door verbs resolve the same way from the same place.

## Why creation does not report an unknown outcome

The umbrella cross-host rule, and the retired launch design that implemented it, required a
state-changing remote verb whose connection dropped to report that its outcome was unknown, and
named a command to settle it. Creation does not.

Cross-host creation is an exec handoff, not a captured invocation. Once the terminal is handed over
there is no camp process left to classify anything: the far side's output reaches the operator
live, and the exit status is ssh's — the far side's camp status, or 255 when the connection fails.
A classified report would need camp to stay in the middle, which is exactly what the handoff
removes. And the question the report existed to answer is cheap to settle: re-running the same
command re-enters a workspace that already exists rather than creating a second one. Cross-host
attach, which also creates the far side's multiplexer session, carries no such report either.

What the umbrella rule still requires is kept: the far side's refusal arrives in its own words, and
the all-hosts form is refused.

## Creation is never a broadcast

The all-hosts form merges answers from every declared machine. Applied to creation it would create
the workspace everywhere at once, so it stays refused.

## Exit status

Camp's own before the handoff, ssh's after it. Every local refusal goes to stderr in camp's own
words and exits `1`, with no connection made.

## State — handed off to the far side's creation

Every local check passed. Camp prints nothing of its own (apart from the nested-multiplexer notice
below, when it applies), flushes its streams, and replaces itself with the `ssh -t` invocation
above. From here the terminal belongs to the far side: its created or re-entered line, its
provisioning guidance, and then its workspace session.

## State — dry run requested through the environment is forwarded

The operator's environment carries camp's dry-run switch, which local camp treats as equivalent to
`--dry-run`. ssh does not carry the local environment across, so camp forwards `--dry-run` on the
far side's command line instead, after the slug and group. The far side prints what it would have
done and creates nothing. An explicit `--dry-run` is forwarded the same way, once.

## State — already inside tmux on this machine

The operator's own terminal is inside a multiplexer. Camp prints the same key-prefix notice
cross-host attach prints, decided from this machine's environment, then hands off anyway. It is a
notice, not a refusal: the nesting is legitimate, only surprising.

## State — host not declared

The named host is not in the operator's hosts file. Refused with the declared host names listed, or
with the hosts file named when it declares none. No connection is attempted, and no group is
resolved.

## State — explicit group not configured on this machine

`--group` names a group this machine has no configuration for. Refused with the group named, the
groups this machine does know listed, and `new` as the verb:

```
camp new: group 'nosuch' is not configured on this machine (known: trailhead, levr)
```

This is distinct from the next state on purpose — telling an operator to pass a group they already
passed would be wrong.

## State — no group resolves from the current directory

No `--group`, and the current directory belongs to no configured group. Refused with the same
needs-group message local creation prints, which names both ways through: configure a group, or
pass `--group`.

## State — resolved group fails the name-confinement check

The resolved group's name fails camp's group-name confinement check, or begins with `-` — a name
the far side's parser would read as a flag. Refused with the check's own message before the name is
ever placed on a command line, by the same shared step cross-host attach refuses it with.

## State — slug missing, empty, or whitespace only

No slug was given, more than one was, or the one given has nothing in it. Refused locally, in the
shared slug guard's words, because there is nothing to forward:

```
camp new: --host requires exactly one workspace slug, got 0
camp new: '   ' is not a valid workspace slug
```

## State — slug begins with a dash

A slug beginning with `-` would reach the far side's argument parser as a flag. Refused locally, so
it never can — including one passed after `--`. The check reads the slug exactly as typed, and that
same raw value is what is forwarded, so the value checked and the value sent can never differ.
Every other slug check is the far side's.

## State — a creation flag is passed

Only `--group` and `--dry-run` are carried across. Any other flag — including local creation's own
`--no-attach`, `--no-session`, `--activate`, and `--json` — is refused by camp's argument parser,
naming the flag, before any connection.

## State — every host requested at once

`camp new -a`, or `--all-hosts` in any form, is refused before any hosts file is read:

```
camp new: --all-hosts has no meaning here
```

## State — the far side answers in its own words

After the handoff, whatever the far side says is what the operator sees: its refusal (an unknown
group there, a missing member checkout), its re-entry of a workspace that already exists, or ssh's
own failure when the connection cannot be made — an unreachable host, a changed host key, no loaded
key. Camp adds nothing to any of them, because camp is no longer running.

What the operator can rely on when reading them: when ssh reports that it could not connect, no
camp ran on the far side and nothing was created. When the connection drops after the far side
started, re-running the same command is safe — it re-enters the workspace if one was created rather
than creating a second — and a workspace left with members still pending shows as such in
`camp status <slug>` run on that machine, and `camp setup` there finishes it.
