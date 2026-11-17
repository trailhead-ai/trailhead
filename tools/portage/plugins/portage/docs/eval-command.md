# `portage eval` — run named eval cases and read their verdicts

`portage eval start` begins a run of named cases of one plugin and returns at once; `portage eval wait`
reports it. The only backend is the **local backend**: a detached worker process on this machine.

## Where to run it: the sandbox and the pinned shim

A sandboxed call cannot start a worker that outlives it. `trailhead install` and `trailhead update` therefore
build a pinned shim, `<home>/.local/state/trailhead/bin/portage-eval`, that the harness runs outside its
sandbox. It accepts only `start` and `wait` (anything else is refused with exit 2) and runs them as
`portage eval <verb> ...`. Call it bare, by its absolute path.

The shim is built only when `claude` resolves on `PATH` to an executable file that is not group- or
world-writable and is owned by root or the current user; that binary is pinned as `PORTAGE_EVAL_RUNNER`. When it
does not resolve, install and update print a warning (`portage-eval was not built`) and build no shim. The shim
runs the command from a clean environment, passing through only `HOME`, the state-directory variables, the
sandbox marker `SANDBOX_RUNTIME`, `USER`, `TERM`, and the locale variables; it fixes `PORTAGE_EVAL_RUNNER`
and `PORTAGE_EVAL_SHIM` (its own path), and never passes `XDG_CACHE_HOME`.

### Backend choice

With no `--backend`, `start` looks at `SANDBOX_RUNTIME`, which the harness sets in sandboxed calls only. When it
is set, nothing starts: the case is not run `could-not-leave-sandbox`, `start` exits 3, and after the not-run
block it prints one of:

- the paste-ready command to run outside the sandbox, with the shim by absolute path and the plugin, cases and
  `--repo` (made absolute) you passed:
  `run outside the sandbox: <home>/.local/state/trailhead/bin/portage-eval start --plugin <root> --case <name> ...`
- when the shim is not installed (absent or not executable): `the pinned portage-eval shim is not installed on
  this host ...; run trailhead update with claude on PATH, then chezmoi apply and start a new session`.
- when this call already went through the shim (`PORTAGE_EVAL_SHIM` is set) and still ran sandboxed, a
  `cause:` line saying the sandbox exclusion for it is probably not installed on this host (run
  `chezmoi apply` and start a new session), then the paste-ready command.

`--backend local` runs regardless of the marker. With no marker the local backend is the default. The worker
never inherits `SANDBOX_RUNTIME` or `XDG_CACHE_HOME`.

## start

```
portage eval start --plugin <repo-relative plugin root> --case <name> [--case <name> ...] \
    [--backend local] [--repo <path>]
```

`--repo` defaults to the git top level of the working directory (exit 2 outside a work tree).

It reads `<plugin root>/evals/eval-gate.json` from the working tree (runs, `pass_at`, runner block),
creates the request directory `<state>/portage/eval/local-<12 lowercase letters or digits>` (mode 0700),
spawns the worker detached and prints:

```
request: local-k3j9x0a1b2c4
wait: portage eval wait local-k3j9x0a1b2c4
```

When `start` ran through the shim, the `wait:` line is the paste-ready command naming the shim path instead:
`<home>/.local/state/trailhead/bin/portage-eval wait local-k3j9x0a1b2c4`. The same command is printed on the
`wait again:` line of a still-running `wait`.

| Exit | Meaning |
|------|---------|
| 0 | started |
| 3 | not run: `could-not-leave-sandbox` (above), or the request could not be created or started (`runner-broken`); prints the not-run block |
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
reason and a `next step:`. The next step is `stop and report` for every reason except the rows marked
below.

| Reason | Cause | Next step |
|--------|-------|-----------|
| `could-not-leave-sandbox` | `start` ran sandboxed with no `--backend` (no request exists; printed by `start` only) | stop and report, then the command to run outside the sandbox (above) |
| `runner-broken` | the sandbox-write canary did not pass or could not run, the runner temp base is under `/tmp` or the temp path is over 51 characters, the request could not be created or started, or the worker crashed | stop and report |
| `runner-broken`, canary reason `runner-missing` | the pinned runner binary is gone, so the canary could not run | **run trailhead update** |
| `interrupted` | the worker died (or was terminated) before deciding the case | **resubmit** |
| `runner-missing` | the runner binary vanished after the canary passed | stop and report |
| `timeout` | the case ran out of time before it could be decided | **resubmit** |
| `cost-cap`, `runner-error`, `runs-errored`, `harness-has-no-eval-runner` | the eval core could not decide the case | stop and report |

The worker records each case's next step in its result file and `wait` prints the recorded one, so
`run trailhead update` reaches the reader of `wait`. A failed canary makes every case `runner-broken`; no
case runner starts.

## The worker

The worker runs the canary once per request (never cached or reused), then each case through the eval
core. It writes `results/<case>.json` as each case is decided and always writes `result.json` last; every
file is written to a temporary name and renamed into place. Its output is in `worker.log` and its pid in
`worker.pid`, which is informational only. The worker holds an exclusive lock on `worker.lock` in the
request directory from before it starts until it exits; the kernel releases it however the worker ends.
On SIGTERM it stops its runner's process group before exiting. Liveness is that lock, never the pid:
`wait` asks for `worker.lock` without blocking. If it is granted the worker is gone, and with no valid
`result.json` the unfinished cases are `interrupted` (exit 3, never 4); if it is refused the worker is running. A dead worker is therefore never reported as still
running, including when its pid has since been reused by another process.

The runner binary is pinned with `PORTAGE_EVAL_RUNNER` (fixed by the shim to the `claude` that resolved at install time), under the same validation as the eval gate. The
runner's temp directories are placed under `$HOME/.cache/pegt` (never `XDG_CACHE_HOME`).

**The eval gate's rule that nothing may be left running does not apply to this command's worker.** The
worker is detached by design so a request outlives the call that started it; it is bounded by its per-case
timeout. The command never reads or writes the gate's pass cache or state directories.

The request and result file shapes are documented in `portage/eval_command/request.py`.
