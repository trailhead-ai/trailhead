# Task

The vault is at `$VAULT`.

We are standing up the Outpost daemon against this vault, and it reads its
per-vault working set from an `outpost/` directory at the root of the vault.
Nothing has created one here yet.

Set it up with a single work-stream declaration for glasswing ingest: name the
stream, mark it active, and point it at the `glasswing-board` site. A small
JSON or TOML file is fine — pick one and be consistent.
