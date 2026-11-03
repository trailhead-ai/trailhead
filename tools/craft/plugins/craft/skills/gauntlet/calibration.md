# Gauntlet calibration — provenance and tuning notes

Reference for `gauntlet/SKILL.md`. This document is provenance and tuning notes held from the pilot
runs that established this protocol, consulted after a run rather than applied during one.

## Two independent failure axes

This is the calibration that justifies the cost, and it is not obvious:

**A spec can be wrong about the world, or underdetermined about the design — and these are
independent.** A spec can have every factual claim confirmed and still be a bad spec, because
"correct about what exists" and "sufficient to build from" are different properties. In the pilot
that established this protocol, **every one of the spec's factual claims verified clean — and the
other passes still produced seven design-changing findings.** A clean fact pass is not evidence the
spec is sound; it is evidence of exactly one thing.

Both axes need passes pointed at them. That's why the roster is what it is.

## Calibration

Held from the pilot runs that established this protocol — these are the failure modes the skill is
shaped to avoid:

- **The premise attack and the divergence probe are the highest-yield passes.** They are also the
  two a naive "just run the council on the spec" version omits. If the budget ever has to shrink,
  these are the last two to go, not the first.
- **A clean fact pass proves one thing only.** See the section above. Do not let it soften the
  adjudication of the other seven.
- **Convergence beats confidence.** A finding two blind passes reached independently outranks a
  finding one pass asserted forcefully.
- **The adjudicator is a reviewer, not a router.** Consolidation, spot-verification, and the single
  recommendation built out of them are the job. Thirty findings forwarded verbatim is a failed
  adjudication.
- **The compound-criterion Critical bar (step 4, item 5) has no standing gate.** CI runs `ruff` and
  `pytest`, which say nothing about whether this bar, or the consistency-auditor check it depends
  on, still detects a compound criterion. The only case that measures it is a legacy hand-shaped
  case: the plugin eval runner does not run it and the push gate does not gate it
  (`_LEGACY_HAND_SHAPED` in `trailhead/tests/test_eval_corpus.py`). An edit to this bar or that
  check is unmeasured until that case is ported to a runner shape, per `docs/eval-protocol.md`;
  `tools/craft/MANUAL-EVAL.md` holds its logged results.
