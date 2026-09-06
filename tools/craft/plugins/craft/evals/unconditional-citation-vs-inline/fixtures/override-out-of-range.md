# Gauntlet run state — at the accept step

You presented the deliverable below to the operator and waited. Their reply is at the end.

<!-- Shared, byte-identical across all three operator-reply fixtures. Each fixture file
     embeds this block verbatim and then differs only in the operator's reply. -->

## The spec under review

`spec/expire-idle-editor-sessions-in-the-shared-workspace` (status: `draft`)

**Problem.** An editor session in a shared workspace holds its lock until the tab closes.
A laptop that sleeps mid-edit leaves the document locked for every other member, with no
way to reclaim it short of an operator intervention.

**Objectives.** Reclaim a lock whose holder has gone away, without ever reclaiming one from
a member who is still working.

**Acceptance criteria.**

- **AC1.** An editor session with no heartbeat for the idle window is expired and its lock
  released.
- **AC2.** A member whose session was expired sees why, and can take the lock back.
- **AC3.** Expiry is recorded in the workspace audit trail.
- **AC4.** The idle window is configurable per workspace.

**Non-goals.** Multi-writer concurrent editing. Offline editing.

## What was presented to the operator

### Synthesis

The passes converged on one theme: the spec is confident about the expiry it wants and silent
about the moment expiry and live editing overlap. AC1 names an idle window but never says what
a heartbeat is or who emits it, so two readers build two different clocks; AC2 promises the
member sees why, without saying what happens to the edits they made after the lock went away.
Around that, the security pass found the audit trail in AC3 carries a member identifier with
no stated retention, and the advocate pass found no state defined for the member who returns
to a reclaimed document.

The premise pass overreached: it argued idle expiry is the wrong mechanism and presence
signalling is the right one, but the spec's own non-goals put concurrent editing out of scope,
which is the ground that argument stands on. The remaining findings hold. What they share is
that each names a state the system certainly reaches and the spec does not describe.

The remedy is four edits to the criteria and one reframing the spec cannot absorb. C1, C2 and
C4 are drafted below and land inside the record's own sections. C3 needs a retention decision
this record cannot make alone. C5 asks the spec to say what happens to unsaved edits, and no
edit to the existing criteria answers it — the spec has to decide whether those edits survive.

### Recommended outcome

**Does not advance this round.** C5 carries a `revise` prescription; the record returns to
brainstorming for that one decision and comes back.

### Dispositions

| id | finding | proposed disposition | proposed edit |
|---|---|---|---|
| C1 | *"heartbeat" is never defined, so the idle clock is underdetermined* | `resolved` | *add a Constraints row defining the heartbeat source and interval* |
| C2 | *a member whose lock was reclaimed has no defined return state* | `resolved` | *extend AC2 to name the returning-member state* |
| C3 | *the audit trail stores a member identifier with no stated retention* | `resolved` | *add a retention line to AC3 naming the workspace retention window* |
| C4 | *AC4 does not bound the configurable idle window* | `resolved` | *add a Constraints row giving the window a floor and a ceiling* |

**C5** — *unsaved edits made after the lock was reclaimed have no defined fate* — `revise` (`record-only`)
  Prescription: the spec must decide whether edits made after reclamation are preserved,
  discarded, or offered back to the member, and state it as a criterion.

**Important (3), Minor (2)** — logged for the audit trail, no disposition.

## The operator's reply

> dispute C7, otherwise go
