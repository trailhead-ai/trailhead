# `trailhead.vcs` — provider-agnostic VCS interface

`trailhead/vcs/` defines one provider-agnostic interface (`Provider`) with four
namespaced surfaces and ships a single `GitHubProvider` backend. The library is
the merge/deploy seam that the `portage` and `landing` plugins build on. Every
`gh`/`git` call routes through the injectable runner (`trailhead/vcs/runner.py`,
`shell=False`), so tests inject a stub and never touch the network.

This doc is the **interface contract** plus a **per-method GitLab mapping**. The
GitLab mapping is paper-only (no GitLab code ships) — its job is to prove the
seam is genuinely provider-agnostic and isn't accidentally GitHub-shaped. It is
an *internal* doc and lives under `trailhead/docs/` deliberately, outside the
landing/leak-gate README surface.

## Provider selection

```python
from trailhead.vcs import get_provider

provider = get_provider()            # defaults to "github"
provider = get_provider("github")    # explicit
provider = get_provider("github", runner=my_stub)   # inject a runner (tests)
```

`get_provider(name="github")` is a **registry-backed factory**. The registry
(`_PROVIDERS` in `trailhead/vcs/__init__.py`) is the single source of truth for
`name → backend class`. The default is `"github"`. An unregistered name raises a
legible `ValueError` that names the registered providers and points at this doc.

### Extension point (adding a second backend)

To add a backend (e.g. GitLab):

1. Implement a `Provider` subclass (e.g. `GitLabProvider`) whose `repos`/`pr`/
   `ci`/`deploy` surfaces fulfil the same method contract, routing every
   `glab`/REST call through `trailhead.vcs.runner` (`shell=False`).
2. Add one entry to `_PROVIDERS` in `trailhead/vcs/__init__.py`:
   `"gitlab": "trailhead.vcs.gitlab:GitLabProvider"`.

No call site changes — `get_provider("gitlab")` then resolves through the same
factory. The registry is the only edit.

## The interface

The `Provider` interface (`trailhead/vcs/interface.py`) exposes four surfaces:

| Surface  | Responsibility |
|----------|----------------|
| `repos`  | Active-repo detection from camp's manifest (membership stays camp's). |
| `pr`     | Open/record, status, evaluate, and merge pull/merge requests. |
| `ci`     | CI checks read + poll-to-actionable. |
| `deploy` | Workflow-run + deployment status + log interrogation. |

## Per-method GitLab mapping

This table maps every `repos`/`pr`/`ci` interface method to the GitHub call it
makes today and the GitLab (`glab` CLI / REST) equivalent a future
`GitLabProvider` would use. GitLab's nearest concepts are noted where the shapes
differ (the impedance mismatches are the design risk this mapping de-risks).

| Interface method | GitHub (today) | GitLab (`glab` / REST) | Notes |
|------------------|----------------|------------------------|-------|
| `repos.detect(manifest_path)` | `git rev-parse` / `git rev-list` / `git status` per worktree (camp manifest drives membership) | **identical** — plain `git` over each member worktree; provider-orthogonal | Detection is pure git; no platform API involved either way. |
| `pr.open(sidecar_path, prs)` | local `prs.json` sidecar write (atomic, `0o600`); PRs created via `gh pr create` | sidecar write **identical**; create via `glab mr create` → `POST /projects/:id/merge_requests` | The sidecar is local state, provider-orthogonal. The create call is the platform-specific half. |
| `pr.read_sidecar(sidecar_path)` | local `prs.json` sidecar read + schema validation | **identical** — local state read, provider-orthogonal | The symmetric read half of `pr.open`; promoted onto the ABC so consumers read the sidecar through the typed surface. |
| `pr.status(repo_path, pr_number)` | `gh pr view --json mergeable,mergeStateStatus,isDraft,reviews` | `glab mr view <iid>` → `GET /projects/:id/merge_requests/:iid` | Field mapping: `mergeable` → `merge_status`/`detailed_merge_status`; `mergeStateStatus` → `detailed_merge_status`; `isDraft` → `draft` (bool); `reviews` → MR approvals/notes. |
| `pr.evaluate(status)` | pure classifier over the `status` dict (`done`/`rebase`/`fix_ci`/`rerun_ci`/`review`/`wait`) | **identical** — operates on the normalized `status` dict, not on raw platform JSON | The normalization in `pr.status`/`ci.checks` is what keeps `evaluate` provider-agnostic. |
| `pr.merge(pr_pairs, manifest_path)` | `gh pr merge <n> --merge --author-email …`; `git push origin --delete -- <branch>` | `glab mr merge <iid>` → `PUT /projects/:id/merge_requests/:iid/merge`; branch delete via `--remove-source-branch` | The ordered-merge safety gate is provider-orthogonal (operates on `merge_order` + manifest). |
| `pr.summary_inputs(repo_path, pr_number)` | `gh pr view --json number,title,body,state,mergeable,statusCheckRollup` + `gh pr diff` + `gh api repos/{o}/{r}/pulls/{n}/comments` | `glab mr view <iid>` + `glab mr diff <iid>` + `GET /projects/:id/merge_requests/:iid/notes` (diff notes) | The single boundary read a PR summary makes; untrusted free text (title/body/diff/comment bodies) is marker-wrapped once at ingress so summaries can't launder it out of the marker. |
| `ci.checks(repo_path, pr_number)` | `gh pr checks --json name,state,link` + `gh api repos/{o}/{r}/check-runs/{job_id}/annotations` | `glab ci status` / `GET /projects/:id/merge_requests/:iid/pipelines` → `GET /projects/:id/pipelines/:pid/jobs` | **Impedance mismatch:** GitLab has no per-job *annotations* concept. The nearest signal is the job trace (`GET /projects/:id/jobs/:job_id/trace`) parsed for failures, or pipeline/job `status`. A `GitLabProvider` normalizes pipeline jobs into the same `failingChecks[{name,state,link,annotations}]` shape `evaluate` expects. |
| `ci.wait(pr_pairs)` | polls `pr.status` → `pr.evaluate` until actionable/timeout | **identical** — built on `pr.status` + `pr.evaluate`, both already mapped | Poll loop is platform-agnostic. |

### `deploy` surface

The `deploy` surface is the post-merge deploy-health signal `landing`'s `doctor`
interrogates. Every call is list-form `gh api` through the runner (`shell=False`);
the jq `-q` filter is a list element, never a shell pipe.

| Interface method | GitHub (today) | GitLab (`glab` / REST) | Notes |
|------------------|----------------|------------------------|-------|
| `deploy.workflow_runs(repo_path, status=…, per_page=…)` | REST `gh api repos/{o}/{r}/actions/runs[?status=&per_page=]`, parse `.workflow_runs[]` (`id` int, `name`, `status`, `conclusion`, `head_sha`, `created_at`, `html_url`, `workflow_id`) | `glab ci list` / `GET /projects/:id/pipelines[?status=&per_page=]` → each pipeline (`id`, `status`, `sha`, `ref`, `created_at`, `web_url`) | **Impedance mismatch:** GHA *workflow runs* ≈ GitLab *pipelines*. GHA `conclusion` (success/failure/…) is folded into GitLab's single pipeline `status` field; a `GitLabProvider` would normalize `status`→(`status`,`conclusion`). Uses REST `id` (int), **not** `gh run list`'s `databaseId`, so the run→job→annotation chain stays consistent. |
| `deploy.status(repo_path)` | `gh api repos/{o}/{r}/deployments` then `.../deployments/{id}/statuses`; parse `state`, `environment`, `created_at`, `log_url`, deployment `id`/`sha` | `GET /projects/:id/deployments` → each deployment's `status`, `environment.name`, `created_at`, `deployable.web_url`, `sha` | GitLab deployments are first-class (no separate statuses sub-resource — `status` is on the deployment). **Zero deployments is a valid steady state on both → `[]`, never raise** (the API is opt-in; out-of-band deploys leave it empty). |
| `deploy.logs(repo_path, job_id=…)` | `gh api repos/{o}/{r}/check-runs/{job_id}/annotations --paginate -q '[… select(.annotation_level=="failure") … ]'` → `[{path, start_line, message}]` + truncation sentinel | `GET /projects/:id/jobs/:job_id/trace` (raw log), parsed for failures | **Impedance mismatch (the sharpest one):** GitLab has **no per-job annotations** concept. GHA annotations give structured `{path, start_line, message}` failure rows; GitLab only exposes the raw job *trace*, so a `GitLabProvider` must regex/parse the trace into the same `{path, start_line, message}` shape `doctor` consumes — a lossy reconstruction, not a 1:1 field map. A clean / not-found job yields `[]` on both (no false alarm). |

## Sources

- [GitLab Merge Requests API](https://docs.gitlab.com/api/merge_requests/)
- [`glab mr` subcommands](https://docs.gitlab.com/cli/mr/)
- [GitLab Pipelines API](https://docs.gitlab.com/ee/api/pipelines.html)
- [GitLab Jobs API](https://docs.gitlab.com/ee/api/jobs.html)
