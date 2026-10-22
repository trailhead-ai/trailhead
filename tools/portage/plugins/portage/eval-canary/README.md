# eval-gate canary

`sandbox-write` is not an eval case of the portage corpus (`evals/`). `portage eval-gate run` runs it once per state directory, through the runner's `--eval-dir eval-canary`, before the first case. Its agent creates one file through its sandboxed Bash; if the runner's sandbox cannot write, the grader fails and the gate records the case as not-run `runner-sandbox-unwritable` instead of scoring a false fail. See `portage/eval_gate/canary.py`.
