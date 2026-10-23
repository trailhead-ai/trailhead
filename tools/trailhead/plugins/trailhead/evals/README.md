# trailhead eval corpus

Behavioural eval cases for trailhead's own prose.

A case here would be a directory run by `claude plugin eval`. The case shape and how to
write and run one are in `docs/eval-protocol.md`. Results would go in
`tools/trailhead/MANUAL-EVAL.md`.

**This corpus stays empty, and that is structural rather than pending.** A
behavioural eval measures what prose causes an agent to do, and this plugin
ships no prose an agent reads: no skills, no agents, no ruleset. Its whole
capability surface is one SessionStart hook script and a plugin manifest, both
of which have real consumers and are tested by running them — which is the
cheaper and more honest of the two ways to test prose, and the right one here.

An empty corpus elsewhere means nobody has got to it. Here it means there is
nothing to measure. If this plugin ever grows a skill, an agent, or a ruleset,
delete this paragraph and write a case.
