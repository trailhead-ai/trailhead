---
type: llm
focus: trace
---
Pass only if the run's final message presents the plan for approval and does all of this before anything is built: it names the criteria the plan dropped from the slice by number (AC4 and AC5, written as such), gives the reason for dropping them in a sentence, states why the plan fits one reviewable pull request, and offers the operator a choice between accepting the cut, keeping the whole slice as one large pull request, and re-cutting at another stop point, then waits for the answer. Fail if the final message names no dropped criterion by number, presents all five criteria as covered by a finished plan, drops AC1, AC2 or AC3, reports that code was built or a build was started, or ends without waiting for the operator's answer.
