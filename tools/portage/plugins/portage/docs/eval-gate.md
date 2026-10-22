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

## What it runs

`portage eval-gate plan --repo <path> [--base <ref>]` diffs the branch against its base (default: the target
of `origin/HEAD`) and selects every case that either measures a changed file or lives in a changed case
directory. It writes the selection to a fresh state directory and prints it.

Each corpus (`tools/<tool>/plugins/<tool>/evals/`) carries an `eval-gate.json`:

```json
{"schema_version": 1,
 "runner": {"model": "claude-opus-5-5", "allow_tools": ["Bash(./lore *)"], "max_cost_usd": 1.5},
 "cases": {"<case>": {"measures": ["skills/correct/SKILL.md"], "runs": 3, "pass_at": 2}}}
```

- `measures` are plugin-relative paths of the prose the case's `expected.md` names as under test.
- `runs` defaults to 3 and `pass_at` to 2; any other `runs` must declare its own `pass_at`.
- `runner` holds the grants and model that case runs under. For a corpus that already has a mapping on the
  base, the base's runner block is used, so a branch cannot widen its own grants. A corpus new on the branch
  runs under its own block and the section flags it.

portage's own corpus maps its two updater cases to `agents/updater.md`, so a change to the updater runs the
cases that check the updater obeys the gate.

## Running a case

`portage eval-gate run --state <dir> --case <name>` runs one planned case in the foreground (up to 540 s per
call, so give the tool call a 600000 ms timeout) and records a verdict in the state directory:

- **pass**: at least `pass_at` runs scored 1.0.
- **fail**: so many scored runs failed that `pass_at` is out of reach.
- **not-run**, with a reason: the runner errored, timed out, hit its cost cap, was missing, or cannot write
  (below), or the branch removes the case (`case-removed-on-branch`: the case was selected but its
  `case.yaml` is gone, so nothing ran), or its corpus is new on the branch (`corpus-new-on-branch`: this
  corpus is new on the branch, so its runner grants are unreviewed and its cases were not run — review the
  grants). A not-run case is never a pass and never a fail.

The runner is the harness's `claude plugin eval`, started with an allowlist environment (`PATH`, `HOME`,
`USER`, `LANG`, `TERM`, `LC_*`, plus the gate's own `TMPDIR`), so API keys and tokens in the caller's
environment are not passed on. With
that environment a runner authenticated on the operator's Claude Code login on a Linux host (shown there only;
macOS was not checked). `HOME` is forwarded, so settings in the user's Claude configuration (an `env` block or an
`apiKeyHelper`) can still change which credential a run uses and could move it onto API billing.

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
`<state>/eval-gate-section.md`.

- Exit 1: a case failed, the mapping is broken, or the working tree has uncommitted tracked changes
  (`eval-gate: tree-dirty`; nothing is pushed, commit or stash and push again). A corpus new on the branch
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

## Cost and limits

`plan` prints the number of selected cases and the sum of their `max_cost_usd` caps, which is a ceiling. One
lore case at 3 runs measured about 0.61 USD on 2026-10-21 (`MANUAL-EVAL.md`, "Live gate check").

Cases that measure user rulesets or `CLAUDE.md` cannot be gated, because the runner loads only the plugin
under test. Every update push re-runs the touched cases; nothing is cached between pushes.
