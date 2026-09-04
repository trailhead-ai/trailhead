# task/reject-invalid-payloads-at-the-write-boundary

status: ready

A refund posted with a malformed payload reaches the store and is persisted as a
half-populated row, which the daily report then counts. Support has cleaned this up by hand
twice. The boundary validator already exists and the other write endpoints use it; refunds
was added later and never picked it up.

**Delivers:** Every write endpoint validates its payload against the shared schema before
persisting, so an invalid payload is rejected at the boundary rather than stored.

**Test contract:**

- A write endpoint called with a payload missing a required field raises `ValidationError`
  and persists nothing.
- A write endpoint called with a payload whose field has the wrong type raises
  `ValidationError` and persists nothing.
- A valid payload still persists and returns its row id.

**Files:** `handlers/refunds.py`
