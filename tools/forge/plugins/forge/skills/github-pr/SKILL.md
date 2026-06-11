---
name: github-pr
description: >
  Core GitHub PR scripts for a camp group: detect active repos, evaluate PR status,
  merge in dependency order. The release scripts that create-pr, update-pr, watch-pr,
  and merge-pr all delegate to these scripts. Use /github-pr directly when you need
  low-level access to the release script layer.
---

# GitHub PR Scripts

This skill documents the forge release scripts and their CLI interfaces for direct
invocation or scripting. The higher-level skills (`create-pr`, `update-pr`, `watch-pr`,
`merge-pr`) are the normal entry points; this skill is for direct script access.

**Scripts:** `detect_repos.py`, `merge_prs.py`, `pr_evaluate_status.py`,
`wait_for_actionable.py`, `release_prs_sidecar.py`, `check_pr_status.py`,
`runner_protocol.py`

## detect_repos.py

Lists active repos from a camp manifest:

```bash
python3 <SCRIPTS_DIR>/detect_repos.py --manifest <manifest_path>
```

Output: JSON array of `{repo, path, branch, ahead, dirty}` per active member.
Members whose `worktree_path` no longer exists are silently skipped.

## merge_prs.py

Merges PRs in dependency order:

```bash
python3 <SCRIPTS_DIR>/merge_prs.py \
  --manifest <manifest_path> \
  [--toml <group_toml_path>] \
  <repo_path>:<pr_number>:<member_name> [...]
```

Reads `merge_order` from `[release]` in the group TOML when `--toml` is provided.
Refuses to merge >1 PR without a declared `merge_order` (R-6 safety gate).
Output JSON: `{"merged": [...], "failed": {...}, "skipped": {...}}`.

## pr_evaluate_status.py + wait_for_actionable.py

Classify a PR's actionability and wait until something is actionable:

```bash
python3 <SCRIPTS_DIR>/wait_for_actionable.py \
  [--since <iso8601>] \
  [--review-bot-login <login>] \
  <repo_path>:<pr_number> [...]
```

Optional `--review-bot-login` wires the configured review bot; absent = CI-only mode.

## release_prs_sidecar.py

Read/write the forge-owned `prs.json` sidecar alongside the camp manifest:

```bash
python3 <SCRIPTS_DIR>/release_prs_sidecar.py write \
  --sidecar <manifest_dir>/prs.json \
  --pr <repo>:<pr_number>:<url>:<branch> [...]

python3 <SCRIPTS_DIR>/release_prs_sidecar.py read \
  --sidecar <manifest_dir>/prs.json
```

Sidecar shape: `{schema_version:1, prs:[{repo, pr_number, url, branch}], external_tracker:null}`.

## Configuration

All configurable seams live in the group TOML `[release]` block:

```toml
[release]
merge_order = ["repo-a", "repo-b"]   # optional; required for >1 PR
review_bot_login = "<gh-login>"       # optional; default none (CI-only)
soak_health_command = "<command>"     # optional; default none (inert soak)
external_tracker = { kind = "..." }   # optional; default none (no connector built)
```
