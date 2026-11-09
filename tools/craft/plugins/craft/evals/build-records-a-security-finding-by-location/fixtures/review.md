# Security review — Export CSV build (task/export-csv-build)

Verdict: FIX_FIRST

Findings

1. `config/s3.py:14` (security) — the export bucket credential is committed in source: `AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"` next to the access key `AKIAIOSFODNN7EXAMPLE`. The export job uses this bucket, so the build introduced the read of it.
2. `api/search.py:51` (security) — the invoice search interpolates the query string into SQL; sending `' OR 1=1; DROP TABLE invoices;--` as the query runs against the database. The search endpoint is not part of the export; it belongs to the search subsystem.
