# camp doctor answers for every declared host

The rendered surface for a health check asked about the machines camp can reach. There is one new
option on one verb — `-a`, already spelled and already resolved for three verbs, now accepted by
`doctor` — one new configuration scalar, and one new flag the far side answers with.

```
camp doctor                     # unchanged: local checks, no network
camp doctor --json              # unchanged: byte-identical to today
camp doctor -a                  # additionally probes every declared host
camp doctor --json --probe      # the far side's own answer about itself
```

## What already holds, and is not re-litigated here

Camp reaches a declared host over a single non-interactive `ssh` invocation with a pinned host key,
no prompting, and a bounded connect time, and classifies what came back into a closed set of
outcomes. Hosts are contacted concurrently, so a machine that is down costs a bounded wait rather
than an additive one. The earlier slices established all of it. This document changes none of it.

What is new is that camp asks those machines a question about themselves rather than about their
sessions, and reports the answer without letting it decide whether the health check passed.

## The probe is a question the operator asks, never one camp asks on its own

Everything below happens only under `-a`. A health check that reaches for the network because a
configuration file happens to exist is a health check whose cost changed without anyone asking, and
camp's existing invocation has real consumers — the operator's shell launchers and the concierge
skill — that run it on a schedule and parse what it prints.

So `camp doctor` and `camp doctor --json` stay exactly as they are: same rows, same keys, same exit
status, same absence of network I/O. The host section is additional output under an option that did
not exist before, not a revision of the output that did.

## Three facts per machine, from one round trip

The operator's question when a merged listing comes back short is always the same: is that machine
off, or is camp not where I said it was, or is something else wrong over there? Those are three
different next moves, and today they are indistinguishable.

Two of the three already exist in the transport's vocabulary. A connection that never completes is
already `Unreachable`, `IdentityUnknown`, `IdentityChanged`, or `CredentialsRefused`; a connection
that completes but cannot run the declared camp is already `CampNotResolvable`. This slice adds no
new failure vocabulary for either — it renders what the transport already distinguishes.

The third fact — whether a terminal multiplexer is present — is one no camp answer carries today,
because nothing has needed it across a machine boundary before. The far camp reports it about
itself.

## The far side answers with a flag that is new, so the answer that is old cannot change

The probe invokes `doctor --json --probe` on the far machine. `--probe` is what makes the far camp
report facts about its own host rather than run its ordinary checks, and because it is new, the
invocation without it is untouched.

This also decides what an older far camp looks like — and it does not look like a refusal. `doctor`
reads its one option by substring and ignores every other word on its command line, so a camp that
predates `--probe` accepts the invocation, exits zero, and answers with its ordinary check report:
well-formed, successful, and carrying no probe data at all. Probe support is read from the presence
of the probe's own field in that answer, never from an exit status and never from how the transport
classified the connection. Camp reports its absence as *the machine answers, camp resolves there,
and the probe is unavailable* — never as *no multiplexer present*. An absent capability rendered as a negative
finding is the mistake this spec already refuses to make about an account's authentication state,
and a stale remote checkout is the likeliest way to meet it.

## Unreachability is a row, not a verdict

The host section never changes the health check's exit status. The local checks decide it, exactly
as they do today.

A check that fails whenever a laptop is closed is a check the operator stops running, and then the
local checks it was actually carrying stop being read too. The machine being unreachable *is* the
reported finding; there is nothing for a status code to add.

## The timeout the probe makes worth setting

Probing is the first place the operator has a reason to spend less time waiting on a machine he
already knows is down. The bound is declared once, beside the hosts it applies to:

```toml
# ~/.config/camp/hosts.toml
connect_timeout = 3.0
```

Absent, it is the stated default. It applies to every host verb rather than to the probe alone,
because it describes the machines, not the question being asked of them — and because camp scans
argv by hand, so an option would have to be taught to every verb that reads it, and could then
disagree between them.

**What it bounds, exactly.** This is the handshake — how long camp waits for a machine to answer at
all. A machine that answers and then wedges partway through its own checks is bounded separately,
by the execution bound, which this slice does not change and does not expose. So lowering this
value makes a *dead* machine cheap; it does not make a *slow* one cheap. The distinction is worth
stating because the two failures feel identical from the operator's chair and only one of them is
what this knob is for.

## The section reads like the rows above it

The existing check rows are a fixed-width verdict in brackets, the check's name, and an indented
detail line when there is something to say. The host section reuses that grammar rather than
inventing a second one: a verdict, the machine's name, and the detail indented beneath it.

Two runs of this command are meant to be read against each other, so the shape of a line cannot
depend on which outcome it carries — a machine that is fine and a machine that is unreachable
occupy the same columns, and only the verdict and the detail differ. The verdict vocabulary is
fixed at three values: the machine is fully answerable, the machine answered with something the
operator should know, or the machine could not be reached at all. Nothing in the host section uses
the failing verdict the local checks use, because nothing in the host section can fail the command.

## State — no remote hosts are declared, so the check answers for this machine alone

The host file is absent, or declares no `[hosts.*]` table.

The section still renders, carrying this machine's row alone. It does not collapse to silence: the
operator asked which machines answer, and "only this one is declared" is that question's answer, not
an absence of one. It reads the same as any other machine's row, so a later host being added changes
what the section lists rather than whether it exists.

No network is contacted, and the check costs what it costs without `-a`.

## State — one declared host answers and its camp and multiplexer both resolve

The connection completed, the declared camp ran, and it reported a multiplexer present.

The row says so on one line, naming the machine. This is the case the operator is confirming before
he trusts a merged answer, so it is stated positively and briefly rather than being left as the
absence of a complaint — a section that only ever speaks up about problems cannot be used to confirm
that there are none.

## State — several declared hosts answer, each rendered as its own block

Every declared machine gets its own block, in the order the host file declares them, whatever order
they answered in.

Declared order rather than completion order is what makes two runs comparable: an operator reading
the section twice is looking for what changed, and a list that reorders itself by network latency
hides that. The machines were contacted concurrently, so the whole section costs one machine's wait
rather than the sum.

## State — a declared host does not answer within the connection timeout

The connection never completed — the machine is down, the name does not resolve, the key is unpinned
or has changed, or every credential was refused.

The row names the machine and the reason the transport gave, each of those being a different next
move: a machine to wake, a name to fix, a key to re-pin, a credential to fix. It is never rendered
as a raw transport error, and never as an empty answer.

The health check's exit status is unchanged by it.

## State — a declared host answers but camp does not resolve there

The connection completed and the declared camp could not be run — the path is wrong, or the checkout
moved.

This is reported as its own finding rather than folded into unreachability, because the machine is
fine and the configuration is not, and the fix is the declared camp location in the host file rather
than anything on the far machine. This is the drift the spec names as an accepted risk, surfaced
here at the moment it starts mattering.

The row names the machine and the camp location that failed, so the correction needs no
construction.

## State — a declared host answers but no terminal multiplexer is present

The connection completed, camp ran, and the probe reported no multiplexer.

The row says so plainly. It is a finding rather than a failure: the machine answers listings
perfectly well without one, and only the verbs that hand over or create a terminal will fail there —
so the operator learns it now, from a check, rather than later, from a launch that cannot complete.

This is distinct from the probe being unavailable, and the two never render alike. A far camp that
does not report the probe says so; a far camp that reports no multiplexer says that.

## State — the health check is run without asking for host probing

No `-a` was given.

Nothing about the invocation changes: the same rows, the same keys under `--json`, the same exit
status, and no socket is opened. The host section is absent entirely rather than present and empty —
an empty section would read as "no machines answered", which is a finding, and no finding was asked
for.
