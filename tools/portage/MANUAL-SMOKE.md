# Manual Parity Smoke — portage/landing deployment-extraction gate

This is the manual parity gate for the deployment-extraction hard cut.

Run this checklist on a **real PR** after the full automated suites are green
and **before** craft's `release` capability cluster is deleted. A written
PASS recorded in the log below is the trigger for that deletion.

For day-to-day portage/landing operation, see the tool READMEs instead.

---

## Entry preconditions

All automated suites must be green before starting. Run per-directory to avoid
the whole-repo pytest basename collision (no `__init__.py` in test dirs):

- `trailhead/tests/` — 772 tests
- `tools/portage/tests/` — 38 tests
- `tools/landing/tests/` — 39 tests

craft also has 3 known-baseline-RED tests that are **environmental, not
regressions from this work**. They are not a gate blocker; do not attribute
them to the extraction. The three failing tests are:

- `test_handoff_capture::test_real_lore_handoff_with_pickup_hints_file`
- `test_handoff_capture::test_pickup_resume` (two variants)

These fail because of lore-vault synthetic-fixture issues in the test
environment, unrelated to the portage/landing code path.

---

## The parity checklist

Run each step on a real camp group with a real PR. Check each box and record
the result in the log section below.

---

### Step 1 — `/portage:open`

Invoke `/portage:open` on a camp group that has uncommitted or unpushed changes.

Confirm:
- [ ] The skill reviews the diff and dispatches `portage:updater`.
- [ ] `updater` detects the active repos via `repos.detect()`.
- [ ] Commits are pushed and a PR is opened for each active repo.
- [ ] The `prs.json` sidecar is written alongside the group TOML, listing the
      opened PR URLs and their dependency order.
- [ ] For a multi-repo group: the sidecar includes all PRs with their sibling
      links populated.

**Pass criteria:** PR(s) exist on the remote; `prs.json` sidecar is present
and parses correctly.

---

### Step 2 — `/portage:monitor` (or background `portage:monitor` agent)

With PRs open from Step 1, invoke `/portage:monitor` (or let the background
agent launched by `/portage:open` run).

Confirm:
- [ ] The monitor reads the `prs.json` sidecar via `pr.read_sidecar()`.
- [ ] It polls CI status via `ci.wait()` and reports the current check state
      in-band (not silently).
- [ ] If a CI check fails, the failure is surfaced to the operator with enough
      context to triage (check name + status).
- [ ] If a provider call fails (bad auth, API outage), the error surfaces
      in-band — the monitor does not silently return a stale or empty result.

**Pass criteria:** CI status is visible in the session; failures are surfaced,
not swallowed.

---

### Step 3 — `/portage:merge`

With PRs mergeable (all CI checks green, no outstanding blocking reviews),
invoke `/portage:merge`.

Confirm:
- [ ] For a **single-PR group**: merge succeeds; branch is deleted; the PR is
      marked merged on the remote.
- [ ] For a **multi-PR group with `merge_order` configured**: PRs are merged
      in the declared order; a PR is never merged before its dependency.
- [ ] For a **multi-PR group WITHOUT `merge_order`**: the skill refuses to merge
      and surfaces a clear message instructing the operator to declare
      `merge_order` in the group TOML.
- [ ] On merge success, the sidecar is updated or removed as appropriate.

**Pass criteria:** correct merge outcome for the group's PR count; the
no-`merge_order` refusal fires as described.

---

### Step 4 — `/landing:soak`

After portage's merge completes (Step 3), invoke `/landing:soak`.

Confirm:
- [ ] **No `soak_health_command` configured:** `soaker` prints
      `soak: n/a — no health command configured` and exits clean. No subprocess
      is spawned. This is the inert-by-default behavior.
- [ ] **`soak_health_command` configured and exits 0:** `soaker` reports a
      healthy deploy and exits clean.
- [ ] **`soak_health_command` configured and exits non-zero (regression):**
      `soaker` escalates to `landing:doctor` automatically; does not silently
      exit 0.

**Pass criteria:** inert-by-default confirmed; regression triggers escalation.

---

### Step 5 — `/landing:resolve` + `landing:doctor`

Simulate (or use a real) regression from Step 4.

Confirm:
- [ ] `landing:doctor` interrogates the GHA deploy log via `deploy.logs()`.
- [ ] On a failing deploy run, `doctor` surfaces the annotation-level failure
      messages from the GHA log — name, path, line, and message are visible.
- [ ] On a **clean deploy run** (no failures in the GHA log), `doctor` does
      NOT false-alarm — it reports healthy and does not escalate.
- [ ] If the GitHub API call fails for a reason other than "no annotations"
      (bad auth, rate-limit, outage), `doctor` raises a `DeployError` in-band
      rather than returning an empty "healthy" result. An unreadable deploy is
      escalated to the human, never silently read as healthy.
- [ ] `/landing:resolve` presents the standard choices: revert, forward-fix,
      recheck, or dismiss; the operator can drive each path.

**Pass criteria:** `doctor` surfaces GHA failures accurately; clean deploys do
not false-alarm; unreadable deploys escalate rather than clear.

---

### Step 6 — Provider failure visibility (in-band contract)

During any of the steps above, if a `gh` call fails, confirm:

- [ ] The failure is reported to the operator in-band (visible in the session
      output), not swallowed into an empty result.
- [ ] The error message names the failing command and the exit code or stderr
      so the operator can diagnose it without reading source.

**Pass criteria:** at least one provider failure was observed (or simulated via
a bad token) and surfaced in-band.

---

## Written-PASS log

Record date, PR URL, and PASS/FAIL per step here. The deletion of
craft's `release` capability is gated on a PASS recorded in this table.

| Date | PR URL | Step 1 open | Step 2 monitor | Step 3 merge | Step 4 soak | Step 5 doctor | Step 6 visibility | Overall |
|------|--------|-------------|----------------|--------------|-------------|---------------|-------------------|---------|
| _pending_ | | | | | | | | |

**Do not proceed with the craft `release` deletion until at least one row
above shows PASS in all step columns.**

---

## Rollback path

The deletion lands as a **single, revertable commit**. If a
post-deletion gap surfaces after the craft `release` cluster is removed, the
escape hatch is:

```
git revert <slice-6b-commit>
```

This revert restores craft's `release` capability cluster exactly. The portage
and landing plugins remain in place — the revert only re-arms the old surface
alongside the new one; no data is lost and no portage/landing work is undone.
Once the gap is closed, the deletion can be re-applied as a new commit.

**The rollback path is fast and non-destructive. Do not hesitate to use it if
post-deletion behavior deviates from the parity smoke.**
