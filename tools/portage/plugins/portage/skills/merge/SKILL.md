---
name: merge
description: >
  Merge PRs in dependency order after verifying all are mergeable and clean. Use for
  /portage:merge, "merge the PRs", "merge and clean up", or when monitor reports all PRs
  are done and you want to merge immediately without the watch loop.
---

# Merge PR

**Recommended tier:** Sonnet/low — pure script orchestration + git cleanup.

Merges open PRs for a camp group in dependency order. Stops if any PR is not ready.
Every camp group member is a peer — there is no privileged member.

**Scripts:** `merge_prs.py`, `detect_repos.py`, `release_prs_sidecar.py` (thin
consumers of `trailhead.vcs`). `SCRIPTS_DIR` is `<portage_plugin_root>/scripts/`.

## Steps

1. **Collect all open PR pairs.** If not provided as arguments, detect them:

   ```bash
   python3 <SCRIPTS_DIR>/detect_repos.py --manifest <manifest_path>
   ```

   For each detected repo: `gh pr list --head <branch> --json number --jq '.[0].number'`.

2. **Run merge_prs.py** with the collected pairs in `<repo_path>:<pr_number>:<member_name>` format:

   ```bash
   python3 <SCRIPTS_DIR>/merge_prs.py \
     --manifest <manifest_path> \
     --toml <group_toml_path> \
     <repo1>:<pr1>:<member1> [<repo2>:<pr2>:<member2> ...]
   ```

   Output JSON: `{"merged": [...], "failed": {...}, "skipped": {...}}`.

   `merge_prs.py` reads `merge_order` from the `[release]` block of the group TOML.
   For >1 PR without `merge_order` declared, the script refuses with a named error —
   honor that exit code:
   `BLOCKED: merge_prs.py requires merge_order configured in [release] of the group TOML`

3. **Report results.** If any failed, surface the reason and suggest next steps.

4. **Clean up** after all PRs merge:

   ```bash
   git -C <repo> checkout main && git -C <repo> pull
   ```

   for each merged repo.

## External tracker seam

After successful merge, check `[release].external_tracker` in the group TOML.
No issue tracker configured — status transitions skipped. Configure
`[release].external_tracker` in the group TOML to wire a tracker.

## Notes

- `merge_prs.py` uses `gh pr merge --merge` (squash flag comes from group config, not
  hardcoded — the provider does not impose a merge strategy).
- Merge order: configured in `[release].merge_order` of the group TOML; absent for
  single-PR groups (no order needed); required for >1 PR to avoid silent mis-merges.
