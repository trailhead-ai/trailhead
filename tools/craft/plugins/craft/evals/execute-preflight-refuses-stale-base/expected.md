# Expected verdict — execute refuses to build on a stale base

Written **before** any arm was run, so it cannot be retrofitted to an observed result.

## What is under test

`## The Loop` -> `### Workspace preflight` in `tools/craft/plugins/craft/skills/_shared/execute.md`
(placed immediately before `### Claiming the run at first dispatch`) — a step that checks whether
the repo(s) a run is about to build in are stale against their base, and refuses to dispatch or
write status when they are, before any rebase-avoidance is possible for the operator to override.

The prose under test is vanilla-usage-only for this case: no camp workspace is materialised, so
the fixture repos exercise the "in vanilla usage, the single current repo" enumeration branch and
its two direct git computations (`git rev-list --count HEAD..<base>` and
`git for-each-ref --format='%(upstream:track)' refs/heads/<branch>`), not the `camp status --json`
branch.

## The fixture set

`fixtures/make-fixture-repo.sh <behind|upstream-gone|current|fetch-failed> <dest>` materialises a
real git repo with a local `origin` clone source, plus `fixtures/task-ready.md`, a minimal
standalone `ready` task naming a one-line `README.md` edit. The task, its `Delivers` / `Test
contract` / `Files` payload, and the eventual diff are held constant across every variant — the
only thing that varies is the git state of the repo the run is pointed at:

| Variant | Git state | Correct verdict |
|---|---|---|
| `behind` | working branch `work` is 3 commits behind `origin/main` (fetched) | **STOP** before the claim, naming `behind=3` and the fix (`git fetch origin && git rebase origin/main`) |
| `upstream-gone` | `branch.work.merge` points at a ref that was never pushed and does not exist on `origin` | **STOP**, naming "upstream gone" and the fix |
| `current` | `work` is exactly at `origin/main` | **PROCEED** — passes silently, run continues to the claim and dispatches against `task-ready.md` |
| `fetch-failed` | clone's cached `origin/main` is 0 commits behind, then the `origin` remote URL is repointed at a nonexistent path so a fresh `fetch origin` fails | **PROCEED** (behind=0 against the cached ref) with the fetch failure named in the report line — not a stop on the fetch alone |

## Pass condition — the one a harness should read

For each variant, the full conjunction:

1. **`behind`** — the run stops before dispatching anything against `task-ready.md`, and its
   report names the repo/member with `behind=3` (or an equivalent count derived from the fixture,
   not a placeholder) and states the fix command (`camp rebase` or `git fetch origin && git rebase
   <base>`).
2. **`upstream-gone`** — the run stops before dispatching, and its report says the upstream is
   gone (or an equivalent phrase naming the missing upstream), not merely that something is wrong.
3. **`current`** — the run does **not** stop on the preflight step; it proceeds to describe
   claiming the task / dispatching the executor against `task-ready.md`.
4. **`fetch-failed`** — the run does **not** stop solely because the fetch failed; it reports the
   fetch failure in its output but reaches the same verdict as `current` (proceeds), because the
   cached `origin/main` still resolves and `behind` computes to 0 against it.

A run that stops on every variant (including `current` and `fetch-failed`) fails 3 and 4. A run
that proceeds on every variant (including `behind` and `upstream-gone`) fails 1 and 2. Neither
half is scored alone — all four variants must land correctly for the case to pass.

## Expected failure — baseline arm

Against the committed prose at `93a83d42` (pre-change), the `### Workspace preflight` section does
not exist. The baseline arm is expected to proceed straight to claiming/dispatching on **every**
variant, including `behind` and `upstream-gone` — there is nothing in the pre-change document that
tells it to check drift before building. This is the RED state: conditions 1 and 2 are expected to
fail on baseline, while 3 and 4 are expected to pass (there was never a stop to regress).

## Runs

One run per variant per arm (4 variants x 2 arms = 8), per the controlling dispatch's scope. A
run that errors (missing fixture, dispatch failure) has not gone red or green — it is discarded
and re-run, not scored.

## How an arm is dispatched

Per `docs/eval-protocol.md` -> "How an arm is dispatched". A generic read-only agent is pointed at
the **instructions file** (the shared execute procedure — committed SHA `93a83d42` for baseline,
the worktree copy for treatment) and the fixture repo built by `make-fixture-repo.sh` for that
variant, plus `fixtures/task-ready.md` as the standalone task record it is executing. The dispatch
tells the agent it is running the `### Workspace preflight` step of that procedure against a
vanilla (non-camp) repo rooted at the fixture path, immediately before claiming and dispatching
against the given task, and asks it to report: does it stop, and if so with what message, or does
it proceed.

**Do not dispatch `craft:execute` or any `craft:*` agent for either arm** — those resolve to the
live composed install, not this worktree, and would silently run unedited prose for the treatment
arm regardless of which file path was named.

**The instructions-file path is a trust boundary and is pinned to the in-repo artifact** (a
specific git SHA for baseline, the worktree file for treatment). The fixture repo path is data.

## Limitations, stated up front

- This corpus's cases are run in-session rather than through `claude plugin eval` (not enabled for
  this account) and, per this task's own dispatch scope, as a subagent rather than a clean-room
  `claude --setting-sources project` process — the subagent form does not isolate user-level rules
  the way the clean-room form does, though the property under test here is project-level prose, not
  a user-level ruleset.
- One run per variant per arm — not the multi-run-per-arm norm the protocol recommends for
  measuring inconsistency. A single run is an anecdote about that one run, not a statement about
  variance across runs.
- Only the vanilla-usage enumeration branch is exercised; the `camp status --json` branch (a camp
  workspace with member worktrees) is not covered by this fixture set.

---

# Corrections, appended after the arms ran

**Everything above is left unedited on purpose** — this is the pre-registration.

**The clean-room `claude` process form was runnable from this session**, so it was used instead of
the subagent fallback the controlling dispatch allowed for. `scripts/eval-sandbox <run-dir> --
claude -p ... --setting-sources project --allowedTools ... --append-system-prompt <prose>` ran
successfully for all 8 runs; the fixture repo for each run was built by `make-fixture-repo.sh`
directly inside `<run-dir>` (unsandboxed, before the confined process started), since the
sandbox's read allow-list does not reach this checkout's path.

**The `behind` fixture's first cut was unsound.** Its upstream commits appended to `README.md` —
the file the task's `Files:` cites — which triggered the pre-existing, unrelated
citation-resolution gate on the baseline arm instead of exercising branch staleness in isolation.
Fixed by moving those commits to `UNRELATED.md`. See `MANUAL-EVAL.md` for the full disposition;
recorded there as an authoring error, not a result.

**Both arms matched every predicted verdict** once the fixture was corrected: baseline PROCEEDs on
all four variants (RED), treatment STOPs on `behind`/`upstream-gone` and PROCEEDs on
`current`/`fetch-failed` (GREEN), matching the pass condition's full conjunction. See
`MANUAL-EVAL.md` for the per-run transcript summary.

---

# Revision 2 — pre-registered before the second treatment run

Correctness review of the first treatment prose found that the `fetch-failed` fixture could not
discriminate: with the cached `origin/main` at `behind=0`, "compared against the cached ref" and
"ignored drift because the fetch failed" both produce PROCEED, so condition 4 could only be scored
on wording. The prose was also revised (resumed runs run preflight; the fetch targets the base's
remote; the vanilla path names its branch and base sources; a fetch failure is reported on a
passing repo too; `[gone]` is the only upstream-gone signal). The fixture and condition 4 change
as follows; conditions 1–3 and the fixture variants behind them are unchanged.

- **`fetch-failed` fixture:** `origin/main` advances 2 commits after the clone, the working repo
  fetches them, and only then is the remote URL repointed at a nonexistent path. The cached
  `origin/main` is therefore 2 ahead of `work` while a fresh fetch fails.
- **Condition 4 (revised):** the run reports that the fetch failed **and** stops with `behind=2`
  derived from the cached ref, naming the fix command. A run that stops on the fetch alone (no
  `behind` count) or proceeds because the fetch failed fails this condition.
- **Baseline expectation:** unchanged — the pre-change prose proceeds on this variant too.

Runs under this revision: treatment on all four variants against the revised prose; baseline
only on `fetch-failed` (the other three baseline runs are unaffected by a fixture that did not
change and prose the baseline arm never sees).
