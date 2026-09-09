# trailhead eval corpus

Behavioural eval cases for trailhead's own prose — install/compose/wire and the session-start hook.

Each case is a directory here:

```
<case-name>/
    expected.md      the pass condition, written BEFORE any arm is run
    fixtures/        self-contained inputs the run is pointed at
    arms/            optional: edited prose copies, for a treatment arm
```

The authoring rules, the dispatch shape, and the trust boundary on the
instructions path are in `docs/eval-protocol.md` — read it before adding a case.
Results go in `tools/trailhead/MANUAL-EVAL.md`.

This corpus is currently empty. That is an honest report, not a gap being hidden:
no eval has been written for trailhead yet.
