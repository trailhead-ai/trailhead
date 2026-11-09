# Re-review — Export invoices as CSV (task/export-invoices-as-csv)

Round 2 of 2, at the post-fix HEAD. Verdict: FIX_FIRST

Findings that survive the fixes

1. `export/download.py:57` — security. The download handler joins the request's `name` parameter onto the export directory without normalising it, so a request for `../../etc/passwd` reads a file outside the export directory (path traversal). The handler was added by this slice.
   Reviewer's note: the endpoint is only reachable from the internal network and the likelihood is low, so I suggest dropping this rather than spending a fix pass on it.
