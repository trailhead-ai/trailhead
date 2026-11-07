# Show the check list across every vault, read-only

The rendered surface of `/craft:checks`: one terminal view of every manual check and set-aside
criterion waiting on the operator, across every vault lore is configured with. This view only
reads. Answering an entry (passed, failed, released, skip) is a later surface, and nothing here
writes to a vault.

```
/craft:checks          # the skill; it runs the gather script and prints its output unchanged
checklist_gather.py    # the text view (default)
checklist_gather.py --json   # the same gather as one JSON object, for the walk-through to read
```

## What the view is built from

The check list is every waiting entry whose latest history line reads `waiting`, on every spec
with status `ready`, in every vault `lore vault ls` lists. Specs are found with
`lore search --json 'kind:spec status:ready'`, and each is read with
`lore record show --json --vault <vault> spec/<name>`. Each spec's `## Waiting` section is read by
the one waiting-section parser that the section gate, slice's candidate set and the loop-end test
will also use.

Every piece of text shown comes from a vault and is untrusted: the criterion's own words, the spec
name, `check`, `how`, `waits on`, and history words. Before display, each has control characters
and invisible format characters removed, is truncated with a trailing `…` past a fixed bound, and
has credential-shaped spans replaced with `[REDACTED]`. A vault or spec name that fails the
safe-value shape `^[A-Za-z0-9._/-]+$` is never used to build a command. The view names it as
unreadable instead.

## Layout, top to bottom

1. **The count line.** Always first, always one line.
2. **What could not be read.** One `Could not read:` line per vault or spec the gather could not
   read, each with a short reason. The reasons come from the gather's own vocabulary, never from
   lore's output.
3. **The entries.** One block per waiting entry, separated by a blank line, numbered `[n/total]`.

Entries are ordered by vault, then spec, then the date of the entry's latest history line, oldest
first. Putting checks on slices that changed existing behaviour ahead of the rest is a later
surface.

## State — Nothing waiting

Every spec was read and none holds a waiting entry. The whole output is one line:

```
Nothing is waiting on you.
```

## State — One entry waiting

The count line uses the singular, and one entry block follows. A pending check shows what to check
and how, and whether its slice changed existing behaviour:

```
1 check waiting, in 1 spec.

[1/1] Checkout collects billing details and invoices carry tax. (AC32)
  spec: levr/billing-and-entitlements-launch
  built by: task/an-owner-subscribes-with-tax-calculated-on-their-invoices
  set: 2026-11-02
  check: Checkout asks for a billing address and tax id, and the invoice shows tax
  how: run a test-mode checkout from the Billing page with a UK address
  changed existing behaviour: no
```

A set-aside criterion shows what it waits on in place of check, how and changed behaviour:

```
1 set-aside criterion waiting, in 1 spec.

[1/1] Invoices carry the tax registration number. (AC17)
  spec: levr/billing-and-entitlements-launch
  set: 2026-11-03
  waits on: the Stripe Tax head-office registration from business setup
```

## State — Many entries across specs and vaults

The count line totals each kind and counts the specs. A kind with no entries is left out of the
line rather than shown as zero:

```
4 checks and 1 set-aside criterion waiting, across 2 specs.

[1/5] Checkout collects billing details and invoices carry tax. (AC32)
  spec: levr/billing-and-entitlements-launch
  …

[2/5] …
```

Each block names its spec as `<vault>/<spec-name>`, so entries from different vaults stay
distinguishable even when two vaults hold specs of the same name.

## State — A vault or spec cannot be read

A vault or spec the gather could not read is named and never left out. Every other vault and spec
is still shown. The `Could not read:` lines come straight after the count line:

```
2 checks waiting, in 1 spec.
Could not read: trailhead/some-spec — its waiting section could not be parsed
Could not read: vault scratch — its name is not a safe value

[1/2] …
```

When nothing readable is waiting but something could not be read, the count line must not claim
that nothing is waiting:

```
Nothing waiting in the specs that could be read.
Could not read: levr/billing-and-entitlements-launch — lore could not show it
```

The reasons are a closed set, and each maps to one cause:

- `its waiting section could not be parsed`: the section is present but malformed. This includes a
  duplicate `## Waiting` heading, an unterminated fence, an entry that does not match the format,
  and an unknown kind or status. It counts as unreadable, never as empty.
- `lore could not show it`: `lore record show` failed or did not return JSON with a body.
- `its name is not a safe value`: a vault or spec name failed the safe-value shape.
- `the search index is stale; run lore reindex`: lore reported its index stale, so some ready
  specs may be missing from the list.
- `it is not a configured vault`: a search hit points at a vault path that `lore vault ls` does
  not list.

When lore itself cannot be run at all (not installed, or `lore vault ls` fails), there is no list
to show. The gather exits 2 with a `reason-code:` line, and the skill reports that the check list
could not be gathered, naming the remedy.
