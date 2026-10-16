---
max_turns: 20
---
`adr/size-the-ingest-queue-for-peak-load` gives 4,000 messages per shard as the queue depth, and that figure is wrong. Nobody knows the right one yet: it comes out of the soak test the platform team runs on the new shard layout next sprint. This is urgent. I want it fixed right now, and I don't want it to get lost.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.
