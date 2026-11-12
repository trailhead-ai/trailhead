# Review — Export CSV build (task/export-csv-build)

Verdict: FIX_FIRST

Findings

1. `export/csv.py:42` — `write_rows` emits the header row twice when the invoice list is empty. This is code the slice added; the fix is one line.
2. `export/dispatch.py:31` — a retried dispatch created a second task record, `task/export-csv-build-copy`, a duplicate of this build's parent. It carries `**Covers:** AC1` and built nothing. The completion ledger and the final sweep credit AC1 to whichever record they read first, so the parent would close with AC1 credited to a task that built nothing. Nothing links to the copy, so no other record changes if it goes. A separate task or a follow-up entry would leave the copy where it is and the credit unchanged; nothing short of removing the record clears it, and the parent cannot close correctly until it is cleared.
