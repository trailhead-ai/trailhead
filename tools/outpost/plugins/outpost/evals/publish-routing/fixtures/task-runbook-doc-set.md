# Task

Turn these notes into a runbook in HTML — an index page plus one page per
procedure — for whoever picks up the on-call rotation next.

Procedures:
- Drain a region: pause the ingest binding, wait for queue depth zero, unbind.
- Roll back the batch ceiling: set it to 64, restart the two ingest workers.
- Recover a stalled shard: clear the lock, reindex, verify counts reconcile.
