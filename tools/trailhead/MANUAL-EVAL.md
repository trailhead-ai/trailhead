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

None, and none are expected. This plugin ships no prose an agent reads — no
skills, no agents, no ruleset — so there is no behaviour for a case to measure.
Its capability surface is one SessionStart hook script and a plugin manifest,
both tested by running them.

See `plugins/trailhead/evals/README.md`. If this plugin grows a skill, an agent,
or a ruleset, that changes and a case belongs here.
