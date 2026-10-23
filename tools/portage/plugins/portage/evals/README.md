# portage eval corpus

Behavioural eval cases for portage's own prose — the PR push/monitor/merge agents and the untrusted-input marker.

Each runner case is a directory here, run by `claude plugin eval`. The case shape, how to
write and run one, and the sandbox it runs in are in `docs/eval-protocol.md` — read it
before adding a case. Results go in `tools/portage/MANUAL-EVAL.md`.

`approval-is-human-only` has no `case.yaml`: it is a legacy hand-shaped case. The runner
does not run it and the push gate does not gate it. It is listed in
`_LEGACY_HAND_SHAPED` in `trailhead/tests/test_eval_corpus.py`, which is also where a
case is removed once it is ported to a runner shape.

This corpus is currently empty. That is an honest report, not a gap being hidden:
no eval has been written for portage yet.
