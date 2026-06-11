# Release Rituals

Forge's `release` capability de-zenithed release scripts that operate on a
**camp group** — reading repos, merging PRs, and evaluating actionability from
the **camp central manifest** (not the retired single-repo worktree manifest format).

## Camp manifest read surface

The release scripts read the camp central manifest at:

    <camp_state_dir>/<group>/worktrees/<slug>/manifest.json

Shape (schema v1):

```json
{
  "schema_version": 1,
  "group": "<group-name>",
  "slug": "<worktree-slug>",
  "branch": "worktree-<slug>",
  "members": [
    {
      "name": "<repo-name>",
      "repo_root": "/absolute/path/to/canonical/repo",
      "worktree_path": "/absolute/path/to/worktree"
    }
  ]
}
```

`worktree_path` is the path each script passes to `git -C`. Scripts take the
manifest path as an explicit CLI arg (`--manifest <path>`) — they do not call
`manifest_path_for` or `load_group` (stdlib json only, B-1 self-containment).

## D-1: prs.json sidecar

PR associations are stored in a **forge-owned sidecar** alongside the camp
manifest, at `<manifest_dir>/prs.json`:

```json
{
  "schema_version": 1,
  "prs": [
    {"repo": "<name>", "pr_number": "42", "url": "https://...", "branch": "worktree-feat"}
  ],
  "external_tracker": null
}
```

`external_tracker` is a reserved, null-defaulting field — no connector is built.
The sidecar is written atomically (temp + os.replace, mode 0o600) via
`release_prs_sidecar.py`.

## D-2: merge order

`merge_prs.py` resolves order from:

1. **`[release].merge_order`** in the group TOML — if present, PRs merge in
   that order.
2. **Manifest member order** — used only for a single PR (no safety concern).

**Safety gate (R-6):** when >1 PR is queued and no `merge_order` is declared,
`merge_prs.py` refuses:

```
refusing to merge N PRs with no merge_order declared —
add merge_order = [...] to the [release] block of your group TOML
```

This converts a potential wrong-order merge (irreversible at GitHub) into a
one-time config requirement. Groups with one repo pay no friction.

## D-3: pluggable release config

Optional keys in `[release]` of the group TOML:

```toml
[release]
merge_order = ["alpha", "beta"]          # D-2: PR merge order
review_bot_login = "my-review-bot"       # D-3: optional review bot
# soak_health_command = "curl -f ..."   # D-3: soak probe (Slice 6)
# external_tracker = { kind = "..." }   # D-3: reserved; no connector
```

All keys are optional. Omitting `review_bot_login` → CI-only PR evaluation
(no bot review path). Omitting `soak_health_command` → soak reports
`n/a — no health command configured` (inert by default).

## Error convention split

The three evaluator scripts (`pr_evaluate_status.py`, `check_pr_status.py`, `wait_for_actionable.py`) return **JSON on stdout including errors** (callers parse structured output). The orchestration scripts (`detect_repos.py`, `merge_prs.py`) use **exit-code + stderr** for errors (exit 2 on config/manifest failure, exit 1 on partial-merge). Do not mix the two conventions within a single script.

## Script inventory

| Script | Role |
|--------|------|
| `manifest_read.py` | Shared manifest reader; single `ManifestReadError` type for both orchestration scripts |
| `detect_repos.py` | Reads manifest members[], returns active repos with branch/ahead/dirty |
| `merge_prs.py` | Merges PRs in resolved order with safety checks |
| `pr_evaluate_status.py` | Classifies a PR: done/rebase/fix_ci/rerun_ci/review/wait |
| `check_pr_status.py` | Fetches raw PR state via gh (inner layer) |
| `wait_for_actionable.py` | Polls pr_evaluate_status until actionable or timeout |
| `release_prs_sidecar.py` | Reads/writes the prs.json sidecar |
| `runner_protocol.py` | Injectable runner protocol for all gh/git calls |

## Injectable runner (R-1, S-4)

All `gh`/`git` calls go through `runner_protocol.run()`:

```python
import runner_protocol as rp

result = rp.run(["gh", "pr", "view", pr_number, "--json", "state"], cwd=repo_path)
```

In tests, inject a stub:

```python
def stub(cmd, **kwargs):
    return subprocess.CompletedProcess(cmd, 0, json.dumps({...}), "")

result = detect_repos(manifest_path, runner=stub)
```

The runner is always `shell=False` (arg-list, not a shell string). A
`pr_number` containing `;` or `$(...)` is passed as a literal string arg to
the called binary — no subshell spawns.

## R-7: missing worktree path

`detect_repos.py` skips members whose `worktree_path` does not exist (e.g.
after `camp break`). The group does not fail because one sibling is gone.

## R-9: no merge lock (known residual)

Concurrent `merge_prs.py` invocations are not guarded by a lock file. Solo
use makes the race unlikely. Revisit if concurrent merges become a pattern.
