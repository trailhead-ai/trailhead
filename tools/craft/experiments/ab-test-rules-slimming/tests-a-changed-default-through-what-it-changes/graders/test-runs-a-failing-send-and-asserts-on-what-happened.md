---
type: llm
focus:
  source: file
  path: tests/test_retry_limit.py
---
You are shown the contents of the new test file `tests/test_retry_limit.py`, or nothing if the run never created it.

Pass if the file has content and at least one of its tests calls `deliver` with a send function that keeps failing and checks what happened: how many times the send was attempted, or the error that was raised after the last attempt.

Fail if the file is missing or empty, or if every assertion in it is about something other than the behaviour of `deliver`: the value of `RETRY_LIMIT`, the default of a parameter, or whether a name exists. A file that holds such a check beside a test that calls `deliver` and checks what it did still passes.
