## A test must execute the thing it tests

Every test runs its subject — calls the function, invokes the CLI, loads the file through its real loader, dispatches the agent — and asserts on what came back; an assertion about source (a symbol is defined, a config key is set, a sentence appears in a markdown file) observes the artifact, not the behaviour, so if nothing was executed before the assertion it is not a test, does not ship, and does not count as coverage.

trailhead core show adr/a-test-executes-its-subject

### A test needs an input it can vary

The subject must have an input that can change its answer, and the test must be one of the points that changes it; a test that restates a constant, snapshot, golden file, inventory, closed vocabulary or sentence, where the unit has only one possible output, guards a decision rather than a behaviour — ask what would have to break for it to go red, and if the answer is that someone edits the same constant or rewords the same sentence, it guards a decision. The one exception is the seam smoke: wiring has no input to vary and is worth exactly one test per seam, never one per constant the seam touches.

`scripts/inert-test-gate` holds both rules mechanically: it flags a test that runs
nothing, and a test whose whole subject is a document it reads and never varies. A
deliberate seam smoke carries `# inert-gate: allow <reason>` on its `def` line, so
the exemptions stay countable and reviewable rather than accumulating unseen.

trailhead core show adr/a-test-needs-an-input-it-can-vary

### The tests worth writing, in the order you should reach for them

Reach for these five first, and they are the floor: the contract exercised end to end through the public entry point; the boundaries that decide something (empty, one, many, either side of a threshold), each its own test; the failure paths, asserting the observable consequence rather than a log line; the regression, pinned by a test that fails against the pre-fix code; and the integration seam, run together with the real adapter where it is cheap.

trailhead core show area/testing

### Presence and absence are not behaviour

Existence is a valid observation only when the test ran the step that produced it (run the installer, then assert the file it wrote, and its contents where you can); never assert that a committed file exists, a symbol is defined or something is callable, and never assert a removal (a file gone, a symbol undefined, a key absent, an endpoint 404) — test the behaviour the removal was for.

trailhead core show adr/presence-and-absence-are-not-behaviour

### Testing a prose artifact — skills, agents, prompts, rulesets

A skill, agent or rule is source, not behaviour: test it only by running it through its consumer (the composer, installer, manifest loader or front-matter parser) and asserting what the consumer produced, or by running the agent against a scenario in a behavioural eval and judging the outcome; asserting that a sentence is present tests only that someone typed it.

trailhead core show area/testing

## Test-driven development — required, not preferred

Write the failing test before the implementation, for every behaviour change, with no "too small to test" carve-out: run it, confirm it fails for its stated reason (not an import error or a typo), write the minimum implementation that passes, then run the tests covering the change's blast radius (the new test, the file's or module's tests, the suites of its callers) and not the whole suite; run the full suite once, at the end, before handing work back, opening a PR or calling the change done, and report the red state. A dispatch to an executor or subagent says explicitly to make the test fail first and to report that failure; a test contract in the task body is not sufficient on its own.

trailhead core show adr/tests-are-written-red-first

### When the code already exists (repair only)

Writing tests after the fact is legitimate only as repair of an already-made mistake, never the plan: derive them from the contract, spec or intended behaviour and never by reading the implementation, mutation-check every test (break the behaviour, confirm the test fails, restore the code exactly, confirm it passes, verify the restore with an empty diff), treat a mutation no test catches as unpinned behaviour and strengthen the test, and name the work as repair when reporting. A mutation check is also the cheapest detector of a test that never executes its subject, since breaking the subject cannot turn it red.

trailhead core show adr/after-the-fact-tests-are-repair-only

### Never

- Weaken, skip, or delete a test to make a suite green.
- Assert a suite passes without having run it.
- Treat "the tests pass" as evidence when the tests were written after the code and
  never mutation-checked.
- Ship an assertion that never ran the code it names.
- Ship an assertion whose subject has only one possible output — a constant, a
  snapshot, an inventory, a sentence in a document — outside a marked seam smoke.
- Keep a test alive because deleting it would lower a coverage number. A test that
  cannot fail for a real reason is not coverage; it is a maintenance cost that
  reads as safety.

## Keeping a test suite fast without weakening it

A slow suite gets run less, and a suite that is run less catches less, so: run the real code in-process where the process boundary is not the subject (one real-process test per seam), and prefer it to a hand-rolled fake, which answers things the real one never does; build shared setup once and copy it per test, with per-test state passed explicitly and no absolute paths, hooks or config the test did not ask for, timing one case against many before cutting case count; use the real disk under the test's temp dir, patching a filesystem call only when no real trigger exists (such as a full disk); wait on an observable, never on a duration; inject a collaborator rather than patch by module path or patch a process-wide function such as `subprocess.run`, adding an injectable seam instead; re-home a slow test to an opt-in tier and never delete it for being slow, saying in the closing line that tiers were left out; add cases to the file that already tests the subject (parametrised, with a mutation check of its subject); and budget the run.

trailhead core show area/testing

## Output wording

This section is for text a person will read in the session. If another agent
dispatched you, or your instructions fix the format of what you return, it does
not apply: write that output exactly as you would without this section. It also
leaves alone any field or line a script reads, plan files, and commit and pull
request text.

When you mention an acceptance criterion to the person reading, give the
criterion's own words, in full or close to it, and put its number after them in
round brackets, as in "Orders over the limit are held for review (AC6)". That
holds for every first mention, a closing summary line included. The sentence
must still make sense with the number deleted, and a short label is not a
substitute for the words. On a later mention, point back in words ("that same
hold for large orders"), with the number still trailing.

When a reply would name more than five acceptance criteria at once — what is
left, what some work covers, a tally — count them by the spec's own sections
instead of listing them. Count a spec with no sections as a whole. Then name the
exceptions, a criterion that does not fit its count (only partly done, say, or
blocked), each by its own words with its number after it, as in "three of five
left in Exporting, none of four in Sharing, and one of those three, exports keep
their original file names, is blocked (AC3)". If the person asked for the
criteria one by one, give them one by one.
