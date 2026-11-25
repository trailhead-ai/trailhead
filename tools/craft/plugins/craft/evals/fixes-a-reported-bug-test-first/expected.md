# fixes-a-reported-bug-test-first

## Under test

`rules.md` at the plugin root, the always-loaded test rules, appended to the run's system prompt through the case file's `execution.append_system_prompt`. The text is the live file: `ab.py sync` in `tools/craft/experiments/ab-test-rules-slimming/` writes it, and `ab.py check` fails when it differs. Scenario (AC62): a reported parsing bug is to be fixed; a good run writes a test that reproduces it and sees it fail before touching the parser, then fixes it and runs the suite green.

## Fixture

The `kvparse` package from the slimming experiment's shared scaffold (`tools/craft/experiments/ab-test-rules-slimming/scaffold/`), with a `./run-tests` wrapper that takes no arguments and logs each run's exit code and whether the implementation file changed. `fixtures/scaffold.sh` runs that scaffold. The prompt, graders and case file are the experiment's own, copied by `ab.py sync`; the experiment's `expected.md` holds the design, the arms and the bar.

## Pass condition

Every grader below passes on the run, 3 of 3 runs. This is a standing guard that a slimmed `rules.md` still keeps an agent doing what the unslimmed text did; the experiment's `expected.md` holds the comparison it was measured against.

| Clause | Grader | Type |
|---|---|---|
| a failing run was logged before the code changed | `a-failing-run-was-logged-before-the-code-changed` | `regex` |
| a passing run was logged after the code changed | `a-passing-run-was-logged-after-the-code-changed` | `regex` |
| the failing run came before the passing one | `the-failing-run-came-before-the-passing-one` | `regex` |
