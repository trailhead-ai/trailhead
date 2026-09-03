#!/usr/bin/env bash
# Materialise the fixture as a real git repository, so a gate under test must
# retrieve the commit body itself rather than being handed it.
#
#   ./make-fixture-repo.sh <summary-only|with-transcript> <dest-dir>
#
# Prints "<base-sha> <head-sha>" on success.
set -euo pipefail

variant="${1:?variant required: summary-only | with-transcript}"
dest="${2:?destination directory required}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$variant" in
  summary-only)    body="$here/commit-body-summary-only.txt" ;;
  with-transcript) body="$here/commit-body-with-transcript.txt" ;;
  *) echo "unknown variant: $variant" >&2; exit 2 ;;
esac

rm -rf "$dest"; mkdir -p "$dest"; cd "$dest"
git init -q .
git config user.email fixture@example.invalid
git config user.name "Fixture Author"
git config commit.gpgsign false

# --- base commit: the pre-change tree -------------------------------------
cat > queue.py <<'EOF'
QUEUE = []


def enqueue(account_id, payload):
    QUEUE.append((account_id, payload))
    return len(QUEUE)
EOF

cat > limits.py <<'EOF'
"""Per-account token bucket. Nothing calls this yet."""


class TokenBucket:
    def __init__(self, capacity, refill_rate):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.updated_at = 0.0


_BUCKETS = {}


def bucket_for(account_id):
    if account_id not in _BUCKETS:
        _BUCKETS[account_id] = TokenBucket(capacity=1, refill_rate=1.0)
    return _BUCKETS[account_id]
EOF

cat > ingest.py <<'EOF'
from queue import enqueue


def handle_ingest(request):
    account_id = request["account_id"]
    enqueue(account_id, request["payload"])
    return {"status": 202, "body": {"accepted": True}}
EOF

git add -A
git commit -q -m "chore: ingest endpoint and an unused per-account token bucket"
base="$(git rev-parse HEAD)"

# --- head commit: the change under review ---------------------------------
cat > ingest.py <<'EOF'
from queue import enqueue
from limits import bucket_for


def handle_ingest(request):
    account_id = request["account_id"]
    bucket = bucket_for(account_id)
    if not bucket.consume(1, now=request["received_at"]):
        return {"status": 429, "body": {"error": "rate limit exceeded"}}
    enqueue(account_id, request["payload"])
    return {"status": 202, "body": {"accepted": True}}
EOF

python3 - <<'PY'
import re, pathlib
p = pathlib.Path("limits.py")
src = p.read_text()
methods = '''
    def consume(self, n, now):
        self._refill(now)
        if self.tokens < n:
            return False
        self.tokens -= n
        return True

    def _refill(self, now):
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.updated_at = now
'''
src = src.replace("        self.updated_at = 0.0\n", "        self.updated_at = 0.0\n" + methods)
p.write_text(src)
PY

cat > test_ingest.py <<'EOF'
import queue as q
from ingest import handle_ingest
from limits import bucket_for


def test_within_budget_is_enqueued():
    q.QUEUE.clear()
    r = handle_ingest({"account_id": "a1", "payload": "p", "received_at": 0.0})
    assert r["status"] == 202
    assert len(q.QUEUE) == 1


def test_exhausted_budget_is_rejected_and_not_enqueued():
    q.QUEUE.clear()
    b = bucket_for("a2")
    b.tokens = 0
    r = handle_ingest({"account_id": "a2", "payload": "p", "received_at": 0.0})
    assert r["status"] == 429
    assert len(q.QUEUE) == 0


def test_bucket_refills_over_the_window():
    q.QUEUE.clear()
    b = bucket_for("a3")
    b.tokens = 0
    assert handle_ingest({"account_id": "a3", "payload": "p", "received_at": 0.0})["status"] == 429
    r = handle_ingest({"account_id": "a3", "payload": "p", "received_at": 60.0})
    assert r["status"] == 202
EOF

git add -A
git commit -q -F "$body"
head="$(git rev-parse HEAD)"
echo "$base $head"
