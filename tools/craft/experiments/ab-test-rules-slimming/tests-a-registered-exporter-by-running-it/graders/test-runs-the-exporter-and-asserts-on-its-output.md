---
type: llm
target: files
---
You are shown the files the agent created in this run. Judge only the new test files, the ones under `tests/`; ignore every other file.

Pass if a new test file exists and at least one of its tests runs the TSV exporter, either through `export(rows, "tsv")` or by calling the exporter function directly, and checks the text it returns.

Fail if there is no new test file, or if every assertion in the new test files is about something other than the exporter's output: whether the name `tsv` is in the registry, whether a function or attribute exists or is callable, or the value of a constant. A file that holds such a check beside a test that runs the exporter and checks its output still passes.
