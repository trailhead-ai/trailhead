# Gauntlet calibration — provenance and tuning notes

Reference for `gauntlet/SKILL.md`. This document is provenance and tuning notes held from the pilot
runs that established this protocol, consulted after a run rather than applied during one.

## Calibration

Held from the pilot runs that established this protocol — these are the failure modes the skill is
shaped to avoid:

- **The premise attack and the divergence probe are the highest-yield passes.** They are also the
  two a naive "just run the council on the spec" version omits. If the budget ever has to shrink,
  these are the last two to go, not the first.
- **A clean fact pass proves one thing only.** See `SKILL.md`'s "Two independent failure axes"
  section. Do not let it soften the adjudication of the other seven.
- **Convergence beats confidence.** A finding two blind passes reached independently outranks a
  finding one pass asserted forcefully.
- **The adjudicator is a reviewer, not a router.** Consolidation, spot-verification, and the single
  recommendation built out of them are the job. Thirty findings forwarded verbatim is a failed
  adjudication.
- **The compound-criterion Critical bar (step 4, item 5) is covered by a manual eval, not CI.** CI
  runs `ruff` and `pytest` and invokes no eval, so a green suite says nothing about whether this
  bar, or the consistency-auditor check it depends on, still detects a compound criterion — a
  passing test run is a one-time gate, not a standing signal. An edit to either this bar or that
  check should re-run the adjudication arm before trusting the change; see
  `tools/craft/MANUAL-EVAL.md` for how an arm is dispatched.
