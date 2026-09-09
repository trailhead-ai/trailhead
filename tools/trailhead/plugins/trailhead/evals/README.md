# trailhead eval corpus

Behavioural eval cases for trailhead's own prose.

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

**This corpus stays empty, and that is structural rather than pending.** A
behavioural eval measures what prose causes an agent to do, and this plugin
ships no prose an agent reads: no skills, no agents, no ruleset. Its whole
capability surface is one SessionStart hook script and a plugin manifest, both
of which have real consumers and are tested by running them — which is the
cheaper and more honest of the two ways to test prose, and the right one here.

An empty corpus elsewhere means nobody has got to it. Here it means there is
nothing to measure. If this plugin ever grows a skill, an agent, or a ruleset,
delete this paragraph and write a case.
