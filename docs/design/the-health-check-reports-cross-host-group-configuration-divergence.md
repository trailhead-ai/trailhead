# The health check reports cross-host group-configuration divergence

`camp doctor -a` already tells the operator that a declared machine answers, that camp resolves
there, that a terminal multiplexer is present, and that each declared account is authenticated. It
does not tell him whether that machine agrees with this one about what a group *is*.

Group configuration is per-machine. A group whose policy differs on the far host produces a
workspace that is not the one the operator expected, and he finds out only after a remote create has
already acted on the far machine's idea of the group. Every other way a remote create can surprise
him is now a row in the health check. This one is still invisible.

This slice adds the last axis: per declared host, whether that host configures a group differently
from this machine.

## What already holds, and is not re-litigated here

The host-probe slice established the per-machine block, the `--probe` contract, and the rule that
probe support is read from key-presence in the parsed payload rather than from an exit status or a
transport outcome. The account slice established that a second axis rides the same answer, renders
into the same block, and gets its own row rather than a second shape.

This slice is the third axis on that same surface. It invents no new block, no new option, and no
new transport.

## Divergence is expected, so the comparison is curated

The spec states it outright: per-host group configuration diverges by design. The operator's group
templates gate each optional member on that member's clone being present on that machine, so two
hosts legitimately configure one group differently with no fault on either side.

A second, harder fact sits beside it. `repo_root` is an absolute filesystem path. On this machine a
member's root reads `/home/tomduffield/code/trailhead`; on a Mac it cannot. A comparison over the
whole configuration therefore reports every group on every host as divergent, permanently, from the
first run. The signal would be buried on day one.

So the comparison is over a **policy projection**, not the configuration. Policy is what the two
machines ought to agree about; placement is what they cannot.

**Compared** — the group name; the branch pattern; the member *name* set; per member, its base and
its declared task names; each task's phase, required flag, timeout, capability, cleanup, and step
command vector; the declared account; shared-vault names; lore scopes.

**Not compared** — every filesystem path: a member's `repo_root`, the launch roots allowlist, and a
shared vault's root. These are placement, they are host-specific by nature, and comparing them
produces noise rather than signal.

The projection is a curated list, and that is a real cost: a group field added later is invisible to
this check until someone extends the projection. The alternative costs more. A projection that
includes paths reports divergence that is always present and never actionable, and an operator stops
reading a row that is always yellow.

## The member set is compared by name, and its divergence is real news

Member-set divergence is by design, but it is not therefore uninteresting. A group that declares
four members here and three there is exactly the case where a remote create produces a workspace
missing the member the operator was about to work in. Reporting it is the point; the spec's
by-design note explains why it happens, not why it should be hidden.

So the member set is compared by name — never by root — and a difference is reported naming the
members involved, in both directions.

## The comparison is local, and the far side ships facts rather than verdicts

The far camp answers with its own policy projection. It does not receive this machine's projection
and it does not compute a verdict.

This keeps the far side's answer a statement about itself, exactly as the account roster and the
multiplexer fact already are, and it means a host never needs to know what any other machine
believes. The diff happens here, where the operator is typing, because the local machine is the
reference frame only by virtue of being the one he is asking from — neither machine is authoritative
about the other.

## The facts ride the existing answer, and an older camp omits them

The projection travels as one more top-level key on the existing `--probe` payload, a sibling of the
multiplexer and account keys rather than anything nested inside the checks list.

Support is read from key-presence in the parsed payload — never from an exit status, never from the
transport outcome. A camp too old to know this key omits it and is reported as unable to tell,
exactly as the account axis already handles the same case. That is the established contract on this
surface and this slice does not vary it.

## Divergence never decides the exit status

A diverging host does not change `camp doctor`'s exit status, for the same reason an unreachable one
does not: a check that fails whenever the other machine is configured slightly differently is a check
the operator stops running. Divergence is a row to read, not a failure to act on.

## Cannot-tell is a verdict, not a quiet "no"

A host that could not be asked, a camp too old to answer, and a configuration that could not be
parsed all report that camp could not tell. None of them reports agreement, and none reports
divergence.

This surface has now collapsed a cannot-tell into a confident wrong answer twice, and both times the
false answer sent the operator to fix a machine that was fine. A host whose config could not be read
is not a host that agrees.

## A detail line names dimensions, never values

A divergence row names *which* dimensions differ — a task, a member, the branch pattern — and never
prints the differing values themselves.

Two reasons, and the weaker one is the usual one. The weaker: a step command vector rendered inline
is unreadable in a terminal row. The stronger: a group's configuration is the operator's own, and a
task's step command is the place a credential most plausibly gets embedded by accident. A diagnostic
that prints the contents of a config field turns a health check into a disclosure surface. Naming
the dimension tells the operator exactly where to look, which is all he needed.

## Machine-readable output stays one fact per row

The JSON path keeps the established flat row shape — host, verdict, detail — one row per fact, with
nothing nested. A host with three groups produces three rows, not one row carrying a groups array.

## The human form groups by machine

The human path renders into the per-machine block that already exists, one indented line per group,
in a stable order so two runs are diffable. Remote-authored text — a group name, a member name, a
field name — is escaped before it reaches the terminal, by the same rule the account axis established:
an embedded newline in remote text would otherwise forge a row camp never emitted.

## State — No declared host configures the group differently

Every declared host answered, and every group's policy projection matches this machine's.

One line per group per machine, each reporting that the policy matches. Not silence: a check that
prints nothing when everything agrees is indistinguishable from a check that did not run, and the
operator cannot tell those apart by staring harder.

## State — Exactly one declared host diverges

One host's projection differs from this machine's for at least one group.

That host's block carries a line naming the group and the dimensions that differ — a task, a member,
the branch pattern — rather than a bare "differs". The dimension is the entire value of the row: an
operator who knows *that* something differs still has to go and look, which is the work this check
exists to save him.

## State — Several declared hosts diverge

More than one host's projection differs from this machine's.

Each host's own block reports its own divergence independently. Nothing is summarized across hosts
and nothing is collapsed into a fleet-level verdict, because the machines are not a fleet — they are
two peers, and the operator acts on one at a time.

## State — A declared host does not configure the group at all

The host answered, and declares no group by that name.

A line naming the group as absent there, distinct in wording from a group that is present and
differs. These are different facts with different remedies — one machine is missing a group
definition, the other has a stale one — and rendering them identically would hide which.

## State — A declared host is unreachable, so divergence cannot be determined

The machine was off, unreachable, or its camp could not be run.

The machine's block is exactly what it is today, with no group lines at all. Absent facts are not
rendered as divergence and not rendered as agreement: the operator has learned nothing about that
machine's groups, and the reachability line already says why there is nothing further.

## State — A declared host answers, but its group configuration cannot be read or parsed

The far camp ran, but its group directory was unreadable, or a group file was malformed.

A cannot-tell line naming the group where one can be named, stating that the configuration could not
be read. Never divergence — a file this machine cannot parse is not evidence that the two machines
disagree, and reporting it as disagreement would send the operator comparing two configs when one of
them simply has a syntax error.

## State — This machine configures no groups, so there is nothing to compare

The local machine declares no groups at all.

One line stating there is nothing to compare, and no per-host group lines anywhere. A comparison
against an empty local set would otherwise report every group on every far machine as absent here,
which is true and useless: the operator has not misconfigured those machines, he has simply asked
the question from one that has no groups yet.

## State — This machine's own group configuration cannot be read

The local group directory is unreadable, or a local group file is malformed, so there is no
projection to compare the far machines against.

One line stating that this machine's own configuration could not be read, and no per-host group
rows — comparing against a projection that could not be built would report every far machine as
divergent, when the fault is here. Crucially the other axes are untouched: the multiplexer and
account rows still render exactly as they would have. A local config problem that silently took the
reachability facts down with it would break the one promise this whole spec is bounded by — that
nothing working today changes — and it would do it on the command the operator reaches for
precisely when something is already wrong.
