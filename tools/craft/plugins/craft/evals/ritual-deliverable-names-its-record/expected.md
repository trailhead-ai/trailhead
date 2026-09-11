# ritual-deliverable-names-its-record — does a ritual's closing report link the record it acted on?

## Outcome of this case — read this first

**Run 2026-09-11. The hypothesis this case was built to test was falsified, and the prose change it
was built to justify was not shipped.** The completed 2x2:

| | no rule resident | reader rule resident |
|---|---|---|
| **unedited prose** | bare (3/3) | **link (3/3)** |
| **edited prose** | bare (3/3) | link (3/3) |

The reader's `## Record links` rule is the sole determinant in all four cells; the proposed ritual
edit had no measured effect in either condition. On that result the ritual edits were reverted, so
`slice/SKILL.md` today is byte-identical to `arms/baseline.md`.

**What that means for the arms.** `arms/baseline.md` and `arms/rule-only.md` were run against
exactly the prose that ships, and their results stand as the live measurement: the reader rule
turns a bare identifier into a correct link at this site, unaided. `arms/treatment.md` and
`arms/reader-absent.md` are the frozen record of a prose variant that was measured and rejected;
they are kept as evidence, not as a description of any text now in the tree. Read every mention
below of "as edited by `task/make-the-seven-ritual-deliverables-name-their-record-as-a-link`" as
naming that rejected variant.

**The gap this case actually located.** Neither prose variant produces a link when no record-link
rule is resident — a craft-only install, which carries no such rule at all. Restating a pointer to
outpost's rule inside a ritual cannot fix that; only giving craft its own rule can. See the
follow-up recorded against this case.

## What is under test

`## Outcome`, the closing section of `tools/craft/plugins/craft/skills/slice/SKILL.md` (today's
committed, unedited text) — specifically its selection-path sentence: "Report the chosen slice,
its value claim ..., and the new parent task's record id — `lore record show <task-id>` prints it
back without the operator needing to know the CLI." Today that sentence names the record it just
wrote only as a bare identifier plus a `lore record show` command the operator has to run
separately; AC6 (`spec/record-mentions-in-agent-output-are-reachable`) requires the deliverable to
identify that record **as a link** instead. Whether the model, given the whole ritual's 581-line
procedure as its operating instructions, still reaches that sentence's intent (or drops it, or
never had a link to render in the first place) is behaviour — a test on the document's text alone
cannot observe it, only that someone typed the sentence.

**Why `slice/SKILL.md`'s `## Outcome` and not one of the other six sites (council guidance,
task body fact 9).** The spec names the ritual deliverables as the surface most likely to lose
this affordance because the instruction competes with a page of surrounding procedure — `slice`
is the sharpest instance of that premise available: the model must carry a fully-stated 581-line,
10-step procedure (spec resolution, status guards, candidate derivation, the value floor, a
`lore record create` invocation, a duplicate recheck) and *still* apply a one-line reporting
convention at the very end, with nothing about the earlier steps hinting that the closing report
matters more than any other. It is also one of the two sites (with `distill`) where the parent
plan's Delta design says an existing `lore record show` read line is **replaced** rather than
appended beside a new link — so today's unedited prose already contains the literal "before"
state the slice diffs against, not just an absence.

**What this fixture does not cover.** It measures the `## Outcome` sentence in isolation from the
other six ritual sites (`brainstorm`, `gauntlet`, `plan`, `execute`, `review`, `distill`) — those
are separate claims the same AC6 makes, not covered by this case. A follow-up case per site is not
authored here; recorded as a limitation below, not silently assumed covered.

## Resolving the task's own unknown: reaching the closing report without running the ritual

The parent plan's unresolved unknown was "whether an eval can reach a ritual's closing report
without running the whole ritual, or whether the fixture must stub the ritual's inputs to get
there affordably." Running `/craft:slice` for real requires a real `ready` spec, a real vault, and
a real `lore record create` write — none of which this fixture may touch (`docs/eval-protocol.md`:
never point a fixture at a real vault, repo, or config). This case resolves the unknown by
**stubbing**: `fixtures/completed-run.md` asserts, as already-true state the arm must not
re-derive or verify, that steps 1–10 of the procedure completed and states their outcome (spec
name, vault, selected slice, value claim, the new task id). The arm is instructed not to use any
tool to check that state and to write only the deliverable "## Outcome" specifies for this point.
This is affordable (no real vault write, no real spec, one `claude -p` process per run) at the
cost of not measuring whether the model reaches step 10 *itself* under the ritual's own guard
logic — recorded under Limitations.

## Why no `scripts/eval-sandbox`

`scripts/eval-sandbox` requires macOS seatbelt (`uname -s` guard in the script itself) and does
not run on this host (Linux) or in CI for this repository; `docs/eval-protocol.md` confines the
sandbox to arms with shell access, because the escape it exists to close — an arm that cannot
complete its task the sanctioned way goes and finds the real machinery instead — requires a shell
to execute. Both arms here (and the treatment and reader-absent arms task 3 will add) are
dispatched with `--allowedTools "Read"` and no shell, `Edit`, `Write`, or `Bash` tool at all: an
arm with no shell cannot invoke `lore`, mutate the developer's real vault, or reach outside the
fixture, so that escape does not apply to this case's tool grant. Precedent for this exact shape,
on this exact host: `tools/outpost/plugins/outpost/evals/record-link-rendering/expected.md`,
"Why no `scripts/eval-sandbox`".

The unconfined-read caveat that file states applies here too, in weaker form: `Read` is not
path-jailed, so an arm could in principle open `tools/craft/plugins/craft/skills/slice/SKILL.md`
directly (it is not the answer key here in the way `rules.md` was there — the ritual prose *is*
the thing under test, appended verbatim via `--append-system-prompt`, so reading the same file a
second time changes nothing it can act on) or wander into the wider repository. The task prompt
gives it exactly one file to read (`fixtures/completed-run.md`) and no reason to look elsewhere;
that is an observational constraint, not an enforced one.

## Arms

- **baseline** (`arms/baseline.md`) — the full, unedited text of
  `tools/craft/plugins/craft/skills/slice/SKILL.md`, confirmed byte-identical to that file before
  every dispatch. This is today's committed ritual prose — the red state this whole slice measures
  against. **Run in this task, 3 times.**
- **treatment** (`arms/treatment.md`, not yet created) — `slice/SKILL.md` as edited by
  `task/make-the-seven-ritual-deliverables-name-their-record-as-a-link`, with the reader's
  `## Record links` rule (`tools/outpost/plugins/outpost/rules.md`) also appended, simulating a
  session where the reader plugin is installed. **Not run in this task** — the edited instruction
  does not exist yet. Its pass condition is pre-registered below so task 3 has nothing left to
  decide about grading when it runs this arm.
- **reader-absent** (`arms/reader-absent.md`, not yet created) — the same edited `slice/SKILL.md`
  from task 3, appended **without** the reader's rule, simulating the craft-only install task 1
  proved receives no record-link rule at all. **Not run in this task.** Its pass condition is
  pre-registered below for the same reason.

- **rule-only** (`arms/rule-only.md`) — today's unedited `slice/SKILL.md`, byte-identical to
  `arms/baseline.md`, with the reader's `## Record links` rule appended: a session where the reader
  plugin is installed but no ritual edit was ever made. **Added after the whole-change correctness
  review**, which found that baseline and treatment differ on two variables at once — the ritual
  edit *and* the appended rule — leaving the ritual edit's own contribution unmeasured. This arm is
  that missing control. Its decision rule is registered below, and committed before the arm is
  built or run.

Both unrun arms are added by `task/make-the-seven-ritual-deliverables-name-their-record-as-a-link`
once the edited wording exists, per that task's own `**Files:**` list, which already names
`arms/treatment.md`. Authoring their prose now is not possible without writing the ritual edit
itself, which is out of this task's scope (fact 8: none of the seven ritual documents may be
edited here) and would make `expected.md`'s pass conditions for those arms retrofit-able around
prose nobody has reviewed yet.

## Dispatch

Each arm is a separate `claude` process, never a subagent (a subagent inherits this session's
live, already-installed ruleset and would contaminate every arm alike):

```sh
COMPLETED_RUN="$(pwd)/tools/craft/plugins/craft/evals/ritual-deliverable-names-its-record/fixtures/completed-run.md"
PROMPT="$(sed "s#FIXTURE_COMPLETED_RUN_PATH#$COMPLETED_RUN#" \
  tools/craft/plugins/craft/evals/ritual-deliverable-names-its-record/fixtures/task-prompt.md)"

claude -p "$PROMPT" --setting-sources project \
  --append-system-prompt "$(cat tools/craft/plugins/craft/evals/ritual-deliverable-names-its-record/arms/baseline.md)" \
  --allowedTools "Read" < /dev/null
```

`--setting-sources project` drops `~/.claude/rules/` (including any installed outpost or craft
ruleset) from the process, so the baseline arm's material is exactly `arms/baseline.md`'s text —
nothing from a real install leaks in. The treatment arm (task 3) additionally appends the reader's
rule text; the reader-absent arm (task 3) does not.

No `fixtures/make-fixture-env.sh`-style run-dir copy is needed: this case does not test path- or
vault-resolution logic (that is already covered by `tools/outpost/plugins/outpost/evals/
record-link-rendering`), so `fixtures/completed-run.md` is read directly from its committed path
rather than staged into a throwaway run directory.

## Contamination check

`slice/SKILL.md`'s own worked examples use `spec/streaming-export` and
`task/the-streaming-export-slice` throughout the file (the Outcome section's own example line
included). `fixtures/completed-run.md` uses a different spec (`spec/quarterly-audit-trail`),
different task id (`task/the-quarterly-audit-trail-slice`), a different vault
(`fieldnotes`, vs. the file's own unnamed implied vault), and a different value-claim subject
(audit trails, not exports) — so a run cannot reach the correct rendering by pattern-matching the
skill file's own example rather than applying the `## Outcome` instruction to the state it was
actually given. No fixture file contains a `[[wikilink]]` or a path resolving to a real vault,
record, or config.

## Pass condition, per arm

**3 runs per arm.** Graded from the captured stdout of each finished run (a completion marker —
the run's exit code plus a non-empty captured response — is required before a run is graded; an
empty or truncated capture is excluded as an infrastructure failure, not scored) — never from a
log line a run wrote about itself, per `docs/eval-protocol.md`.

- **baseline — PASS for this task's red-state claim** if in 3/3 runs the response names the new
  parent task (`task/the-quarterly-audit-trail-slice`) as **bare text** — no markdown link syntax
  (`[...](...)`) wrapping it anywhere in the response. A bare `lore record show
  task/the-quarterly-audit-trail-slice`-style line, or the identifier typed plainly in prose, both
  count as bare. **FAIL for the red-state claim** if any run renders it as a markdown link — that
  would mean the affordance is already present, unprompted, in unedited prose, which would falsify
  this slice's own premise and must be reported as such rather than reinterpreted.
- **treatment (task 3, not run here)** — **PASS** only at 3/3: in every run, the new parent task
  is rendered as a markdown link whose visible text is exactly `kind/slug`
  (`task/the-quarterly-audit-trail-slice`) and whose target is
  `<base>/records/fieldnotes/task/the-quarterly-audit-trail-slice`; the closing handoff command
  (`/craft:plan task/the-quarterly-audit-trail-slice`) stays bare, on its own line, never itself a
  link. **2/3 is a FAIL, not a pass with a caveat** — per the task body's stated threshold. Any
  run that links the handoff command, or fabricates a URL for a value claim or vault name, is also
  a FAIL for that run.
- **reader-absent (task 3, not run here)** — **PASS** only at 3/3: in every run, the new parent
  task is printed as the bare `kind/slug` identifier (`task/the-quarterly-audit-trail-slice`), no
  URL assembled around it in any form (no hand-built `<base>/records/...`, no bare
  `http://127.0.0.1:7313/...`, no markdown link with an invented target). This is the pass
  condition Council Critical 1's resolution exists to make measurable: a session with the ritual
  edit but no resident link rule must degrade to the same bare state as today's baseline, never
  hand-assemble a URL from the grammar it was never given.

- **rule-only** — **not scored pass/fail.** This arm *attributes* the treatment result, and both
  of its outcomes are reportable findings rather than errors. If in 3/3 runs the new parent task is
  rendered as a markdown link, the reader's rule alone is sufficient at this site and the ritual
  edit's marginal contribution to producing the link is nil — this slice's own premise (that the
  instruction is crowded out by 581 lines of surrounding procedure) is **falsified at this site**,
  and must be reported as falsified, never reinterpreted into a weaker claim that still reads as a
  pass. If any of the 3 runs leaves the identifier bare, the rule alone is not reliable here and
  the ritual edit carries measurable weight; report the count. Registering both branches before the
  arm exists is what stops either result from being graded retrospectively into the answer this
  slice wanted.

**INCONCLUSIVE (applies to treatment vs. reader-absent, once both are run):** if the reader-absent
arm also renders a markdown link (correct or not), the "conditional on the rule being resident"
claim is unmeasured — the model produced a link with no rule to have derived the form from, which
means either arm's result cannot be attributed to the rule's presence or absence. Report this
explicitly rather than scoring either arm as a pass.

**Infrastructure failure is excluded, never scored as non-compliance** (task body fact 7): a
`claude -p` process that times out, rate-limits, or crashes is excluded and re-run; the log records
the exclusion and its reason so it is visible rather than silently dropped from the denominator.

## Re-run trigger

Re-dispatch every already-run arm whenever any of the following changes:

- the `## Outcome` section of `slice/SKILL.md` itself, in any way;
- the record-URL contract's path form or base-URL precedence
  (`tools/lore/plugins/lore/lore/record_url.py`);
- the `## Record links` section of `tools/outpost/plugins/outpost/rules.md`.

A case with no written trigger is run once at ship and never again; this line is that trigger.

## Limitations

One ritual site of the seven AC6 covers (`slice`'s `## Outcome`); the other six
(`brainstorm`, `gauntlet`, `plan`, `execute`, `review`, `distill`) are not measured by this case.
Steps 1–10 of the procedure are stubbed as already-true state rather than actually executed, so
this case says nothing about whether the model reaches a correct step-10 outcome on its own — only
about what it reports once there. One model tier, 3 runs per arm (this task) plus 3 runs each for
two arms not yet run. `Read`-only tools, no shell, no PreToolUse hook (settings-sources project
drops user-level hooks along with rules) — so, as with the outpost precedent, this case says
nothing about what happens when an arm can reach for a shell instead of following the instruction.
The `Read`-only grant means no arm can run `lore vault ls` or read `config.json` or
`LORE_RECORD_URL_BASE`, so a run's correct-looking `fieldnotes` vault segment and
`http://127.0.0.1:7313` base cannot be distinguished from interpolation of the fixture's own
strings, and the rule's own "print it bare when the vault can't be resolved" branch is never
exercised here. Until the rule-only arm above was added, baseline and treatment differed on two
variables at once, so no result in this case attributed the link to the ritual edit rather than to
the appended rule; that confound is what the rule-only arm exists to resolve.
Does not cover first-mention-vs-every-row behaviour in a table/list, adherence late in a long
session, or the other six rituals' own crowding conditions, which may differ in shape and severity
from `slice`'s.

## Extension to the ritual set (task/extend-the-eval-case-to-the-ritual-set-and-pre-register-the-expected-verdict)

**Committed before any of the six new arms below is run.** This section closes AC6's breadth gap
(does the reader's rule hold at every one of the seven pinned rituals, or only at `slice`?) and
opens AC7's baseline measurement (does each ritual's committed prose already place its one next
command alone on its own line, or does it print the command mid-sentence, the shape
`task/every-pinned-ritual-links-the-record-it-acted-on-and-prints-its-one-next-command-on-its-own-line`
names as today's defect)? Both are graded off the **same** captured run, one per ritual, using the
grader `task/ship-a-tested-grader-for-the-two-deliverable-predicates` shipped:
`tools/craft/plugins/craft/scripts/ritual_deliverable_grader.py`.

**No prose is edited here.** Every arm below is built from the ritual's committed text exactly as
it stands in this repository at the time this section was written — the `rule-only` construction
(that ritual's full prose plus the reader plugin's `## Record links` rule tail, appended verbatim,
the same 14-line block `arms/rule-only.md` above appends). This is the arm that reflects a real
installed session: craft plus outpost. There is no `baseline` (no-rule) or `treatment` (edited
prose) arm for these six rituals — the 2x2 that established the rule as the sole determinant was
already run once, at `slice`, and re-running the no-rule cells at six more sites tests the reader
plugin again, not anything about these rituals.

**Which single outcome per ritual is measured.** Per `## U3 resolved — ritual outcome inventory`
on `task/every-pinned-ritual-links-the-record-it-acted-on-and-prints-its-one-next-command-on-its-own-line`,
each ritual has one or more terminal outcomes; the classification there is ground truth and is not
re-derived here. One outcome per ritual is fixtured — chosen for being the clearest one-command (or,
for `execute`, the pinned by-design-zero-command) case, matching this case's own precedent of
measuring one site's one outcome rather than every branch. The branching outcomes each ritual also
has are not fixtured; AC7 exempts them by construction, so a grader run against one would only
re-confirm the `--exempt` bucket the branching-outcome tests in
`tools/craft/tests/test_ritual_deliverable_grader.py` already cover.

| Ritual | Fixture / Arm | Site (committed prose) | Record acted on | Grader invocation |
|---|---|---|---|---|
| brainstorm | `fixtures/brainstorm-completed-run.md` / `arms/brainstorm-rule-only.md` | `skills/brainstorm/SKILL.md:533-557`, Exit Gate, all-green handoff | `spec/warehouse-picking-batches` (vault `northlight`) | `--record spec/warehouse-picking-batches --command "/craft:gauntlet spec/warehouse-picking-batches"` |
| gauntlet | `fixtures/gauntlet-completed-run.md` / `arms/gauntlet-rule-only.md` | `skills/gauntlet/SKILL.md:435-499`, step 6 advance handoff | `spec/permit-renewal-workflow` (vault `northlight`) | `--record spec/permit-renewal-workflow --command "/craft:slice spec/permit-renewal-workflow"` |
| plan | `fixtures/plan-completed-run.md` / `arms/plan-rule-only.md` | `skills/plan/SKILL.md:563-586`, step 9 handoff prompt | `task/the-ledger-reconciliation-slice` (vault `causewell`) | `--record task/the-ledger-reconciliation-slice --command "/craft:execute task/the-ledger-reconciliation-slice"` |
| execute | `fixtures/execute-completed-run.md` / `arms/execute-rule-only.md` | `skills/_shared/execute.md:979-988`, clean-close completion report | `task/the-ledger-reconciliation-slice` (vault `causewell`) | `--record task/the-ledger-reconciliation-slice --exempt` (Finding 1: zero commands by design, not a failure) |
| review | `fixtures/review-completed-run.md` / `arms/review-rule-only.md` | `skills/review/SKILL.md:102-124`, "loop complete" handoff (:118-120) | `spec/dock-scheduling-windows` (vault `harborlight`) | `--record spec/dock-scheduling-windows --command "/craft:distill spec/dock-scheduling-windows"` |
| distill | `fixtures/distill-completed-run.md` / `arms/distill-rule-only.md` | `skills/distill/SKILL.md:435-446`, single-cluster "ADRs written" outcome | `adr/dock-scheduling-windows-use-fifo-slots` (vault `harborlight`) | `--record adr/dock-scheduling-windows-use-fifo-slots --command "lore record show adr/dock-scheduling-windows-use-fifo-slots"` |

**Why `execute`'s record and `plan`'s record are the same identifier.** `plan`'s fixture stubs the
Council Review as already accepted for the slice-parent `task/the-ledger-reconciliation-slice`; the
`execute` fixture stubs a *separate, independent* run of the pipeline's next stage against that same
task id (its own stubbed state — dispatch counts, phase outcomes — is unrelated to plan's). The two
fixtures are never dispatched together, and reusing the id only means both sites are measured
against a plausible, internally-consistent slice name; it creates no data dependency between the
two `claude -p` processes Task 4 runs.

**`execute`'s baseline arm.** `skills/execute/SKILL.md` is a 55-line wrapper that "does not restate"
the procedure and instructs the reader to follow `../_shared/execute.md` "end to end." The site
under test (:979-988) lives entirely inside `_shared/execute.md`, so `arms/execute-rule-only.md` is
the wrapper **plus** the full shared procedure, concatenated in that order, with the reader rule
tail appended last — the wrapper alone never reaches the completion-report text, and appending only
the shared file would silently drop the "read this file and follow it" instruction the wrapper
issues. The other four documents `execute/SKILL.md` names (`status-ownership.md`, `refine.md`,
`slice.md`, `security.md`) govern the per-task loop and the task-shape branch, not the closing
report — the fixture stubs every task already built and every phase already closed, so those four
documents' content is never consulted to produce the deliverable under test, and appending them
would only pad the arm's context with unused instructions.

**Contamination check, per ritual — against that ritual's own worked examples, not `slice`'s.**
Every one of `brainstorm`, `gauntlet`, `plan`, and `review`'s own handoff examples uses
`spec/streaming-export` (brainstorm, gauntlet, review) or `task/streaming-export` (plan) as the
placeholder-substitution example; `distill`'s own worked example uses
`adr/record-ops-locate-by-config-order-scan`; `execute`'s shared procedure names no concrete record
id anywhere (confirmed: `grep -o 'task/[a-z0-9-]*' skills/_shared/execute.md` matches nothing but
the bare word `task/` inside prose, never a full worked identifier). None of the six fixtures above
reuses any of those strings, or `fieldnotes`/`quarterly-audit-trail` (the `slice` case's own
identifiers) — each fixture's spec/task/adr slug, and its vault (`northlight`, `causewell`,
`harborlight` — three new names, none of them a real configured vault on any development machine),
is novel. A run cannot reach the correct rendering by pattern-matching a worked example already
present in its own instructions; it has to apply the instruction to the state the fixture actually
gives it. No fixture file contains a `[[wikilink]]` or a path resolving to a real vault, record,
config file, or repository — this was checked by grep against each of the six new fixture files for
`northlight`, `causewell`, `harborlight`, and every slug above, confirming each string's only
occurrences are inside the eval case's own `fixtures/` and `arms/` files and this `expected.md`.

## Dispatch (six new processes per run, 18 total; 3 runs each = the run budget below)

Same shape as the existing dispatch, one `claude -p` process per arm per run, never a subagent, with
the ritual-specific fixture substituted for `FIXTURE_COMPLETED_RUN_PATH`:

```sh
RITUAL=brainstorm   # or gauntlet, plan, execute, review, distill
EVAL="$(pwd)/tools/craft/plugins/craft/evals/ritual-deliverable-names-its-record"
COMPLETED_RUN="$EVAL/fixtures/${RITUAL}-completed-run.md"
PROMPT="$(sed "s#FIXTURE_COMPLETED_RUN_PATH#$COMPLETED_RUN#" "$EVAL/fixtures/task-prompt.md")"

claude -p "$PROMPT" --setting-sources project \
  --append-system-prompt "$(cat "$EVAL/arms/${RITUAL}-rule-only.md")" \
  --allowedTools "Read" < /dev/null
```

`fixtures/task-prompt.md` is unchanged and reused as-is — it names no ritual, only the fixture path
substituted into it, so the one file already committed serves all seven arms.

## Run budget

**3 runs per ritual, 6 rituals, 18 processes** — the run budget for this task's extension, decided
by the operator at planning alongside the original `slice` case's own 3-per-arm budget (recorded in
the parent plan's Given Axioms). This extension grades a single rule-only-style axis, so only
`slice`'s already-run `rule-only` arm (3 processes, already spent) counts toward the combined total
— its `baseline` arm belongs to the earlier, already-closed 2x2 axis and is not part of this count.
18 new + 3 already-spent is **21 processes total across the whole ritual set** — this task commits
`expected.md` and spends none of them;
`task/run-the-baseline-across-the-seven-rituals-and-grade-it` (Task 4) dispatches and grades all 18.

## Pre-registered split rule (Council amendment, Important, folded in)

Each ritual's 3 runs are graded independently per predicate (`record-link`, `next-command`). A
predicate's 3 runs can land 3-0, 2-1, or (in principle) split further if a run is excluded as an
infrastructure failure and re-run. The rule below is decided now, before any run, so a 2-1 result
cannot be read into whichever verdict this task's authors preferred:

- **3/3 agreeing** — that predicate's verdict for that ritual is decided: PASS if all 3 are the
  passing verdict for that predicate (`link` for record-link; `own-line` or `exempt` for
  next-command), FAIL if all 3 are the failing verdict.
- **2-1 split** — **re-run that ritual's arm 3 more times** (6 runs total for that ritual, not a
  cherry-picked re-run of only the outlier — a fresh batch, so an unlucky single sample is not
  mistaken for the model's modal behaviour). Combine all 6: if the combined result is a clean
  majority in either direction (5-1 or 6-0 either way; 4-2 is **not** clean — see below), report
  that majority as the verdict and name the split explicitly in the report rather than presenting it
  as an uncomplicated 3/3. If the combined 6 is still not a clean majority (4-2, or another 2-1-style
  tie after the re-run), **report that predicate for that ritual as AMBIGUOUS** — not as a pass with
  a caveat, not as a fail with a caveat. An ambiguous predicate is not scored either way in the AC6 /
  AC7 attestation; it is named as unresolved for `task/every-pinned-ritual-links-the-record-it-acted-
  on-and-prints-its-one-next-command-on-its-own-line`'s own judgment, the same way this case already
  refuses to reinterpret a falsification into a weaker passing claim.
- This mirrors, and does not relax, the existing case's own rule for its `treatment` and
  `reader-absent` arms above ("2/3 is a FAIL, not a pass with a caveat") — those arms use a 3/3-only
  bar because they are graded against a single fixed pass condition with no re-run provision written
  for them; this section adds the re-run-once-then-ambiguous provision those two arms' pass
  conditions do not carry, because those two arms are not part of this task's extension and this
  task may not silently loosen their already-registered rule.

## Stated limitation — the capability grant (Council amendment, Critical 4, resolved)

Every arm above, like `arms/baseline.md` and `arms/rule-only.md`, is dispatched with
`--allowedTools "Read"` and no shell — no `Bash`, `Edit`, `Write`, or any tool that could reach
outside the fixture. This is containment by capability grant, not by sandbox: `scripts/eval-sandbox`
is a macOS seatbelt profile and this host is Linux, so it is not available to any arm run for this
task (see "Why no `scripts/eval-sandbox`" above, which this extension does not restate). A read-only
arm with no shell cannot invoke `lore`, resolve a real vault, read a real `config.json`, or read
`LORE_RECORD_URL_BASE` — so, exactly as the existing case's own Limitations section already states
for `slice`, **a correct-looking `northlight` / `causewell` / `harborlight` vault segment or
`http://127.0.0.1:7313` base in a captured response is not by itself proof the agent resolved
anything; it is equally explained by interpolation of the fixture's own strings.** No fixture used
by this task's extension points at a real vault, repository, or configuration, not even read-only —
each fixture's vault name and record slugs are fabricated for this case alone (see the contamination
check above) and resolve to nothing on disk.

**Diff the developer's real state after Task 4's batch runs** — the real vaults under
`$XDG_STATE_HOME/lore/vaults` (or the platform-appropriate equivalent) and any path named in any of
the fixtures above — is Task 4's obligation, not this task's, since this task dispatches nothing.

## Pre-registered vacuity rule for record-link, per ritual (committed before any arm runs)

`record-link` is not a uniform "link is the pass" predicate across the seven. Whether a ritual's
committed deliverable carries a **linkable prose mention** of the acted-on record — a mention
**outside** the handoff command, since the reader rule's own "a handoff command stays bare" clause
forbids linking the command itself — determines what each verdict *means* at that site. This is
decided now, from the committed prose, before any of the 18 processes runs, so a `bare` result
cannot be read after the fact as "that site was vacuous all along."

Per ritual, verified against the committed file:

- **brainstorm** (`skills/brainstorm/SKILL.md:552-557`) — the handoff prose names the record *kind*
  only ("The spec is saved as a lore `spec` record"); the id itself appears solely inside the
  fully-formed handoff command (:557, `` `/craft:gauntlet spec/streaming-export` ``). **No linkable
  prose mention.**
- **gauntlet** (`skills/gauntlet/SKILL.md:481-485`) — the wrap-up prose names no id outside the
  command; the id appears only inside `` `/craft:slice spec/streaming-export` `` (:485). **No
  linkable prose mention.**
- **plan** (`skills/plan/SKILL.md:574-586`) — "Plan is written to your vault" names no id in prose;
  the id appears only inside `` `/craft:execute task/streaming-export` `` (:586). **No linkable
  prose mention.**
- **execute** (`skills/_shared/execute.md:979-988`) — the completion report's worked example names
  no record id at all, and prints zero commands. **No linkable prose mention.**
- **review, fixtured at the loop-complete outcome** (`skills/review/SKILL.md:118-120`) — "The slice
  loop reports spec/streaming-export closed out." is prose naming the id, outside the backtick-wrapped
  command on the line that follows it. **Linkable prose mention exists.**
- **distill** (`skills/distill/SKILL.md:437`) — "the ADRs written (with their record ids)"
  *instructs* the outcome report to name each written ADR's id in prose, separately from the `lore
  record show` command given at :446 — unlike review and slice, this site carries no literal
  worked-example sentence with a record id already embedded in prose; it is an instruction to
  produce one. **Linkable prose mention exists, but on weaker evidence than review's and slice's —
  see the two-sided note below.**
- **slice** (already measured in the committed case above) — `## Outcome`'s selection-path sentence
  names the new parent task's id in prose, separately from the `lore record show` command it
  replaces. **Linkable prose mention exists** — matches the measured 3/3 `link` result.

**What each predicate outcome means, per the above:**

- **Where a linkable prose mention exists** (review at loop-complete, distill, slice): `record-link:
  link` is AC6 **holding** at that site. `record-link: bare` is **AC6 falsified at that ritual** —
  reported as falsification, never softened into a weaker passing claim.
- **Distill's evidence for "linkable prose mention exists" is weaker than review's and slice's, and
  that asymmetry is pre-registered here rather than left to be noticed after results land.** Review
  and slice each carry a literal worked-example sentence with a record id already embedded in
  prose; distill's site (:437, "the ADRs written (with their record ids)") is an *instruction* to
  name the id, not a worked example already containing one. Two-sided consequence: a `record-link:
  link` result at distill counts as AC6 holding there on exactly the same terms as at review or
  slice — the instruction was followed. A `record-link: bare` result at distill is still **scored
  FAIL, reported as AC6 falsified at distill** — the pass condition below does not change — but the
  report must flag alongside that FAIL that it rests on weaker pre-run evidence than the review or
  slice falsification would, because distill's site never demonstrated in prose that the model
  produces the id, only that it is told to. A bare result there is not to be read as AC6 failing
  more severely than at review or slice, nor is it to be softened, excused, or moved out of the FAIL
  bucket on account of the weaker evidence — the weaker evidence base is a note on the finding's
  strength, not a route to a different verdict.
- **Where none exists** (brainstorm, gauntlet, plan, execute): `record-link: bare` is **correct
  behaviour, not a failure** — the criterion is vacuously satisfied, exactly as AC7 is vacuously
  satisfied at execute's zero-command close. **AC6's breadth claim is therefore established at three
  sites (review, distill, slice), not seven** — distill's contribution to that count resting on the
  weaker instruction-only evidence noted above — and the completion report must say so in those
  words rather than implying seven-ritual coverage.
- **A `record-link: link` at a vacuous site** (brainstorm, gauntlet, plan, execute) would mean the
  agent linked the handoff command itself, which the reader rule's "a handoff command stays bare"
  clause forbids. This is pre-registered as **its own distinct finding — a rule violation, not a
  pass** — and reported as such rather than folded into either PASS or FAIL bucket above.

## Pass condition, per ritual (two-sided, per the shape this case already uses)

For each ritual above, both predicates are graded from the grader's stdout on each of the 3 (or,
under a 2-1 split, 6) captured runs, subject to the same completion-marker and infrastructure-
exclusion rules the existing `slice` arms use (a `claude -p` process that times out, rate-limits, or
crashes is excluded and re-run; never scored as non-compliance).

- **record-link** — graded per the vacuity rule immediately above, not uniformly. At a
  **linkable-mention site** (review at loop-complete, distill, slice — distill's on the weaker,
  instruction-only evidence base pre-registered above): **PASS (AC6 holds)** if the
  grader reports `record-link: link` for every run counted under the split rule below; **FAIL,
  reported as a falsification of AC6's breadth claim at this site** — not reinterpreted as a partial
  pass, and at distill flagged alongside the weaker-evidence note rather than softened by it — if it
  reports `record-link: bare` for every counted run. At a **vacuous site** (brainstorm,
  gauntlet, plan, execute): `record-link: bare` for every counted run is the expected, correct
  result and is **not scored as a failure**; `record-link: link` for any counted run is **not scored
  as a pass** either — it is reported as a rule-violation finding (the handoff command was linked).
  A split that does not resolve per the rule below is AMBIGUOUS, not PASS, at either kind of site.
- **next-command — PASS (AC7 holds at this ritual)** if the grader reports `next-command: own-line`
  (or, for `execute`, `exempt`) for every counted run. **FAIL, and reported as a falsification of
  AC7's baseline claim at this site** if the grader reports `next-command: embedded` or `absent` for
  every counted run — this is the concrete shape `task/every-pinned-ritual-links-the-record-it-
  acted-on-and-prints-its-one-next-command-on-its-own-line`'s own Given Axioms predicted for at
  least `brainstorm` (:552-557), `gauntlet` (:484-485), and `review` (:118-120), each of whose committed
  prose embeds the handoff command inside a sentence rather than placing it alone; this section does
  not pre-judge that prediction as failing, since the grader — not this document — is what decides
  it once the fixture is actually run, but a FAIL here is expected to be unsurprising at those three
  sites and should not be treated as evidence of a fixture defect before the captured text is read.
- **Per-ritual, per-predicate result feeds `task/every-pinned-ritual-links-the-record-it-acted-on-
  and-prints-its-one-next-command-on-its-own-line`'s U1 and U2 unknowns directly**: U2 (does AC6
  hold, at the three sites where it is measurable?) closes once every linkable-mention-site ritual
  (review, distill, slice) reports `record-link: link` per the vacuity rule above, or is corrected
  to name whichever of those three falsifies it (per that parent's Council amendment: an AC6 failure
  at any ritual blocks the slice from closing, not a task to route around here — distill's
  falsification carries the weaker-evidence flag pre-registered above but is not exempted from this
  closure rule) — the four vacuous
  sites (brainstorm, gauntlet, plan, execute) contribute no PASS toward this closure, only the
  absence-of-violation check the vacuity rule names. U1 (does
  the baseline already satisfy AC7?) closes per-ritual from the next-command predicate above; any
  ritual whose next-command predicate reports FAIL is a candidate for
  `task/conditional-fix-the-handoff-shape-where-the-baseline-fails-ac7-and-re-measure` (Task 5),
  scoped to that one ritual's own handoff shape and re-measured against its own baseline so exactly
  one variable moves, per the parent's Delta design.


## Extension — Task 5, the conditional AC7 remediation at `plan` and `review`

**Committed before either treatment arm below is run.** Task 4's baseline measured
`next-command: embedded` 3/3 at `plan` and 6/6 at `review` — the only two of the six rituals AC7
falsified at. This section pre-registers the re-measurement of those two rituals only, each against
its own already-measured `rule-only` baseline (`arms/plan-rule-only.md`, `arms/review-rule-only.md`
— unedited prose + reader rule), so exactly one variable moves per ritual: the edited handoff shape.
Brainstorm, gauntlet, execute, and distill are untouched and not re-measured — their `next-command`
predicate already holds at baseline (Task 4).

**The edit.** `plan/SKILL.md` step 9's handoff prompt and `review/SKILL.md`'s closing handoff (the
loop-complete → distill outcome, the one outcome Task 4's fixture exercises) are edited so the
single next command is printed alone, in a fenced code block, separated from the quoted prose —
never embedded mid-sentence. No other change is made to either file: `arms/plan-treatment.md` and
`arms/review-treatment.md` are byte-identical to `arms/plan-rule-only.md` and
`arms/review-rule-only.md` respectively except for this one handoff's shape (confirmed by `diff`
before dispatch — each diff touches only the handoff paragraph and its replacement fenced block, no
other line). The record-link rule tail each arm appends is byte-identical to the tail
`plan-rule-only.md`/`review-rule-only.md` already carry — the reader rule is not touched, per this
task's own instruction not to restate or re-derive the record-link rule.

**Fixtures reused unmodified** — `fixtures/plan-completed-run.md` and
`fixtures/review-completed-run.md`, the same two fixtures Task 4 ran, unedited. Dispatch command is
Task 4's own extension-dispatch shape with `RITUAL` fixed to `plan` and `review` and `-treatment`
substituted for `-rule-only` in the arm path:

```sh
RITUAL=plan   # or review
EVAL="$(pwd)/tools/craft/plugins/craft/evals/ritual-deliverable-names-its-record"
COMPLETED_RUN="$EVAL/fixtures/${RITUAL}-completed-run.md"
PROMPT="$(sed "s#FIXTURE_COMPLETED_RUN_PATH#$COMPLETED_RUN#" "$EVAL/fixtures/task-prompt.md")"

claude -p "$PROMPT" --setting-sources project \
  --append-system-prompt "$(cat "$EVAL/arms/${RITUAL}-treatment.md")" \
  --allowedTools "Read" < /dev/null
```

**Run budget.** 3 runs per ritual, 2 rituals, 6 processes — matching Task 4's own 3-per-ritual
budget and the parent's Given Axioms.

**Pass condition, two-sided, per ritual** — graded from
`tools/craft/scripts/ritual_deliverable_grader.py` on each captured run (`--record
task/the-ledger-reconciliation-slice --command "/craft:execute
task/the-ledger-reconciliation-slice"` for `plan`; `--record spec/dock-scheduling-windows --command
"/craft:distill spec/dock-scheduling-windows"` for `review`), never an inline regex, subject to the
same completion-marker and infrastructure-exclusion rules as every other arm in this case:

- **Treatment worked (AC7 now holds at this ritual)** — `next-command: own-line` for every one of
  the 3 counted runs. Report this as the edit moving the measurement from its own `embedded` 3/3 (or
  6/6) baseline to `own-line` 3/3.
- **Treatment failed (the edit did not move the measurement)** — `next-command: embedded` or
  `absent` for any of the 3 counted runs. Report this as a negative result, exactly as
  `expected.md`'s existing falsification language requires elsewhere in this case — never
  reinterpreted into a weaker claim that still reads as a pass. Per the parent task's binding revert
  rule, a ritual that fails here has its edit reverted to the pre-edit text before this task closes,
  verified by an empty `diff` against the pre-edit baseline.
- **A 2-1 split within either ritual's 3 runs** — re-run that ritual's arm 3 more times (6 total),
  per Task 3's pre-registered split rule above; a combined result that is not a clean majority
  (4-2 or another non-resolving split) is AMBIGUOUS and is treated as a failure to move the
  measurement for purposes of the revert rule — AMBIGUOUS is not evidence the treatment worked.
- **record-link is not re-graded here.** Neither edit touches the record-link rule or the sentence
  it acts on; Task 4 already measured `record-link: link` 3/3 (`plan`, vacuous site — a rule-
  violation-shaped finding, not scored) and 5/6 (`review`, PASS) against the unedited prose, and
  this task's edit changes no text `record-link` reads. Re-running that predicate here would not be
  measuring anything this task changed.

**Contamination check.** Both treatment arms are checked identical to their own `rule-only`
counterpart apart from the one handoff paragraph, by `diff` before dispatch (see "The edit" above) —
no other line, including the appended reader-rule tail, differs.

## Extension — Task 3, pre-registration for the three remaining AC7 outcome sites

**Committed before any arm below is run.** Task 4 measures these three sites against current
committed prose; Task 5 edits whichever of them AC7 falsifies; Task 6 re-measures. This section
fixes, in advance, the arm construction, the exact grader invocation, the run count, the pass
condition, and the tie-break — so no decision here can be retrofitted after a result is seen.

**The three sites** are `execute`'s completion report, `review`'s **second** outcome (the slice loop
is *not* complete, handing back to `/craft:slice`), and `slice`'s selection handoff. `slice`'s
worked-example sentence is **not** a site: it emits no deliverable, and is handled as prose
consistency in the treatment task, never graded here.

### Ruling — `execute` is measurable, not exempt (Council amendment, Critical, Reliability)

The previous slice graded `execute` `--exempt` on the basis recorded at "Finding 1: zero commands by
design, not a failure" — execute's completion report mandates no handoff, because
`_shared/execute.md` instructs it **not** to invoke `/portage:pull_request` automatically, leaving
that call to the operator. **That basis is restated here and rejected as the wrong predicate**, per
this task's instruction not to re-use the classification without restating its basis.

The basis answers "does this ritual mandate a next command?" AC7 asks a different question: "when
this ritual prints a command, is it on its own line?" Those come apart exactly here. The changed
grader (Task 2) settles it as fact rather than as reading: all three committed `execute` captures
under `runs/` report `exempt-observation: embedded` once a command is supplied. The deliverable does
name a command, and it names it mid-sentence — the failing shape — at the one site the previous
measurement recorded as passing. An exempt verdict there reported a pass at an artifact nobody had
looked at, which is the hole Task 2 closed.

**Ruling: `execute` is graded as a measurable site, with `--command "/portage:pull_request"` and no
`--exempt` flag.** Its pass condition is `next-command: own-line`, identical to every other
measurable site.

**The considered alternative, and why it loses.** Printing the command on its own line could be read
as contradicting execute's deliberate "this call is yours" framing — an own-line command looks like
a handoff to paste now. It does not: the deferral sentence and the own-line command compose without
conflict (state that no PR was opened and that the call is the operator's, *then* print the command
alone on its line). There is no tension to trade off, so the framing is not a reason to exempt.

**Footprint consequence, inherited not re-derived.** This ruling puts `execute/SKILL.md` and
`_shared/execute.md` inside Task 5's file footprint if and only if Task 4 measures `execute` as
falsifying AC7. Task 5 inherits this decision and does not re-open it.

### Per-site pre-registration

Every arm below is current committed prose plus the reader-rule tail — the live arms Task 1's
manifest already pins as byte-identical to their sources. **No new arm file is created for the
baseline**; Task 5 creates treatment arms if and only if it edits a site.

| Site | Arm | Fixture | Grader invocation | Runs |
|---|---|---|---|---|
| `execute` | `arms/execute-rule-only.md` | `fixtures/execute-completed-run.md` | `--record task/the-ledger-reconciliation-slice --command "/portage:pull_request"` | 3 |
| `review` second outcome | `arms/review-treatment.md` | `fixtures/review-loop-open-completed-run.md` (new, see below) | `--record spec/dock-scheduling-windows --command "/craft:slice spec/dock-scheduling-windows"` | 3 |
| `slice` selection handoff | `arms/rule-only.md` | `fixtures/slice-completed-run.md` (new, see below) | `--record task/the-berth-allocation-slice --command "/craft:plan task/the-berth-allocation-slice"` | 3 |

**Run budget: 9 processes** — 3 per site, matching this case's existing 3-per-ritual budget.

### Pass condition, per site — two-sided, in the grader's published tokens

Graded only by `tools/craft/plugins/craft/scripts/ritual_deliverable_grader.py`, never an inline
regex. The tokens below are the grader's own contract vocabulary — `own-line`, `embedded`,
`absent`, `exempt` — quoted verbatim so a later grading pass cannot reinterpret them:

- **AC7 holds at this site** — `next-command: own-line` for all 3 counted runs.
- **AC7 falsified at this site** — `next-command: embedded` or `next-command: absent` for any
  counted run. Reported as a falsification, never softened into a weaker claim that still reads as a
  pass. A falsified site enters Task 5's treatment footprint.
- **`next-command: exempt` is not a reachable verdict at any of these three sites**, because no
  invocation above passes `--exempt`. A grading pass that emits it means the invocation drifted from
  this pre-registration, and is a defect in the run rather than a result.
- **`record-link` is not graded at these three sites.** Each is a `next-command` measurement only;
  AC6's per-ritual vacuity rule above already governs `record-link` and none of these sites changes
  the text that predicate reads.

### Tie-break — a 2-1 split within one site's 3 runs

Re-run that site's arm 3 more times, 6 total. A clean majority across the 6 resolves the site. A
combined result that is not a clean majority (4-2, or any other non-resolving split) is
**AMBIGUOUS**, and AMBIGUOUS is treated as **AC7 falsified** for that site — never as evidence the
site passes. This matches the split rule this case already pre-registered for the Task 5 treatment
arms, deliberately rather than by coincidence.

### What counts as a reachable fixture for `review`'s second outcome

The previous slice re-pointed `fixtures/review-completed-run.md` to the loop-complete branch, so the
second outcome has no fixture today. A fixture is **reachable** for this measurement when it meets
all three, and the site is measured if and only if it does:

1. It asserts the ritual's pre-handoff state as already true and asks only for the closing report —
   the technique every existing fixture in this case already uses — so the arm needs no tool beyond
   `Read` and performs no vault write.
2. It differs from `fixtures/review-completed-run.md` **only** in the state that selects the branch:
   the `craft/slice-loop` marker reads as *not* complete, so slices remain to be chosen. Every other
   line is identical, so the branch is the one variable.
3. The captured deliverable is `review`'s second-outcome handoff and not its loop-complete handoff —
   confirmed by the capture naming `/craft:slice` rather than `/craft:distill`. A capture landing on
   the wrong branch is a fixture defect, discarded and rebuilt; it is never graded.

The same three conditions govern `slice`'s new fixture, with the branch condition replaced by: the
spec, its ledger, and the chosen slice are asserted as already decided, so the deliverable is the
selection handoff alone and no vault write is required to reach it.

**If a site proves unreachable under these conditions, it is reported unmeasured — never estimated,
and never inferred from a neighbouring site.** An unreachable site does not enter Task 5's treatment
footprint, because an edit whose effect cannot be measured is not a remediation.

### Capture naming — the historical pin must keep its original invocation

New captures from Task 4 and Task 6 land under `runs/` alongside the 30 the previous slice
committed, which are immutable evidence. `execute`'s pinned *historical* invocation is `--exempt`;
its pinned *future* invocation is the measurable one ruled above. Both must survive, so new captures
carry a filename prefix distinct from any existing one rather than extending an existing ritual's
numbering. Re-grading history under a later ruling would rewrite what the previous measurement
actually recorded, which is precisely what the regression pin exists to prevent.
