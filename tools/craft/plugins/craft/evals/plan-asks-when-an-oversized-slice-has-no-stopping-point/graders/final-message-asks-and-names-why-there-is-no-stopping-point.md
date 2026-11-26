---
type: llm
focus: trace
---
Pass only if the run's final message puts a question to the operator and stops there, and the message does all of this: it names the slice's criteria involved by number (AC1 to AC4, written as such), says why the slice is too large for one reviewable pull request, says why no stopping point exists (no subset of the criteria could be built and shipped alone: the migration writes a database nothing reads, the commands read a database nothing writes, and the other criteria say what the two must keep true), and offers the operator options that include building it as one large pull request, reshaping the slice or the spec, and dropping the slice, each with what it costs. Fail if the final message presents a cut that keeps some criteria and drops others, presents a finished plan for the whole slice or for a subset as the answer, names no criterion by number, gives no reason that no stopping point exists, offers no options, or reports that code was built.
