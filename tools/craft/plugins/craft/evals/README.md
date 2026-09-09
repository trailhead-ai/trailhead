# craft eval corpus

Behavioural eval cases for craft's own prose — the spec gauntlet, the council
lenses, the execute loop, and the maturity/citation machinery.

Each case is a directory here:

```
<case-name>/
    expected.md      the pass condition, written BEFORE any arm is run
    fixtures/        self-contained inputs the run is pointed at
    arms/            optional: edited prose copies, for a treatment arm
```

The authoring rules, the dispatch shape, and the trust boundary on the
instructions path are in `docs/eval-protocol.md` — read it before adding a case.
Results, with per-case evidence and limitations, go in `tools/craft/MANUAL-EVAL.md`.

This is the corpus the protocol was extracted from; the seven cases here are the
worked examples for the other tools.
