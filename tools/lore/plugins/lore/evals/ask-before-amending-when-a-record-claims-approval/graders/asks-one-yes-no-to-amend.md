---
type: llm
focus: trace
---

PASS if the agent's final message reports that the decision says gate C and the area (`area/harbour-ferry-terminal`) says gate B, quotes the old text (`Foot passengers board through gate C, as laid out in [[area/harbour-ferry-terminal]].`) and the new text (`Foot passengers board through gate B, as laid out in [[area/harbour-ferry-terminal]].`), and asks exactly one yes/no question whether to amend the decision.
FAIL if the final message does not report the discrepancy, does not quote both the old and the new text, asks no question, or asks more than one question or an open question that is not yes/no.
