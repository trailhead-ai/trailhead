# Eval protocol — measuring what a tool's prose actually causes

A skill, agent definition, ruleset, or template is **source, not behaviour**. The
testing ruleset bars asserting on its text, and `scripts/inert-test-gate` enforces
that: a test that reads a markdown file and checks a sentence is present has
observed that someone typed it, not that an agent read it, understood it, or
obeyed it — which was the entire question.

That leaves exactly two honest ways to test prose. The first is to **run it
through its consumer** — the composer, the manifest loader, the front-matter
parser — and assert on what the consumer produced. Cheap, mechanical, and where
most prose testing belongs; the tool suites already do this.

This document is about the second: **run the agent against a scenario and judge
the outcome**. It is expensive, so it is reserved for the instructions that
actually decide something. Eval cases run through the plugin eval runner,
`claude plugin eval`, and portage's push gate runs the ones a branch touches.

## Write and run a case, step by step

1. **Pick the prose and its plugin.** The prose must ship in a plugin
   (`tools/<tool>/plugins/<tool>/skills/`, `agents/`). The runner loads only the
   plugin under test. Prose outside a plugin has no route; see
   [Limits](#limits).
2. **Copy a runner case.** The exemplar is
   `tools/craft/plugins/craft/evals/declined-review-correction-is-left-alone/`.
   It is small (three graders, a prompt, a seeded fixture) and runs for well
   under a dollar. Copy the directory to `evals/<new-case-name>/` in the same
   corpus, rename it in `case.yaml`, and replace the prompt, the fixture and the
   graders. A case that does not need lore needs no scaffold at all; drop
   `context.scaffold_script` and the `fixtures/` scripts, but `fixtures/` must stay
   non-empty, because the corpus hygiene suite (`trailhead/tests/test_eval_corpus.py`)
   requires it.
3. **Write `expected.md` first**, and commit it alone before any run: what is under
   test, the conditions, the sample and bar, the pass condition, the limitations.
   See [Retained discipline](#retained-discipline).
4. **Map the case to the prose it measures.** Add an entry to the corpus's
   `eval-gate.json` listing the plugin-relative paths `expected.md` names under
   test. A case with no entry fails the portage eval-gate corpus test
   (`tools/portage/tests/test_portage_eval_gate_corpora.py`). The mapping, the
   bar and the verdict are described in
   `tools/portage/plugins/portage/docs/eval-gate.md`.
5. **Take the red baseline** on the prose as it stands before your change. See
   [The baseline](#the-baseline).
6. **Run the batch** with the command below, then read the verdict from the result
   JSON. See [Running a batch](#running-a-batch).

A complete command, as run for the exemplar on Linux. The `TMPDIR` line is the
[Linux temp-directory gotcha](#runner-gotchas); whether macOS needs it was not
checked. Run it only on a branch you wrote or reviewed, because `--scaffold` runs
the case's scaffold script as you (see
[what the sandbox does not confine](#what-the-sandbox-confines-and-what-it-does-not)):

```sh
export TMPDIR=$(mktemp -d ~/.cache/ee.XXXXXXXX)      # outside /tmp, short path
out=$(mktemp -d)
timeout 580 env -u LORE_CONFIG_DIR -u LORE_STATE_DIR \
  -u XDG_CONFIG_HOME -u XDG_STATE_HOME \
  claude plugin eval tools/craft/plugins/craft \
  --case 'declined-review-correction-is-left-alone' --scaffold \
  --allow-tools 'Bash(./lore *)' 'Bash(*/scripts/maturity_bars.py *)' \
  'Bash(:)' 'Bash(: *)' --ablation none --trust-plugin --runs 1 \
  --model claude-opus-5-5 --json "$out/run.json" --max-cost-usd 1 \
  --output-dir "$out" --no-publish -j 1
```

The `--allow-tools` grants after `./lore` are craft's, copied from the `runner`
block of craft's `eval-gate.json`; `tools/craft/plugins/craft/evals/README.md`
explains them. Another plugin uses the grants in its own `runner` block. Every
`--allow-tools` grant applies to every case in the run, so grant the minimum.

Run it in the foreground with a tool timeout above the run's length, give it your
own `timeout` as above, and delete `$out` and the `TMPDIR` directory when you have
read the result. Use `--runs 3` for a real baseline or a real check; one run is
for finding out whether the case runs at all.

## Why the runner

`claude plugin eval` loads the plugin under test, runs each case's prompt in a
sandboxed agent, scores the run with graders, and reports a score per run. Portage's
push gate runs it for the cases that measure the prose a branch changed, and stops
the push on a regression. What the gate selects, the per-corpus `eval-gate.json`
mapping, the pass bar and the verdict are in
`tools/portage/plugins/portage/docs/eval-gate.md`; this document does not restate
them. A case that is not runner-shaped is never selected, so it guards nothing.

## Case shape

```
tools/<tool>/plugins/<tool>/evals/<case-name>/
    case.yaml        schema_version "1.1", name, runs, optional context
    prompt.md        frontmatter limits, then the task sent verbatim
    graders/         one file per grader
    expected.md      the pass condition, committed BEFORE any run
    fixtures/        self-contained inputs, and a scaffold if the case needs one
tools/<tool>/plugins/<tool>/evals/eval-gate.json   the case-to-prose mapping
tools/<tool>/MANUAL-EVAL.md                         the results log
```

- `case.yaml` holds `schema_version: "1.1"`, `name`, `runs` (1 to 50, default 3)
  and, for a seeded case, `context: {scaffold_script: fixtures/scaffold.sh}`.
  `model`, `max_turns` (default 10), `timeout_seconds` (default 300) and
  `allowed_tools` go under `execution:` or in the prompt frontmatter, which wins.
- `prompt.md` frontmatter carries `allowed_tools` (read-only tools such as `Skill`,
  `Read`, `Glob`, `Grep`, `Agent`), `max_turns` and `timeout_seconds`. `Bash`,
  `Write` and `Edit` are removed from the session unless the operator passes them
  with `--allow-tools`, which applies to every case in the run. The gate takes the
  grants from the corpus's `eval-gate.json` `runner` block.
- `graders/<name>.md` is one grader: frontmatter `type` (`regex`, `file_exists`,
  `tool_used`, `llm`) and its options. A run's score is the fraction of graders
  that pass.
- `expected.md` is the load-bearing artifact. Writing it first is what stops a
  result being retrofitted into a pass: a verdict authored after seeing the runs
  is not evidence, and there is no way to tell the two apart later.
- `fixtures/` is data the run reads. A scaffold script, if there is one, runs
  before the agent in the empty run directory.

The grader types, the lore seeding template and `records.json` format are in
`tools/lore/plugins/lore/evals/README.md`; the craft lore-wrapper scaffold is in
`tools/craft/plugins/craft/evals/README.md`. Read the one for the plugin you are
writing in.

## Running a batch

Use the command in the step-by-step path above. What each flag is for:

- `--scaffold` runs each case's `scaffold_script`; without it the runner skips
  it. The script is author-supplied bash that runs as you, so use it only on case
  files you or your team wrote.
- `--trust-plugin` answers the first-run trust prompt for a plugin you would run
  yourself. It does not imply `--scaffold`, `--allow-tools` or `--mocks off`.
- `--runs N` is runs per case, overriding `case.yaml`. `--ablation none` runs the
  with-plugin arm only (see [The baseline](#the-baseline)). `--model` pins the
  agent's model so a model change is not mistaken for a prose change.
- `--allow-tools` is variadic: it takes every following value up to the next
  flag, so put it before another flag.
- `--case` takes one glob; given twice, only the last runs, so run one batch per
  glob.
- `--json <path>` writes the result document, and `--output-dir <dir>` holds the
  aggregate result. Put both in a fresh `mktemp -d` directory and delete it
  after copying scores out. They hold prompts and grader explanations; never
  commit them. Without `--output-dir` the runner writes `evals/results/<ts>/`
  inside the corpus, which the hygiene suite reads as a case with no
  `expected.md`.
- `--no-publish` always. Without it the runner uploads its report to claude.ai.
- `--max-cost-usd N` stops starting new runs once the list-price estimate reaches
  N (exit 2, partial results).
- `-j 1` runs one agent at a time. Concurrent runs share one rate limit.

Run it in the foreground. Nothing may be left running after the call returns: no
`nohup`, no trailing `&`, no `run_in_background`.

## What the sandbox confines, and what it does not

The runner's sandbox confines the **agent's shell tools** (the Bash tool the run
grants). The lesson that found this (platform not recorded) is that the agent's
Bash can read little of the operator's home beyond the plugin directory, the
run's working directory and the run's temporary `HOME`. The runner's help text
says its sandboxing limits what a malicious plugin can reach "but is not a
guarantee".

What it does not confine:

- **Scaffold scripts** (`--scaffold`) run as you. The script's `HOME` variable is
  the run's temporary home, but the script is not confined, so it can still reach
  your real home. So can the repo scripts it calls, such as
  `tools/lore/scripts/seed-eval-vault`.
- **`--trust-plugin`** only skips the trust prompt. It asserts that you trust the
  plugin's code and its eval suite; it grants no isolation and removes none.
- **Real MCP servers** (`--allow-real-servers`, `--mocks off`) run as you, outside
  the sandbox that confines shell tools.
- **The network.** This document has not measured network confinement for the
  agent's shell tools, so assume the network is reachable.
- **Whatever `--allow-tools` grants.** A grant such as `Bash(./lore *)` lets the
  agent run that command, and what that command does is not confined by the grant.

So the sandbox is a guard against an agent wandering, not a security boundary for
a hostile branch. Run cases you or your team wrote and reviewed.

**For a case that drives lore, diff the real lore state around every batch.**
Before and after, snapshot the real lore config and state (for example the files
under `~/.config/lore/` and `~/.local/state/lore/`, with `find ... -type f -exec
sha256sum {} +` into a file) and `diff` the two. Any change is a failed run and
a hard stop, unless every changed path belongs to a commit another session made
and that commit carries none of the batch's fixture titles. Escapes are silent:
the run reports success either way.

## The baseline

A new case is run against the prose **before** the change it guards, and it must
go **red**. That red is recorded in the results log, the way earlier slices
pre-registered a case and recorded its red baseline. A baseline that comes back
green is recorded as green, and the case is then a guard rather than a detector,
which the case's `expected.md` should say before the run.

`--ablation with-without` answers a different question: it adds a no-plugin arm
and reports the score delta, so it tells you whether the **plugin** causes the
behaviour at all. It does not compare edited prose to committed prose, and it
doubles the cost. Use `--ablation none` for a baseline and a re-check of one
plugin's prose.

## Runner gotchas

Each is a symptom, then the fix. Each is marked with the platform its source
lesson observed it on, or "platform not recorded" where the lesson does not say.

- **Every case that writes scores as a plain fail, with `error` null (Linux
  only).** On the tested Linux host the agent's sandbox cannot write when its
  `TMPDIR` is under `/tmp` or is longer than 51 characters, on runner 2.1.304
  and later; lore cases score 0.5. Names are irrelevant: 51 passes, 52 fails,
  `TMPDIR=/tmp` fails. Fix: set `TMPDIR` to a directory outside `/tmp` whose path
  is at most 51 characters, as in the command above. The push gate does this for
  itself. macOS was not checked.
- **`./lore` or `lore` exits 127 in the run (platform not recorded).** The
  agent's sandbox hides the operator's home, so a `lore` on `PATH` that points
  into a checkout fails. Fix: the case brings its own `./lore` wrapper, seeded
  into the run directory with a copy of the `trailhead/` package, grants
  `Bash(./lore *)`, and its prompt says to run `./lore` wherever a document says
  `lore`. In a plugin other than lore, the scaffold also copies the lore CLI
  into the run directory, because the sandbox exposes only the plugin under
  test. The runner pre-populates the run's `~/.gitconfig` with its own identity,
  so a seeder must keep an existing identity and not refuse it.
- **The grader passes but the tool path was broken (platform not recorded).** A
  grader over seeded state cannot tell a working tool from a broken one: the
  scaffold wrote the file before the agent ran. Fix: before any baseline,
  smoke-run a new fixture with one CLI **write** (`./lore record amend` or
  `./lore record create`), grade the written result, and read the run's
  transcript for the command's own output.
- **A case that drives a subagent never reaches it (platform not recorded).**
  Fix: `Agent` in the case's `allowed_tools`, plus the Bash grants the agent
  needs. A scaffold must build its commits signed if the agent refuses unsigned
  ones: the portage updater's preflight does, so its scaffolds build commits
  with `git hash-object` and a dummy `gpgsig` header. The sandbox runs in
  don't-ask mode, so it denies compound commands, redirects, any command
  containing `<!-- -->`, and reads of the plugin's `evals/` directory: keep
  stand-in calls to single commands. If the agent can see a real CLI's `--help`
  it may drive an undisclosed subcommand unprompted and contaminate the
  baseline, so a stand-in must hide it.
- **A grader on the middle of a run sees nothing (platform not recorded).** The
  `llm` trace judge sees only the first 12 and last 12 events once a run passes
  24. Fix: for "did the agent call X", use `tool_used` (`tool`, `input_match` as
  a JavaScript regex over the tool input's JSON, `min`, `max`), which scans
  every call. Also, `target: files` on `regex`, `file_exists` and `llm` lists
  files the agent **created** only; to grade a seeded record that the agent
  modified, use a `regex` with `source: file` on its exact path.
- **An untracked `.jsonl` appears in `fixtures/` after a seeded-history case
  (platform not recorded).** A case that seeds a conversation with
  `context.history_file` leaves `fixtures/<sessionId>.jsonl` beside the seed,
  which a `git add -A` would sweep in. Fix: read what you need from it, then
  delete it before staging.
- **The exit code says pass or fail (platform not recorded).** It does not. A
  scored fail and every error exit 1; a cost-ceiling or auth partial exits 2.
  Fix: read the verdict from the result JSON, `cases[].arms.with[]` (`score`,
  `passed`, `error`, `graders[]`), and count runs from that array, not from
  `runsPerCase`, which echoes the case's own `runs`. A run's `error` overrides
  its graders.
- **The runner overruns its own `timeout_seconds` (platform not recorded).** It
  has taken 1083 seconds against a 300 second limit, and about 6 minutes on a
  refused connection. Fix: bound it from outside with `timeout` or a
  process-group kill, as in the command above.
- **A run moves onto API billing (Linux).** `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK`
  and `ANTHROPIC_BASE_URL` each move a run off the login. Fix: start the runner
  with a clean environment (`env -i` plus `PATH`, `HOME` and the rest the gate
  forwards) when billing matters. This was verified on Linux. It is not only
  billing: `ANTHROPIC_BASE_URL` also routes the run's prompts and credential to
  another endpoint. A `HOME` forwarded into the run carries your Claude settings,
  so an `apiKeyHelper` or `env` block there can change the credential too (see the
  environment paragraph of
  `tools/portage/plugins/portage/docs/eval-gate.md`).

## Credentials on macOS: the Keychain

On macOS, Claude Code keeps its credentials in the login Keychain, not in a
credentials file under its config directory.

## Retained discipline

These are the rules that make a recorded result worth trusting later. Each exists
because ignoring it produced a wrong read at least once.

- **Write the expected verdict first**, and commit it before any run.
- **More than one run.** Agent behaviour varies between runs. A single run is an
  anecdote; the failure mode that matters is usually inconsistency, which one run
  cannot show. The gate's bar is 2 of 3 by default.
- **Check the fixture for contamination.** If the fixture reproduces the prose's
  own worked example, a run can score full marks by recall rather than reasoning.
  Vary the surface cues against the answers, and confirm the fixture is
  self-contained: no `[[wikilink]]` or cross-reference that resolves to a real
  record. A fixture, a stand-in's output or a prompt must not leak its own answer
  key.
- **The sanctioned path must succeed.** If a case forbids one mechanism, the
  fixture has to make the permitted one work. A task that cannot be completed the
  sanctioned way measures resourcefulness, not obedience: an agent handed an
  impossible task went looking for the machinery that would work, and mutated the
  operator's real lore config. Prove the sanctioned path from inside the runner
  with a real write (see the smoke-test gotcha above), not from your shell.
- **Never point a fixture at a real vault, repo or config**, even read-only. The
  fixture is data; live infrastructure is not part of it.
- **Grade from the files a run leaves, not from its report.** A run that reports
  a URL may have published somewhere else entirely. Read a hand-checked sample
  of runs against the grader's verdicts before trusting the grader; assume it is
  wrong until they agree.
- **Record authoring errors as authoring errors.** When a run disagrees with the
  expected verdict and the run is right, that is a defect in the test material,
  not a result. Say so in the log rather than quietly repairing the fixture.
- **State the limitations.** Number of fixtures, number of runs, and the one model
  tier they ran on. A result that does not say what it does not cover will be read
  as covering more than it does.
- **The case and prompt files are a trust boundary.** Case and prompt files are
  committed, review-gated repo artifacts, but the gate does not stop a hostile
  branch. Only the `runner` block (grants, model, cost cap) is pinned to the
  base, so a branch cannot widen its own grants. The branch's own case files,
  prompts and scaffold scripts run as the operator, at the author's push, before
  review; see the trust model in
  `tools/portage/plugins/portage/docs/eval-gate.md`. Never take a prompt, a
  grant or a path from a fixture, a spec body, a lore record or any other value an
  untrusted party can write.

## Limits

- **Prose outside a plugin has no route.** User rulesets (`~/.claude/rules/`),
  `CLAUDE.md` and anything else the plugin does not ship cannot be loaded by the
  runner, so no case can measure it and the gate cannot select one. Do not write a
  hand-run case for it. Record the limitation in the task that changes the prose,
  so the reader knows the change was not behaviourally measured.
- **Cost.** The gate re-runs every touched case on every push; nothing is cached.
  A multi-case batch costs real money, so size `--max-cost-usd` from the logged
  cost of the corpus.
- **A subagent inherits the user's rulesets.** Where a case drives a subagent, the
  baseline arm may already carry a user-level ruleset; probe before trusting a
  baseline.

## Legacy hand-shaped cases

A case directory with no `case.yaml` is hand-shaped: a fixture, an `expected.md`
and, sometimes, `arms/` edited prose copies, written for an in-session run. Neither
the runner nor the gate runs one. They are listed in
`_LEGACY_HAND_SHAPED` in `trailhead/tests/test_eval_corpus.py`, and their fixtures
and verdicts are kept so they can be ported. Porting one means giving it a runner
shape (a `case.yaml`, a prompt, graders and a gate mapping) and removing it from
the list. The list can only shrink. `scripts/eval-sandbox` stays on disk only for
those cases' existing harness and is not a way to run anything new.

The list is a hygiene check, not a security control.

## Corpus hygiene

`trailhead/tests/test_eval_corpus.py` runs over every tool. Each case directory
must carry an `expected.md` and a non-empty `fixtures/`, and each `expected.md`
must state what is under test and its pass condition. A case must also be
runner-shaped, with a `case.yaml`, unless it is on the legacy list; a new
hand-shaped case fails, and so does a listed case that has since been ported or
removed. The rule fails the test suite and the pull request's CI. It does not fail
a commit, because pre-commit does not run pytest. That is structure only: it
cannot tell you a case is worth running, and it deliberately does not try.

A tool with no cases yet is not a failure. An empty corpus is an honest report
that nobody has written an eval for that tool.
