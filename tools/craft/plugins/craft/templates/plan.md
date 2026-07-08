# {{name}} Implementation Plan

<!-- This is the PARENT task body. A plan is a parent `task` record; each slice is its own
child `task` record (Delivers / Test contract / Files — see templates/task.md) wired to this
parent via `--parent` and ordered with `--depends-on`. Do NOT inline `### Slice` sections
here — render one child task per slice instead. -->

**Goal:** <!-- One or two sentences: what this plan achieves when every child task is done. -->

**Delta design:** <!-- High-level design of the change: the components touched, the data flow,
key constraints. Keep it brief — the child tasks carry the per-slice detail. -->

**Given Axioms (ground truth this plan rests on):**
<!-- The facts this plan depends on, each as a citation: a file:line reference, a recorded
decision/ADR, or a constraint the user stated. If you'd have to investigate to know it's true,
it belongs in Known Unknowns, not here. -->

**Known Unknowns:**
<!-- Open questions that must be resolved before or during implementation. Each is a checkbox
so it can be ticked as resolved; note which child task it blocks. -->
- [ ] <!-- describe unknown — blocks which child task -->

## Flow-out

<!-- The knowledge-flow-out completion gate. Tick every item before setting this parent task
`done` — a parent completed without this section gets a flow-out reminder from the CLI. -->

- [ ] Touched area/subsystem profiles updated with what changed
- [ ] Prover-validated assumptions captured as session candidates (durable at flush)
- [ ] New decisions / lessons / follow-ups surfaced during the build recorded
