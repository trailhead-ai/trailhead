# Persisting plans & specs via the lore CLI (shared reference)

Persist plans and specs through the `lore` CLI. The planning skills (`brainstorm`,
`plan`, `polish`) and the `planner` agent use the commands below for their plan/spec
lifecycle operations.

- Craft owns the plan/spec template **bodies** at
  `${CLAUDE_PLUGIN_ROOT}/templates/plan.md` and `${CLAUDE_PLUGIN_ROOT}/templates/spec.md`
  (section skeleton only — Goal / Architecture / Slices …; Problem / Objectives /
  Acceptance Criteria …). Render the body, then pipe it to `lore record create`.
- `plan` and `spec` are lore **record kinds** with their own status vocabs
  (plan: `draft → ready → in-progress → complete`; spec: `draft → ready → planned →
  complete`). Persisting a plan/spec is `lore record create --kind plan|spec` — **not**
  `lore new`.
- **Vault-write rule:** record bodies are authored **through the lore CLI** (`lore record
  create` reads the body from stdin), never by direct file edits to a vault path.

## Lifecycle operations

### `create(kind, title, initial-status, body)`

Render craft's template body for `kind`, then pipe it on **stdin** to `lore record create`.
`--kind` selects the record kind; `--title` derives the name slug; `--status <initial>`
stamps the starting status (omit to take the kind's default — the first vocab element).
The new record ID (`<kind>/<name>`) is printed on stdout.

```sh
printf '%s' "$BODY" | lore record create \
  --kind plan \
  --title "<topic>" \
  --status draft
```

### `status(handle, status)`

Advance a record's status with `lore record update` and the `--status <value>` flag (the
status is validated against the kind's vocab):

```sh
lore record update <kind>/<name> --status ready
```

This covers brainstorm's `draft → ready`, planning's `ready → planned`, and execute's
`in-progress → complete`.

### `link(plan → spec)`

Point a plan at its upstream spec by adding the spec to the plan's `related` map under the
`spec` kind (the `--related <kind>=<name>` flag, which appends to that kind's list):

```sh
lore record update <plan-kind>/<plan-name> --related spec=<spec-name>
```
