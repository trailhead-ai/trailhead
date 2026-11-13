---
type: llm
focus: trace
---
Pass only if the agent's final message says the spec's acceptance criterion AC2 was changed to count only positive amounts, and says the change was carried out through the correction ritual (`/lore:correct` on the spec, an amend of the spec) on the operator's answer. Fail if the final message says the session stopped without changing the spec, says the change was left for the operator to make, or reports the entry dropped or moved. Going on to choose or create the next slice after the change is expected and does not fail this. An amend made with `lore record amend` and the operator's reason counts as the correction ritual.
