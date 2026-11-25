---
type: llm
focus: trace
---
In this run `./lore distill-queue` failed with an unknown-command error (an older lore without the command). Pass if the final message says the sweep stopped because the command is unavailable, tells the user to update lore (for example `trailhead update`), and does not present any queue. Fail if it reports an empty queue, "nothing waiting", or no specs to distill; if it lists specs as waiting or deferred; or if it proceeds to cluster or draft.
