# Vault write pattern — shared reference

All capture skills follow this pattern:

1. **Gather fields** conversationally — only ask for what is missing; infer what you can.
2. **Run `lore new <type>`** with `--title` and any other flags to render the template and write the note.
3. **Open the written file** and fill in the body sections with the user's answers using the Edit tool.
4. **Confirm the note path** to the user.

## No-session fallback

When no active session note is found, `lore new` prints a skip notice to stderr and exits 0. This is not an error — continue normally.

## Inferring project

If the user hasn't stated a project name, run:

```bash
git remote get-url origin
```

and extract the repo name (last path segment, without `.git`). Pass it as `--project`.

## Vault path

The vault is resolved via `$LORE_VAULT` automatically. Pass `--vault "$LORE_VAULT"` explicitly only when the env var is not set in the current shell.
