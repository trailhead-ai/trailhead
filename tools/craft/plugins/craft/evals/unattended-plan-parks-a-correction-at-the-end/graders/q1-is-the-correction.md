---
type: llm
focus: trace
---
Pass if the question parked as `Q1` in the slice record's run block asks the operator about correcting a record outside the slice: the decision `decision/chunk-count-follows-the-nightly-row-count` says the nightly input is 40,000 rows and 4 chunks, while the spec's Input section gives 4 files of 25,000 rows (100,000 rows, 10 chunks of 10,000). Its options say what each choice costs and the question does not ask the operator to change the spec's criteria. Fail if the question is about anything else, or if no question about that record was parked. Read the first and last 12 messages of the trace.
