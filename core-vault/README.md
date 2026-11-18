# core-vault

This folder is trailhead's bundled doctrine vault. It is edited by hand and
reviewed like source, and lore never writes to it.

Install copies it into a directory trailhead owns, and that copy is what gets
registered as a vault. Edits here reach the copy the next time install runs.

Records here cite neither commit IDs nor file fingerprints, so a 7-to-40-character hex string fails `scripts/core-content-gate`.

Lore search matches the words of a record's title and opening text, and never its id, so every word of a record's slug must appear in its title or opening text, or the record cannot be found by its name. The seam test fails a record that breaks this.

It holds trailhead's testing doctrine as lore records, one folder per kind
(for example `adr/`), each a `.md` body with a `.json` sidecar.

A record id such as `adr/a-test-executes-its-subject` is a public pointer that
skills and rules cite. Renaming one breaks those pointers, so a rename is a
successor record, not an edit.

`trailhead core show <kind>/<name>` is the canonical way to read a record. A
non-zero exit (3 means the core vault is unavailable) leaves the inline
one-line rule at the point of use binding; see `docs/core-record-pointers.md`.
