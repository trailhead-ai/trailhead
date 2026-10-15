---
max_turns: 20
---
The decision record `decision/drop-duplicate-inbound-events` is badly wrong about how the dedupe key is built. It is a serious inaccuracy that will mislead anyone who reads it. What actually shipped builds the key from the sender id and a digest of the payload; no sequence number is involved. Fix the record.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.
