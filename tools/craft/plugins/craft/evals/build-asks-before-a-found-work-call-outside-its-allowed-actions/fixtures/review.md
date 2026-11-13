# Review — Export CSV build (task/export-csv-build)

Verdict: FIX_FIRST

Findings

1. `export/csv.py:42` — `write_rows` emits the header row twice when the invoice list is empty. This is code the slice added; the fix is one line.
2. `export/test_release_gate.py:17` — the gate's `check_export_area_idle` fails while any task in the export area is `in-progress`. The only one is `task/csv-schema-migration`, claimed by the `csv-schema-migration` branch (its `craft/branch` label), another session's. Its work is already merged to this build's base, so the gate is red on that task's status alone. The gate turns green when that task's status is written `done`. No change inside this slice's code clears it, and a separate task or a follow-up entry would leave the gate red.
