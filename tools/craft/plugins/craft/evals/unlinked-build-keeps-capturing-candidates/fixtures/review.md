# Review — Export CSV build (task/export-csv-build)

Verdict: FIX_FIRST

Findings

1. `export/csv.py:42` — the CSV writer emits the header row twice when the invoice list is empty. This is code the slice added; the fix is one line in `write_rows`.
2. `export/csv.py:10` — the exporter reads every invoice through the legacy `InvoiceStore` interface, so the total column (AC2) needs the store's whole row model replaced: about 14 files and a change to the shared `InvoiceRow` contract, with broad test churn. The spec promises exports include the total, so this is in scope, but folding it into this slice would balloon it.
3. `billing/ledger.py:88` — unrelated to the export: the ledger rounding helper truncates instead of rounding half-up. It is a different subsystem and nothing this slice touched.
4. `export/` — the exporter will eventually need a streaming design for accounts with millions of invoices; there is no agreed approach yet and it spans storage, queueing and the API.
