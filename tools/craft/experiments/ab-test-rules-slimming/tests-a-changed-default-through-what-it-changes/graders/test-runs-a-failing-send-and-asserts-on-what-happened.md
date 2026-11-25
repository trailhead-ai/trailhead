---
type: llm
target: files
---
You are shown the files the agent created in this run. Judge only the new test files, the ones under `tests/`; ignore every other file.

Pass if a new test file exists and at least one of its tests calls `deliver` with a send function that keeps failing and checks what happened: how many times the send was attempted, or the error that was raised after the last attempt.

Fail if there is no new test file, or if every assertion in the new test files is about something other than the behaviour of `deliver`: the value of `RETRY_LIMIT`, the default of a parameter, or whether a name exists. A file that holds such a check beside a test that calls `deliver` and checks what it did still passes.
