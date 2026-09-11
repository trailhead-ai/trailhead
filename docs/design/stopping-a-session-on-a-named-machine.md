# Stopping a session on a named machine

The rendered surface for a stop directed at a declared host — what camp prints before, instead of,
or after the far side reclaims the memory. There is one new option on one verb: `--host`, already
spelled and already resolved for four verbs, now accepted by `kill`.

```
camp kill <ref> --host <name>     # the session is stopped there
camp kill <ref> -a                # refused: stopping is not a broadcast
camp kill <ref> --host <name> --group <g>   # refused: a reference names the session
```

## What already holds, and is not re-litigated here

Camp reaches a declared host over a single non-interactive `ssh` invocation with a pinned host key,
no prompting, and a bounded connect time; it classifies what came back into a closed set of
outcomes; it carries the far side's own stderr through with control sequences stripped; and it
states once, in `classify_certainty`, whether an outcome means the operation happened, did not, or
is unknown. The launch slice established all of it. This document changes none of it.

What is new is that the operation being relayed is *destructive*, and that the verb carrying it is
addressed by a reference rather than by a group.

## The reference names the session; the group is not asked for

`camp kill` is groupless by construction — the reference names the session, the session names
everything else, and the verb is deliberately reachable from a plain shell outside every group
directory because it is what an operator reaches for when something is already wrong.

None of that changes across a machine boundary. The far side resolves the reference against its own
session pool exactly as it would locally, so `--host` carries the reference and nothing else.

This is the opposite of `launch --host`, which *requires* `--group`, and the difference is not an
inconsistency to smooth over: a launch has to be told where to create something, and a stop is told
which thing to end. Passing `--group` alongside `--host` here is refused the way `attach` already
refuses it — one remote host and one local group named at once.

The consequence in code is that "changes state on the far side" and "must be told a group" stop
being the same set. The first drives the all-hosts refusal and now holds two verbs; the second
drives the `--group` requirement and still holds one. Collapsing them was correct while `launch`
was the only state-changing verb and is wrong the moment a second one arrives.

## Stopping is never a broadcast, and for a sharper reason than launching

The all-hosts form is refused before any machine is contacted, in the verb's own words. That rule
is inherited, but the stake is higher: a broadcast launch would create sessions an operator did not
ask for, and a broadcast stop would destroy sessions an operator did not name. The refusal points
at the single-host form, because the intent behind reaching for it — end this over there — is one
option away.

## A stop answers in two shapes, and the relay must carry both

Every state-changing answer camp relays today is one object. A stop is one object too — until the
reference matches more than one session, at which point the far side answers with *rows*: the
candidate list, on stdout, because the rows are the answer to what was asked.

So a single verb produces both shapes, and the object-only relay cannot carry it. The relay gains
one payload reader that accepts either an object or an array of rows and reports which came back;
the existing object relay is re-expressed on top of it, unchanged in behaviour, rather than
duplicated beside it.

## Two reserved exit codes, not one

A stop's exit status has to carry four distinguishable outcomes, and a scripted caller must be able
to branch on them without parsing prose:

- **0** — the stop happened (or the session was already down; both are successes, told apart by the
  answer's own `outcome` field, never by the status).
- **2** — the reference was ambiguous. The same status a local `camp kill` uses, over the same
  candidate rows, so a consumer that learned the shape locally reads the remote answer unchanged.
- **3** — the outcome is unknown. Distinct from success and from every certain failure alike.
- **1** — every certain failure: unreachable, unpinned or changed key, refused credentials, camp not
  resolvable there, a far-side refusal in its own words, or an answer camp could not parse.

Both **2** and **3** are reserved against pass-through. The far side's own exit code is never
relayed into either: a remote camp that happens to exit 2 or 3 for its own reasons would otherwise
impersonate camp's "this was ambiguous" or camp's "I do not know", and the two signals a script can
act on without reading prose would stop meaning what they say. The status is decided here, from the
payload camp actually parsed, and a remote status landing on a reserved code collapses to 1.

## A retry is the dangerous move, so the uncertain report leads with the check

An operator's reflex on a failed command is to run it again. After an uncertain *launch* that
actually succeeded, a retry leaves two sessions on one workspace. After an uncertain *stop* it is
worse in kind: the reference survives a stop — `camp launch --resume <id>` brings the session back
under the same reference — so a retried stop can end a session the operator has since deliberately
restarted, and there is nothing in the second stop's report to reveal that it killed a different
run of the same session.

So the uncertain report names the command that settles the question first, before any explanation,
and reads as an instruction. An operator who reads only the first line must still do the right
thing.

## Camp's own sentence leads; the far side's words follow

A declared host is trusted to run commands, not to write camp's most consequential sentence. Camp's
own line — whether the session was certainly stopped, certainly not, or unknown — holds the first
position on stderr, and the far side's refusal is relayed after it, in its own words, unwrapped.
Left in front, remote text crafted to read like camp's own check-before-retry instruction would
send the operator to exactly the wrong place.

## Relayed row values are the operator's next decision, so they are stripped too

Camp already strips terminal control sequences from a far side's stderr, and from every string
inside a relayed single-object answer. It does not strip them from the values inside relayed *rows*
— those are stamped with the machine and printed as the far side wrote them.

That was tolerable while rows were only ever a listing. It is not tolerable here. The first rows
this slice relays are the candidate list an operator reads immediately before retrying a
destructive verb, and a far side that can move the cursor or clear a line inside a candidate's name
can hide a row from that list or make two of them render as one. The operator then stops whichever
session he believes he saw. The strip already exists and already recurses; it applies to rows as
well, on every relay path.

## An answer camp cannot parse is a failure, on every path

The transport in use does not propagate the remote command's exit status, so a far side whose camp
could not run at all comes back classified as `Answered` with an empty stdout and a status of zero.
The rows relay reads that as an answer it could not parse, synthesizes an `ok: false` row — and then
exits **zero**, because it passes the remote's status through.

That is a success report for a machine that ran nothing. It is fixed here rather than deferred,
because the same rule is being written for the stop verb anyway and a contract that holds for one
host verb and not the other two is not a contract. An unparsable answer exits non-zero on every
relay path.

This changes `list --host` and `sessions --host`, both of which reach an operator only through an
option that did not exist before this spec's own earlier slices — so nothing that worked before this
work changes behaviour, cost, or failure mode. The all-hosts merge is untouched: its status has
always been decided by the local answer, by design.

## State — the named machine stops the session and reports it stopped

The far side resolved the reference, signalled the session, confirmed its absence, and answered with
one object naming the session id, the multiplexer name, and its outcome.

Camp relays that answer with the machine stamped on it. Stdout carries only the session id, so
`$(camp kill <ref> --host ...)` captures the same value whichever machine ran it; the machine, the
name, and the fact that the reference still resumes the session go to stderr.

A session that was **already down** is the second success and is not flattened into the first. Both
exit 0, so the status cannot carry the difference — the answer's own `outcome` field does, exactly
as it does locally, and the stderr line says which happened. A caller that has to know whether it
actually reclaimed anything reads the field, not the prose.

## State — the named machine has no session matching the reference

The far side resolved nothing and refused in its own words, distinguishing a mistyped reference
against a populated pool from an empty pool that explains itself.

Camp relays that refusal unwrapped, behind its own line stating that no session was stopped. This is
a certain failure: nothing changed on the far side, and there is nothing to go check.

## State — the reference matches more than one session on the named machine

The far side refused to guess and answered with the candidate rows.

Camp relays the rows on stdout — the same bytes the far side emitted, stamped with the machine —
and exits 2, the status a local ambiguity already uses. The far side's own sentence naming how many
matched, and in which accounts, follows camp's line on stderr. Nothing was stopped, and the
correction is a longer prefix, which the relayed rows are what make possible.

## State — the named machine refuses the stop and states its own reason

The far side resolved exactly one session and declined to stop it: the session owns no multiplexer
session to signal, a foreign pane holds the name, the stop would target camp's own session, or the
session was still present after the signal and its memory was not reclaimed.

Every one of those reasons is the far camp's to state, and each points at a different next move. Camp
relays the sentence verbatim behind its own line, and never collapses them into a generic remote
failure. This is the criterion the slice exists to make true of a destructive verb.

## State — the named machine cannot be reached

The connection never completed — the host is down, the name does not resolve, the key is unpinned or
has changed, or every credential was refused — or it completed and answered that camp could not be
run there.

These render as they already do, with one addition: the report states plainly that no session was
stopped. Without that sentence the operator holds a failure of unknown consequence and has to go
look, which is the cost the certain cases exist to avoid.

## State — the connection drops after the stop was sent and the outcome is unknown

The connection completed and the far side then failed to answer within the bound.

Camp reports that it does not know whether the session was stopped, names the single command that
settles it, and exits 3. It does not guess in either direction: a stop reported as failed that
actually succeeded sends the operator hunting for a session that is gone, and a stop reported as
succeeded that never ran leaves memory held by a session nobody is watching.

The instruction to check comes first, before the explanation. The command it names is the listing
directed at the same machine, so the operator's next step needs no construction.

That machine is, by construction, the one that just stopped answering, so the check can fail the
same way the stop did. A report that names only the check hands an operator a loop. It names the
fallback too — reach the machine directly and look — on the line after the check and before the
explanation.

## State — the all-hosts form is asked for a state-changing verb and is refused

Camp refuses before contacting any machine, naming the reason as the verb's own: this verb changes
state, so it acts on the one machine the operator names.

The wording is the verb's, not a generic "has no meaning here" — a generic refusal would read as an
oversight rather than the decision it is. The refusal names the single-host form so the real intent
is one option away.

## State — the named machine's camp could not run at all, and the empty answer is reported as a failure rather than as a successful empty result

The far side answered with nothing parseable — an empty stdout, a shell error, a JSON array whose
elements are not rows — while the transport reported success because it does not carry the remote
command's status.

Camp synthesizes its own `ok: false` row, as it already does, and now exits non-zero as well. An
empty answer read as "the host has nothing to report" is the one confident wrong answer the whole
host surface exists to remove, and an exit status of zero says exactly that to every caller that
does not parse the rows.
