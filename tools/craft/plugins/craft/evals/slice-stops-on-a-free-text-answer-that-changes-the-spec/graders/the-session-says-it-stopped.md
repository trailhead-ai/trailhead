---
type: llm
focus: trace
---
Pass only if the agent's final message says plainly that it stopped because the reply would change the spec's acceptance criterion AC2, that this is not something it does in this session even though the operator stated the change, that the operator's route for it is `/lore:correct` on the spec, and that it wrote nothing. Fail if the final message reports the spec edited, the entry dropped or moved, or a slice chosen, or if it carries on without saying it stopped.
