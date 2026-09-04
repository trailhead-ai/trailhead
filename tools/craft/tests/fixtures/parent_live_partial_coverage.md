**Value claim:** An operator running the slice loop on a spec whose criterion is delivered
across two slices several passes apart gets the uncovered half kept in the candidate set, so
the loop cannot report the spec complete while that criterion is still half-built. This is the
failure the spec's Problem section names, and the one that full-coverage termination — the
previous slice — structurally cannot reach.

**Covers:** AC5, AC6

**Scope note:** this slice touches no visual surface, so it carries no `## Enumerated states`
section.

**Selection note:** chosen at levels 1 and 2 of `_shared/slice.md`. Level 1 — the ledger's
coverage interface has a proven read path, so partial coverage is the earliest incomplete
phase on it; the criterion-content bars sit in polish on a checker that already exists.
Level 2 — the partial-coverage token shape is the interface the remaining ledger criteria all
read through, so it wants to exist while it is still cheap to change. Level 3 then took the
write and read sides together rather than the write side alone, because a partial marker that
nothing reads delivers nothing observable.

**Goal:** a slice that delivers only part of a criterion records that partiality, the ledger
reconcile carries it forward, and the next pass's candidate set retains the uncovered
remainder rather than dropping the criterion whole.

**Known boundary:** the sole-writer, append-only, union invariant is not this slice's subject.
It is the baseline the partial-coverage representation must not break, and the assertion
pinning it stays uncovered until a later pass claims it.

**Known boundary:** two ledger lines on this spec carry no coverage token at all, because both
their parents predate the `**Covers:**` field. The candidate-set gate therefore returns those
criteria as candidates on every pass, and the operator excludes them by hand. Settling that is
`task/reconcile-the-spec-ledger-coverage-tokens-and-settle-what-early-stop-names`, not this
slice.

## Delta design

Partial coverage is expressed as a second field beside `**Covers:**` rather than as a new
grammar inside it. A slice parent may carry `**Partially covers:** AC2`; the ledger reconcile
carries that into an optional sixth token on the entry's trailing parenthetical
(`, partially covers AC2`) after the existing `covers` token; and `candidate_set.py` reports a
fourth output line, `partial:`, while computing `candidates:` against the fully covered set
alone — so a partial-only criterion stays a candidate and no pass can terminate on it.

A second field, not `AC2 (partial)` inside the first, for three reasons in descending weight.
The ledger's trailing-parenthetical scanner is anchored on a paren group containing no nested
parens, so an inner `(partial)` would be matched as the whole field block and the entry would
fail to parse outright. A separate field reuses `parse_covers` and the drafted-value shape
check verbatim, so no new way exists for the two gates to disagree about what an identifier
list is. And it reads correctly in the rendered record, which is the surface an operator sees.

Union semantics follow the invariant the spec already pins: full coverage wins over partial
regardless of line order, because the reconcile is append-only and the union is order-free.
Eligibility is deliberately NOT the blocking mechanism — an entry carrying either field is a
modern entry, and only an entry carrying neither stays legacy. What blocks termination on a
half-built criterion is the candidate set, which is correct, because a spec can be entirely
modern and still have a criterion delivered by only one of the two slices that serve it.

## Given Axioms

- The ledger entry's trailing-parenthetical scanner forbids nested parens by construction:
  `_LEDGER_TRAILING_PAREN_RE = re.compile(r"\(([^()]*)\)\s*$")` at
  `tools/craft/plugins/craft/scripts/candidate_set.py:131`. This is what rules out an inline
  `(partial)` marker.
- The ledger field grammar is a single regex whose `covers` group is greedy `.+`:
  `_LEDGER_FIELDS_RE` at `tools/craft/plugins/craft/scripts/candidate_set.py:132`. A sixth
  field appended after it would be swallowed by that group, so the parenthetical must be split
  on the new token before the existing regex is applied to the head.
- `parse_covers` validates one identifier-list grammar and rejects duplicates, at
  `tools/craft/plugins/craft/scripts/covers_gate.py:212`. A second field of the same shape
  reuses it unchanged.
- `--covers` is currently `required=True` at
  `tools/craft/plugins/craft/scripts/covers_gate.py:233`; a slice that only partially covers a
  criterion needs it to become conditionally required instead.
- Coverage is the union across ledger lines, a line is never edited after append, and the
  reconcile is the sole writer of a coverage field — pinned as AC8 of
  `spec/acceptance-criteria-are-atomic-assertions-a-slice-carries` and as C5 of its gauntlet.
- This repository ships zero third-party runtime dependencies, declared prescriptively in
  `CLAUDE.md` rather than inferred from a manifest.
- The full repository suite requires `NO_COLOR=1` and the `./.venv/bin/python` interpreter;
  the PATH `python3` lacks `pytest-xdist` and the root config passes `-n auto`.

## Known Unknowns

None. Every axiom above is verifiable at a cited line, so no child task is gated on a proof.

## Lessons consulted

- `lesson/name-a-live-instance-as-a-fixture-source-when-a-dispatch-builds-a-parser-over-a-stored-format`
  — applies directly and is the sharpest one here: the previous slice's Critical defect was a
  parser whose every fixture used the tidy single-line shape from the format's own
  documentation. Prevention is carried into each task's test contract below as a named
  requirement to fixture the wrapped, real-world ledger shape, not only the tidy one.
- `lesson/re-mutation-check-a-defense-after-any-refactor-touches-the-surface-it-sits-on`
  — the ledger scanner is the surface being changed, and the fenced-block and HTML-comment
  defenses sit on it. Task 2 re-mutation-checks both after the change rather than trusting the
  existing tests to still discriminate.
- `lesson/the-untrusted-value-rules-are-not-confined-to-execute-md-s-phase-5` — the new
  `**Partially covers:**` value is vault-sourced and enters a command line, so it takes the
  same shape check the existing drafted `--covers` value takes, at its own site.
- `lesson/phrase-pinned-prose-contracts-break-on-line-wraps` and the no-prose-presence-tests
  constraint — the skill-wiring task asserts behaviour through the gate, never that the
  document contains a phrase.
- `lesson/a-timeout-mandate-cannot-rescue-a-suite-that-outruns-the-harness-ceiling-dispatch-a-scoped-suite`
  — each task's test contract names a scoped suite; the full suite runs once at the end.

## Flow-out

- [ ] Lessons written for anything that cost a retry
- [ ] Follow-ups filed for anything deliberately deferred
- [ ] Decisions worth keeping captured as records
- [ ] Session candidates flushed

## Council Review

*Reviewed at:* 2026-09-04T02:40:00Z
*Members dispatched:* builder, breaker, attacker, advocate

*Critical:* none surviving consolidation.

- One Critical was raised and **refuted at verification**, recorded here for the audit trail
  rather than dispositioned: Advocate claimed `candidate_set.py`'s ledger parser is assigned to
  no child task. The record refutes it — that work is
  `task/derive-the-candidate-set-with-partially-covered-criteria-retained`, which names the
  file in its Files list and whose whole test contract is the field split and the `partial:`
  line. The lens named tasks 1 and 3 by id and never read task 2. Verified in the adjudicating
  session against the child record itself.

*Important:*
- A partially covered criterion and one no slice has touched appear identically in
  `candidates:`, so the operator applying the selection rule cannot see that closing a
  candidate would finish a half-delivered criterion (raised by: Reliability AND Advocate —
  convergent across two lenses that could not see each other) — folded into task 3 as a
  step 4 reporting requirement with its own behavioural assertion.
- Four Given Axioms cited line numbers off by two to four, while the Known Unknowns section
  closed on the claim that every axiom is verifiable at a cited line (raised by: Builder) —
  verified wrong in the adjudicating session and corrected to 131, 132, 212, 233.
- Task 3's contract validated the drafted value's shape but never asserted `--covers` and
  `--partial-covers` are passed as two distinct quoted shell arguments, so a later edit
  collapsing them into one interpolated string could word-split a certified value beside an
  uncertified one (raised by: Security) — folded into task 3's test contract.
- The duplicate-identifier guard on `--partial-covers` comes only from the shared
  `parse_covers`, and was not asserted at its own call site, so a refactor that stopped
  sharing the function would drop it silently (raised by: Security) — folded into task 1.
- Concurrent `## Slices` appends are untested under the dual-field shape; a second optional
  token widens the surface a torn append can mangle, and the spec's own gauntlet logged
  concurrent appends as unaddressed (raised by: Reliability) — folded into task 2 as a
  fails-closed assertion rather than an assumption.
- The task 1 to task 2 dependency edge is sequencing preference, not a code dependency: task 2
  imports `parse_covers`, which task 1 does not modify (raised by: Builder). Left in place —
  the two touch adjacent grammar and serial execution costs nothing here — but named so it is
  not mistaken for a hard constraint.

*Minor:*
- Outpost's Specs surface has no marker distinguishing partial from full coverage, so a human
  skimming a rendered spec can misread partial as done (raised by: Reliability AND Advocate) —
  already an accepted risk with an owner and revisit condition on the spec itself.
- The pre-existing fail-closed gap where trailing operator prose after an entry drops that
  entry's coverage is inherited unchanged by the partial field (raised by: Reliability).
- Task 3 asserts the skill wiring by exercising the gate scripts rather than by running an
  agent through the skill text — an inherited gap the existing `--covers` wiring shares, not
  new risk (raised by: Builder).
- How the overlap refusal is surfaced to the operator distinctly from other gate refusals is

## Run Metrics

| Task | Band | Dispatches | Status | Drift-Gate | Model | Elapsed |
|---|---|---|---|---|---|---|
| certify-a-drafted-partial-coverage-list-at-the-covers-gate | Medium | 1 | DONE | DRIFT | Sonnet | 6m |

## End Phases

- base: c3181b79d3736f64de4d6517c473289a5811a8bb
- Drift note (task 1): DRIFT was bookkeeping only — the commit body's observation-points
  enumeration reported 3 grep hits where the delivered tree has 9, and omitted the new
  `--partial-covers` call site. Independently re-verified: no code defect, all 9 contract items
  tested and mutation-evidenced, scoped suite 77/77, next task unblocked. No re-dispatch.
| derive-the-candidate-set-with-partially-covered-criteria-retained | Large | 1 | DONE | DRIFT | Sonnet | 9m |
- Drift note (task 2): DRIFT was bookkeeping only again — two miscounted greps in the commit
  body's observation-points enumeration, with the underlying properties independently
  confirmed to hold. Every high-risk contract item verified firsthand by the gate: the wrapped
  live-shape fixture, both forged-structure fixtures, both masking-defense re-mutation checks,
  the torn-append mutation, and the item-6 mechanism pin. Scoped suite 97/97. No re-dispatch.
- Recurring pattern across tasks 1 and 2: executors report grep counts in observation-points
  that do not survive a literal re-run. Candidate dispatch lesson for the postmortem.
| wire-partial-coverage-through-the-slice-ritual-end-to-end | Large | 1 | DONE | DRIFT | Sonnet | 8m |
- Drift note (task 3): DRIFT bookkeeping only for the third consecutive task — one grep count
  cited as 9 where a literal re-run gives 10; the other four cited counts reproduce exactly.
  Additivity confirmed (no heading renamed or reordered), no prose-presence tests (every new
  test drives the real gate scripts via subprocess), prose verified accurate against both
  scripts, all 6 items tested and mutation-evidenced. Full craft suite 1443/1 skipped.
- Security-surface accrued (task 3): slice/SKILL.md documents the dual-flag certify invocation
  as two individually quoted shell arguments, never interpolated — test-pinned, not merely
  asserted in prose. Phase 4 security trigger (b) fires on this.
- [x] Phase 1 — test-runner gate: PASS, 6525 passed / 8 skipped / 0 failed (194.5s full repo).
- [x] Phase 2 — simplify: `8b34f97`, DONE_WITH_CONCERNS. Collapsed the duplicated full/partial
  arms in both scripts (one grammar pass + one membership pass at the covers gate;
  insertion-ordered dicts replacing the parallel list/set pairs in the ledger reader). No
  cross-script grammar duplication existed — `candidate_set.py` already imports the parser,
  the masker, and the heading finder from `covers_gate.py`. Fixtures left intact: they share
  boilerplate but each pins a distinct scenario, so collapsing any pair would remove
  discrimination. `slice/SKILL.md` untouched. Both masking defenses re-mutation-checked after
  the ledger-surface edit — fence mask drove 6 tests RED, comment mask 8 RED, each restored to
  an empty diff and green. Footprint guard re-run on a clean tree: exit 0.
- Simplifier flagged, not applied (carried into Phase 3 as review input):
  `parse_covers` hardcodes `--covers` in its ValueError text, so a rejection on the
  `--partial-covers` path misnames the flag to the operator, and no test pins the flag name;
  and the unknown-identifier rejection emits byte-identical stderr for both lists, so an
  operator cannot tell which list was at fault.
- [x] Phase 3 — correctness: round 1 FIX_FIRST (1 Important, 6 Minor); 5 of 7 triaged in and
  fixed at `4eb5df2`; full-suite re-gate PASS 6531/8 skipped. Re-review round (the one
  permitted) returned FIX_FIRST again on fresh, smaller findings — every invariant verified
  held: exit contract, the reason-code set with exactly one addition, both masking defenses
  still discriminated, and partial-subset-of-candidates proven by construction so termination
  cannot fire on half-built work. The repositioned exit-2 paragraph was confirmed genuinely
  intact rather than accidentally re-satisfied.
- Measurement tally (correctness findings, cited to plan section):
  round 1 Important x1 — local-to-one-task, task 1's Delivers (`--partial-covers` certification);
  round 1 Minor x6 — 4 local-to-one-task (tasks 1 and 2 Delivers), 2 cross-task (the step-4
  surfacing requirement spanning task 2's output line and task 3's wiring).
  round 2 Important x3 / Minor x6 — all local-to-one-task, all in test/doc prose rather than
  behaviour. Zero cross-task Criticals across both rounds.
- [x] Phase 3 (cont.) — hygiene pass `1faac4b`: all seven residual review items applied (three
  convention violations, one vacuous assertion, a string-replace hack replaced by the real
  `flag=` parameter with its message now pinned, two prose inaccuracies). Full suite 6532/8.
- [x] Phase 4 — security: TRIGGERED (accrued `Security-surface:` flag from task 3) and run on the
  final form. One Critical, reproduced with a working PoC and independently confirmed: the
  `--title` substitution wrapped untrusted vault prose in DOUBLE quotes while its scrub stripped
  only `'`, newline, backtick and `$` — never `"`, the character that actually breaks out there.
  Pre-existing (identical text at `c3181b79`), but demonstrated and in a file this change edits,
  with the sibling fix precedent inside this very commit range. Fixed at `77255ae` by switching
  to single-quote wrapping, matching `_shared/execute.md`'s existing precedent, pinned by a new
  behavioural test. Everything new to this change held under adversarial probing: CommonMark-only
  line splitting refused a U+2028-hidden heading, fence and comment masking resisted every forgery
  including unclosed comments, duplicate headings failed closed, the dual-flag contract held, and
  the spec-name allow-list is applied at all ~10 substitution sites.
  Not fixed, surfaced as operator decisions: ledger entries carry no provenance check, so a
  well-formed forged entry reads as covered; and the `reason:`-never-persisted invariant holds by
  control-flow shape rather than a code-level control.
- [x] Phase 5 — flow-out: dispatch lesson written
  (`lesson/require-observation-points-counts-to-be-re-run-after-the-final-edit`); seven session
  candidates captured (2 decision, 3 lesson, 2 task) awaiting `/lore:flush`; area profile
  `area/craft-the-dev-ritual-toolkit` updated with the design, the security fix and its miss
  shape, and the two named limits. Credential scrub run over every body written this run — no
  match.
- [x] Flow-out checklist: lessons written; follow-ups filed (as `task` candidates, pending flush);
  decisions captured; session candidates captured and awaiting `/lore:flush`.
- [x] Phase 6 — close: branch `worktree-skill-prose` pushed to origin (new branch, 7 commits);
  final full suite 6535 passed / 8 skipped / 0 failed. Parent carries no `## Enumerated states`
  (the slice touches no visual surface), so the state-coverage gate does not apply.

| Run total | Tasks | Dispatches | Dispatches/task | Wall clock | Lessons | Retrieval |
|---|---|---|---|---|---|---|
| — | 3 | 6 (3 task + 3 fix/hygiene/security) | 1.0 per task | ~72m | 1 written | 20 loaded |
  unstated (raised by: Advocate).
