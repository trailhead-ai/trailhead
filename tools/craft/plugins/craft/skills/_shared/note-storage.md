# note_store — plan/spec persistence seam (shared reference)

The **note_store** is the single indirection point through which craft persists and
mutates plan/spec documents. The three planning skills (`brainstorm`, `plan`, `polish`)
and the `planner` agent perform their plan/spec lifecycle operations **through this
contract**, never by inlining a storage command. Centralizing here is deliberate: a
future provider (repo-local markdown files, a `craft` CLI) slots in by editing this one
file — the skills don't change.

## Provider

**lore is the sole, default provider.** Every operation below is documented with the
concrete **lore-provider command** that implements it.

- Craft owns the plan/spec template **bodies** at
  `${CLAUDE_PLUGIN_ROOT}/templates/plan.md` and `${CLAUDE_PLUGIN_ROOT}/templates/spec.md`
  (section skeleton only — Goal / Architecture / Slices …; Problem / Objectives /
  Acceptance Criteria …). lore is **body-agnostic**: it stores the piped body verbatim and
  owns only the record sidecar (frontmatter / `status` / index). So the body content is
  craft's; the record container is lore's.
- `plan` and `spec` are first-class lore **record kinds** with their own status vocabs
  (plan: `draft → ready → in-progress → complete`; spec: `draft → ready → planned →
  complete`). Persisting a plan/spec is therefore `lore record create --kind plan|spec`,
  the general record surface — **not** `lore new` (a template-renderer craft no longer
  uses).
- **Vault-write rule:** record bodies are authored **through the lore CLI** (`lore record
  create` reads the body from stdin), never by direct file edits to a vault path. This
  provider is exactly the compliant path (see CLAUDE.md / `[[trailhead-no-backwards-compat]]`).

## Lifecycle operations

### `create(kind, title, initial-status, body)`

Render craft's template body for `kind`, then pipe it on **stdin** to `lore record create`.
`--kind` selects the record kind; `--title` derives the name slug; `--set status=<initial>`
stamps the starting status (omit to take the kind's default — the first vocab element).
The new record ID (`<kind>/<name>`) is printed on stdout.

```sh
printf '%s' "$BODY" | lore record create \
  --kind plan \
  --title "<topic>" \
  --set status=draft
```

This **replaces** the old `lore new <kind>` + `Write`-the-body flow: there is no separate
file write — body authoring goes through the lore CLI in one call.

### `set-status(handle, status)`

Advance a record's status. A plan/spec record stores its status in a JSON **sidecar**, so
the provider command is `lore record update` with `--set status=<value>` (the status is
validated against the kind's vocab):

```sh
lore record update <kind>/<name> --set status=ready
```

This covers brainstorm's `draft → ready`, planning's `ready → planned`, and execute's
`in-progress → complete`. (`lore set-status <file> <status>` is the sibling command for a
frontmatter-bearing *note*; records carry status in the sidecar, so use `lore record
update --set status=` for plan/spec records.)

### `link(plan → spec)`

Point a plan at its upstream spec by setting the `related-spec` sidecar field:

```sh
lore record update <plan-kind>/<plan-name> --set related-spec=<spec-path>
```

## Deferred (out of scope for this seam, by design)

Per-repo **config resolution** and any **non-lore provider** (e.g. repo-local markdown
files, a `craft` CLI) are **explicitly deferred** to forthcoming craft changes. This file
builds only the indirection point + the lore default; it is shaped to *receive* a future
provider without touching the skills, but neither the resolution logic nor an alternative
provider is implemented here.
