# task/reject-bursts-above-the-per-account-rate-limit

status: ready

The ingest endpoint accepts unbounded request volume from a single account. One account's
retry storm has twice saturated the worker pool and degraded ingest for every other account.
A per-account token bucket exists in `limits.py` but nothing calls it.

**Delivers:** The ingest endpoint rejects a request from an account that has exhausted its
per-account token bucket, with HTTP 429, and does not enqueue the request.

**Test contract:**

- An account within its budget has its request enqueued and receives 202.
- An account that has exhausted its budget receives 429 and nothing is enqueued.
- The bucket refills over elapsed time, so an account rejected at T is accepted at T+window.

**Files:** `ingest.py`, `limits.py`
