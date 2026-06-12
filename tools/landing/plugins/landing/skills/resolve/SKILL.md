---
name: resolve
description: >
  Human-in-the-loop decision handler for post-merge deploy-health incidents. Invoked
  after soak escalates a regression via doctor, or directly by the user once they have
  reviewed the investigation output. Use for /landing:resolve, "revert the incident",
  "dismiss as flake", or "apply the forward fix".
---

# Resolve

After the landing soak escalates a regression, the user reviews the `doctor`
investigation output and decides on an action. This skill dispatches the
corresponding handler.

**Agents:** `doctor` (for investigation context), `soaker` (for wait-and-recheck)
**Scripts:** `git`/`gh` for revert/fix flows

## Usage

```
/landing:resolve <incident-context> <action> [reason]
```

Or trigger phrases:
- "revert the failing deploy"
- "dismiss this as a flake — reason: ..."
- "apply the forward fix from doctor"
- "wait and recheck"

## Actions

### `revert`

For each repo in the merge set:

1. Create a revert branch (`revert-<slug>`) from `main` in the repo's worktree path.
2. `git revert -m 1 <merge_sha>` (targets the first parent — the main branch).
3. `git push origin <branch>`.
4. Open a revert PR via the `/portage:open` flow.

Report the revert PR URLs for each repo.

### `forward-fix`

1. Note the causal chain from `doctor`'s recommendation.
2. Apply the identified fix inline or dispatch a craft implementer subagent
   (`subagent_type: "executor"`) if multi-file.
3. Follow the normal `/portage:open` / `/portage:monitor` flow to ship the fix.

### `wait-and-recheck`

Re-run the soak manually after a delay to re-check the health command:

```
Agent({
  subagent_type: "soaker",
  run_in_background: true,
  prompt: |
    manifest_path: <manifest_path>
    group_toml_path: <group_toml_path>
    merge_pairs: <merge_pairs>
})
```

### `flake-dismiss`

Requires a `reason` argument. Mark the incident as a flake:
- Log: "flake dismissed — reason: <reason>"
- No state is mutated in the camp manifest or prs.json sidecar.
- The user may want to update the `soak_health_command` to be more resilient to
  the transient condition.

## Idempotency

All actions are safe to retry. A revert PR that already exists is reported and skipped.
A dismiss that has already been logged is a no-op.

## Notes

- No external tracker configured — incident state is tracked in terminal output only.
  Configure `[release].external_tracker` in the group TOML to wire an incident tracker.
