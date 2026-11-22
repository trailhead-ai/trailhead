# The eval gate

Before `updater` pushes, portage runs the eval cases that measure the prose the branch changed. A case
that scores below its bar stops the push. A gate that cannot run lets the push through and says so in the
pull request. The decisions live in code (`portage eval-gate`); `agents/updater.md` only calls it and obeys
the verdict.

## Trust model

The gate runs the branch's own eval cases with the runner's `--scaffold` and `--trust-plugin` flags. Scaffold
scripts, and repo scripts they call such as `tools/lore/scripts/seed-eval-vault`, therefore run as the operator
with their real home directory. The gate is a regression detector for the operator's own branches, not a
control against a hostile branch: only push branches you would run yourself. A branch can also make the gate
not run (delete a case, break a scaffold); the PR section shows that skip, and nothing prevents it. The
environment allowlist below keeps environment-borne keys and tokens out of the runner; it is billing hygiene,
not isolation. A corpus that is new on the branch is not run until its grants are reviewed
(`corpus-new-on-branch`).

Merge-tier runs (see "Before merge") execute the branch's scaffolds the same way, as the operator and
unattended, on whatever commits the PR holds when the gate runs. `portage merge` pins the merge to the head
commit the gate ran on (`gh pr merge --match-head-commit`), so a push after the gate makes `gh` refuse
instead of merging content nobody evaluated. That limits what the gate ran to the commits present when it
ran; it does not make a hostile branch safe to gate.

The pass cache and the state directory are not integrity boundaries against the code under test. Scaffolds
run as the operator, so they can write the cache entries and the state directory's results, plan and canary
files just as the operator can. The cache lives outside any one repo and persists across branches. A cache hit
is reused only for identical committed content: the key covers the case's own files, its measured files, the
corpus scaffolds and the runner block, and `run` refuses a plan whose repo is no longer at the planned head.

## What it runs

`portage eval-gate plan --repo <path> [--base <ref>] [--tier push|merge]` diffs the branch against its base
(default: the target of `origin/HEAD`) and selects every case that either measures a changed file or lives in
a changed case directory. It writes the selection to a fresh state directory and prints it. The tier decides
which of those cases run: `push` (the default, and what the updater calls) leaves out cases marked
`merge_only`; `merge` selects every touched case. `plan.json` records the `tier`, the `head` commit at plan
time, and how many touched `merge_only` cases a push left out (`merge_only_pending`); `total_max_cost_usd`
counts only the cases the tier runs.

Each corpus (`tools/<tool>/plugins/<tool>/evals/`) carries an `eval-gate.json`:

```json
{"schema_version": 1,
 "runner": {"model": "claude-opus-5-5", "push_model": "claude-sonnet-5-5",
            "allow_tools": ["Bash(./lore *)"], "max_cost_usd": 1.5},
 "cases": {"<case>": {"measures": ["skills/correct/SKILL.md"], "runs": 3, "pass_at": 2, "merge_only": false,
                      "needs_merge_model": "judges a long diff the smaller model truncates"}}}
```

- `measures` are plugin-relative paths of the prose the case's `expected.md` names as under test.
- `runs` defaults to 3 and `pass_at` to 2; any other `runs` must declare its own `pass_at`.
- `merge_only` is optional and defaults to false; it must be a boolean. A `merge_only` case is left out of the
  push tier and runs at the merge tier. For a corpus that already has a mapping on the base, the base's value
  is used, as for `runs` and `pass_at`.
- `needs_merge_model` is optional: a reason, 1-200 characters on one line, that marks a case the push tier
  cannot trust to `push_model` and so runs on `model` at both tiers. It starts with a letter or digit and
  otherwise uses letters, digits, space and `.,;'()_-` (no `:`, `/` or `@`, and no `www.`), so it cannot form a scheme, path, address or `www.` link; GitHub's same-repo references such as `GH-123` or a commit SHA still link. It is read from the base mapping, so a
  mark takes effect once it is merged. Agents never add one: when a case fails on the push model, ask the
  operator whether to mark it.
- `runner.push_model` is optional and has the same model-id shape as `model`. The push tier runs every case on
  it, except the marked ones; absent, the push tier uses `model`. The merge tier always uses `model`. It is
  read from the base mapping like the rest of the runner block.
- `runner` holds the grants and model that case runs under. For a corpus that already has a mapping on the
  base, the base's runner block is used, so a branch cannot widen its own grants. A corpus new on the branch
  runs under its own block and the section flags it.

portage's own corpus maps its two updater cases to `agents/updater.md`, so a change to the updater runs the
cases that check the updater obeys the gate.

## Tiers

The gate has two tiers, chosen by how often a case should pay for itself:

- **Core** cases (no `merge_only`) run on every push that touches what they measure.
- **`merge_only`** cases run once, before merge, over everything the change touches. A push leaves them out
  and the PR section counts them as pending; `portage merge` refuses a change that touches one without a
  current merge-tier verdict.

A file that many cases measure keeps one detector per behaviour it decides as core and marks the rest
`merge_only`. A `merge_only` case's declared `timeout_seconds` fits the gate budget (540 s); a case that
declares more stays core, because it could never finish at merge and would always block. A case whose runs
take more than about half the budget can still go not-run at merge when its first two runs split, because the
call that decides it then has less time left than the first call took (`timeout`); re-running the merge-tier
gate is the remedy. Every prose file some case measures keeps at least one core case, so a
push that edits it always runs something; the corpus test enforces this from each mapping. Passes are cached
by content (see "Cost and limits"), so a case already passed on the same content costs nothing at either tier.

## Running a case

`portage eval-gate run --state <dir> --case <name>` runs one planned case in the foreground (up to 540 s per
call, counting every batch, so give the tool call a 600000 ms timeout) and records a verdict in the state directory:

- **pass**: at least `pass_at` runs scored 1.0.
- **fail**: so many scored runs failed that `pass_at` is out of reach.
- **not-run**, with a reason: the runner errored, timed out, hit its cost cap, was missing, or cannot write
  (below), or the branch removes the case (`case-removed-on-branch`: the case was selected but its
  `case.yaml` is gone, so nothing ran), or its corpus is new on the branch (`corpus-new-on-branch`: this
  corpus is new on the branch, so its runner grants are unreviewed and its cases were not run — review the
  grants), or the repo's `HEAD` is no longer the plan's `head`, or a tracked file differs from it
  (`head-moved`: a result describes the head the plan was made at, so `run` checks before it looks anything
  up or starts anything; plan again at the current head). A not-run case is never a pass and never a fail.

A case is decided in batches, not in one call for all its `runs`. The first call asks for the fewest runs
that could decide it, `min(pass_at, runs - pass_at + 1)`: two for the default three runs, two to pass. When
those agree the case is decided from them. When they split, the next call asks for the smallest batch that
could still decide it, up to `runs` in all. The verdict rule is the same as if every run had been taken.
Later calls get what is left of the case's `max_cost_usd` (as a hard `--max-cost-usd`) and of the 540 s
budget. A later call does not start, and the case is not-run, when:

- the last call added no scored run (`runs-errored`);
- the cost reported so far has used the cap, or the runner reported no valid `costUsd` (`cost-cap`);
- less time is left than the first call took (`timeout`).

The `runs` in the result hold every call's runs, in order.

The runner is the harness's `claude plugin eval`, started with an allowlist environment (`PATH`, `HOME`,
`USER`, `LANG`, `TERM`, `LC_*`, plus the gate's own `TMPDIR`), so API keys and tokens in the caller's
environment are not passed on. With
that environment a runner authenticated on the operator's Claude Code login on a Linux host (shown there only;
macOS was not checked). `HOME` is forwarded, so settings in the user's Claude configuration (an `env` block or an
`apiKeyHelper`) can still change which credential a run uses and could move it onto API billing.

Every case a runner was started for also appends one JSON line to `history.jsonl` in the `eval-gate` directory
of portage's cache directory (the pass cache's directory; per host, never synced): the case, its plugin, the
pass-cache fingerprint (null when uncacheable), the runner's version, whether `PORTAGE_EVAL_RUNNER` pinned the
runner, the tier, the verdict and reason, the call's timeout, the case's wall time without a fresh canary's, and
each run's pass, error, duration and cost figures. A `timeout` is written with the runs that came back. A cache
hit, and a not-run decided before any runner started (`case-removed-on-branch`, `corpus-new-on-branch`,
`head-moved`, a harness with no eval runner, a failed canary) write nothing. A `runner-missing` decision writes
no line either, including one where a later batch fails to launch: the eval core's not-run result already drops the
earlier batches' runs, so there is nothing to record. A file over 5 MB is renamed to
`history.1.jsonl` before the next line, so two generations are kept. A history that cannot be written prints one
`eval-gate: timing history not written: <reason>` line on stderr and changes no verdict, exit code or result
file. The file's shape is in the `eval_gate/history.py` docstring.

Nothing may leave a case running after the call returns: no `nohup`, no trailing `&`, no runner script, no
`run_in_background`.

## The canary and `PORTAGE_EVAL_RUNNER`

Before the first case in a state directory, `run` starts the sandbox-write canary
(`plugins/portage/eval-canary/sandbox-write`): one cheap run (`claude-haiku-5-5`, 4 turns) whose agent
creates a file through its sandboxed Bash. If the runner's sandbox cannot write, the grader fails and the
case is recorded as not-run `runner-sandbox-unwritable` instead of a false fail. The canary result is reused
for the rest of that state directory.

The gate places the runner's temp directory under the user cache, because the runner's sandbox could not
write when its temp dir was under `/tmp` on the tested Linux host (where `/tmp` is a separate mount; measured
2026-10-22, macOS not checked). On runner 2.1.308 there, a temp path outside `/tmp` worked at 51 characters or
fewer and failed at 52 or more; `TMPDIR=/tmp` itself failed. That is an empirical limit of that runner, not a
documented one. Each runner call, the canary and the case, gets a private directory
`$XDG_CACHE_HOME/pegt/<8 characters>` (`$HOME/.cache/pegt/...` when `XDG_CACHE_HOME` is unset or not absolute)
as its `TMPDIR`, removed when the call ends. A per-call path over 51 characters is recorded not-run
`runner-sandbox-unwritable` before any runner starts, canary included, and a stderr line names the length;
set a shorter `XDG_CACHE_HOME`. A base directory that is a symlink, owned by another user or group- or
world-writable makes the case not-run `runner-error` and no runner starts. A `TMPDIR` in the caller's
environment is never passed on. The canary still catches a runner that cannot write on another host or
version.

`PORTAGE_EVAL_RUNNER` pins the runner binary for the gate on one machine: an absolute path to an executable
file, used for both the canary and the case in place of the harness's own `claude` lookup. A value that is
not an absolute path to an executable makes the case not-run `runner-missing`. The gate reads it from its own
environment and never passes it on to the runner.

```sh
PORTAGE_EVAL_RUNNER=/absolute/path/to/a/claude/build portage eval-gate run --state <dir> --case <name>
```

## The verdict and the PR section

`portage eval-gate verdict --state <dir>` exits 0 (push) or 1 (stop), and 2 on a fault. Its stdout opens
with `eval-gate: <outcome>` and `persistent: <codes>|none`, and ends with the PR section between
`<!-- portage:eval-gate:start -->` and `<!-- portage:eval-gate:end -->`. The section is also saved to
`<state>/eval-gate-section.md`. The section names the tier (`Tier: push.`); on a push it adds a line counting
the touched merge-only cases that will run before merge, so a reviewer knows they are pending, not skipped.

After the `ignored:` lines (when any) come the marks, ``needs-merge-model: `<plugin_root>/<case>` — "<reason>"`` for each case
the base marks (or `needs-merge-model: none`), then ``needs-merge-model changed: `<plugin_root>/<case>`
(added|altered|removed)`` for each mark the branch adds, alters or removes (an added or altered one says it
takes effect once merged); the target is in a code span so a case or corpus name cannot autolink. The same lines end the section. A failing case's counts line ends with the model it
ran on; when a push-tier case failed on a `push_model` that differs from `model`, the output also asks the
operator whether to add a mark.

- Exit 1: a case failed, the mapping is broken, or the working tree has uncommitted tracked changes
  (`eval-gate: tree-dirty`; nothing is pushed, commit or stash and push again), or the base starts with `-`
  (`eval-gate: base-invalid`: the base must not start with `-`). A corpus new on the branch
  is recorded not-run `corpus-new-on-branch`, which is not persistent and does not stop the push.
  After a failed case or a broken mapping the state directory is kept, with the per-run grader
  verdicts, and the output lists the ways forward (fix the prose or the case, or re-run; there is no
  override).
- Exit 0 with a not-run case: the push goes ahead and the section lists each case with its reason.
- `persistent:` names the reasons nothing about the branch will change (the same machine will skip on every
  push), and `base-unresolved` when the base ref cannot be resolved. `updater` marks them on the `**Eval gate:**` line that leads its report, and `/portage:pull_request`
  relays that line first.

`portage eval-gate verdict --gate-error <step>` renders the section for a gate command that crashed;
`updater` then pushes anyway. `portage eval-gate splice --body-file <f> (--state <dir> | --section-file <f>)
[--out <path>]` puts the section into an existing PR body, replacing an earlier one.

## Before merge

Cases mapped `merge_only` do not run on push; they run at the merge tier, and `portage merge` will not merge
a change that touches one, or a split-procedure case, without a current verdict.

1. `portage eval-gate plan --repo <repo> --tier merge` selects every touched case, `merge_only` included, and
   records the tier, the repo and `HEAD` in `plan.json`.
2. `portage eval-gate run --state <dir> --case <name>` once per planned case.
3. `portage eval-gate verdict --state <dir>` exits 0 or 1. At the merge tier a not-run `merge_only` case
   stops the change (exit 1, reason `merge-only-not-run`), and a not-run split-procedure case stops it the
   same way (reason `split-procedure-not-run`; a case whose corpus is new on the branch reports
   `corpus-new-on-branch`, and the remedy is to land the corpus mapping first, then re-plan), and the verdict names each case's not-run reason
   and its recovery: `timeout` (the runs split and the budget ran out) means re-run the merge-tier gate,
   `cost-cap` (the case hit its own cost ceiling) means raise its `max_cost_usd` or merge by hand,
   `runner-error` and `runs-errored` mean re-run after the usage limit resets, or merge by hand, `head-moved`
   means re-plan at the current head, any other reason means merge by hand. A `merge_only` case
   that can never run, because the branch removes it (`case-removed-on-branch`) or its corpus is new on the
   branch (`corpus-new-on-branch`), is reported as on push and does not stop the change. A not-run core case
   still lets the change through, as on push.
4. `portage merge --manifest <m> [--toml <t>] --eval-state <repo>=<dir> <repo>:<pr>:<member>` (repeatable,
   one per repo).

The check applies to a repo only when its change touches a `merge_only` or split-procedure case, decided by the selection
`plan` uses. A case counts as `merge_only` when the merge-base's mapping or HEAD's committed mapping marks it,
so a stale `origin/HEAD` cannot hide a flag a newer base carries. Only commits are read: the mappings, the
measured paths and the `case.yaml` files come from the merge-base and HEAD, and an uncommitted edit changes
no answer. Where the base does not resolve or a mapping is invalid the answer is unknown, so the check
applies. Where it does not apply and the repo holds no `eval-gate.json` mapping (at HEAD or the merge-base),
`portage merge` behaves as without the flag (a given `--eval-state` is not read, no head is read, and the
merge is not pinned). Where it does not apply but the repo holds any mapping, the PR's remote head is read:
a head that is not this checkout's `HEAD` is refused with exit 2 as `local-head-stale` (pull the PR head and
retry), because a commit that landed only on the remote was never checked for what it touches or what it
marks `merge_only`; a head that matches pins the merge to it; an unreadable head is refused. A git or OS
failure while answering is refused as `selection-failed`; only git's own "not a git repository" makes the
check not apply.

Where it applies, the merge is refused with exit 2, before any provider merge and before the `auto_merge`
refusal, when any of these holds: no state was given; the state directory is not a real, caller-owned 0700
directory; `plan.json` is unusable; the plan's tier is not `merge`; its `repo` is another repo; its status is
not `cases-selected`; its `head` is not a full lowercase hex commit id; it lacks a merge-only or split-procedure case the change
touches; the PR's current remote head cannot be read or differs from the plan's `head` (a push landed after
the gate); or `verdict` stops the change. `--operator-directed` waives none of this. On success the merge is
pinned to the verified head. When the refusal comes from a plan or repo whose base did not resolve, the
remedy names `portage eval-gate plan --repo <repo> --tier merge --base <ref>`.

A refusal prints, on stderr, `portage merge: eval-gate check failed for <repo>: <check> — <detail>`, where
`<check>` is one of `no-state`, `state-unusable`, `plan-unusable`, `state-tier-not-merge`,
`state-for-another-repo`, `plan-not-selected`, `plan-head-malformed`, `plan-misses-merge-only-case`, `plan-misses-split-procedure-case`,
`head-unreadable`, `head-mismatch`, `verdict-stops-the-change`, `selection-failed` or `local-head-stale`,
followed by the `plan` / `run` / `verdict` commands that produce a valid state (a `local-head-stale` refusal
instead says to pull the PR head and retry). After a `head-mismatch`, plan, run and verify again on the new
head.

## Reading the timing history

`portage eval-gate timings --repo <path> [--plugin <name>]` reads both history generations and the repository's
`eval-gate.json` mappings and prints, per measured case, what the gate itself measured. It writes nothing and
exits 0, or 2 with a `portage:` message on a usage error or an unreadable mapping.

A history line counts only when its `plugin` and `case` have the mapping's case-name shape and name a mapped case,
its `fingerprint` equals the case's current pass-cache key for the line's own tier (either tier's key when the line
records none), computed from the repository, and `pinned_runner` is not true. Once a corpus sets `push_model`, one row
mixes push-model and merge-model runs, so its median, batch-max and mean cost blend both models. The closing line tallies the others (`corrupt`, `mis-shaped`, `stale`, `pinned`); nothing from them is printed.
A history with no lines prints that no runs are recorded yet.

The output is a provenance line (the history path, the dates of the counted lines, the runner versions seen), a
header row and one row per measured case, flagged cases first, then by plugin and case, and a closing line naming
the measured cases with fewer than two decisions and the mapped cases with no counted lines. A row gives the tier
(`core` or `merge-only`), the declared `timeout_seconds`, the decisions and runs counted, the decisions that ended
`timeout` and the scored runs with no duration, the median and maximum duration of scored runs that have one, the
largest single-batch elapsed time, and the mean cost per decision (run plus judge cost).

"Single-batch" is worked out from the line: a decision holding at least one run and no more runs than the gate's first batch,
`min(pass_at, runs - pass_at + 1)` for the mapped `runs` and `pass_at`, took one batch, so its `elapsed_seconds` is
that batch's time. A multi-batch decision's elapsed time is not a batch time and is left out.

The budget left is `MAX_TIMEOUT_SECONDS` minus the canary's timeout (420 s). Flags:

- `timed-out`: a decision ended `timeout`, or a scored run has no duration. The case counts as at least the full budget
  (shown `>=540`, `MAX_TIMEOUT_SECONDS`) and also takes the next two flags.
- `cannot-finish-a-split`: a single-batch decision took longer than half the budget left, the gate's own test of
  the first call's elapsed time against the budget left.
- `over-merge-margin`: a `merge-only` case whose largest single-batch elapsed time is over the budget left.

## Pricing a one-file edit

`portage eval-gate cost --repo <path> --plugin <name> [--runs-from <dir>]...` prices, for each procedure file (a
regular `.md` file under the plugin's `skills/` that at least one case selects), the cases a one-file edit to it selects
at the push and merge tiers (the gate's own selection, HEAD as the base; push leaves out `merge_only` cases). Each
case is priced at the median recorded cost per run (run plus judge cost) at each tier on the model that tier runs (push on `push_model`, or on `model` for a case the base marks `needs_merge_model`; merge on `model`), from the
timing history and from every `*.json` runner output in each `--runs-from` directory and in each of its immediate
subdirectories (so a request directory's `<case>/batch-2.json` and `<case>/aggregate-result.json` are read without being
named; a symlinked subdirectory is not followed and nothing deeper is read; a directory's own files are read once however it was reached, and a `--runs-from` directory always has its subdirectories read, so the order of the `--runs-from` directories does not change the report). That once-only check keys on device and inode numbers, so on a filesystem that reports non-unique or zero inode numbers (some FUSE and network mounts) distinct directories can be taken for one and the files of all but the first are dropped with no tally. A `--runs-from` that is itself a symlink is refused as not a directory (`portage: eval-gate cost: runs-from unreadable: <path> is not a directory`). A run repeated across those files is
counted once; a repeat with no valid `startedAt` is not deduplicated. Every runner output is read before any
history line, and a counted history run is then matched one-to-one against the counted runner-output runs of the same
plugin, case and model with exactly the same per-run cost (run plus judge); a match is excluded as `duplicate-history`,
so a gate run that is in both sources is priced once. Matching consumes runner-output runs only, so two identical
history runs need a counterpart each, and an excluded run (history `other-plugin` or `pinned`; runner-output
`ablation`, `multi-plugin`, `other-plugin` or `run-cap`) neither matches nor is matched. A history run with no
counterpart is counted. The rule accepts one false merge: two distinct runs of one case on one model with bit-identical
cost, one in each source, count once. A file's total is its
push-tier prices plus its merge-tier prices; the baseline is the median of the file totals, or
`baseline: incomplete (<n> cases unpriced); lower bound <median>` while a selected case has no run. The report opens
with the baseline, the mean and the unpriced count, then the counts of files and runs read, skipped and excluded
by reason, a row per file, the unpriced and priced cases (a price from one or two runs is flagged `thin`) and the
files no case selects; its last line is the `baseline:` line. The runner-output counts end with `unlistable-directory <n>` and `too-many-runs <n>` among the skip reasons (a subdirectory that could not be
opened or listed; a `--runs-from` that cannot be read is still exit 2) and then `listings-truncated <n>`.
A `history lines: read <n>; skipped: corrupt <n>, mis-shaped <n>` line follows (the words and order
`eval-gate timings` uses for the same file): `corrupt` is a line that is not a JSON object, `mis-shaped` any other unusable line (a field, `plugin` or `case` outside its shape); the two commands classify a line with the same function.
Three bounds cover the whole call, not each directory: 64 MiB of bytes read (every byte read from a runner output,
parsed or not; a file past it is `directory-bound`), 20000 listing entries (every entry listed in any listing, files,
subdirectories and other names alike; a listing cut short is `listings-truncated`), and 100000 retained runs (a run
past it is excluded as `run-cap`). When any of `directory-bound`, `listings-truncated`, `unlistable-directory` or
`run-cap`, `too-large` (a file over 1 MiB) or `too-many-runs` (a file with more than 1000 runs, which has its own
runner-output skip reason; `bad-shape` stays a malformed document) is nonzero, or any history line was skipped, both
`baseline:` lines end with `(partial: <name> <n>, ...)` after the figure, naming only the nonzero ones in that order,
the history skips last as `history-corrupt` and `history-mis-shaped`. The malformed-file reasons (`not-json`,
`too-deep`, `bad-shape`, `bad-number`, `not-regular`) stay out of the suffix because they are bad input, not a bound.
The byte bound is checked against a file's size before the file is read, so a call can exceed it by at most one file (`MAX_FILE_BYTES` + 1). The bounds are fixed constants in the reader (`MAX_FILE_BYTES`, `MAX_RUNS_PER_FILE`, `MAX_TOTAL_BYTES`,
`MAX_TOTAL_ENTRIES`, `MAX_RETAINED_RUNS`) and the command has no flag for them, so to get a full figure narrow
`--runs-from` to the directories that matter. A partial naming `history-corrupt` or `history-mis-shaped` means the
history file holds lines the reader cannot use; they are skipped, not repaired, so inspect the history file. It writes nothing. Exit 0 on a report, 2 with a
`portage: eval-gate cost: <source> unreadable` line when the repo, mapping, a run directory or the history cannot be read.

## Cost and limits

`plan` prints the number of selected cases and the sum of their `max_cost_usd` caps, which is a ceiling. One
lore case at 3 runs measured about 0.61 USD on 2026-10-21 (`MANUAL-EVAL.md`, "Live gate check").

Cases that measure user rulesets or `CLAUDE.md` cannot be gated, because the runner loads only the plugin
under test.

A pass is reused. Before the canary and the runner, `run` looks the case up in a per-machine cache at
`<cache dir>/portage/eval-gate/<fingerprint>.json` (`$XDG_CACHE_HOME`, else `~/.cache`). A hit records
`pass` with `cached: true`, starts nothing and costs nothing, and the section lists the case as
`pass (cached)`. Only a fresh pass is stored; a fail or a not-run never is, so the next push runs the case
again. The fingerprint is a SHA-256 over:

- every file in the case directory, with its executable bit;
- every file of the case's `measures`, with its executable bit;
- every file directly under the corpus `evals/` directory except `eval-gate.json` (the shared scaffolds),
  with its executable bit;
- the case's runner block, `runs`, `pass_at`, plugin root and name.

A symlink anywhere among those inputs makes the case uncacheable: it is never looked up and never stored.
With `PORTAGE_EVAL_RUNNER` set the cache is neither read nor written, because the key does not cover which
runner produced a pass. A fresh pass is stored only when the fingerprint, recomputed after the run, equals
the one looked up before it, so inputs that changed while the case ran are never stored.

The key is content alone, so a pass carries over between branches and worktrees. It has known limits, and a
change to any of them does not change it: a script a scaffold calls from outside `evals/`, other plugin
prose the case does not list in `measures`, the `claude` CLI version, and a model alias the runner
re-targets. Delete `<cache dir>/portage/eval-gate/` to force a re-run; there is no flag. A cache directory
that is a symlink, owned by another user or group- or world-writable is not used, and an unreadable or
corrupt entry is a miss, so the case just runs.
