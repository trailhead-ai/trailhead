# tests-a-registered-exporter-by-running-it

## Under test

`rules.md` at the plugin root, the always-loaded test rules, appended to the run's system prompt through the case file's `execution.append_system_prompt`. The text is the live file: `ab.py sync` in `tools/craft/experiments/ab-test-rules-slimming/` writes it, and `ab.py check` fails when it differs. Scenario (AC61): a TSV exporter is registered under `tsv` and the request is to add a test for it: the cheap test asserts the name is in the registry; a good run calls the exporter and asserts on the text it returns.

## Fixture

The `reportbay` package from the slimming experiment's shared scaffold (`tools/craft/experiments/ab-test-rules-slimming/scaffold/`), with a `./run-tests` wrapper that takes no arguments and logs each run's exit code and whether the implementation file changed. `fixtures/scaffold.sh` runs that scaffold. The prompt, graders and case file are the experiment's own, copied by `ab.py sync`; the experiment's `expected.md` holds the design, the arms and the bar.

## Pass condition

Every grader below passes on the run, 3 of 3 runs. This is a standing guard that a slimmed `rules.md` still keeps an agent doing what the unslimmed text did; the experiment's `expected.md` holds the comparison it was measured against.

| Clause | Grader | Type |
|---|---|---|
| a new test file was created | `a-new-test-file-was-created` | `file_exists` |
| test runs the exporter and asserts on its output | `test-runs-the-exporter-and-asserts-on-its-output` | `llm` |
