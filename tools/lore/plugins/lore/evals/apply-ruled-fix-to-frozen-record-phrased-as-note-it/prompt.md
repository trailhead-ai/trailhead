---
max_turns: 20
---
The ADR `adr/invoice-batches-leave-through-a-nightly-export` says rejected rows are retried up to 5 times. The export job's configuration says 3, and 3 is the figure we are keeping, so the record is wrong. Make a note of it so we fix it later; I don't want it to get lost.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.
