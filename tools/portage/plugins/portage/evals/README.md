# portage eval corpus

Behavioural eval cases for portage's own prose — the PR push/monitor/merge agents and the untrusted-input marker.

Each runner case is a directory here, run by `claude plugin eval`. The case shape, how to
write and run one, and the sandbox it runs in are in `docs/eval-protocol.md` — read it
before adding a case. Results go in `tools/portage/MANUAL-EVAL.md`.

The corpus's runner cases (`a-failing-eval-gate-stops-the-push`,
`an-unrunnable-eval-gate-still-pushes-and-says-so`, and the five
`approval-is-human-only-<scenario>` cases, which share `approval-fixture-env.sh`, and the four `monitor-*` merge-tier cases and `pull-request-merge-runs-the-merge-tier-gate`, which share `monitor-merge-tier-fixture-env.sh`) are mapped to the prose they measure
in this directory's `eval-gate.json`. The monitor, updater and pull_request cases that are not the
detectors for their file are `"merge_only": true` and run before merge; the merge-tier cases above stay core,
and every measured file keeps at least one core case.
