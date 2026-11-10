---
allowed_tools: [Read, Glob, Grep, Agent]
max_turns: 20
timeout_seconds: 420
---
I want only the Reliability lens of the council on the spec in `spec.md`. The council's instructions are in `council.md`, and the maturity calibration block for this spec is in `calibration.md`.

Read all three. Fill `council.md`'s "Prompt template" for the Reliability member, exactly as `council.md` says to fill it for a spec review: the spec in `spec.md` is the material under review, and the contents of `calibration.md` are the calibration block. Dispatch the `craft:breaker` agent with that filled prompt, then give me its findings verbatim.
