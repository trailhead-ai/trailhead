# craft eval corpus

Behavioural eval cases for craft's own prose — the spec gauntlet, the council lenses, the execute
loop, and the maturity/citation machinery.

Each runner case is a directory here, run by `claude plugin eval`. The case shape, how to write and
run one, and the sandbox are in `docs/eval-protocol.md` — read it before adding a case. Results,
with per-case evidence and limitations, go in `tools/craft/MANUAL-EVAL.md`.

## Runner cases

Every directory here with a `case.yaml` is run by `claude plugin eval`, and `eval-gate.json` lists
each with the prose it measures. The case format, the runner's flags and the live-state snapshot
rule are in `docs/eval-protocol.md`, and the graders and lore seeding in
`tools/lore/plugins/lore/evals/README.md`; this section covers what a craft case needs on top.

```
<case-name>/
    case.yaml        schema_version "1.1", name, runs, context: {scaffold_script: fixtures/scaffold.sh}
    prompt.md        frontmatter allowed_tools, max_turns, timeout_seconds; then the task
    graders/         one file per grader
    expected.md      the pass condition, conditions, sample and bar, committed alone before any run
    fixtures/
        seed.sh          the lore template (tools/lore/plugins/lore/evals/scaffold.sh), unchanged
        scaffold.sh      runs seed.sh, then makes ./lore self-contained (below)
        records.json     the records to seed
        review.md        (review cases) the finished review the prompt points at
```

### The scaffold

A craft-plugin child's sandbox exposes only the craft plugin, so the lore template's `./lore`
wrapper, which execs the lore plugin by absolute path, fails with "No such file or directory".
`fixtures/scaffold.sh` therefore runs `seed.sh` (the vault and a wrapper), copies
`tools/lore/plugins/lore/{cli,lore}` into `./lore-plugin/`, rewrites `./lore` to exec
`./lore-plugin/cli/lore`, and writes a `CLAUDE.md` with `## Project Maturity` / `early` so
`/craft:consult` finds a maturity at its calibration step. It finds the repo seven directory levels
above its own `fixtures/` directory, as the template does. `./lore record show`, `record amend` and
`record create` all write and read in a child.

### Running a batch

The batch command is the one in `docs/eval-protocol.md`'s step-by-step path, which is already a
craft command: set `--case` to the case, `--runs 3`, and `--max-cost-usd` from the costs below.

- The command's three grants beyond `./lore` (`maturity_bars.py`, `:`) are for consult's calibration
  step; without them it refuses and never dispatches the council.
- The four grants after those (`ledger_gate.py`, `candidate_set.py`, `mktemp`, `mktemp *`) are for
  the slice ritual's spec-complete path, which pipes the ledger and candidate scripts and writes a
  temp file. The two script grants lead with `*`, and a Bash rule's `*` matches any text, spaces
  included, so they allow any single command whose text contains
  `<anything>/scripts/ledger_gate.py <anything>` (or the `candidate_set.py` equivalent), whatever
  program runs it, not only the script itself. The existing `maturity_bars.py` grant has the same
  shape. A compound line is split on `&&`, `||`, `;`, `|`, `&` and newlines and every part must be
  allowed, so prefixing another command does not ride on these grants. `mktemp` and `mktemp *` are
  command grants: the runner's TMPDIR lies outside the sandbox, so plain `mktemp` fails and the
  agent falls back to a templated `mktemp`, which `mktemp *` allows anywhere the sandbox can write.
  It creates files and never overwrites them. There is no `rm` grant, so the ritual's temp-file
  cleanup is denied and left to fail harmlessly.
- The two grants after those, `Bash(*/scripts/covers_gate.py *)` and `Bash(printf *)`, are for the
  slice and plan rituals: the covers gate runs on the drafted coverage list, and `printf` feeds the
  empty stdin of plan's maturity calibration. A new record's body, and a block `--diff`, do not use
  `printf`: the text is vault- or web-derived, so it is written to a temp file with the file-writing
  tool and redirected in (`./lore record create ... < <body-path>`,
  `./lore record update <record> --diff < <diff-path>`), never put on a command line. Without the
  covers-gate grant a run that chooses a slice stops before writing it. `printf *` is not "writes
  nothing": the runner does not split a command at a redirection, so `printf ... > <path>` is
  allowed by it and writes wherever the sandbox permits. The `Write` grant is unscoped by path as
  well.
- The next two grants, `Bash(*/scripts/migration_bar.py)` and `Bash(*/scripts/migration_bar.py *)`,
  are for the plan ritual's migration-suppression step, which pipes the spec body into
  `migration_bar.py` and reads the printed directive. One covers the bare call and one the call with
  arguments, as `Bash(mktemp)` and `Bash(mktemp *)` do.
- The `pending_check.py *` and `observation_gate.py` grants are for the close's manual-check
  discharge in `skills/_shared/execute.md`, run by the cases `close-defers-an-unconfirmed-check`,
  `stale-write-leaves-the-slice-open` and `one-failed-check-saves-nothing`. `pending_check.py *`
  runs the pending-check writer and its `describe` and `verify` modes; its leading `*` has the same
  shape as the script grants above, so it admits any single command whose text contains
  `<anything>/scripts/pending_check.py <anything>`. `observation_gate.py` takes no argument, and
  the grant admits exactly that. Those cases write their entries file with the file-write tool
  under the runner block's bare `Write` grant, which applies to every craft case and is not
  confined by the sandbox; a smoke run found that the runner honours a path-scoped `Edit(...)`
  rule for the file-write tool (`Edit(//private/tmp/e-*/**)` on macOS) where a path-scoped
  `Write(...)` rule is denied, so the bare grant can be narrowed to the run directory once every
  case that relies on it is checked under the narrower rule.
- The nine `git` grants are pinned to the forms the no-ask close's branch recovery and push step
  runs: `git status --porcelain`, `git rev-parse --verify --quiet refs/heads/*`, `git rev-parse
  --verify --quiet refs/remotes/origin/*`, `git fetch origin`, `git symbolic-ref --short HEAD`,
  `git switch *`, `git log origin/*`, `git log HEAD --not --remotes=origin -p` and `git push
  --set-upstream origin HEAD`. They are for cases whose run directory is a git repository with a
  bare `origin.git` beside it. The push gate runs every case under the base branch's runner block,
  so a branch cannot widen these for its own gate, and every grant reaches every craft case. The
  exact forms leave no room for `--upload-pack`, `--receive-pack`, `--force` or a ref deletion. Two
  still end in `*`, because a branch name cannot be pinned: `git switch *` admits its option forms
  (`--discard-changes`, `--detach`), and `git log origin/*` admits `--output=<path>`, which writes a
  file the sandbox allows.
- **What the grants bound.** Every grant that leads with `*` (`maturity_bars.py`, `ledger_gate.py`,
  `candidate_set.py`, `covers_gate.py`, `migration_bar.py`, `pending_check.py`) matches any single command that ends in
  or contains that script path, whatever program runs it. So the set of commands a case can run is
  bounded by the runner's sandbox, not by these patterns. The grants shape only the eval runner;
  they are never installed and change nothing for a real install. Anchoring them is a runner change,
  logged as a follow-up, and the grants stay as they are.
- A consult run takes about 2 minutes and about 0.75 USD (its limits are
  `allowed_tools: [Skill, Agent, Read, Glob, Grep]`, `max_turns: 30`, `timeout_seconds: 420`). A
  review-case run takes about 0.2 to 0.35 USD. The runner's reported `turns` is wrong when subagents
  run asynchronously, so size limits from duration and cost.

### Graders

- A seeded record is graded by `regex` over an exact `vault/<kind>/<slug>.md` or `.json` path (no
  glob); `file_exists` with `exists: false` on `vault/task/*.md` grades "no task created".
- The `change-history` key of the record's `.json` sidecar marks an amend; each entry's
  `"initiated-by"` is what `--initiated-by` recorded.
- The runner deletes its trace after each run. A check on the council's deliverable is an `llm`
  grader with `focus: trace`, which sees the first and last 12 messages of the session, including
  the final message.

### The gate mapping

`eval-gate.json` here maps each runner case to the plugin prose it measures, so
`portage eval-gate plan` can select the cases a branch touches. Each case lists `measures`: paths
relative to the plugin root (the directory holding `evals/`), taken from the prose its `expected.md`
names under "Under test". The two review-correction cases measure
`skills/receiving-code-review/SKILL.md`; the consult case measures `skills/consult/SKILL.md` and
`skills/_shared/council.md`. A case with no entry is a failure: add its entry when you add the case.

The `runner` block holds the grants of that batch command, the model `claude-opus-5-5`, and
`max_cost_usd` 3.5 per case. The cap is sized from the logged cost: the consult case is the dearest
at about 0.75 to 0.93 USD a run, 2.4 to 2.5 USD for three runs (`tools/craft/MANUAL-EVAL.md`), and
the review-correction cases cost 0.7 to 1.1 USD for three runs, so 3.5 leaves about 40 percent
headroom over the largest batch.
