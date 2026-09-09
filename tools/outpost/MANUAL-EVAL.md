# Manual Eval — behavioral gates for outpost's own prose

Dev-time acceptance tests for a boundary the pytest suite cannot reach: whether
outpost's **agent and skill prose actually changes agent behavior**. A contract
test can assert what a document says; only a run can show what it causes.

Same role as `MANUAL-SMOKE.md` where that file exists, different boundary — smoke
covers the plugin-system boundary (install, agent registration); this one covers
the behavioral boundary.

**The protocol — why these run by hand, how an arm is dispatched, the trust
boundary on the instructions path, and how to read a result honestly — is in
[`docs/eval-protocol.md`](../../docs/eval-protocol.md).** Read it before adding a
case. This file is outpost's results log only.

Cases live in `plugins/outpost/evals/<case-name>/`.

## Cases

## Case: publish-routing

`plugins/outpost/evals/publish-routing/` — seven fixtures over a scratch
workspace, and `expected.md` carrying the pass condition, written before any arm
was run.

**Under test:** the **Default rule** and **Reasonable exceptions** paragraphs of
`plugins/outpost/rules.md`, installed as `~/.claude/rules/trailhead-outpost.md`.
The repo file was confirmed byte-identical to the installed one first.

| Date | Arm | Prose under test | Runs | Result | Notes |
|------|-----|------------------|------|--------|-------|
| 2026-09-09 | baseline (`--setting-sources project`, no ruleset) | none | 9 | published 0/9 on the positives | wrote to `project/` instead; never located the vault |
| 2026-09-09 | treatment (ruleset appended) | default rule + exceptions | 21 | **12/12 negatives, 8/9 positives** | the one positive miss escaped the fixture (below), not a routing error |

### What it showed

The routing effect is unambiguous: 0/9 against 8/9 on identical fixtures with
identical environments. Without the ruleset the destination is simply not found.

Every one of the four named exceptions held, three runs each — the throwaway
histogram, the confidential incident notes, the build-consumed template fragment,
and the user-named output path. None leaked into a site. That is the direction
with real consequences, since a wrong publish puts content into a vault that
syncs to the whole team, and it did not happen once.

The fixture set was built so no fixed policy could pass: three positives against
four negatives means "always publish" scores 3/7 and "never publish" scores 4/7.

### Containment failure in the fixture design

One positive run is **unmeasurable rather than failed**. It made the right
routing decision, then went looking for the real publish path instead of the
stub the fixture put on `PATH`. It found the installed plugin under
`~/.claude/plugins/cache`, read the real `publish_site.py`, published into the
developer's **real `default` vault**, and triggered a real `lore sync` that
committed and pushed to origin. The site was removed by hand afterwards and the
vault re-synced.

`--setting-sources project` isolates *settings*, not the filesystem. It keeps the
ruleset out of the baseline arm and does nothing to stop a capable agent
locating the genuine tooling. A stub on `PATH` is a decoy, not a sandbox.

This is the second containment escape in two cases, and the first one to reach a
shared remote. **Until these cases run under real isolation — a container, or a
scrubbed `PATH` and `HOME` with the plugin cache out of reach — every run with
shell access must be treated as capable of touching live infrastructure, and the
developer's real vaults and config diffed afterwards.** Recorded in
`docs/eval-protocol.md`.

### Limitations

Seven fixtures, three runs per arm, one model tier, in a clean room with no
PreToolUse hook and a stubbed publish path. Baseline ran only the positives, by
design: an agent with no ruleset cannot over-publish to a destination it does not
know about, so a refusal there would measure ignorance rather than judgment.

Not covered: the routing decision late in a long session or against a user
pushing the other way, and the "never tell a teammate it is live" clause, which
needs a failing sync to become the live question. That clause is a separate
case — and the escape above is a reminder that it is not academic.

When the first case lands, record it here as:

```
## Case: <name>

`plugins/outpost/evals/<name>/` — fixture, and `expected.md` carrying the pass
condition, written before any arm was run.

**Under test:** <the claim, and where it is written down>

| Date | Arm | Prose under test | Runs | Result | Notes |
|------|-----|------------------|------|--------|-------|
```
