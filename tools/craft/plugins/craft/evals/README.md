# craft eval corpus

Behavioural eval cases for craft's own prose — the spec gauntlet, the council
lenses, the execute loop, and the maturity/citation machinery.

Each runner case is a directory here, run by `claude plugin eval`. The case shape, how to
write and run one, and the sandbox are in `docs/eval-protocol.md` — read it before adding
a case. Results, with per-case evidence and limitations, go in `tools/craft/MANUAL-EVAL.md`.

Eighteen cases here have no `case.yaml`: they are legacy hand-shaped cases. The runner
does not run them and the push gate does not gate them. They are listed in
`_LEGACY_HAND_SHAPED` in `trailhead/tests/test_eval_corpus.py`, which is also where a
case is removed once it is ported to a runner shape.

## Runner cases

Three cases are run by `claude plugin eval`:
`consult-lists-a-sibling-correction-without-writing-it`,
`accepted-review-correction-is-applied-not-parked` and
`declined-review-correction-is-left-alone`. The case format, the runner's flags and the
live-state snapshot rule are in `docs/eval-protocol.md`, and the graders and lore seeding
in `tools/lore/plugins/lore/evals/README.md`; this section covers what a craft case needs
on top.

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

A craft-plugin child's sandbox exposes only the craft plugin, so the lore template's
`./lore` wrapper, which execs the lore plugin by absolute path, fails with "No such
file or directory". `fixtures/scaffold.sh` therefore runs `seed.sh` (the vault and a
wrapper), copies `tools/lore/plugins/lore/{cli,lore}` into `./lore-plugin/`, rewrites
`./lore` to exec `./lore-plugin/cli/lore`, and writes a `CLAUDE.md` with
`## Project Maturity` / `early` so `/craft:consult` finds a maturity at its
calibration step. It finds the repo seven directory levels above its own `fixtures/`
directory, as the template does. `./lore record show`, `record amend` and
`record create` all write and read in a child.

### Running a batch

The batch command is the one in `docs/eval-protocol.md`'s step-by-step path, which is
already a craft command: set `--case` to the case, `--runs 3`, and `--max-cost-usd` from
the costs below.

- The command's three grants beyond `./lore` (`maturity_bars.py`, `:`) are for
  consult's calibration step; without them it refuses and never dispatches the
  council.
- A consult run takes about 2 minutes and about 0.75 USD (its limits are
  `allowed_tools: [Skill, Agent, Read, Glob, Grep]`, `max_turns: 30`,
  `timeout_seconds: 420`). A review-case run takes about 0.2 to 0.35 USD. The
  runner's reported `turns` is wrong when subagents run asynchronously, so size
  limits from duration and cost.

### Graders

- A seeded record is graded by `regex` over an exact `vault/<kind>/<slug>.md` or
  `.json` path (no glob); `file_exists` with `exists: false` on `vault/task/*.md`
  grades "no task created".
- The `change-history` key of the record's `.json` sidecar marks an amend; each
  entry's `"initiated-by"` is what `--initiated-by` recorded.
- The runner deletes its trace after each run. A check on the council's deliverable
  is an `llm` grader with `focus: trace`, which sees the first and last 12 messages
  of the session, including the final message.

### The gate mapping

`eval-gate.json` here maps each runner case to the plugin prose it measures, so
`portage eval-gate plan` can select the cases a branch touches. Each case lists
`measures`: paths relative to the plugin root (the directory holding `evals/`), taken
from the prose its `expected.md` names under "Under test". The two review-correction
cases measure `skills/receiving-code-review/SKILL.md`; the consult case measures
`skills/consult/SKILL.md` and `skills/_shared/council.md`. A case with no entry is a
failure: add its entry when you add the case.

The `runner` block holds the grants of that batch command, the model
`claude-opus-5-5`, and `max_cost_usd` 3.5 per case. The cap is sized from the logged
cost: the consult case is the dearest at about 0.75 to 0.93 USD a run, 2.4 to 2.5 USD for
three runs (`tools/craft/MANUAL-EVAL.md`), and the review-correction cases cost 0.7 to
1.1 USD for three runs, so 3.5 leaves about 40 percent headroom over the largest batch.
