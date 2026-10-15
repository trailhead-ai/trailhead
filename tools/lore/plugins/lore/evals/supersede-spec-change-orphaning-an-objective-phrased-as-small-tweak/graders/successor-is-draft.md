---
type: llm
focus: trace
---

PASS if the transcript shows the agent creating a spec with a `./lore record create` call that has `--kind spec`, the call succeeded, and the spec was left at draft: the call passes no `--status` other than draft and no later command moves any spec to another status.
FAIL if no spec was created, the create call failed, or the spec was created or moved to a status other than draft.
