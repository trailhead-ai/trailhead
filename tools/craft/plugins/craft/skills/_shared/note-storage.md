# Persisting plans & specs via the lore CLI (shared reference)

Persist plans and specs through the `lore` CLI. The planning skills (`brainstorm`, `plan`, `polish`)
and the `planner` agent use the commands below for their plan/spec lifecycle operations.

- Craft owns the template **bodies** at `${CLAUDE_PLUGIN_ROOT}/templates/plan.md`,
  `${CLAUDE_PLUGIN_ROOT}/templates/task.md`, and `${CLAUDE_PLUGIN_ROOT}/templates/spec.md` (section
  skeletons only — parent task: Goal / Delta design / Given Axioms / Known Unknowns / `## Flow-out`;
  child task: Delivers / Test contract / Files; spec: Problem / Objectives / Acceptance Criteria …).
  Render the body, then pipe it to `lore record create`.
- A **plan is a `task` record graph**, not a single document. The parent plan is one `task` record
  (rendered from `${CLAUDE_PLUGIN_ROOT}/templates/plan.md`); each task is a child `task` record
  (rendered from `${CLAUDE_PLUGIN_ROOT}/templates/task.md`) wired to the parent with `--parent` and
  ordered against its siblings with `--depends-on`. A **spec** is a `spec` record. `task` and `spec`
  each carry their own status vocab (task: `open → ready → in-progress → done`, off-path `blocked` /
  `dropped` / `superseded`; spec: `draft → ready → complete`, with `planned` retained in the
  vocabulary but no longer written). Persisting a plan or spec is
  `lore record create --kind task|spec` — **not** `lore new`.
- **Vault-write rule:** record bodies are authored **through the lore CLI** (`lore record create`
  reads the body from stdin), never by direct file edits to a vault path.

## Lifecycle operations

### `create(kind, title, initial-status, body)`

Render craft's template body for `kind`, then pipe it on **stdin** to `lore record create`. `--kind`
selects the record kind; `--title` derives the name slug; `--status <initial>` stamps the starting
status (omit to take the kind's default — the first vocab element). The new record ID
(`<kind>/<name>`) is printed on stdout.

```sh
printf '%s' "$BODY" | lore record create \
  --kind task \
  --title "<topic>" \
  --status ready
```

### `graph(parent, children)`

A plan is a parent task plus its child tasks. Create the parent first, then create each child with
`--parent <parent-name>` (the containment edge) and, where a task depends on an earlier one,
`--depends-on <sibling-name>` (the ordering edge). Both flags are `task`-only.

```sh
# parent
printf '%s' "$PARENT_BODY" | lore record create \
  --kind task --title "<plan topic>" --status ready
# child task, contained by the parent and ordered after an earlier task
printf '%s' "$CHILD_BODY" | lore record create \
  --kind task --title "<task topic>" --status ready \
  --parent <parent-name> --depends-on <earlier-task-name>
```

Render the resulting graph — containment subtree, `depends-on` edges, per-task status, and runnable
markers on workable leaves — with `lore task graph <parent-name>`.

### `status(handle, status)`

Advance a record's status with `lore record update` and the `--status <value>` flag (the status is
validated against the kind's vocab):

```sh
lore record update task/<name> --status in-progress
```

This covers the parent plan task's `ready → in-progress → done` lifecycle. Child tasks under a plan
walk `ready → done` — `in-progress` is the run's claim on the parent, not a per-task value. Setting
a parent `--status done` while it still has non-terminal children is refused (the children are named
in the error); a parent completed without a `## Flow-out` section gets a non-blocking flow-out
reminder.

Under `execute`'s task-status contract, the parent's `ready → in-progress` write happens at the
first dispatch, paired in the same command with `--label craft/branch=<bare-branch>` — that write
belongs to the orchestrating session, not to these skills, which write only the `open → ready`
promotion on the records they create. Ownership of every other status value belongs to a separate
contract they never need to open.

### `link(plan → spec)`

Point the parent plan task at its upstream spec by adding the spec to the task's `related` map under
the `spec` kind (the `--related <kind>=<name>` flag, which appends to that kind's list):

```sh
lore record update task/<parent-name> --related spec=<spec-name>
```
