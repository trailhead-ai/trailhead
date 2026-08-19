# `lore pipeline` — the cross-vault board of in-flight design work

```
lore pipeline [--vault NAME ...] [--json]
```

Read-only. Nothing is written, nothing is claimed, and the index is never
touched. The board is derived from the configured vaults' record sidecars on
every invocation and stored nowhere, so it is never stale and never needs a
reindex.

## What is on the board

One lineage per in-flight design root:

- an `adr` together with the non-terminal `spec` records whose own
  `related: adr=` edges point at it — the lineage renders while the root is
  `draft`, or while at least one non-terminal spec still points at it;
- a `spec` whose `related: adr=` edge resolves to nothing, as a single-record
  lineage flagged `unresolved-root`;
- an `open` `task` carrying `route=brainstorm`, as a single-record lineage
  flagged `routed-task`.

A terminal spec is omitted from its lineage's rendered members. `complete` and
`superseded` still count toward the lineage's `completed_count`; `dropped` does
not.

Lineages split into two tiers. A lineage whose **root** carries a `priority`
label joins the `priority` tier, integer values ascending and any other value
after them; every other lineage joins the `recency` tier, newest `updated-at`
first. A root in a `shared: true` vault has its `priority` label ignored for
tiering — the human view marks it `(ignored: shared)` in place.

## Vault set

The vaults walked and the set of `shared: true` names both come from a single
read of `config.json`. A missing `config.json` is a vanilla install: the board
renders over the default floor vault. Camp-group layer resolution is a
different notion of "shared" and is deliberately not on this path.

`--vault NAME` (repeatable) restricts the walk to named configured vaults. An
unknown name is refused before any vault directory is opened.

## Reads

Only `<vault>/{adr,spec,task}/*.json` sidecars are read — never the `.md` body
beside them. That is a correctness requirement: a vault is a git working tree
that a background sync can update mid-walk, and a record read as a
body/sidecar pair can be torn across that update. One sidecar is one
atomically-written file, so every record on the board is a coherent snapshot.

Each sidecar is read exactly once per invocation. The same mapping feeds both
the lineage derivation and the dependency evaluation.

## Dependency gating

A `spec` or `adr` record's `depends-on` entries are evaluated against the
design records of **the vault the record itself came from**. Each entry
projects one object, in stored order, duplicates included:

| key | meaning |
| --- | --- |
| `kind` | the target's kind (`spec` / `adr`), or `null` when unresolvable |
| `name` | the target's name, or `null` when the entry could not be split |
| `stage` | the `@stage` qualifier the entry asked for, or `null` |
| `met` | `true`, `false`, or `null` (unevaluated — see below) |
| `reason` | always a non-empty human-readable explanation |
| `reason_code` | `null`, `missing`, `short-of-stage`, or `target-failed` |

A record with any unmet entry is flagged `gated` and **still appears in its
lineage**, with the reason beside it. Gating is never a silent exclusion: a
dependency the operator cannot see is one they cannot act on.

A `task` record's `depends-on` is a different grammar — bare task names, not
qualified `kind/name[@stage]` ids — and is not evaluated here. Its entries
project with `met: null` and a reason saying so, and a routed task is never
flagged `gated`.

### `flags` is the authoritative gating signal

`met` is per-entry evaluator detail and is three-valued. A consumer that tests
it for falsiness reads a routed task's `met: null` as blocked when nothing is
blocking it. Branch on the record's `flags` array instead. It is drawn from a
closed vocabulary:

- `orphaned-seed` — a live spec whose root adr is `dropped` or `superseded`
- `unresolved-root` — the record's `related: adr=` edge resolved to nothing
- `routed-task` — an `open` task on the brainstorm route
- `gated` — at least one `depends-on` entry is unmet

## Own-vault confinement

Both the `related: adr=` edge resolution and the dependency evaluation are
confined to the vault the record came from, and no mapping spanning two vaults
is ever built. Two vaults holding a record of the same name anchor two
lineages that never see each other, and a dependency satisfied only in another
vault reads `missing`. This is a security property, not a convenience: without
it a record in a shared vault could decide what a personal vault's board shows,
including whether a record renders as blocked.

## Shared-vault content is fenced on every output path

Content from a `shared: true` vault is untrusted input on its way into an
agent's context, and record titles, labels, edge values, filename stems and
dependency reason strings are all authored there.

- Human mode splices each shared vault's block into an `<external-memory>`
  data channel, which entity-escapes what it wraps. Every vault-authored value
  on a line is additionally neutralized, so no stem or title can forge a line
  break or emit a terminal escape sequence.
- `--json` entity-escapes the same fields itself and marks every projected
  record and warning with `layer: "shared"`. The escaping is for the
  downstream consumer that renders parsed values into its own context —
  keeping the JSON document well-formed is not the same protection.

## `--json` envelope

```json
{
  "schema": 1,
  "vaults":   [ { "name": …, "shared": …, "record_count": …, "error": … } ],
  "warnings": [ { "vault": …, "layer": …, "file": …, "message": … } ],
  "tiers": {
    "priority": [ lineage, … ],
    "recency":  [ lineage, … ]
  }
}
```

A lineage is `{ "id", "root", "members", "completed_count" }`, where `id` is
`<vault>:<root record id>` — the vault qualifier is always present, singletons
included, so a lineage id is unique across the whole board.

A record is `{ "id", "vault", "layer", "kind", "title", "status",
"updated-at", "labels", "related", "flags", "depends-on" }`.

Envelope and lineage keys are snake_case; per-record keys keep their sidecar
spelling, so they are kebab-case (`updated-at`, `depends-on`). Pin on
`schema`: it changes only when a released key changes meaning or disappears.

A vault present in `vaults` with `record_count: 0` and `error: null` was
consulted and held nothing — which is not the same fact as a vault absent from
the list, which was not consulted at all.

## Exit codes and partial reads

Nonzero means the board could not be derived at all, never that some part of
it is missing:

| exit | when |
| --- | --- |
| `0` | the board rendered, possibly degraded |
| `1` | `config.json` is present but unparseable |
| `1` | an unknown `--vault` name was given |
| `1` | no configured vault could be read at all |

**A zero exit does not mean the board is complete.** A vault that could not be
read degrades the board rather than blanking it, and a record whose evaluation
failed is dropped from membership with a warning rather than aborting the
command. Before trusting `tiers`, a consumer must inspect every `vaults[]`
entry's `error` field and read `warnings`. This is the deliberate trade: one
broken vault must not cost the operator the others.
