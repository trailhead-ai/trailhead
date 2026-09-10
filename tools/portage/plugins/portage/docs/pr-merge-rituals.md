# PR & Merge Rituals

portage's `release` capability operates on a **camp group** — reading repos,
opening/updating PRs, watching CI, and merging in dependency order from the
**camp central manifest**. The deterministic plumbing lives in the shared
`trailhead.vcs` provider library; this doc records the rituals the plugin's
agents and skills follow.

## Camp manifest read surface

The PR/merge flow reads the camp central manifest at:

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

`worktree_path` is the path each operation passes to `git -C`. The provider
takes the manifest path as an explicit input — it does not call
`manifest_path_for` or `load_group` (stdlib json only).

## prs.json sidecar

PR associations are stored in a **sidecar** alongside the camp manifest, at
`<manifest_dir>/prs.json`:

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
The sidecar is written atomically (temp + os.replace, mode 0o600).

## Merge order

The merge order resolves from:

1. **`[release].merge_order`** in the group TOML — if present, PRs merge in
   that order.
2. **Manifest member order** — used only for a single PR (no safety concern).

**Safety gate:** when >1 PR is queued and no `merge_order` is declared, the
merge refuses:

```
refusing to merge N PRs with no merge_order declared —
add merge_order = [...] to the [release] block of your group TOML
```

This converts a potential wrong-order merge (irreversible at GitHub) into a
one-time config requirement. Groups with one repo pay no friction.

## Pluggable release config

Optional keys in `[release]` of the group TOML:

```toml
[release]
merge_order = ["alpha", "beta"]          # PR merge order
merge_method = "squash"                  # merge / squash / rebase / automatic — default: automatic
review_bot_login = "my-review-bot"       # optional review bot
# external_tracker = { kind = "..." }    # reserved; no connector
```

All keys are optional, but omitting `merge_method` is not a no-op: it selects `automatic`
selection, announced on stderr via the notice `portage merge` prints, rather than leaving
merging unconfigured. Omitting `review_bot_login` → CI-only PR evaluation (no bot review path).

## Automatic merge-strategy selection

When `merge_method` is `automatic` (the default), `portage merge` resolves a strategy per pull
request rather than using one strategy for the whole run:

1. Rebase is used whenever the target repository permits it.
2. Otherwise, when exactly one of `merge`/`squash` is permitted, that one is used.
3. Otherwise — rebase forbidden and both `merge` and `squash` permitted — the pull request's
   commit series decides. A series that could not be read, or was truncated by the provider's
   page size, falls back to squashing. A series that resolved cleanly is classified for fix-up
   dominance: more than half its commits counted as fix-ups resolves to squashing; otherwise to
   a merge commit.
4. If the repository's permitted-strategy lookup itself failed, or it reports no permitted
   strategy at all, the fallback is squashing.

A commit counts toward fix-up dominance when its subject opens with a recognised marker — a git
interactive-rebase marker (`fixup!`, `squash!`, `amend!`), this project's `[fix]` convention, a
bare or scoped conventional-commit `fix:`/`fix(scope):` type, or a `wip`/`WIP:` marker — or it
repeats an earlier commit's subject in the same series exactly (only the repeat counts toward
dominance, not the first occurrence) — **and**, either way, its changed-line count (additions +
deletions) does not exceed 50. A marked or repeated commit above that threshold is treated as
real work and excluded from the count.

`portage merge` prints the resolved strategy and the reason for it to stderr before each merge —
see "Strategy disclosure" in `monitor.md` for the exact line shape and how the watch agent
surfaces it.

## Error convention split

The PR-evaluator surface (evaluate-status, check-status, wait-for-actionable)
returns **JSON on stdout including errors** (callers parse structured output).
The orchestration surface (repo detection, merge) uses **exit-code + stderr**
for errors (exit 2 on config/manifest failure, exit 1 on partial-merge). The
two conventions are not mixed within a single operation.

## Injectable runner

All `gh`/`git` calls go through an injectable runner protocol so tests can stub
the external binaries:

```python
result = run(["gh", "pr", "view", pr_number, "--json", "state"], cwd=repo_path)
```

The runner is always `shell=False` (arg-list, not a shell string). A
`pr_number` containing `;` or `$(...)` is passed as a literal string arg to
the called binary — no subshell spawns.

## Missing worktree path

Repo detection skips members whose `worktree_path` does not exist (e.g. after
`camp break`). The group does not fail because one sibling is gone.

## No merge lock (known residual)

Concurrent merges are not guarded by a lock file. Solo use makes the race
unlikely. Revisit if concurrent merges become a pattern.
