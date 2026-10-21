# The eval gate

Before `updater` pushes, portage runs the eval cases that measure the prose the branch changed. A case
that scores below its bar stops the push. A gate that cannot run lets the push through and says so in the
pull request. The decisions live in code (`portage eval-gate`); `agents/updater.md` only calls it and obeys
the verdict.

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
  (below). A not-run case is never a pass and never a fail.

The runner is the harness's `claude plugin eval`, started with an allowlist environment (`PATH`, `HOME`,
`USER`, `LANG`, `TERM`, `LC_*`), so API keys and tokens in the caller's environment never reach it and the
runs use the operator's Claude Code login.

Nothing may leave a case running after the call returns: no `nohup`, no trailing `&`, no runner script, no
`run_in_background`.

## The canary and `PORTAGE_EVAL_RUNNER`

Before the first case in a state directory, `run` starts the sandbox-write canary
(`plugins/portage/eval-canary/sandbox-write`): one cheap run (`claude-haiku-5-5`, 4 turns) whose agent
creates a file through its sandboxed Bash. If the runner's sandbox cannot write, the grader fails and the
case is recorded as not-run `runner-sandbox-unwritable` instead of a false fail. The canary result is reused
for the rest of that state directory.

On the Linux host this was built on (2026-10-21), runner 2.1.303 passes the canary and 2.1.306, the `claude`
on `PATH`, fails it. Another host or version may behave differently; the canary is how the gate finds out.

`PORTAGE_EVAL_RUNNER` pins the runner binary for the gate on one machine: an absolute path to an executable
file, used for both the canary and the case in place of the harness's own `claude` lookup. A value that is
not an absolute path to an executable makes the case not-run `runner-missing`. The gate reads it from its own
environment and never passes it on to the runner.

```sh
PORTAGE_EVAL_RUNNER=$HOME/.local/share/claude/versions/2.1.303 portage eval-gate run --state <dir> --case <name>
```

## The verdict and the PR section

`portage eval-gate verdict --state <dir>` exits 0 (push) or 1 (stop), and 2 on a fault. Its stdout opens
with `eval-gate: <outcome>` and `persistent: <codes>|none`, and ends with the PR section between
`<!-- portage:eval-gate:start -->` and `<!-- portage:eval-gate:end -->`. The section is also saved to
`<state>/eval-gate-section.md`.

- Exit 1: a case failed or the mapping is broken. The state directory is kept, with the per-run grader
  verdicts, and the output lists the ways forward (fix the prose or the case, or re-run; there is no
  override).
- Exit 0 with a not-run case: the push goes ahead and the section lists each case with its reason.
- `persistent:` names the reasons nothing about the branch will change (the same machine will skip on every
  push). `updater` marks them on the `**Eval gate:**` line that leads its report, and `/portage:pull_request`
  relays that line first.

`portage eval-gate verdict --gate-error <step>` renders the section for a gate command that crashed;
`updater` then pushes anyway. `portage eval-gate splice --body-file <f> (--state <dir> | --section-file <f>)
[--out <path>]` puts the section into an existing PR body, replacing an earlier one.

## Cost and limits

`plan` prints the number of selected cases and the sum of their `max_cost_usd` caps, which is a ceiling. One
lore case at 3 runs measured about 0.61 USD on 2026-10-21 (`MANUAL-EVAL.md`, "Live gate check").

Cases that measure user rulesets or `CLAUDE.md` cannot be gated, because the runner loads only the plugin
under test. Every update push re-runs the touched cases; nothing is cached between pushes.
