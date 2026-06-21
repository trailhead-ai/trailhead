# Vault write pattern — shared reference

All vault writes go through the `lore` CLI — never edit vault files directly.
There are two capture surfaces:

## A persistent vault record — `lore record create`

For a durable, standalone record (a decision, lesson, area profile, spec, …):

1. **Gather fields** conversationally — only ask for what is missing; infer what you can.
2. **Run `lore record create`** with `--kind` and `--title`, plus any dedicated
   per-field flags (`--status`, repeatable `--keyword`/`--related-file`/
   `--related-url`/`--related-phase`, `--related KIND=NAME`) and routing flags
   (`--repo`/`--product`/`--suite`/`--team`):

   ```bash
   lore record create --kind <kind> --title "<title>" [--status S] [--keyword K …]
   ```

3. **Confirm the record** to the user. Run `lore record create --help` for the
   full flag set; related sub-actions are `lore record update|delete|blob`.

## A session-scoped marker — `lore session …`

For an item that belongs to the *active session* (a candidate to promote at
finish, or a reference to an existing record used this session):

```bash
printf '%s' "<the item, in your own words>" \
  | lore session candidate --kind <kind> --phase <phase>

lore session referenced <kind>/<record-name>
```

The session note is lazy-created on first capture, and the session id auto-resolves
from `$CLAUDE_CODE_SESSION_ID` — no note needs to exist beforehand.

## Vault path

The vault is resolved via `$LORE_VAULT` automatically. Pass `--vault "$LORE_VAULT"`
explicitly only when the env var is not set in the current shell.
