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

## Case: record-link-rendering

`plugins/outpost/evals/record-link-rendering/` — five fixture records over a
scratch environment, and `expected.md` carrying three separately-judged pass
conditions, written before any arm was run.

**Under test:** the **Record links** section of `plugins/outpost/rules.md`
(landed in commit `4d586099`), installed as `~/.claude/rules/trailhead-outpost.md`.
Both arms were `Read`-only with no shell tool at all — `expected.md` states why
`scripts/eval-sandbox` does not apply to this case.

| Date | Arm | Prose under test | Runs | Result | Notes |
|------|-----|------------------|------|--------|-------|
| 2026-09-10 | baseline (`--setting-sources project`, no ruleset) | none | 3 | linked record 1 in 3/3, but with a wrong target (missing `/records/` segment); linked record 2 (non-standard-path vault) in 3/3; bare on records 3–5 in 3/3 | never told about the rule's link form, resolution steps, or path constraint |
| 2026-09-10 | treatment (Record links section appended) | link form + base/vault resolution + grammar and path fallbacks | 3 | **3/3 correct on all five records** | record 1 linked with the right visible text and right target; records 2–5 all bare |

### What it showed

**Pass**, against the pass condition written into `expected.md` before either
arm ran: 3/3 treatment runs linked record 1 correctly (`[gearshed/note/rotate-tires](http://127.0.0.1:9199/records/gearshed/note/rotate-tires)`)
and printed records 2–5 bare, and baseline did not match treatment's correct
form closely enough to trigger the INCONCLUSIVE clause (below).

The comparison against baseline is more informative than a bare PASS suggests,
and cuts two different ways per fallback:

- **The non-standard-path fallback (record 2, `attic-archive`) is clearly rule-caused.**
  Baseline linked it in 3/3 runs — it has no notion that a vault's path must be
  a direct child of the vaults root, so it treated "the vault resolves in the
  listing" as sufficient. Treatment printed it bare in 3/3. This is the cleanest
  evidence in the case that the rule changes behavior rather than restating a
  default habit.
- **The unresolvable-vault fallback (record 3, `ghost-vault`) is not
  differentiating.** Both arms printed it bare in 3/3 — an agent with no rule
  at all still declines to invent a link for a vault it cannot find anywhere in
  the listing. This condition's bare-printing is not shown to be caused by the
  rule; it may just be ordinary caution.
- **The grammar fallback (records 4–5, out-of-grammar slug/kind) is likewise
  not differentiating.** Both arms printed both bare in 3/3 (6/6 per arm).
  Baseline reasoned its way there independently — one baseline run even
  invoked a slug-grammar regex (`^[a-z0-9][a-z0-9._-]*$`) it was never given,
  evidently from its own general training rather than anything in this
  fixture or arm. **Recorded as a finding, not repaired into the fixture**: the
  written pass condition (condition 3 of the task's test contract) is satisfied
  by treatment's own consistency, but this run gives no evidence that the rule
  — rather than ordinary model caution about malformed identifiers — is what
  produces it.
- **The link-target form is rule-caused.** Baseline's link for record 1 pointed
  at `http://127.0.0.1:9199/gearshed/note/rotate-tires` — missing the
  `/records/` path segment the record-URL contract requires. Treatment's link
  matched the contract exactly in 3/3. This is what keeps the overall result
  out of INCONCLUSIVE: baseline links "as often" as treatment on record 1, but
  not "as correctly."

### Real-state check

Both arms ran with `--allowedTools "Read"` and no shell, `Edit`, or `Write`
tool. Diffed the developer's real state after the batch: `~/.config/lore/`
unchanged, `~/.claude/rules/trailhead-outpost.md` untouched (mtime predates
this session), and every configured lore vault's git status is clean except
`trailhead`, whose only pending changes are this plan's own task-status
bookkeeping (unrelated `task/*.json` `status`/`updated-at` edits from the
plan run this case belongs to) — nothing attributable to these six eval runs.

### Limitations

Five fixture records (one linked, four bare across three distinct fallback
reasons), 3 runs per arm, one model tier, in a clean room with no PreToolUse
hook, `Read`-only tools, and no shell — so this case says nothing about what an
agent does when it *can* reach for a shell instead of following the rule.
Not covered: first-mention-vs-every-row behavior in a table or list, handoff
commands staying bare, the never-link-into-a-record-body rule, or adherence
late in a long session. Two of the case's three named fallback reasons
(unresolvable vault, grammar) were not shown to be caused by the rule rather
than by ordinary model caution — only the non-standard-path fallback and the
link's exact target form were.

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
