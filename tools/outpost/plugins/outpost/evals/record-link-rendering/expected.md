# record-link-rendering — does the record-link rule cause linked, resolved, safe output?

## What is under test

The **Record links** section of `tools/outpost/plugins/outpost/rules.md`
(landed in commit `4d586099`), installed by trailhead as
`~/.claude/rules/trailhead-outpost.md`. The rule states a link form, a
two-step base/vault resolution, and two bare-text fallbacks — none of which
a test on the document itself can observe, because whether an agent
actually renders a link, resolves a vault from a listing, or refuses to
interpolate an out-of-grammar segment is behaviour, not text.

Three separately-judged pass conditions, each graded per run:

1. **Link rendering.** A record named in the agent's prose arrives as a
   markdown link whose visible text is `kind/slug` and whose target is that
   record's reader page (`<base>/records/vault/kind/slug`) — the target still
   carries the vault segment; only the visible text omits it.
2. **Vault-resolution fallback.** A record in a vault the fixture configures
   at a non-standard path (not a direct child of the vaults root), and a
   record whose vault cannot be resolved from the listing at all, are
   **both** printed bare rather than linked.
3. **Grammar fallback.** A record whose stem carries a segment (slug or
   kind) outside lowercase alphanumerics and hyphens is printed bare rather
   than interpolated into a URL.

## Why no `scripts/eval-sandbox`

Every arm here is dispatched with `--allowedTools "Read"` and no shell tool
at all. `docs/eval-protocol.md` names the write-side escape the sandbox
confines — an arm that cannot complete a task the sanctioned way goes and
finds the real machinery instead, as happened twice in `publish-routing`
(a real vault published into, a real `lore sync` pushed to origin). That
escape requires a shell: neither arm here can invoke one, write a file, or
publish anything, so that failure mode does not apply to this case's tool
grant.

The read side is a different claim, and this case does not confine it the
way `scripts/eval-sandbox` does. `docs/eval-protocol.md` states default-deny
on the read side as the sandbox's actual point, not an incidental property
of denying shell access — and an unconfined `Read` tool can open any path
the process can read, including the answer key: this repository's own
`rules.md`, or the installed `~/.claude/rules/trailhead-outpost.md`. Neither
arm is prevented from reading either file directly. What stands in for
confinement here is weaker than a boundary: the task prompt tells the arm to
use the given file *locations* rather than reading the record files'
contents, and the baseline arm's own output is the only evidence this held —
its record-1 link was missing the `/records/` path segment the real rule
requires, which is inconsistent with having read the answer key. That is
observational, not enforced, and does not generalize past this run.

## Arms

- **treatment** — `arms/treatment.md`: the short baseline brief with the
  *current* `## Record links` section appended. This is the **live** arm —
  it is rebuilt to match `tools/outpost/plugins/outpost/rules.md` every time
  the section changes, and `tools/outpost/tests/
  test_record_link_rendering_arms_contract.py` pins it byte-identical to a
  fresh rebuild. Every future re-run-trigger dispatch uses this arm.
- **treatment-pre-rule-edit** — `arms/treatment-pre-rule-edit.md`: the same
  brief with the `## Record links` section frozen exactly as it stood at
  commit `19866500` — the text the committed rows below the "record-link
  rendering" case's own results table were actually measured against, before
  the record-links rule salience treatment (`bc8c493e`) relocated the
  table/list case into its own standalone statement and worked example. Kept
  so those rows' "prose under test" stays reconstructible rather than
  silently reread against today's wording. The same contract test pins its
  provenance (source revision `19866500`) and proves the pin discriminates
  (a reconstruction against a wrong revision does not match). Never edit
  this file to track a later rule change — freeze a new `-pre-rule-edit`
  copy instead and re-record the measurement, mirroring
  `tools/craft/plugins/craft/evals/ritual-deliverable-names-its-record/`'s
  live/frozen arm pattern.
- **baseline** — `arms/baseline.md`: the identical brief with no ruleset
  appended.

Dispatched as separate `claude` processes, never as a subagent:

```
LORE_STATE_DIR="<run-dir>" claude -p "<the task prompt>" --setting-sources project \
  --append-system-prompt "$(cat arms/<arm>.md)" \
  --allowedTools "Read" < /dev/null
```

`LORE_STATE_DIR` must be set in each arm process's own environment — the arms
run as separate `claude -p` processes that can only observe the environment
they are actually given, never the dispatcher's. It pins the resolved vaults
root to exactly `<run-dir>/vaults`, which is what makes fixture record 1
(`vaults/gearshed/...`) a direct child of the vaults root and fixture record 2
(`other-storage/attic-archive/...`) genuinely not one.

`--setting-sources project` drops `~/.claude/rules/trailhead-outpost.md`
from both arms' context. A subagent dispatch cannot be used for either arm:
it would inherit that live user-level ruleset, making the baseline arm carry
the very rule it is supposed to lack, and both arms would become the
treatment arm.

**What the probe below did and did not establish on this run.** The installed
copy of that ruleset predated this change and carried no `## Record links`
section when these arms ran — `bin/trailhead install` had not been re-run,
and running it to make the probe meaningful is barred (never mutate the
developer's real install from a test). So a probe that quotes nothing back
cannot distinguish the flag dropping the rule from there having been no rule
to drop. Read it as a mechanism check, not as proof of isolation. The
isolation is load-bearing for every re-run after the next install, when the
installed copy will carry the rule; the arms' controlling prose reaches them
through `--append-system-prompt` regardless, which is why the measured
baseline/treatment split does not rest on this point.

**No-tools probe, run before trusting the baseline:** dispatch a probe agent
under the same `--setting-sources project` flag, with no ruleset appended,
asking it to quote back verbatim every operating rule mentioning "record" or
"link". A clean baseline quotes nothing from a "Record links" section — only
the harness's own unrelated Memory-section text (which uses "link" for
`[[wikilink]]`-style memory cross-references and "record" in "the repo
already records") may legitimately turn up, and must not be confused with
the rule under test.

## Fixture

`fixtures/make-fixture-env.sh <run-dir>` builds a self-contained scratch
environment: a `vault-ls.txt` reproducing `lore vault ls`'s real
tab-separated output shape (name, scope, path, kinds column), and five
record files under it. The standard-path vault (`gearshed`) is laid down at
`<run-dir>/vaults/gearshed` — a direct child of `<run-dir>/vaults` — and the
dispatch command above pins `LORE_STATE_DIR=<run-dir>` so the rule's
resolved vaults root is exactly `<run-dir>/vaults`. Without that variable set
in the arm process's own environment, every record here is formally outside
the vaults root and the fixture cannot discriminate "the clause is obeyed"
from "the clause is ignored" — this replaces an earlier `vaults-root/` layout
that had exactly that defect.

| # | File location (relative to run dir) | Vault | Resolvable? | Path standard? | Kind/slug grammar |
|---|---|---|---|---|---|
| 1 | `vaults/gearshed/note/rotate-tires.md` | `gearshed` | yes (in listing) | yes (direct child of `vaults/`, with `LORE_STATE_DIR` pinned) | both clean |
| 2 | `other-storage/attic-archive/log/winter-inventory.md` | `attic-archive` | yes (in listing) | **no** — lives under `other-storage/`, not a direct child of the vaults root | both clean |
| 3 | `untracked/ghost-vault/memo/unfiled-thought.md` | `ghost-vault` | **no** — not a row in `vault-ls.txt` at all | n/a | both clean |
| 4 | `vaults/gearshed/note/Rotate_Tires.md` | `gearshed` | yes | yes | **slug** `Rotate_Tires` — uppercase + underscore |
| 5 | `vaults/gearshed/Field Note/check-in.md` | `gearshed` | yes | yes | **kind** `Field Note` — space + uppercase |

`task.md` (generated from `task.md.in` by the same script) gives the arm
each record's file location and asks for one paragraph mentioning all five,
in order, plus the record-URL base to use (`http://127.0.0.1:9199`, an
otherwise-unused port, not the rule's own default or worked-example port).
The arm is told explicitly to use the file location rather than reading the
record file's own contents, and is granted `Read` only — enough to read the
vault listing, nothing to reach outside the fixture with.

## Expected verdict, per record

| # | Identifier | Expected treatment rendering |
|---|---|---|
| 1 | `gearshed/note/rotate-tires` | Link: `[note/rotate-tires](http://127.0.0.1:9199/records/gearshed/note/rotate-tires)` |
| 2 | `attic-archive/log/winter-inventory` | Bare: `attic-archive/log/winter-inventory` |
| 3 | `ghost-vault/memo/unfiled-thought` | Bare: `ghost-vault/memo/unfiled-thought` |
| 4 | `gearshed/note/Rotate_Tires` | Bare: `gearshed/note/Rotate_Tires` |
| 5 | `gearshed/Field Note/check-in` | Bare: `gearshed/Field Note/check-in` |

Baseline is expected to render all five bare (it has never seen the rule,
so it has no link form or grammar rule to apply and no reason to invent
one) — the baseline's role is not to pass a bar but to show the treatment's
behaviour is not already the model's default habit.

## Pass condition

**3 runs per arm** (6 runs total), one model tier.

- **PASS** — across 3/3 treatment runs, record 1 is linked correctly (right
  visible text, right target) in every run, and records 2–5 are printed bare
  in every run. Baseline is graded for contrast only: it is not required to
  link anything, and a baseline that spontaneously produces a correct link
  in the identical form would itself be a finding (recall or convention,
  not the rule) worth recording in the notes.
- **INCONCLUSIVE** — baseline links record 1 as often and as correctly as
  treatment. The rule would then be unmeasured, not vindicated.
- **FAIL** — any treatment run links a record from 2–5, mislinks record 1
  (wrong visible text or wrong target), or bare-prints record 1, in more
  than 1/3 runs for that record.

Each of the three pass conditions in the task's Test contract is graded
independently per run: condition 1 against record 1's rendering, condition 2
against records 2 and 3, condition 3 against records 4 and 5.

## Contamination analysis

The rule's own worked example is
`[task/example](http://127.0.0.1:7313/records/trailhead/task/example)`.
This fixture reuses none of its surface cues: different vault names
(`gearshed`, `attic-archive`, `ghost-vault` vs. `trailhead`), different kinds
(`note`, `log`, `memo`, `Field Note` vs. `task`), different slugs, and a
different base port (`9199` vs. the rule's own default `7313`), so a run
cannot score by pattern-matching the example rather than applying the rule's
resolution steps. No fixture file contains a `[[wikilink]]` or a path
resolving to a real vault, record, or config.

## Re-run trigger

Re-dispatch both arms whenever any of the following changes:

- the record-URL contract's path form, or its base-URL precedence order
  (`tools/lore/plugins/lore/lore/record_url.py`);
- the vault-segment resolution rule (matching a record's path against
  `lore vault ls`'s path column, and the direct-child-of-vaults-root
  requirement);
- the `## Record links` section of `tools/outpost/plugins/outpost/rules.md`
  itself, in any way.

A case with no written trigger is run once at ship and never again; this
line is that trigger.

## Limitations

Five fixture records (one linked, four bare across three distinct fallback
reasons), 3 runs per arm, one model tier, in a clean room with no
PreToolUse hook, `Read`-only tools, and no shell — so nothing here measures
what an agent does when it *can* reach for a shell instead of following the
rule (that failure mode is what `scripts/eval-sandbox` exists to confine
elsewhere, and doesn't apply to a read-only arm). This case does not cover
first-mention-vs-every-row behaviour in a table/list, handoff-command
bareness, or the never-link-into-a-record-body rule — those are separate
claims the same rule makes but that this task's contract does not cover.
It also does not cover adherence late in a long session or under any
pressure to abbreviate.

**Deviation from task 2's contract.** The task's test contract calls for two
arms differing in exactly one variable: "the committed ruleset, and a copy
under `arms/` with the record-link section removed." What was actually built
is a synthetic five-line brief (`arms/baseline.md`) plus that same brief with
the `## Record links` section appended (`arms/treatment.md`) — not the full
committed ruleset minus the section. The cost: this case measures the rule
in isolation, with no surrounding ruleset content competing for the model's
attention, so it says nothing about whether the rule survives ship-time
context density — the crowding-out condition the spec named as this eval's
whole purpose (spec `record-mentions-in-agent-output-are-reachable`, Open
Questions/Risks: "one behavioural eval aimed at a ritual deliverable, where
the instruction is most likely to be crowded out by surrounding
procedure"). A future
re-run against the real committed ruleset with the section stripped, rather
than a synthetic brief, would close this gap; this run does not, and the
recorded PASS above should be read accordingly.

## Table vs paragraph condition — U1

**Written before any run of either condition is dispatched.** This section
answers `task/prove-whether-table-cell-context-is-what-splits-record-link-rendering`'s
Known Unknown U1: is table-cell rendering context the discriminator behind
`distill`'s split record-link rendering, or a confound? It reuses the exact
fixture, arm, and dispatch shape above (`arms/treatment.md`, unmodified,
`--setting-sources project`, `--allowedTools "Read"`, `LORE_STATE_DIR` pinned
to the run directory) and varies exactly one thing: the rendering context
`task.md` asks for.

### Condition selector

`fixtures/make-fixture-env.sh <run-dir> <condition>` — `<condition>` is now a
required second positional argument, one of `table` or `paragraph`. Both
conditions copy the identical five-record set and `vault-ls.txt` (the
generator's vault-tree and listing steps do not depend on the condition);
only the generated `task.md`'s requested output shape differs:

- `paragraph` selects `fixtures/task.md.in` — "Write one short paragraph
  that mentions all five records" (the existing, previously-measured
  condition; unchanged from the case's earlier rows above).
- `table` selects `fixtures/table-task.md.in` — "Write a short table with
  two columns, Record and Note, one row per record" over the same five
  records in the same order, with the same per-record notes.

An unrecognized condition (anything other than `table` or `paragraph`) is
refused with a nonzero exit and no `task.md` written — never silently
defaulted to either condition.

Both generated files are named `task.md` inside their own run directory;
since each condition is built into its own fresh run directory (never both
conditions into one), there is no collision. The other generated files
(`vault-ls.txt`, the copied `vaults/`, `other-storage/`, `untracked/` trees)
are condition-independent and byte-identical in content (modulo the
run-directory-specific absolute paths each run resolves for itself).

### Per-record expected rendering, per condition

Records 2–5 are expected bare under **both** conditions — their bareness is
caused by the rule's vault-resolution and grammar fallbacks (Conditions 2
and 3 above), which have nothing to do with rendering context. If a run
links any of records 2–5 under either condition, or renders them
differently between conditions, that is a confound in its own right and
must be reported as a finding distinct from the U1 verdict, not folded into
it.

Record 1 (`gearshed/note/rotate-tires`, the only record with nothing wrong —
resolvable vault, standard path, clean grammar) is the one whose rendering
this section measures:

| Record | Paragraph condition (expected if H0: table-ness has no effect) | Table condition (expected if H0 holds) |
|---|---|---|
| 1 — `gearshed/note/rotate-tires` | Link: `[note/rotate-tires](http://127.0.0.1:9199/records/gearshed/note/rotate-tires)` | Link: `[note/rotate-tires](http://127.0.0.1:9199/records/gearshed/note/rotate-tires)` |
| 2 — `attic-archive/log/winter-inventory` | Bare | Bare |
| 3 — `ghost-vault/memo/unfiled-thought` | Bare | Bare |
| 4 — `gearshed/note/Rotate_Tires` | Bare | Bare |
| 5 — `gearshed/Field Note/check-in` | Bare | Bare |

H0 (table-ness has no effect on record 1's rendering) predicts the two
condition columns for record 1 are identical. U1's Delta-design hypothesis
predicts they diverge: record 1 links reliably in the paragraph condition
and fails to link reliably (renders bare, or as a code span, or as plain
`vault/kind/slug` text) in the table condition — the same degradation
pattern observed in `distill`'s six committed captures.

### Dispatch

**At least 3 runs per condition** (6 runs total for record 1's comparison),
matching this case's existing run count, against `arms/treatment.md`
unmodified:

```
LORE_STATE_DIR="<run-dir>" claude -p "<the task prompt>" --setting-sources project \
  --append-system-prompt "$(cat arms/treatment.md)" \
  --allowedTools "Read" < /dev/null
```

Each run's transcript captures a completion marker as its last act; grading
reads only marked-complete transcripts, never a directory count. Per-record
verdicts are read off each finished transcript by hand and re-derived at
report time from the captures — never quoted from an earlier count in this
task.

### Pass condition — pre-registered before dispatch

Record 1's **link rate** in a condition is the count of runs (out of 3) in
which it renders as a correct link (right visible text `note/rotate-tires`,
right target `http://127.0.0.1:9199/records/gearshed/note/rotate-tires`).
Let `Δ = paragraph_link_rate − table_link_rate` (range 0–3).

- **U1 VALIDATED** — `Δ ≥ 2` (paragraph links record 1 in at least two more
  of its three runs than table does) **and** records 2–5 render bare under
  both conditions (no unexplained confound). Table-cell context is shown to
  degrade record-link rendering under an identical arm and record set; the
  Delta design's fix direction (edit the rule's table-row salience) is
  supported and the plan proceeds.
- **U1 INVALIDATED** — `Δ ≤ 1` (the two conditions' link rates for record 1
  differ by at most one run out of three — statistically indistinguishable
  at this sample size). The split observed at `distill` is not explained by
  table-cell rendering context alone; the Delta design's fix direction is
  wrong, and the plan is invalidated rather than adjusted. Report and stop —
  do not reach for a second hypothesis in this task.
- **Confound flag, orthogonal to the Δ verdict** — any run under either
  condition links a record from 2–5, or renders records 2–5 differently
  between conditions. Report this regardless of which side of the Δ
  threshold the run lands on; it means the fixture itself, not just
  table-ness, is doing something unaccounted for.

### Limitations specific to this section

Record 1 is the only discriminating record in this fixture (2–5 are bare
for reasons unrelated to rendering context under both conditions), so this
section's whole evidentiary weight rests on 3 runs × 1 record × 2
conditions = 6 rendering observations of a single record. One model tier,
one host, one point in time, `Read`-only with no shell (same clean-room
shape as the rest of this case, for the same reason: no
`scripts/eval-sandbox` applicability). A result here is not evidence that
the effect generalizes to a different model, a different host platform, or
a table with more than one linkable row — it is evidence about whether
table-cell context, specifically, moves this one rule's application to this
one record, on this one measured configuration.
