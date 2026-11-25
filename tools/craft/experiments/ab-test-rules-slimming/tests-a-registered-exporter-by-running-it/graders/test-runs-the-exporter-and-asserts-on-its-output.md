---
type: llm
focus:
  source: file
  path: tests/test_tsv_export.py
---
You are shown the contents of the new test file `tests/test_tsv_export.py`, or nothing if the run never created it.

Pass if the file has content and at least one of its tests runs the TSV exporter, either through `export(rows, "tsv")` or by calling the exporter function directly, and checks the text it returns.

Fail if the file is missing or empty, or if every assertion in it is about something other than the exporter's output: whether the name `tsv` is in the registry, whether a function or attribute exists or is callable, or the value of a constant. A file that holds such a check beside a test that runs the exporter and checks its output still passes.
