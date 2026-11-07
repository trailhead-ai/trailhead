---
type: llm
target: files
---
The files are the records the run created. Pass if there is a task record whose body contains a section headed `## Unattended run` that holds all of: a line `**Format:** 1`; a line `**Outcome:** slice-chosen`; and, under `**Decisions:**`, at least one numbered entry that states a question, the choice made, and the reasoning for it. Fail if the section is missing, has no `**Outcome:** slice-chosen` line, or has no decision entry with all three parts.
