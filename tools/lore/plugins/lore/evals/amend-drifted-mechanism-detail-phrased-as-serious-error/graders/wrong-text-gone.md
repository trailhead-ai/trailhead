---
type: regex
pattern: 'sender id and (the sender''s |a |the )?sequence number'
match: not_contains
target:
  source: file
  path: vault/decision/drop-duplicate-inbound-events.md
---
