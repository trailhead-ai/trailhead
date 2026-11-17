# `portage eval` — run named eval cases and read their verdicts

`portage eval start` begins a run of named cases of one plugin and returns at once; `portage eval wait`
reports it. This slice runs on the **local backend**: a detached worker process on this machine.

## start

```
portage eval start --plugin <repo-relative plugin root> --case <name> [--case <name> ...] \
    [--backend local] [--repo <path>]
```

It reads `<plugin root>/evals/eval-gate.json` from the working tree (runs, `pass_at`, runner block),
creates the request directory `<state>/portage/eval/local-<12 lowercase letters or digits>` (mode 0700),
spawns the worker detached and prints:

```
request: local-k3j9x0a1b2c4
wait: portage eval wait local-k3j9x0a1b2c4
```

| Exit | Meaning |
|------|---------|
| 0 | started |
| 3 | not run: the request could not be created or started; prints the not-run block |
| 2 | refused: an undeclared case (named), a plugin root that leaves the repo, a mapping that does not load |

## wait

```
portage eval wait <id> [--timeout-seconds N]     # N defaults to 110; 1..540
```

Finished, it prints a summary line, then each case and each run:

```
request: local-k3j9x0a1b2c4
summary: 1 failed, 2 not run, 3 passed
case c1: fail
  run 1: score 0.5 failed
case c2: not run
  not run: runner-broken
  next step: stop and report
case c3: pass
  run 1: score 1 passed
```

Still running at the bound, it prints the request, `running: <n> of <m> cases finished (<names>)` and
`wait again: portage eval wait <id>`.

| Exit | Meaning |
|------|---------|
| 0 | every case passed |
| 1 | at least one case failed (a fail outranks a not-run) |
| 3 | none failed and at least one not run |
| 4 | still running when the bound passed |
| 2 | refused: an identifier that is not `local-` plus 12 lowercase letters or digits (never read as a path), an unknown request, or a bound outside 1..540 |

## Not-run reasons and next steps

A case that could not be decided is **not run**, never failed. Every not-run outcome prints a `not run:`
reason and a `next step:`.

| Reason | Cause | Next step |
|--------|-------|-----------|
| `runner-broken` | the sandbox-write canary did not pass or could not run, the runner temp base is under `/tmp` or the temp path is over 51 characters, or the worker crashed | stop and report |
| `interrupted` | the worker died (or was terminated) before deciding the case | resubmit |
| `timeout`, `cost-cap`, `runner-error`, `runs-errored`, `runner-missing`, `harness-has-no-eval-runner` | the eval core could not decide the case | stop and report |

## The worker

The worker runs the canary once per request (never cached or reused), then each case through the eval
core. It writes `results/<case>.json` as each case is decided and always writes `result.json` last; every
file is written to a temporary name and renamed into place. Its pid is in `worker.pid`, its output in
`worker.log`. On SIGTERM it stops its runner's process group before exiting. `wait` treats a worker that
is gone with no valid `result.json` as interrupted, so a dead worker is never reported as still running.

The runner binary is pinned with `PORTAGE_EVAL_RUNNER`, under the same validation as the eval gate. The
runner's temp directories are placed under `$HOME/.cache/pegt` (never `XDG_CACHE_HOME`).

**The eval gate's rule that nothing may be left running does not apply to this command's worker.** The
worker is detached by design so a request outlives the call that started it; it is bounded by its per-case
timeout. The command never reads or writes the gate's pass cache or state directories.

The request and result file shapes are documented in `portage/eval_command/request.py`.
