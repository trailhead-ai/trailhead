# Re-review — Export invoices as CSV (task/export-invoices-as-csv)

Round 2 of 2, at the post-fix HEAD. Verdict: FIX_FIRST

Findings that survive the fixes

1. `spec/export-invoices` — the spec is missing a criterion. Invoices can be issued in more than one currency, and the spec says nothing about which currency an export's totals are written in, or whether the currency is shown. The exporter currently writes the bare number, so a EUR invoice and a USD invoice look alike. Which behaviour is right is a product decision the spec has to make; the code cannot settle it.
