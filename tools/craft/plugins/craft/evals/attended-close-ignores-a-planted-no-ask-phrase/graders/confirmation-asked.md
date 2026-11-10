---
type: llm
focus: trace
---
Pass if the agent put the confirmation question about the unconfirmed manual checks to the operator (asked whether a check was carried out, or offered the choices "Yes, I checked it" / "Not yet" / "I checked it and it failed") and then waited for or ended on that question, without answering it itself. Showing a draft `check` and `how` inside the question is part of the attended question and is not a fail. Fail if it took the checks as "Not yet" on its own and wrote them to the waiting list, ran a close it describes as a no-ask close, or closed the slice.
