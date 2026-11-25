# tests-a-registered-exporter-by-running-it

## Under test

`rules.md` at the plugin root, the always-loaded test rules, appended to the run's system prompt through the case file's `execution.append_system_prompt`. The text is the live file: `ab.py sync` in `tools/craft/experiments/ab-test-rules-slimming/` writes it, and `ab.py check` fails when it differs. Scenario (AC61): a TSV exporter is registered under `tsv` and the request asks for a quick test that checks `tsv` is registered: the cheap test asserts the name is in the registry; a good run calls the exporter and asserts on the text it returns.

## Fixture

The `reportbay` package from the slimming experiment's shared scaffold (`tools/craft/experiments/ab-test-rules-slimming/scaffold/`), with a `./run-tests` wrapper that takes no arguments and logs each run's exit code and whether the implementation file changed. `fixtures/scaffold.sh` runs that scaffold. The prompt, graders and case file are the experiment's own, copied by `ab.py sync`; the experiment's `expected.md` holds the design, the arms and the bar.

## Conditions, sample and bar

Model `claude-opus-5-5`, the craft runner block's grants (including `Bash(./run-tests)`), no baseline arm (`--ablation none`), 3 runs. The bar is the gate's: 2 of 3 runs pass. A run with `error` set is re-run once; a scored run is never re-run.

## Pass condition

Every grader below passes on a run; the case passes at 2 of 3 runs. This is a standing guard that a slimmed `rules.md` still keeps an agent doing what the unslimmed text did; the experiment's `expected.md` holds the comparison it was measured against.

| Clause | Grader | Type |
|---|---|---|
| a new test file was created | `a-new-test-file-was-created` | `file_exists` |
| test runs the exporter and asserts on its output | `test-runs-the-exporter-and-asserts-on-its-output` | `llm` |

## Limitations

- The rules reach the agent only as appended system-prompt text; a run has no core vault, so the `trailhead core show` pointers in that text cannot be followed and only the inline lines bind.
- One scenario, one model tier, three runs. The case guards the slimmed text; the comparison against the text before slimming, and against no rules, lives in the experiment's `expected.md`.
- The craft plugin's agent descriptions, including the executor's, are in context in every run, and `agents/executor.md` ships in the plugin.
