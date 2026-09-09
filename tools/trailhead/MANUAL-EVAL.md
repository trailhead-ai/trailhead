# Manual Eval — behavioral gates for trailhead's own prose

Dev-time acceptance tests for a boundary the pytest suite cannot reach: whether
trailhead's **agent and skill prose actually changes agent behavior**. A contract
test can assert what a document says; only a run can show what it causes.

Same role as `MANUAL-SMOKE.md` where that file exists, different boundary — smoke
covers the plugin-system boundary (install, agent registration); this one covers
the behavioral boundary.

**The protocol — why these run by hand, how an arm is dispatched, the trust
boundary on the instructions path, and how to read a result honestly — is in
[`docs/eval-protocol.md`](../../docs/eval-protocol.md).** Read it before adding a
case. This file is trailhead's results log only.

Cases live in `plugins/trailhead/evals/<case-name>/`.

## Cases

None yet. Trailhead's prose has not been measured behaviourally.

When the first case lands, record it here as:

```
## Case: <name>

`plugins/trailhead/evals/<name>/` — fixture, and `expected.md` carrying the pass
condition, written before any arm was run.

**Under test:** <the claim, and where it is written down>

| Date | Arm | Prose under test | Runs | Result | Notes |
|------|-----|------------------|------|--------|-------|
```
