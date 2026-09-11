# ritual-deliverable-names-its-record — does a ritual's closing report link the record it acted on?

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
