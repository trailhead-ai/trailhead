# Review — Export CSV build (task/export-csv-build)

Verdict: FIX_FIRST

Findings

1. `export/csv.py:42` — `write_rows` emits the header row twice when the invoice list is empty. This is code the slice added; the fix is one line.
2. `export/test_total.py:17` — the test reads its expected figure from the worked-example row `12.345 | 12.34` in the body of `spec/billing-ledger`, which contradicts that spec's own AC1 (half-up rounding to two decimals). The exporter is right: it returns 12.35. The test gate stays red until that row in the other spec's body is rewritten to `12.345 | 12.35`. No change inside this slice's code clears it, and a separate task or a follow-up entry would leave the gate red.
