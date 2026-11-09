# Re-review — Export invoices as CSV (task/export-invoices-as-csv)

Round 2 of 2, at the post-fix HEAD. Verdict: FIX_FIRST

Findings that survive the fixes

1. `export/csv.py:42` — `write_rows` writes the header row twice when the invoice list is empty. This is code the slice added; the fix is one line and a test.
2. `billing/ledger.py:88` — unrelated to the export: the ledger's rounding helper truncates instead of rounding half-up. It belongs to the billing subsystem and nothing this slice touched calls it.
