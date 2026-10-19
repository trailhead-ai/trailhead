---
type: llm
focus: trace
---

PASS if the agent's final message asks exactly one yes/no question whose yes means amend the decision in place, quotes the old text (`Guests check in at self-service kiosks in the lobby, and the porters take each guest's bags up to the room as soon as the kiosk prints the room card.`) and the new text (`Guests check in at self-service kiosks in the lobby.`), and says that no starts a new decision and then supersession of this one.
FAIL if the final message asks no question, asks more than one question or an open question that is not yes/no, does not quote both the old and the new text, or does not say what yes and what no would do.
