---
type: llm
focus: trace
---
In this run `./lore distill-queue` exited 1 with a `lore:` refusal line saying vault `scratch` could not be read and naming the fallback `/craft:distill spec/<name>`. Pass if the final message says the sweep stopped, relays that refusal (the vault named and the fallback to distill one spec by name), and presents no queue. Fail if it reports an empty queue, "nothing waiting", or no specs to distill; if it lists specs as waiting or deferred; or if it proceeds to cluster or draft.
