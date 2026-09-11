# A session lands on the account its group declares

The rendered surface for the account a camp launch chooses — what camp says about it, and
when it refuses. There is no new command here and no new option: the account is the group's
to declare, not the operator's to pick per invocation, so the whole surface is what a launch
prints to stderr before it hands the pane over.

```
camp launch <slug>         # the group declares an account: the session lands on it
camp launch <slug>         # the group declares none: the session lands on a stated default
```

## What already holds, and is not re-litigated here

Camp already decides the account rather than inheriting it. A group declares an opaque
`account` under `[launch]`; the harness turns that declaration into the environment
assignments a launched session must carry; camp applies the harness's scrub and then those
assignments, in that order, as operands of the pane's own `env` command — so a tmux server's
stale globals cannot win. One resolution feeds both the trust pre-seed's target and the
pane's environment, because two reads of one declaration are two answers that can disagree.

That is not a claim about intent; it is the shipped behaviour, and it was built in response
to a real outage in which every launch landed on the wrong account for a day. This document
changes none of it.

**What is missing is not the decision. It is camp's ability to say what it decided.**

## The gap: a binding is not an identity

The harness states an account as environment assignments, and for the default account it
states it as an assignment's *absence* — no value reproduces the state the harness starts
from when the variable is unset, so the default is expressed by scrubbing the name rather
than by setting it.

That is correct, and it is exactly why a launch declaring no account can presently report
only that *a* default applied, never *which* one. The mapping is one-way: assignments do not
carry back the identity they bind to, and an empty mapping carries nothing at all.

So camp needs a second answer from the harness — not a second resolution. **The identity and
the binding must come from one resolution inside the harness**, or they can disagree, and a
disagreement here is invisible until a session is trusted in one account's file and started
under another's. That failure has already happened once, and the fix for it was precisely the
collapse of two reads into one. Re-introducing a second read to report on the first would
undo it.

## Existence rides with identity, for the same reason

There is a second question about an account that camp already asks: does it have configuration
behind it. Today that is answered by a resolver camp reaches into directly, outside the seam —
and the two resolvers involved do not agree with each other under every environment, because
one honours a relocation variable the other deliberately ignores.

Answering identity alone would leave that second reader in place next to the new one, and camp
would then be able to name one account while having checked another. That is the same
divergence as before, wearing a report that makes it look verified.

So the seam answers both from one resolution, and camp's own resolver leaves that call site. It
is a relocation of a check that already exists, not a new capability: it is strictly weaker
than asking whether an account is authenticated, which stays where it belongs, with the surface
that consumes it.

## Identity is the harness's word, carried verbatim

Camp prints what the harness answers and interprets none of it. It does not parse the value,
does not compare it against a path, does not decide whether it looks like a directory, and
names no credential location of its own. An account value is opaque to camp on the way in,
and an account identity is opaque to camp on the way out.

A harness that offers no account identity answers with nothing, and camp says what it says
today. That is a real state, not a degenerate one — it is what every harness other than the
one shipped is, by construction.

## Enumeration is deliberately not built here

"Which accounts exist" is a different question from "which account is this", and it has no
sound answer for the shipped harness: there is no manifest of accounts, only directories that
happen to look like one, so any enumeration would be a glob and a guess.

It is also not needed here. Its real consumer is the host health check, which reports per
declared account whether that account is authenticated — and that surface can make the
heuristic's limits visible in a way a launch-time warning cannot. Building it now, with a
warning as its only consumer, would bind a later surface to a data model that has not baked.

This is why the slice covers the account-identity half of the harness account capability and
leaves the enumeration half to the slice that consumes it.

## An unknown account is a warning, not a refusal

A declared account whose configuration does not exist yet is **not** an error, and camp must
not refuse it.

The reason is that camp cannot distinguish the two cases that produce it. A mistyped account
name has no configuration. A brand-new account, correctly named and not yet logged in, also
has no configuration — and refusing it would make camp the thing standing between the
operator and the first launch on a new account. The states are indistinguishable from
outside, and only one of them is a mistake.

So camp states the resolved identity plainly and continues. The operator reading "the session
will use <identity>" is the check: an identity they do not recognise is the typo, visible
before the session starts rather than after it stalls at a trust prompt it cannot answer.

Note the asymmetry with a declaration the harness actively *refuses* — a malformed value, or
one contradicting another statement of the same thing. That is a refusal today and stays one:
camp was given an instruction it cannot honour, and launching anyway would put the session on
an account contradicting the group's own statement of intent. "I cannot honour this" and "I
have not seen this before" are different answers and get different outcomes.

## State — the group declares an account and the session starts under it

The declaration is passed to the harness, which returns both the assignments binding the
session to it and the identity it resolved to. The pane carries the assignments; the operator
is told the identity.

The report names the declaration *and* the resolved identity, because they are not the same
string and the difference is the operator's only view of what the harness did with what the
group wrote.

## State — the group declares no account and the session starts under the stated default

No declaration, so the harness resolves its own default and answers with that identity. Camp
states it as a default explicitly — the operator must be able to tell which account a session
landed on without knowing whether their group declared anything.

The pane may carry no assignment at all in this case, the default being stated as an
absence. The report says so rather than announcing an assignment camp does not have: an
empty binding described in the language of assignment reads, to someone skimming, as an
account named by the empty string.

## State — the ambient environment names a different account than the group declares

The ambient value is not an input. It is removed by the harness's scrub before any assignment
is made, and the resolved binding is then applied over the scrubbed environment — so a
different account named ambiently, and no account named at all, reach the same place.

Nothing new is reported for this state. The operator is told the account the session landed
on, which is the same statement whether or not something else was in the environment; camp
does not narrate what it ignored. This state is listed because it is the one the whole
mechanism exists for, and because it must stay closed: it is the shipped behaviour this
document must not regress.

## State — the declared account is not one the harness knows

The harness resolves an identity for the declaration but has no configuration behind it.

Camp warns, names the resolved identity, and launches. Per the asymmetry above, this is
indistinguishable from a legitimate first launch on a new account, and refusing it would
block that.

The identity named and the configuration checked come from one seam answer, so the warning
cannot name an account other than the one it looked for.

The warning's job is to make the identity visible at the moment it is cheap to notice. It
states what the session will use, not what camp believes about the operator's intent.

## State — the harness cannot enumerate accounts at all

The harness offers no account identity: it answers with nothing.

Camp reports exactly what it reports today — the declaration, or that a default applied — and
adds no claim it cannot support. It does not guess an identity, does not fall back to reading
a location of its own, and does not warn about an account it cannot name.

This is the default for any harness that has not implemented the capability, so it is the
state every harness is in until it opts out of it. It must therefore be the safe one: a
launch in this state behaves exactly as a launch behaves today.
