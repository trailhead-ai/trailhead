# Tests are written red first: TDD with a failing test first, a blast-radius run, and the full suite once at the end

Tests are written red first. Write the failing test before the implementation, watch it fail for its stated reason, then make it pass. This is test-driven development (TDD) for every behaviour change.

## Context

A test written after the code encodes what the code does rather than what it should do, which locks in the implementation along with its bugs. It also passes on its first run, so it has never shown that it can fail. A green suite proves nothing about tests that were never red.

Running the whole suite after every edit has its own cost: minutes of waiting for a signal a targeted run gives in seconds, and the failure that matters is buried in output about code nobody touched.

Worked example, entirely synthetic. A function `slugify(title)` should lowercase a title and join its words with hyphens. The change adds trimming of leading and trailing spaces.

First write the test:

    def test_slugify_trims_surrounding_spaces():
        assert slugify("  Hello World ") == "hello-world"

Run it. It fails with `AssertionError: '--hello-world-' != 'hello-world'`. That is the stated reason: surrounding spaces become hyphens. Only now add the trim. Run the test again and it passes.

Suppose the first run had instead failed with `NameError: name 'slugify' is not defined`. That is a typo or a missing import, not the behaviour, so the red is not yet evidence of anything.

## Decision

Write the failing test first, and see it fail for its stated reason, not for an import error or a typo, before implementing. Then write the minimum implementation that makes it pass. Report the red state; it is part of the work product.

After each cycle run the tests covering the change's blast radius: the new test, the existing tests of the file or module, and the suites of the callers of what changed. State the blast radius when it is not obvious. If it is unclear, grep the callers to find out; unsureness is the reason to look, not the excuse to run everything.

Run the full suite once, at the end, before handing work back, opening a pull request or calling the change done. Not after every red and green cycle.

Anyone given a slice to build is told to make the test fail first and to report that failure. A task body that carries a test contract is not sufficient on its own.

## Consequences

- Every behaviour change starts with a test that has demonstrably been able to fail.
- Feedback comes in seconds from scoped runs, and the full suite is paid for once per change.
- A caller's suite catches a break the changed file's own tests could not see, because the blast radius includes callers.
- Work that was not done red first is not presented as if it had been; see [[adr/after-the-fact-tests-are-repair-only]] for the one legitimate exception and how it is reported.
- A failing test that fails for the wrong reason is fixed before any implementation is written.
