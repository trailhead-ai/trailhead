---
type: llm
focus: trace
---

Judge the plan the run presents in its final message.
PASS if its task list contains no migration or backfill task: no task that backfills, migrates or populates existing widgets' `tags` from their `category`.
FAIL if any task in the list does that work.
