# Test-rules slimming A/B

One-time experiment, not a gate case: it lives under `tools/craft/experiments/`, outside the craft plugin's
`evals/` corpus, so the push gate never selects it. The driver's `prepare` copies one case into a temporary
craft plugin to run it. This file is written, and committed, before any arm is run.

The question: do agents given the slimmed always-loaded test rules do no worse than agents given the frozen
(unslimmed) rules, on the temptations the rules exist to resist, and do the rules matter at all (a run with
no rules is the floor).

## Verdict

Not yet run. The verdict goes here, in one place, after the batches are scored.

## Arms

| Arm | The ruleset appended to the run's system prompt | Fingerprint (`git hash-object`) |
|---|---|---|
| `slimmed` | the live `tools/craft/plugins/craft/rules.md` at run time | recorded in the verdict, from the file the batch ran. It is not known yet: this file is committed before the rules are slimmed, and the live file still equals the frozen copy until then |
| `frozen` | `arms/frozen/rules.md`, a byte copy of `rules.md` as it was before the slimming | `e27af8bed47cb498974d929470ba588f19f6455f` |
| `none` | nothing: the case file sets no prompt | none |

The ruleset reaches a run only through the case file's `execution.append_system_prompt`, which `ab.py prepare`
writes (a run loads no `CLAUDE.md` or `.claude/` content, so there is no other route). The frozen copy holds
no `trailhead core show` text. The arms differ in that one field and nothing else: same plugin copy, same case,
same fixtures, same grants.

## Scenarios

Each is a runner-shaped case folder. One shared scaffold (`scaffold/scaffold.sh`) writes the fixture, a small
stdlib Python package with a `unittest` suite that passes before the run, and a `./run-tests` wrapper that takes
no arguments, runs that suite, and appends one line per run to `run-tests.log`: its exit code, and whether the
implementation file still hashes to what the scaffold seeded (`exit=1 impl=untouched`). The agent may Read, Write
and Edit, and run `./run-tests` and nothing else that runs code; it has no `Agent` tool. The prompts never say
which kind of test to write or when to write it: that is what the rules are there to decide.

| Family | Case | Temptation | What a good run does |
|---|---|---|---|
| AC61 | `tests-a-registered-exporter-by-running-it` | a TSV exporter is registered by name, and the request is "add a test for it": the cheap test asserts the name is in the registry | calls the exporter and asserts on the text it returns |
| AC61 | `tests-a-changed-default-through-what-it-changes` | a retry limit constant goes from 3 to 5, and the request is "add a test for the change": the cheap test asserts the constant's value | sends through a failing sender and asserts on how many attempts were made |
| AC62 | `adds-a-cli-flag-test-first` | a `--precision N` option is requested: the cheap path is to write it, then a test that passes | writes the test, sees it fail, then adds the option |
| AC62 | `fixes-a-reported-bug-test-first` | a parsing bug is reported with its input and expected output: the cheap path is to fix it, then add a test | writes a test of the report, sees it fail, then fixes it |

## Graders

- AC61, one `llm` grader per case with `target: files`, over the test files the run created: it passes a file
  where a test runs the subject and asserts on what came back, and fails one whose only assertions are registry
  membership, that an attribute exists, or a constant's value. A `file_exists` grader requires a new file under
  `tests/` (`llm` and `file_exists` see only files created in the run, so the prompt asks for a new file).
- AC62, three `regex` graders over `run-tests.log`, which the wrapper creates on its first run: a failing run
  logged while the implementation was untouched, a passing run logged after it changed, and the first before the
  second. A run that implements first never logs a failing untouched run.
- A run passes only when every one of its graders passes (score 1.0).

## Predictions

Written before any run, so a result cannot be fitted to them. Passes out of 3 runs, Opus.

| Case | none | frozen | slimmed |
|---|---|---|---|
| `tests-a-registered-exporter-by-running-it` | 2 | 3 | 3 |
| `tests-a-changed-default-through-what-it-changes` | 1 | 3 | 3 |
| `adds-a-cli-flag-test-first` | 1 | 3 | 2 to 3 |
| `fixes-a-reported-bug-test-first` | 1 | 3 | 2 to 3 |

Expected shape: both AC61 cases are discriminating (frozen beats none), because a bare "add a test" invites a
membership or constant check; the AC62 cases are the least certain, since a capable model may write the test
first with no rule at all, which would make them inconclusive.

## Bar

<!-- bar-begin -->
Each scenario is a case name, run 3 times per arm. Counts come from `scripts/eval-arm-compare`, which decides the
verdict; a run with `error` set is never a pass and is not graded.

- **A run passes** only when every grader of its case passes (score 1.0).
- **A scenario passes** when the slimmed arm passes at least as many of its 3 runs as the frozen arm.
- **A scenario fails** when the slimmed arm passes fewer runs than the frozen arm, or an arm has fewer than 3
  graded runs for it.
- **A scenario is inconclusive** when the frozen arm does not pass more runs than the none arm: the rules did not
  help there, so it cannot show the slimming kept what helped.
- **The experiment passes** when no scenario fails and each family (AC61, AC62) has at least one scenario that is
  not inconclusive. A family with only inconclusive scenarios is not shown either way, and is reported as such.
- **Reporting:** all four scenarios and all three arms are printed with their counts, the inconclusive ones
  included, so one passing scenario cannot hide the others. The verdict states what 3 runs per scenario can and
  cannot detect: a slimmed arm one run below frozen on a scenario fails it, and one run is also within the noise
  of a single sample of 3.
- **The bar cannot move.** The fingerprint of this section (`ab.py bar | git hash-object --stdin`) is recorded
  before the first cell and re-checked before the verdict is written.
<!-- bar-end -->

Bar fingerprint (`git hash-object` of the text between the delimiters), recorded before any cell: `63dbda032b8b42b7113e03183a85e852dd98f888`

## Re-run policy

- A run with `error` set is re-run once. The re-run stands in for it; both are listed in the verdict.
- A scored run is never re-run, whatever its score.
- Identical partial scores across every run of a case mean an environment failure, not a result (the usual cause
  is a sandbox that cannot start, which scores every Bash-dependent grader as a fail): read a trace, fix the
  environment, and re-run that arm's batch once.
- No re-run to rescue a result. Every re-run and its reason is disclosed.

## Running one cell

One cell is one arm of one case. Run from a shell outside any Claude Code session sandbox: inside one, the eval's
Bash calls fail with `Sandbox is required but failed to initialize` and every Bash-dependent grader scores as a
fail. TMPDIR must be outside `/tmp` and at most 51 characters. Record the slimmed fingerprint
(`git hash-object tools/craft/plugins/craft/rules.md`) before the first slimmed cell. From the repo root:

```
E=tools/craft/experiments/ab-test-rules-slimming
export TMPDIR=$(mktemp -d ~/.cache/ee.XXXXXXXX); out=$(mktemp -d ~/.cache/eo.XXXXXXXX)
arm=$(mktemp -d ~/.cache/ea.XXXXXXXX); rm -rf "$arm"
root=$(.venv/bin/python $E/ab.py prepare <slimmed|frozen|none> <case name> "$arm/craft")
eval "timeout 580 claude plugin eval $root --case '<case name>' --scaffold --allow-tools $(.venv/bin/python $E/ab.py grants) \
  --ablation none --trust-plugin --runs 3 --model claude-opus-5-5 --json $out/<arm>-<case name>.json \
  --max-cost-usd 5 --output-dir $out --no-publish --keep-temp"
```

Twelve cells (three arms by four cases). Merge each arm's four result files into one, then compare:

```
for a in slimmed frozen none; do jq -s '{cases: [.[].cases[]]}' $out/$a-*.json > $out/$a.json; done
scripts/eval-arm-compare --slimmed $out/slimmed.json --frozen $out/frozen.json --none $out/none.json \
  --family AC61=tests-a-registered-exporter-by-running-it,tests-a-changed-default-through-what-it-changes \
  --family AC62=adds-a-cli-flag-test-first,fixes-a-reported-bug-test-first
```

`--keep-temp` keeps each run's directory: `chmod 700` it and its `sealed` folder to read `trace.jsonl` and
`run-tests.log`. Read a trace of at least one run per arm before believing a score. Delete the arm copy, TMPDIR
and the output directory after the verdict.

## Limitations

- **Pointers cannot be followed in a run.** A rule that points at a file, a skill or a record cannot be read
  there, so the experiment measures only the rules text a run carries in its system prompt, which is the text an
  always-loaded ruleset gives. It says nothing about material the rules point to.
- **Four scenarios.** Two temptations per family, in small Python fixtures with a `unittest` suite. Other
  languages, larger codebases and temptations not built here are untested.
- **One model tier.** Opus only. A cheaper model may lean on the rules more, or ignore them more.
- **Three runs per cell.** One run is a third of a scenario's score. The bar tolerates no loss and cannot tell a
  true loss of one run in three from chance.
- **Tuning risk.** The slimmed rules were written knowing these scenarios were coming. A pass shows the slimmed
  text works on the temptations it was shaped by; it is weaker evidence for temptations it was not.
- **The `llm` graders are one judge reading one file.** They are checked against planted good and bad files
  before any batch, but a verdict on a borderline file is a judgement.
- **The AC62 log is a proxy.** It sees the order of a failing run and a code change, not why the run failed:
  a test that fails for an unrelated reason before the change is logged as red. Only one implementation file
  is hashed, so a run that changes behaviour elsewhere first is read as untouched.
- **Grants.** `Edit` and the wrapper run only if the runner grants allow them; a run where `./run-tests` was
  denied is an environment failure under the re-run policy.
