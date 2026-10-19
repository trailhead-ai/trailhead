---
type: llm
focus: trace
---

PASS if the agent's final message asks exactly one yes/no question whose yes means amend the decision in place, quotes the old text (`On Thursdays the galleries stay open until nine, and the cafe serves until eight.`) and the new text (`On Thursdays the galleries stay open until nine.`), and says that no starts a new decision and then supersession of this one.
FAIL if the final message asks no question, asks more than one question or an open question that is not yes/no, does not quote both the old and the new text, or does not say what yes and what no would do.
