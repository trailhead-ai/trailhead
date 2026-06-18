# lore promote — sharing notes to a shared vault

## Personal by default, sharing is deliberate

lore's memory model has two layers:

- **Personal vault** (`$LORE_VAULT`): always present, always private, always trusted.
  All `lore new …` commands write here by default.
- **Shared vault(s)**: declared in your group's camp config (`[[shared_vaults]]`). Content
  here is visible to everyone with access to that vault's repository.

Capture targets the personal layer by default. `lore new decision …` always writes to
your personal vault — no flag required, no config needed.

## `lore promote` — the only path into a shared vault

To share a personal note into a shared vault, use `lore promote`:

```
lore promote <path/to/note.md>
lore promote <path/to/note.md> --to <layer-name>   # when multiple shared vaults exist
```

`lore promote` is **interactive-only**. It must be run at an interactive terminal by a
human. Automated agents (scripts, CI, subprocesses with piped stdin) are refused before
any write occurs. This is the privacy boundary: sharing is a deliberate human act, not
an automated one.

The flow:
1. Prints a preview (source path, exact destination, a `WARNING: writing to SHARED vault`
   line at the point of write).
2. Prompts for confirmation (`y/N`).
3. On `y`: copies the note to the shared vault root (the personal original is never
   deleted — promote is always a copy, never a move).
4. On `n` or EOF: exits cleanly, nothing written.

`--yes` is refused. There is no non-interactive promote path by design.

## Shared vault repos: default private

A shared vault is a git repository. The privacy of that repository determines who can
read its contents.

**Making a shared vault's repository public world-exposes every note it contains** —
including decisions, lessons, dead-ends, and any other notes that have been promoted
there. Team decisions, architectural choices, and institutional knowledge become publicly
readable.

Default stance: **keep shared vault repositories private**. Only make one public if the
team has explicitly decided the contents are safe to expose publicly.

## D-6: the always-loaded area menu stays personal-scoped

The `lore areas` menu (pointed at from SessionStart) is personal-vault-scoped only. It
lists area names and keywords from your personal vault.

Shared-layer content surfaces **only on an explicit `lore search` query** — it does not
appear in the always-loaded pointer. This means:

- The always-on pointer is always trusted (self-authored) content.
- Shared-layer content (which may be authored by others and is structurally delimited
  as a data channel) only enters context when you explicitly query for it via `lore search`.

If you want to pull memory for a shared-layer area, run:

```
lore search 'area:<area-name>'
```

Shared hits appear wrapped in `<external-memory layer="shared" source="<vault>">` in
the search output — the structural signal that this content is reference data from
others, not your personal self-authored notes.

## Declaring a shared vault

Add a `[[shared_vaults]]` block to your group's camp config:

```toml
[[shared_vaults]]
name = "team"
root = "/path/to/team-vault"
```

With no `[[shared_vaults]]` block declared, lore degrades silently to single-personal-vault
behavior — identical to the pre-shared-vaults behavior. No shared framing appears in
recall output; `lore promote` prints an actionable error telling you where to add the block.
