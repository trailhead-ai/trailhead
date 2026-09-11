# A session starts on a named machine

The rendered surface for a launch directed at a declared host — what camp prints before, instead
of, or after the far side does the work. There is one new option on one verb: `--host`, already
spelled and already resolved for the three read verbs, now accepted by `launch`.

```
camp launch --host <name> --group <group> <slug>    # the session starts there
camp launch -a <slug>                               # refused: launching is not a broadcast
```

## What already holds, and is not re-litigated here

Camp reaches a declared host over a single non-interactive `ssh` invocation with a pinned host key,
no prompting, and a bounded connect time; it classifies what came back into a closed set of
outcomes; and it carries the far side's own stderr through to the operator with control sequences
stripped. Attach already hands the terminal to a named machine. All of that is shipped, and this
document changes none of it.

What is new is not the connection. **It is that camp has never before asked another machine to
*do* something.**

## The gap: a relay shaped like a listing

Every remote answer camp renders today is a list of rows. The relay parses the far side's stdout as
a JSON array, stamps each row with the machine it came from, and prints them. A verb that changes
state does not answer in rows — a launch answers with one session, or with nothing at all — so the
existing relay cannot carry it, and a second rendering shape is what this slice adds.

The deeper difference is not the shape of the answer but the meaning of its absence. When a listing
does not come back, camp has failed to learn something and nothing in the world has changed. When a
launch does not come back, camp has failed to learn whether *something changed*. Those are not the
same report and must not render alike.

## Certainty is a property of the outcome, not of the verb

Camp can say exactly which failures happened before the far side could act. A connection that never
completed — an unreachable host, an unpinned or changed key, a refused credential — ran no camp at
all. A host that answered that it cannot find camp ran no camp either. A camp that ran and refused
did not start a session, and said so in its own words.

Exactly one outcome is genuinely uncertain: the connection completed, and then the invocation
exceeded its bound without answering. There, camp does not know whether a session exists.

This mapping is the substance of the slice. It is stated once, for every state-changing verb that
follows, rather than re-derived per verb.

## The uncertain case is the common case

A launch stalls at the harness's own trust prompt often enough to be measured, and a stalled launch
does not answer. The uncertain outcome is therefore the ordinary one, not a rare edge, and its
report is written to be read by an operator who will see it regularly — it names the one command
that settles the question rather than describing the ambiguity in the abstract.

## Camp does not reach across to clean up

An interrupted launch may leave the far side's camp running. For a listing that residual was
accepted as a leaked read. Here it is not a leak at all: a camp launch deliberately creates a
detached session, whose entire purpose is to outlive the process that started it. Tearing it down
on hangup would destroy the thing that was asked for.

So camp does not attempt remote cleanup, and the uncertainty is resolved by looking rather than by
undoing.

## Launching is never a broadcast

The all-hosts form exists to merge answers from every declared machine. Applying it to a verb that
changes state would start a session on every machine at once. That is refused, and refused for the
stated reason that the verb changes state — not with the wording a verb gets when the option simply
does not apply to it, which would read as an oversight rather than a decision.

## The group is named, never inferred

A launch needs a group, and on the far side no group resolves: the invocation lands in a home
directory belonging to no workspace. Camp does not fill that gap from its own working directory —
a local directory deciding what happens on another machine is the exact substitution the host axis
forbids. The operator names the group, or the launch is refused before any connection is made.

## State — the named machine starts the session and reports where it is running

The far side launched. Camp prints the session's identity on stdout exactly as a local launch does,
so a caller capturing it gets the same value whichever machine it came from, and puts the machine's
name alongside the workspace and the attach handle on stderr.

The report names the machine explicitly. An operator who has just started a session somewhere other
than where they are sitting needs to be told where it went, and the next thing they will do is
reach it — so the report is written to make the follow-on attach obvious without being an
instruction.

## State — the named machine has no such workspace to launch into

The far side's camp resolved the group, found no workspace for that slug, and refused. Nothing was
started.

Camp relays that refusal in the far side's own words and adds nothing. The temptation is to help by
listing what workspaces do exist there, but that is a second invocation and a second answer that
can disagree with the first; the operator already has a command that answers it.

## State — a session is already running on the named machine for the same workspace

Camp's launches are not exclusive — a workspace may hold several sessions, and this is a supported
state rather than a collision. The far side decides what to do, exactly as it would for a launch
typed there directly, and camp carries the result.

This state is enumerated because it is the one an operator most expects to be an error and it is
not. The report reads as an ordinary success, naming the new session, because that is what
happened.

## State — the named machine refuses the launch and states its own reason

The far side ran and declined for any reason it owns — the directory is outside the group's
allowlist, the group is unknown there, the harness is missing. Its reason reaches the operator
verbatim, with control sequences stripped, and its exit status is camp's own.

Camp adds no generic wrapper around it. A remote refusal rendered as "the remote command failed"
discards the only part of the answer worth reading, and a refusal the operator cannot act on is
indistinguishable from a bug in camp.

## State — the named machine cannot be reached

The connection never completed: the host is down, the name does not resolve, the key is unpinned or
has changed, or every credential was refused.

These render as they already do for a listing, with one addition — the report states plainly that
no session was started. That sentence is the whole point of the state. Without it the operator is
left holding a failure of unknown consequence and must go check, which is the cost the certain
cases exist to avoid.

## State — the connection drops after the launch was sent and the outcome is unknown

The connection completed and the far side then failed to answer within the bound.

Camp reports that it does not know whether a session was started, and names the single command that
settles it. It does not guess, and it does not pick the reassuring answer in either direction: a
launch reported as failed that actually started leaves a session nobody is tracking, and a launch
reported as started that never ran sends the operator to attach to nothing.

The exit status is its own — distinct from success and from every certain failure alike. Sharing a
status with a certain failure would be the dangerous choice: the reflex on a failed command is to
run it again, launches are not exclusive, and camp does not reach across to clean up, so a retry
after an uncertain launch that actually succeeded leaves two live sessions on one workspace with
nothing to tell them apart. The distinction is carried in the status so a script can act on it, and
in the words so a person can.

The instruction to check comes first, before the explanation, and reads as an instruction naming
the command to run. An operator who reads only the first line must still do the right thing.

## State — the all-hosts form is asked for a state-changing verb and is refused

Camp refuses before contacting any machine, naming the reason as the verb's own: this verb changes
state, so it acts on one machine that the operator names.

The refusal points at the single-host form rather than only rejecting. An operator who reached for
the all-hosts option was expressing a real intent — do this over there — and the correction is one
option away.
