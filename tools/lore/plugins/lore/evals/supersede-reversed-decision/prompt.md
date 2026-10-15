---
max_turns: 20
---
We have turned `decision/keep-attachments-inside-the-record-row` around. From now on attachments live in a separate object store and are no longer stored inline in the record row. Make the vault reflect that.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.
