# Attaching reaches a running session on any machine

The rendered surface for `camp attach` — the verb that hands the operator's terminal to a
running session, on this machine or a declared one, without the operator first working out
which machine it is on.

```
camp attach                            # numbered picker over local running sessions
camp attach -a                          # picker across every declared host
camp attach <ref>                       # attach directly, local
camp attach <ref> --host andromeda      # attach directly on that host
camp attach <ref> -a                    # find that ref on any host; ambiguous refuses
```

What this surface establishes is the **resolution-then-handoff** shape: a reference is
resolved to exactly one running session on exactly one machine, or it is refused, and only
then is the terminal given away. Every later verb aimed at a single named machine reuses
that resolution; none of them reuses the handoff, which is attach's alone.

## The reference is `camp kill`'s reference

`<ref>` is an unambiguous prefix of a session's derived name (`camp-<slug>-<uuid8>`) or of
its session id — the grammar `camp kill <ref>` and `camp launch --resume <ref>` already
share through one resolver. Attach reuses that resolver unforked rather than restating its
matching rule, so the three verbs cannot drift into three grammars.

The resolver's existing three-way answer — resolved, ambiguous, no match — is also reused
verbatim, including its exit code 2 for an ambiguous reference and its candidate listing.
Attach adds no fourth answer at this layer.

## Resolution is not the same question on each axis

The three addressing forms ask the resolver three different questions, and the difference
is where the pool comes from.

- **Local** (`camp attach <ref>`, no machine option) — the pool is this machine's own
  transcripts and live records, exactly as `camp kill` builds it.
- **One named machine** (`--host <name>`) — the local side resolves nothing. The reference
  is carried across as-is and **that machine's camp** resolves it against its own pool,
  because a remote invocation is a pass-through: the far side makes every decision it
  makes locally, and its refusal reaches the operator in its own words.
- **Every machine** (`-a`) — the local side must know *which machines match* before it can
  hand the terminal to one of them, and it must refuse rather than choose when more than
  one does.

## Why `-a` probes rather than reading the merged listing

The obvious implementation of `-a` — take `camp sessions -a --json`, prefix-match the ref
against the rows — does not work, for a reason worth writing down.

A merged session row carries `session_id` and the harness's own `name`. It does **not**
carry the derived name (`camp-<slug>-<uuid8>`), and it cannot be made to: the
single-machine machine-readable key set is frozen so existing parsers are unaffected.
Matching the ref against rows would therefore accept a session-id prefix and silently miss
a derived-name prefix — half of the grammar the local form accepts. Two forms of one verb
would answer differently for the same reference, which is the exact drift reusing one
resolver was meant to prevent.

So `-a` **asks each machine to resolve the reference itself**, over the existing
non-interactive transport, and merges the answers:

```
<camp_bin> attach <ref> --resolve --json
```

Each machine answers for its own pool with the same resolver the local form uses. The
local machine answers the same question directly, without a transport. The local side then
counts machines that resolved, and only that count decides:

| machines resolving | outcome |
|---|---|
| 0 | no match — nothing to attach to |
| 1 | hand the terminal to that machine |
| more than 1 | refuse, naming every machine that matched |

`--resolve` is a machine-readable sub-mode of attach, reachable only through a flag that
did not exist before, so no invocation written today changes shape or cost. Its only
consumer is `camp attach -a` itself.

Hosts are probed concurrently, on the same bounded pool the merged listing already uses. A
host that does not answer within the connection timeout does not silently reduce the match
count — see the state below.

## The handoff is an exec, not a subprocess

Once exactly one target is known, camp **replaces itself** with the attaching command
rather than spawning it as a child:

- local — `tmux attach -t <derived name>`
- remote — `ssh -t <destination> <camp_bin> attach <ref>`

Replacing the process is what makes the terminal handoff clean: no camp process sits in
the middle relaying bytes, the multiplexer owns the terminal directly, and the exit status
the operator sees is the one the multiplexer produced. It is also why the handoff is the
one part of this surface that cannot be observed by an automated test — the process the
test is running would be gone. The exec is therefore reached through an injected seam:
tests assert on the argv that *would* be exec'd, and the real handoff is attested by the
operator.

**The remote handoff cannot reuse the listing transport.** That transport deliberately
allocates no pty, because a pty would apply CRLF translation to the byte-verbatim JSON
stream it exists to relay. An interactive attach needs exactly the pty that transport
refuses, so the two are separate paths that share only the host declaration — the
destination and the per-host camp location.

## Attach only offers sessions camp owns

The harness seam states plainly that its `controllable` field conveys no attach capability
and that a consumer building attach must define its own model. Attach's model is the one
`camp kill` already applies: a session is attachable when its tmux pane's start command is
one camp itself composed. The predicate is not restated here — it is reached through the
same ownership check the kill path uses, so a change to what camp launches cannot leave
attach believing it owns a pane that kill would disown.

A session that fails the ownership check is not offered in the picker and is not attached
by reference. It is not an error; it is simply not camp's to hand over.

**What that check is worth, stated plainly, because attach raises the stakes on it.** The
kill path documents this ownership test as an accepted risk and explicitly *not* an
authorization boundary: both pane-command shapes are public and reproducible, so a process
running as the same operating-system user, knowing a target's derived name and session id,
can spawn a pane that passes it. Closing that properly needs verified process ancestry of
the harness binary, which is a larger change than either verb.

Attach inherits that gap with a worse consequence than kill has. A spoofed match against
kill terminates something spuriously. A spoofed match against attach hands a live terminal
— every subsequent keystroke — to a pane the operator did not launch. The threat model that
made the risk acceptable is unchanged (a hostile process already running as the operator
has better options than this), so the check is still the right one to reuse rather than
fork. But the severity delta is real, it is not visible from the kill path's own wording,
and a later reader deciding whether to harden this should find it written down here rather
than rediscovering it.

## The key-prefix conflict warning is decided locally

Attaching to a multiplexer from inside a multiplexer means two layers competing for one
prefix key, and the operator has to know before the terminal is gone. The warning is
decided from **the environment the operator is typing in** — this machine's own
environment — and never from the far side's, which is why it appears on a remote attach
from inside a local multiplexer just as it does on a local one.

It is a notice on stderr, not a refusal. The nesting is legitimate; it is only surprising.

## The picker

With no reference given, attach lists the running sessions it can attach and reads one
line from standard input.

- Most recently active first. Each line identifies machine, group, and slug.
- Anything that is not one of the listed numbers re-prompts rather than exiting, so a
  mistyped choice costs a keystroke instead of a re-run.
- The list is the same pool the reference form resolves against, filtered to running and
  to camp-owned. A stopped session is never offered, because attaching cannot revive one —
  bringing a dead session back is `camp launch --resume`, and pointing at it here would
  make the picker a second, worse door to a path that already exists.

`-a` widens the picker across machines exactly as it widens a listing; every line then
carries which machine it is on, and choosing one is the same handoff as naming it.

## Exit status

Attach's own exit status is the multiplexer's once the handoff happens, and camp's own
before it. A refusal is `1`; an ambiguous reference is `2`, matching `camp kill` and
`camp launch --resume`, so a caller that already narrows on `2` needs no new case.

An unreachable host is the one place this surface departs from the merged listing's rule.
A listing may report an unreachable machine as a row and still succeed, because the
operator can see what is missing. An attach cannot: a machine that did not answer might
have been the one holding the match, so proceeding would attach to the wrong session or
report no match when one exists.

## State — no session is running

The picker has nothing to offer. Attach states that no running session was found and
exits — it never presents an empty numbered list and never waits for a choice against one.

With `-a`, the same statement names that every declared machine was asked, so "nothing is
running" is distinguishable from "this machine has nothing running".

## State — one session is running

The picker still prompts. A single candidate is presented as a one-item numbered list and
the operator confirms it by choosing it.

Auto-attaching the only candidate is deliberately not done: attach gives the terminal away,
and a verb that does that without an explicit choice behaves differently depending on how
many sessions happen to be running at that moment.

## State — many sessions are running

The numbered list, most recently active first, each line carrying machine, group, and
slug. One line is read from standard input. A listed number attaches; anything else —
blank, out of range, not a number — re-prompts.

## State — the running-session collection could not be read

The pool could not be enumerated at all: the harness could not be asked, or the multiplexer
is absent. Attach refuses and names which of the two failed.

This is where attach parts company with `camp sessions`, which degrades to an empty list
and exit 0 on the same failure. An empty listing is an honest answer to "what is running";
"nothing to attach to" is *not* an honest answer to "attach me", because the pool was never
read. A refusal is the only statement that does not assert something unverified.

## State — output is not a terminal

The picker needs a terminal to prompt into and a terminal to hand over. When it has
neither, it refuses with a stated reason instead of waiting on a choice nobody can make.

The refusal names the reference form as the way through: `camp attach <ref>` needs no
prompt. That form is refused too when the terminal is absent — the handoff itself requires
one — but it is refused by the multiplexer, in its own words, rather than by a guard that
would have to guess.

## State — the named session was not found

The reference matched nothing in the pool that was searched. The refusal names the pool it
searched — this machine, a named machine, or every declared machine — because "not found"
means three different things across the three forms and the operator is owed which one.

## State — the named session matches on more than one machine

Two machines each hold a session the reference matches. Attach refuses and lists every
matching machine with the session it matched, so the operator can re-run against one of
them with `--host`.

Camp does not choose. A reference that matches twice carries no information about which
was meant, and this is the verb that hands over the terminal — the wrong guess types into
the wrong machine's session.

This mirrors, at the machine axis, the ambiguity refusal the resolver already applies
within one machine, and it uses the same exit code.

## State — the named session is not running

The reference resolved to a session the harness knows about, but it is not live — a
transcript, not a running pane. Attach refuses and names `camp launch --resume <ref>` as
the way to bring it back.

The refusal is deliberately specific rather than falling back to "not found". The
difference between "no such session" and "that session is stopped" is the difference
between a typo and a decision, and only one of them has a next command.

## State — a declared host did not answer while resolving

A machine did not answer within the connection timeout while `-a` was asking it to resolve
the reference.

Attach refuses. The match count is now unknown: the silent machine might have held the only
match, or a second one. Attaching on the strength of the machines that did answer would
either attach to the wrong session or report no match while one exists — both worse than
stopping.

The refusal names which machines did not answer, and names `--host <name>` as the way to
attach without waiting on them.
