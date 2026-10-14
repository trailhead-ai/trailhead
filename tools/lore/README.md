# lore

Agent-native project memory that works with your existing setup. Lore captures
the durable, non-obvious things worth remembering across sessions — decisions,
lessons, tasks (work to revisit, abandoned approaches, things to watch),
area mental models, and a running session log — and surfaces what's relevant on
demand.

**No MCP required.** Agents read and write the vault only through the `lore`
CLI (invoked over `Bash`) plus `Bash(git)` — no MCP server, no bespoke tools.

## What lore captures

Capture is one skill — `/lore:record` (`lore record create --kind <kind>`) —
over a **closed set of nine kinds**:

| Kind | What it records |
|---|---|
| `decision` | Non-obvious architectural choices and their reasoning |
| `adr` | A more formal architecture decision record — frozen once it leaves `draft` (see the freeze table below) |
| `lesson` | A mistake plus a concrete prevention check |
| `task` | Anything worth seeing through to completion — implementation work, a deferred item, an abandoned approach, or an external thing to watch — distinguished by `status` (`open` / `ready` / `in-progress` / `blocked` / `done` / `dropped` / `superseded`) |
| `area` | Mental model of a codebase area (files, gotchas, conventions) |
| `spec` | Specification artifacts — frozen once they leave `draft` (see the freeze table below) |
| `collaboration` | Working-style preferences and conventions |
| `blob` | Freeform capture that doesn't fit another kind |
| `session` | One note per working session — the running log |

A meeting or call transcript is a `blob` labeled `transcript=true` — one
record per meeting, redacted before import, targeted with
`has:label.transcript` (excluded with `-has:label.transcript`). See
`/lore:record` for the full import recipe.

Session lifecycle is automatic: capture-worthy items are logged as session
candidates mid-session, and `/lore:flush` evaluates them into durable records
and wraps the session (`status: dirty → clean`). `/lore:sync` commits and pushes
the vault at any point.

## Install

Lore is installed as part of Trailhead — see the [root README](../../README.md)
for `trailhead install` instructions.

After install, set up your vault:

```
lore init   # scaffold the default vault
```

`lore init` scaffolds the vault; subsequent commands find it automatically —
nothing to export or configure.

Open Claude Code in any project. The agent-rules surface carries a short
disposition primer (what lore is, when to capture, when to record directly)
plus the mandatory vault-write rules — not an area map or a vault index.
Agents pull prior context on demand with `lore areas` and `lore search`.

## Skills

| Skill | Description |
|---|---|
| `/lore:record` | Log a single deliberate item now (`lore record` / `lore session`) |
| `/lore:search` | Query the vault (KQL-subset) — the read path |
| `/lore:research` | Dispatch the `investigator` (deep) or `researcher` (light) agent |
| `/lore:flush` | Evaluate outstanding session candidates into records and wrap the session (`dirty → clean`) |
| `/lore:sync` | Commit and push every configured vault |

## The `lore` CLI

The `lore` CLI handles the deterministic operations skills delegate to it.

<!-- Intentionally untagged (no sh/bash): this is a reference listing, not a runnable snippet. -->
```
lore init                 Scaffold the default vault
lore flush                Evaluate session candidates and wrap the session (dirty → clean), commit, and request a background publish for every vault (--wait to sync inline, --no-sync to skip)
lore sync                 Stage, commit, pull, and push every configured vault (--vault <name> for one)
lore status               Report ruleset drift, host signing state, and any vault holding unsynced records (--json: the same host prerequisites as data, which `trailhead doctor` reads)
lore search <query>       Query all records (KQL-subset: field:value, full-text, and/or/not)
lore record show <id>     Read a record's body (add --json for the sidecar)
lore record amend <id>    Correct a record's body in place, even a frozen record's, recording who/why/diff in its change-history (--reason, --initiated-by operator|agent, body on stdin or --diff)
lore record history <id>  Print a record's change history, newest first, entries numbered (what amend recorded: when, by whom, who asked, why, the diff); --body-before N previews the body before entry N
lore record criteria <spec-id>  Print, read-only, one JSON object `{record, declared, removed}`: the criterion IDs a spec's body declares (document order) and the ones its change-history recorded as removed (numeric order); refuses a non-spec, a missing or duplicate `## Acceptance Criteria` heading, or a malformed stored history
lore areas                List the area profiles across every configured non-shared vault
lore signing enable       Generate (or adopt with --key) a no-passphrase SSH key this host signs vault commits with
lore signing status       Report whether this host signs vault commits unattended, or what's missing
```

Run `lore <subcommand> --help` for full options.

### Amending a record: `lore record amend`

`lore record update` refuses to change the authored body of a frozen record. What
is frozen is judged on the record's status **before** the write:

| Kind | Frozen statuses | Plain update still allowed |
|---|---|---|
| `adr` | `active`, `superseded`, `dropped` | metadata only |
| `spec` | `ready`, `planned`, `complete`, `superseded`, `dropped` | metadata, and changes confined to `## Slices` |
| `decision` | every status | metadata only |
| `task` | `done`, `dropped`, `superseded` | metadata, and changes confined to `## Driver run` blocks |

`lesson`, `area`, `collaboration`, `blob` and `session` are never frozen. Authored
content is the body minus the kind's pipeline-maintained sections, so the slice
ledger and the driver checkpoint stay writable. The refusal is
`graph-guard [frozen-body]`; it names the first authored line that differs and the
exact amend command for that record. An `adr` or `spec` also cannot leave a frozen
status for `draft` (`graph-guard [frozen-status]`); a `task` can be reopened with a
status-only update. A material change to an `adr` is a supersession with a new adr;
for any other kind a change of scope is a supersession too. Record a supersession
with `lore record supersede <predecessor-id> --by <successor-id>`: it writes the
`supersedes` edge on the successor and flips the predecessor to `superseded` in one
step, and it is the only way to reach `superseded` — `lore record create` and
`lore record update` refuse `--status superseded` (`graph-guard [superseded-status]`).
To correct the body in
place (a typo, a wrong link), pipe the corrected body to amend (read its revision
first). An agent always passes `--initiated-by agent`:

```
lore record show adr/some-decision > /dev/null                   # stderr: revision: <hex>
lore record amend adr/some-decision --reason "fix the cited ticket number" --initiated-by operator --base <revision> < corrected.md
lore record amend adr/some-decision --reason "drop a stale sentence" --initiated-by agent --diff < change.diff
```

Amend writes the new body (a full replacement, or with `--diff` a unified diff
applied to the stored body), keeps the record's id, kind, status, labels and links,
and appends one entry to the record's `change-history`: when, by whom (the
committer identity), who asked, why, and the diff. Every other guard still runs.
It refuses, writing nothing, a missing, multi-line, control-character or over-long `--reason`, a missing or
invalid `--initiated-by`, empty stdin, a `--diff` that does not apply, a body
identical to the stored one, a missing record, and any `session` record.

- **`--base REVISION`** is the stale-write handshake. A record's revision is the
  SHA-256 of its stored body; `lore record show --json` carries it as `revision`,
  and plain `lore record show` prints `revision: <hex>` on stderr (stdout stays the
  body, byte for byte). A full-body amend must pass the revision it composed
  against. Under the vault lock, after the implicit pull, amend compares it with the
  stored body's revision and, on a mismatch, exits non-zero and writes nothing; the
  message gives both revisions, who last changed the record and when (withheld in a
  shared vault, where that text is not the operator's own), a pointer to
  `lore record history` when the change was an amend, and the remedy: re-read with
  `lore record show`, reapply the correction, amend again. A full-body amend
  without `--base` is refused with the same pointer. **`--diff` may omit `--base`**:
  a diff whose context still matches overwrites nobody's change, and an edit made
  since the read is kept when it overlaps no hunk and shifts no lines above one; with a stale `--base` it is
  refused even if it would apply. The revision covers the body only, so a label or
  status change after the read neither changes it nor is overwritten.
- **`--initiated-by`** says who asked for the edit. `operator` means a human
  instructed this edit in the current session; `agent` means the agent proposed it.
- **Attested, not verified.** The value is recorded as stated; lore cannot check it.
  `initiated-by` and `by` are audit labels, not permission checks: `by` is the
  unauthenticated git committer identity (or `LORE_EMAIL`), so anyone who can
  write the vault can claim any value. Use `--initiated-by operator` only for an
  edit a human instructed.
- **Reason limits.** `--reason` is one line of printable text, at most 500
  characters; control characters and Unicode line breaks are refused.
- **Permanent.** The reason and the diff are kept for good, so amend is not a way
  to scrub text: the removed body stays in the diff and in the vault's git history.
- **Older lore.** A record carrying `change-history` needs a lore new enough to know
  the field. An older lore refuses to write such a record with
  `unsupported key 'change-history'`; pull trailhead to fix it.

### Reading a record's history: `lore record history`

```
lore record history adr/some-decision [--vault NAME]
```

Prints the record's `change-history` newest first: each entry's date, `by`,
`initiated-by` and reason, then its diff. A record that was never amended prints
one line saying so and exits 0; a missing record is a `lore:` error. In a shared
vault the listing is wrapped in the same `<external-memory>` fence `record show`
uses, with the reason and diff entity-escaped.

Each entry is numbered `entry: N of M`, where 1 is the oldest; the listing stays
newest first, so the numbers descend down the page.

#### Previewing an earlier body: `--body-before N`

```
lore record history adr/some-decision --body-before 2 [--vault NAME]
```

Prints to stdout, as a read-only preview, the body as it stood before entry N:
entries M down to N are reverse-applied to the record's current body.
`--body-before 1` is the body before any amend. Nothing is written. **A change made
outside amend is kept in the output when it overlaps no amend hunk and shifts no
lines above one**; otherwise that entry no longer reverses and the command refuses.
Each hunk is located at its recorded line, with no offset search, so a plain update
that inserts or removes lines above an amend hunk makes the entry refuse.
A body whose CRLF line endings were written outside lore is reconstructed with LF
line endings. The preview is fence-neutralized, and wrapped in a shared vault, exactly as the
listing is. It exits non-zero with a `lore:` message and empty stdout for an N that
is out of range or not an integer, a record with no history, a malformed stored
diff, and an entry that no longer reverses (the message names the entry by number
and gives the exact `git -C <vault root> log -p -- <kind>/<name>.md` to run).

#### Reverting: `lore record amend --revert-to N`

```
lore record amend adr/some-decision --revert-to 2 --base REVISION --reason "..." --initiated-by operator
```

Computes the same reconstruction inside lore, from the stored body and history
(never from the escaped or wrapped preview text), and writes it as an ordinary
amend with its own `change-history` entry. Preview it first with `--body-before`.
A body whose CRLF line endings were written outside lore is reverted to LF line endings.
Stdin must be empty, and `--revert-to` with a piped body or with `--diff` is
refused. Every amend refusal applies: `--base` is required and a stale one refuses,
a reconstruction identical to the current body refuses, and so does an entry that
no longer reverses.

- **`record show --json`** carries the history in `sidecar["change-history"]`.
- **Plain `record show`** keeps stdout exactly the body. When the record has
  history it adds one stderr line, ``amended once; see `lore record history <id>` ``
  (``amended N times; ...`` from two amends on),
  so a reader knows the body was corrected while piped consumers see no change.

## Unattended vault-commit signing

A host running lore with nobody watching — the sweep, an automatic publish, a
session flush after a reboot — cannot answer a passphrase prompt. If commit
signing falls back to the operator's personal GPG/SSH key, it hangs on that
prompt and publishing stops.

`lore signing enable` fixes this once per host:

- With no argument, it generates a fresh, no-passphrase ed25519 key under
  lore's state directory and configures it as this host's signing key.
- With `--key <path>`, it adopts an existing SSH private key **in place**
  (never copying or modifying it) — provided it has no passphrase, is a
  regular file, and is readable only by its owner. A key needing a
  passphrase is refused on every platform: it cannot sign with nobody
  present.

Either way it writes the key's absolute path and an allowed-signers file into
`state_dir("lore")/signing/`, and every git commit lore makes or replays in
any vault signs with that key from then on — via git's environment config
channel, so no vault's own `.git/config` is touched and the operator's own
signing setup on an unconfigured host is left exactly as it was.

Re-running `enable` with no argument is a no-op if a usable key is already
configured; `enable --key` always switches to the named key.

`lore signing status` (and the "signing" line in `lore status`) reports
whether this host is set up to sign unattended, or names the missing/broken
piece and the exact command that fixes it. On a host with no key whose own
git config does not sign commits (`commit.gpgsign` unset or false), the
`lore status` line reads "not configured" with no remedy: vault commits
follow your git settings and publish unsigned, as they always have.

There is no `disable` verb — deleting `state_dir("lore")/signing/` reverts a
host to today's behavior (the adopter's own signing config), and `status`
reports that plainly.

**Threat model.** This key has no passphrase, by design — that is what lets
it sign with nobody present. That means its `0600` file mode (and the
containing directory's `0700` mode) is the *only* control standing between
"this host signs unattended" and "anyone with a shell on this host can forge
a signed commit in any vault it can reach." There is no agent, no passphrase
prompt, and no hardware token in the loop to fall back on if the file
permissions are ever widened. Treat this key the same way you would treat any
other no-passphrase credential on the box: keep it per-host, never copy it to
another machine, and never let it anywhere near a synced vault, a backup
that leaves the host, or a dotfiles repo. `enable --key` refuses a path
inside any configured vault outright — such a key would be auto-staged and
pushed by `lore sync`'s untracked-file allowlist — and `status` flags a
configuration that already names one, the same way it flags any other
broken configuration.

## How search works

The agent pulls what's relevant when it needs it — explained, not guessed.

Lore keeps an **area map**: a compact menu of every area profile in your vault,
listing each area's name, one-line summary, and keywords. It is available on
demand via `lore areas`.

The agent reads the area map as part of its normal task analysis, matches the
current task to one or more areas, and runs `lore search 'area:<name>'` to
pull that area's accumulated memory — decisions, lessons, and tasks
linked to that area — into the conversation. The search is **scoped and
explainable**: the agent can say "I searched the auth-module area because the
task touches login flows," not just "here is some context." Search also covers
full-text and the other record facets (`kind:`, `status:`, `keyword:`, date
ranges) — `lore search 'area:<name>'` is the area-membership case of the one
general query interface.

**To register an area**, create an `area` record with
`lore record create --kind area` (or `/lore:record`). Give it a name, a
one-line summary, and a set of keywords. The profile feeds the area map; the
agent uses keywords and the summary to decide when to pull the area's memory.

## Status vocabulary

Every kind has a canonical `status:` set (first value is the create default).
The pre-commit guard rejects non-canonical values. See the vault's
`glossary.md` for the full list.

Key transitions:

- **session:** `dirty` → `clean`
- **task:** `open` → `ready` → `in-progress` → `done` (off-path: `blocked` / `dropped` / `superseded`)
- **decision:** `active` → `superseded` / `dropped`
- **adr:** `draft` → `active` → `superseded` / `dropped`
- **lesson:** `active` → `conditional`
- **spec:** `draft` → `ready` → … → `complete` (off-path: `superseded` / `dropped`)

## Record kinds

The kind set is closed — nine kinds:

- `session` — one note per working session (the running log)
- `area` — mental models of codebase areas
- `decision` — lightweight ADRs
- `adr` — a more formal architecture decision record (body frozen once it leaves `draft`)
- `lesson` — mistakes plus prevention checks
- `task` — work to track to completion: implementation work, deferred items, abandoned approaches, things to watch (status: open/ready/in-progress/blocked/done/dropped/superseded)
- `collaboration` — working-style preferences
- `spec` — specification artifacts
- `blob` — freeform captures that don't fit another kind
  - a meeting/call transcript is a `blob` labeled `transcript=true`, one
    record per meeting, redacted before import; target with
    `has:label.transcript` / exclude with `-has:label.transcript` — full
    recipe at `/lore:record`

## Searching the vault

Use the `/lore:librarian` agent to search and synthesize across the vault.
It works only through the `lore` CLI (`lore search` + `lore record show`) — no
MCP, no direct vault reads — and returns a cited synthesis, not a raw dump.

## Development

```bash
# Run the test suite
python -m pytest tests/
```

A **pre-commit leak gate** keeps project- or machine-specific tokens out of the
shipped plugin surface, lore's included. It is wired for the whole monorepo, so
there is nothing lore-specific to install:

```bash
pre-commit install     # from the repo root, once per clone
```

See the Contributing section of the repo root [README](../../README.md) for
what it scans and which denylists feed it.

See `MANUAL-SMOKE.md` for the plugin-system boundary smoke test (hook
registration, `${CLAUDE_PLUGIN_ROOT}`, skill namespacing).

## Extending lore for your project

To bolt your own project layer on top of lore and the craft dev agents — wiring
craft's extension points (feature flags, issue tracker, observability, test
commands) to your own stack — see the adopter cookbook in
[`docs/EXTENDING.md`](docs/EXTENDING.md).
