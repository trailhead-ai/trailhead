# The health check reports whether each declared account is authenticated

The rendered surface for the account axis of `camp doctor -a` — what camp prints, per declared
machine and per declared account, about whether that account can authenticate there. There is no new
option: the probe that already runs under `-a` carries more facts back.

```
camp doctor          # unchanged: no network, no accounts
camp doctor -a       # per machine: reachability, camp, multiplexer — and now each declared account
```

## What already holds, and is not re-litigated here

The host probe runs only when asked for, contacts every declared machine concurrently, and renders
each machine's answer as a row rather than as a failure. Probe support is read from the presence of
the probe's own field in the parsed answer, never from an exit status and never from how the
transport classified the connection. Host-section verdicts never decide the check's exit status. The
launch and host-probe slices established all of it; this document changes none of it.

What is new is that a machine now answers about something that can be true, false, **or unknowable**,
and that the unknowable case is the one with teeth.

## Camp interprets no account value

An account is an opaque string a group declares. Camp passes it to the harness and renders the
verdict that comes back. Camp does not read it, resolve it to a path, open a file at that path, or
know what a credential looks like on any platform.

This is not fastidiousness — it is the only way the answer stays correct on a machine whose harness
stores credentials somewhere camp has never heard of. A camp that knew where credentials live would
be wrong on exactly the machines the operator most needs a straight answer about.

The consequence is that camp cannot improve on a harness that declines to answer. It renders
"unavailable" and says so plainly, which is the honest report.

## Three values, and the third is not a worse second

The capability answers authenticated, not authenticated, or cannot tell. The third is a first-class
answer with its own rendered wording, never folded into the second.

Reporting "not authenticated" when the truth is "I could not check" is the confident wrong answer
this entire diagnostic surface exists to remove. It sends the operator to re-authenticate an account
that was fine, on a machine that was fine, and — worse — teaches him that the check lies, after which
he stops reading it. A check believed 80% of the time is worth less than no check, because it still
costs him the time to read.

The same rule governs a harness that has no such concept at all. It answers "cannot tell" by default
rather than guessing, so a harness added later reports honestly before anyone implements anything.

## The roster is each machine's own

A machine answers about the accounts **its** groups declare, plus the default one. Camp does not send
the local roster across and ask a far machine to comment on it.

This follows from the same reasoning as every other host verb: the far camp makes every decision it
would make locally. It also happens to be the only correct answer, because per-host group
configuration diverges by design — the operator's templates gate members on what is present on each
machine — so a roster resolved here and applied there would describe a machine that does not exist.

The roster is deduplicated by the **resolved binding**, not by the declared string, so two groups
spelling one account differently are one entry rather than two. That rule already ships; this reuses
it rather than inventing a second one.

## The facts ride the existing answer, and an older camp omits them

The probe answer gains one more top-level field. A camp that predates this work omits it while
answering everything else correctly, and the reader renders the accounts as unavailable rather than
failing.

This is the same key-presence rule the host-probe slice pinned, applied to a second field. It is
stated again here only because it now governs a field whose absence and whose "cannot tell" value
render identically — and that identity is deliberate. From the operator's side, a machine running an
older camp and a machine whose platform exposes no check are the same situation: camp cannot tell him,
and says so.

## A remote string is stripped before it is rendered

Every string that crosses from a far machine into a rendered line — the account as that machine
declared it, and any failure reason it carried — has terminal control sequences removed first.

This is not a new rule. Camp already strips them from a far side's stderr and, recursively, from
every string inside a relayed answer, on every relay path. What is new is that the probe does not
take a relay path: it answers with a single object where every other host verb answers with an array
of rows, so it runs the transport directly and interprets that object itself. That fork was free
while the only values it read were a flag and a boolean. This slice puts the first remote-controlled
string on it, and the strip has to come along.

The reasoning is the one already written for relayed rows. A declared host is trusted to run
commands, not to write what the operator reads. A far side that can move the cursor or clear a line
inside an account name can hide a WARN line from the block, or make two machines render as one — and
this block exists precisely to be scanned before the operator relies on a merged answer.

The machine-readable form is stripped at the point the roster is produced, not only where it is
printed. A consumer parsing camp's JSON inherits whatever camp puts there, and a field that is safe
only because the human renderer happens to clean it is not safe.

## Machine-readable output stays one fact per row

Each fact is its own row, carrying the machine it came from. An account fact is a row exactly as
reachability is a row.

The row shape is closed, and every path that produces one converts into it before it reaches a
renderer. That rule was written after a row of a foreign shape crashed the human renderer outright,
and the account rows do not get an exception: the alternative — nesting accounts inside the host's
own row — would reintroduce precisely the shape the renderer cannot assume.

## The human form groups by machine

The rendered block names each machine once and indents its facts beneath it, each carrying its own
verdict.

The flat form the host-probe slice shipped repeats the machine name on every line. That was free when
a machine contributed one line and stops being free the moment it contributes one per account: the
operator reading a fleet of four machines with three accounts each would read the machine names more
often than the facts. The grouping is what keeps the block scannable, which is the only reason the
block exists.

Rendering is the only thing that changes. The facts, their verdicts, and their wording are the same
on both paths, and the machine-readable form is untouched by this choice.

## Cannot-tell gets its own verdict, not a shared one

The account axis reads four verdicts, not three. Authenticated passes. Not authenticated, and a
capability that failed outright, both warn — each names something the operator can act on. A check
camp could not perform renders as its own verdict, distinct from all of them.

Collapsing the third into a warning is the failure this slice exists to prevent, one level up from
the wording. An operator scanning a fleet reads verdicts first and details second; that is what a
verdict column is for. If "log in again" and "we could not look" share a token, the scan stops
working and he has to read every line — at which point the block's whole value, being scannable
before he relies on a merged answer, is gone.

It also keeps the two situations the spec separates genuinely separate: a platform that exposes no
check and a harness that failed trying are not the same report, and neither is the operator's next
move.

## An account verdict never decides the exit status

An unauthenticated account is a row. So is an unavailable check.

The reasoning is the one already written for an unreachable host: a check that fails whenever a login
lapses is a check the operator stops running, and this check's whole job is to be worth running
before he relies on a merged answer. The exit status continues to be decided by the local checks
alone.

## State — A host declares no accounts

Every group on that machine declares no account, so the roster is the default one alone.

The machine's block carries its reachability facts and exactly one account line, naming the default
account rather than printing nothing. Printing nothing would be indistinguishable from a camp that
does not support the probe, and the operator cannot tell those apart by staring harder.

## State — One declared account, authenticated

The harness answered that the account can authenticate.

One account line, PASS, naming the account as the group declared it — carried verbatim, because the
declared string is what the operator recognizes and what he would edit to change it.

## State — Several declared accounts, each carrying its own verdict

The machine's groups declare more than one account, deduplicated by resolved binding.

One line per account, each with its own verdict, in a stable order so two runs are diffable. A
machine with three accounts where one has lapsed shows two PASS lines and one WARN, not a single
summary verdict — a summary would hide which account needs attention, which is the only thing the
operator asked.

## State — A declared account that is not authenticated

The harness answered that this account cannot authenticate.

WARN, naming the account, stating that it is not authenticated. Not DOWN: the machine is reachable
and camp works there, and conflating a lapsed login with an unreachable machine would send the
operator to the wrong remedy. The line says what is wrong; it does not tell him to log in, because
camp does not know whether he wants that account on that machine at all.

## State — The platform does not expose an authentication check

The harness declined to answer for this account — no such concept, or no way to tell without
prompting.

The cannot-tell verdict, naming the account, stating plainly that the check is unavailable here. Not
a warning: there is nothing for the operator to act on, and the wording never implies a credential
problem, because there is no evidence of one. This is also the state a machine
running an older camp lands in, and the two are deliberately indistinguishable: in both, camp cannot
tell him.

## State — The account capability fails on a host

The harness raised rather than answering — a broken config, an unreadable declaration, a harness that
could not be resolved for a group at all.

The machine keeps its reachability facts, and the account axis warns, carrying the failure's own
reason through — stripped of control sequences first, since that reason is remote-authored. This is
a warning rather than the cannot-tell verdict: something is broken and the operator can fix it,
which is exactly what separates it from a platform that never offered the check. One account's failure never discards another's answer, and it
never discards the machine's reachability facts, which were established before the roster was built.

A diagnostic is tolerant where a security boundary is strict: the existing every-declared-account
enumeration refuses outright when a config is unreadable, because an incomplete deny list is
dangerous. Here an incomplete answer is merely incomplete, so the machine reports what it could
resolve and names what it could not.

## State — The host never answered, so no account verdicts exist

The machine was unreachable, stopped responding, or its camp could not be run.

The machine's block is exactly what it is today, with no account lines at all. Absent account facts
are not rendered as unavailable accounts: the operator has not learned anything about the accounts,
and a machine that is simply off should not accumulate a line per account telling him so. The
reachability line already says why there is nothing further.
