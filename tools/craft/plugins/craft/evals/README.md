# craft eval corpus

Behavioural eval cases for craft's own prose — the spec gauntlet, the council
lenses, the execute loop, and the maturity/citation machinery.

Each case is a directory here:

```
<case-name>/
    expected.md      the pass condition, written BEFORE any arm is run
    fixtures/        self-contained inputs the run is pointed at
    arms/            optional: edited prose copies, for a treatment arm
```

The authoring rules, the dispatch shape, and the trust boundary on the
instructions path are in `docs/eval-protocol.md` — read it before adding a case.
Results, with per-case evidence and limitations, go in `tools/craft/MANUAL-EVAL.md`.

This is the corpus the protocol was extracted from; the seven cases here are the
worked examples for the other tools.

## Runner cases

Three cases are run by `claude plugin eval` instead of by hand:
`consult-lists-a-sibling-correction-without-writing-it`,
`accepted-review-correction-is-applied-not-parked` and
`declined-review-correction-is-left-alone`. The runner skips the hand-dispatched
cases above, which have no `case.yaml`. The case format, graders, the runner's flags
and the live-state snapshot rule are in `tools/lore/plugins/lore/evals/README.md`;
this section covers what a craft case needs on top.

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

```sh
out=$(mktemp -d)
env -u LORE_CONFIG_DIR -u LORE_STATE_DIR -u XDG_CONFIG_HOME -u XDG_STATE_HOME \
  claude plugin eval tools/craft/plugins/craft --case '<case-name>' --scaffold \
  --allow-tools 'Bash(./lore *)' 'Bash(*/scripts/maturity_bars.py *)' 'Bash(:)' 'Bash(: *)' \
  --ablation none --trust-plugin --runs 3 --model claude-opus-5-5 \
  --json "$out/run.json" --max-cost-usd N --output-dir "$out" --no-publish -j 1
```

- The three extra grants (`maturity_bars.py`, `:`) are for consult's calibration
  step; without them it refuses and never dispatches the council.
- A consult run takes about 2 minutes and about 0.75 USD (its limits are
  `allowed_tools: [Skill, Agent, Read, Glob, Grep]`, `max_turns: 30`,
  `timeout_seconds: 420`). A review-case run takes about 0.2 to 0.35 USD. The
  runner's reported `turns` is wrong when subagents run asynchronously, so size
  limits from duration and cost.
- `--output-dir` and `--json` go in a fresh temp directory, which holds traces:
  delete it after copying the scores out, and never commit it.

### Graders

- A seeded record is graded by `regex` over an exact `vault/<kind>/<slug>.md` or
  `.json` path (no glob); `file_exists` with `exists: false` on `vault/task/*.md`
  grades "no task created".
- The `change-history` key of the record's `.json` sidecar marks an amend; each
  entry's `"initiated-by"` is what `--initiated-by` recorded.
- The runner deletes its trace after each run. A check on the council's deliverable
  is an `llm` grader with `focus: trace`, which sees the first and last 12 messages
  of the session, including the final message.
