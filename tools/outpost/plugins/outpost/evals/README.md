# outpost eval corpus

Behavioural eval cases for outpost's own prose — the site daemon and the publish-site skill.

Every case here is a legacy hand-shaped case: a directory with an `expected.md`,
`fixtures/` and, sometimes, `arms/`, and no `case.yaml`. The runner does not run them and
the push gate does not gate them. They are listed in `_LEGACY_HAND_SHAPED` in
`trailhead/tests/test_eval_corpus.py`, which is also where a case is removed once it is
ported to a runner shape. The case shape, how to write and run a runner case, and the
sandbox are in `docs/eval-protocol.md` — read it before adding a case. Results go in
`tools/outpost/MANUAL-EVAL.md`.

Each case directory under here is one entry in this corpus; results for all of
them are in `tools/outpost/MANUAL-EVAL.md`.
