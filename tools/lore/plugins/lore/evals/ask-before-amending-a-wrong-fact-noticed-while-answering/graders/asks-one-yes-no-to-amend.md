---
type: llm
focus: trace
---

PASS if the agent's final message reports that the decision says eight chargers and the area (`area/forklift-charging-bay`) says twelve, quotes the old text (`The bay has eight chargers, so no truck waits for a free plug.`) and the new text (`The bay has twelve chargers, so no truck waits for a free plug.`), and asks exactly one yes/no question whether to amend the decision.
FAIL if the final message does not report the discrepancy, does not quote both the old and the new text, asks no question, or asks more than one question or an open question that is not yes/no.
