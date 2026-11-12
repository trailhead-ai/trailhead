# Core record pointers

A skill, agent or rule that points at a record in trailhead's bundled core vault
uses one form:

    trailhead core show <kind>/<name>

The command reads the record from the vault named `core` and prints it. It reads
only when that vault is trailhead's own copy (exactly one vault named `core`, at
trailhead's copy, read-only and unshared, with no other vault at, containing or
inside the copy). Otherwise it prints nothing from any vault. The same ownership
rule decides whether `trailhead install` registers the copy.

## Exit codes

| Code | Meaning | Streams |
|------|---------|---------|
| 0 | Owned, and the record was read | lore's stdout is relayed |
| 1 | Owned, but lore failed for any reason (record not in core, any other non-zero exit, a timeout) | lore's stderr is passed through; stdout empty |
| 2 | The id was refused before any lore call (not `<kind>/<name>` in lowercase letters, digits and `-`) | stderr names the refusal; stdout empty |
| 3 | The core vault is unavailable: not owned, lore missing, `lore vault ls` failed, malformed or timed out | one line, `trailhead: core vault unavailable: <reason>`, on stderr; stdout empty |

**Any non-zero exit means the inline one-line rule at the point of use still
binds.** A pointer never replaces that rule; it adds the detail.

A read made while `trailhead install` is swapping in a refreshed copy can briefly
return 1 (the record is momentarily absent). Retrying after the install finishes
returns the record.
