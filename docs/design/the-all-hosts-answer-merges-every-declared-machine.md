# The all-hosts answer merges every declared machine

The rendered surface for `camp list -a` / `--all-hosts` and `camp sessions -a` — every
declared machine plus the one being typed on, contacted concurrently and merged into one
answer where every row says which machine it came from.

`--host <name>` names exactly one machine and relays that machine's answer. `-a` merges.
What this surface establishes is the merged row shape and the non-exiting per-host answer
that every later cross-host form wraps.

## The two axes

Machines and groups are independent options, and they compose:

|                  | the group resolved here | every configured group |
|------------------|-------------------------|------------------------|
| **this machine** | `camp list`             | `camp list -g`         |
| **every machine**| `camp list -a`          | `camp list -ag`        |

`-ag` is not a third flag. It is the two short options bundled, so the combined form
cannot drift from its parts.

`-a` widens the machine axis and leaves the group axis exactly where it was: from inside a
group, `camp list -a` answers for *that* group on every machine. This is what makes the
merged form the same question as the local one rather than a different one, and it is what
makes `-ag` mean something on its own.

**How the narrowing is applied, and why it is applied locally.** A remote invocation is
always the all-groups form — no group resolved from the local working directory ever
crosses the wire, which is the pass-through property the named-host surface already
established and depends on. `-a`'s narrowing is therefore a **filter on the merged rows**,
applied on this side against the `group` field every row already carries. The cost is that
a remote machine answers with groups this invocation then discards; against the operator's
topology that is a few rows, and the alternative would make the remote answer depend on
local state.

A row whose `group` is null is not in the resolved group and is filtered out with the rest.

**With no group resolved here, `-a` has no key to narrow by.** It refuses, naming both
ways forward — `-ag` for every group, or `--group <name>` to name one. It never falls
through to the legacy standalone-worktree source, whose rows report no group at all.

## Identifying the local machine

Every row identifies its machine, including the local ones, and the local machine appears
without being declared anywhere.

- When `hosts.toml` declares `self_name`, that is the local machine's name — the one
  existing mechanism for a machine to name itself, reused rather than duplicated.
- When it does not, the machine-readable form carries `"host": null` and the human form
  says `this machine`.

`null` cannot collide with a declared host name, and it is the honest statement: this
machine has no declared name. Within one merged answer exactly one machine is the local
one, so `null` identifies it unambiguously. Nothing derives a name from the operating
system — that would be a second naming mechanism beside `self_name`, and the two would
disagree the first time a host is declared under a different name than its hostname.

## Order, and what is never re-sorted

The local block first, then declared hosts in the order `hosts.toml` declares them. Within
a block, that machine's own row order, untouched.

Nothing is sorted across machines and nothing is de-duplicated. Two machines holding the
same group and slug produce two rows, because collapsing them would hide exactly the
divergence the merged answer exists to reveal.

## Concurrency

Declared hosts are contacted concurrently through a bounded thread pool — one worker per
declared host. The transport builds an argv and runs a subprocess; it holds no shared
state, so it is used from several threads unchanged.

The local answer is computed while the remote invocations are in flight, so the local work
costs nothing against the wall clock.

The consequence that matters: the time a merged answer takes when several hosts fail to
respond is bounded by the connection timeout, not by the number of hosts. Declaring a
machine that is currently down costs a bounded wait rather than an additive one — the
difference between a command the operator keeps using and one he learns to avoid.

## Exit status

The merged answer exits with the status the local answer would have exited with, whatever
the declared machines did. Unreachability is read from the rows, never from the exit
status, so a script reading this output is not rewritten every time a laptop is closed.

## The per-host answer

Every machine's contribution is produced as a value rather than printed and exited: rows
already stamped with their machine, the stderr lines that machine owes, and the exit code
it produced. The named-host surface is the same value printed and exited on immediately;
the merged surface is several of them concatenated with the exit status taken from the
local one.

This is the seam every later cross-host form rides. It exists here because a surface that
exits inside its per-host branch cannot be merged at all.

## State — zero

Every machine answered, and no machine has a row to report — including the local one.

Human: each machine's header, with nothing beneath it. An empty block is unambiguous,
because a machine that failed carries its failure line instead; and a header with nothing
under it is the local zero rendering — silence — placed under the name of the machine that
was silent. Machine-readable: an empty array. Exit: the local answer's status.

## State — one

Exactly one row across every machine. Rendered identically to *many* below, with one row.
There is no singular form and no count line: the merged answer's shape does not change
with how much of it is filled.

## State — many

Rows under each machine's header, in the order fixed above.

Human, from inside the `trailhead` group:

```
this machine
  camp-attach   /home/tom/.local/state/camp/trailhead/worktrees/camp-attach
andromeda
  camp-attach   /home/tom/.local/state/camp/trailhead/worktrees/camp-attach
  lookout-fix   /home/tom/.local/state/camp/trailhead/worktrees/lookout-fix
```

Machine-readable: one flat array, in the same order, every row carrying its machine in
`host` and — for a session row — its group in `group`. The array is flat rather than
nested by machine so that a consumer of the single-machine form reads it with the same
code path; the machine grouping is a rendering of the human form only.

Every key the single-machine form carries is carried here unchanged. `host` is added;
nothing is removed, renamed, or reordered.

## State — collection failure

A source that could not be read at all, as distinct from a source that answered with
nothing. Three of them reach this surface, and all three are rendered the same way: a
stated line on stderr, a machine-readable `ok: false` row carrying the reason, and no
effect on the exit status beyond what the local answer already decided.

- **A local group config that will not parse.** Already stated by the local answer; it
  arrives here as its existing failure row, with the local machine stamped onto it.
- **A remote machine's own collection failure.** The remote camp already decided it and
  said so in its own words; this side stamps the machine onto the row and relays it.
- **A `hosts.toml` that will not parse.** The declared machines cannot be enumerated at
  all. The local answer is still printed, with one `ok: false` row naming the file and the
  parse failure, because discarding an answer this side already has would be a worse
  outcome than an incomplete one — and silently returning only local rows would read as
  *no machines are declared*, which is a different and false statement.

A machine that could not be *reached* is not a collection failure — it is its own state
below.

## State — no hosts declared

No `hosts.toml`, or one declaring no hosts. The merged answer is the local answer, and it
succeeds. Nothing is printed about the absence: an operator who has declared no machines
is not asking about machines, and `-a` is the same question as the bare verb for him.

This is the state that makes `-a` safe to put in a shell alias before any machine is
declared.

## State — every declared host failed to answer

Every declared machine failed, each for its own reason — unreachable, wedged, an
unrecognized key, no camp over there. The local rows still print, each declared machine
carries its own failure line under its own header, and the exit status is still the local
answer's.

Human:

```
this machine
  camp-attach   /home/tom/.local/state/camp/trailhead/worktrees/camp-attach
andromeda
  unreachable — no response within 10s
lookout
  answered, but camp could not be run there — declare camp_bin for this host in hosts.toml
```

Machine-readable: the local rows, then one `ok: false` row per failed machine carrying
`host` and `reason`. The reasons are the ones the named-host surface already fixes for
each way a machine can fail to answer — the same words, in a row instead of as the whole
answer. That is the entire transformation this surface owes for those states.

The failure is never an omission, never an empty answer, never a raw transport error, and
never fatal to the machines that did answer.

## State — some hosts answered and some failed

The mixed case, and the one the operator actually meets. Answered machines carry their
rows; failed machines carry their failure line; both appear under their own header, in the
declared order, in one answer.

Nothing about a failed machine changes how an answered machine is rendered, and nothing
about a failed machine changes the exit status. A partial answer is stated as a partial
answer by its own rows rather than by a status a script has to interpret.

## State — same slug on two machines

Two machines holding the same group and the same slug produce two rows, under their two
machine headers, with no merge, no de-duplication, and no marker calling them out.

This is the state the merged answer exists for. The operator asks it when a session has
gone missing and he does not yet know which machine it went missing on; collapsing the two
rows into one would hide precisely the divergence he is looking for. The machine is what
tells them apart, which is why every row carries it.
