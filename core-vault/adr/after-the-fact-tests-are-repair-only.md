# After-the-fact tests are repair only: post-hoc tests need a mutation check and are reported as repair, not as test-first coverage

After-the-fact tests are repair only. Writing tests once the code exists is legitimate only as repair of an already-made mistake, and every such post-hoc test is mutation-checked before it counts as coverage.

## Context

Test-first is the plan (see [[adr/tests-are-written-red-first]]). Sometimes the mistake has already happened: someone else shipped a change with no tests, or your own change already went out. Writing the tests afterwards is worth doing, but it carries the risks test-first avoids. A test derived by reading the implementation reproduces the implementation's bugs as expectations, and a test that has never been red has never shown it can fail.

Worked example, entirely synthetic. A function `shipping_cost(weight_kg)` shipped untested. The contract says: up to 1 kg costs 5, above 1 kg adds 2 per further kg, rounded up.

Derive the tests from that contract, not from the code:

    def test_shipping_cost_at_the_one_kg_boundary():
        assert shipping_cost(1) == 5
        assert shipping_cost(1.2) == 7
        assert shipping_cost(3) == 9

Then mutation-check the test. Save a copy of the file, change the boundary comparison from "greater than 1" to "greater than or equal to 1", and run the test. It must fail on the first assertion, which proves it binds the boundary. Restore the copy and diff it against the saved one; the diff must be empty. Run the test again and it passes.

Had the mutation left the test green, the behaviour would be unpinned, and the test would be strengthened and checked again before moving on.

## Decision

Post-hoc tests are derived from the contract, spec or intended behaviour, never from reading the implementation.

Each one is mutation-checked: deliberately break the corresponding behaviour, confirm the test fails, restore the code exactly, confirm it passes, and verify the restore with a diff that comes back empty. A mutation that no test catches means the behaviour is unpinned; strengthen the test and check again before moving on. A mutation check is also the cheapest detector for a test that never executes its subject, because such a test cannot be made to fail by breaking the subject.

The report names the work as repair. Post-hoc coverage that has passed a mutation check is worth having, but presenting it as though it were test-first is not.

## Consequences

- Late tests still catch regressions, because they were shown to fail when the behaviour broke.
- The extra mutation step costs time per test; that cost is the price of not shipping green tests that prove nothing.
- Readers of a report can tell repair from test-first work, and weigh the evidence accordingly.
- A test that cannot be made to fail is reworked or dropped, not kept to raise a coverage number.
- It rests on [[adr/a-test-executes-its-subject]]: a mutation check fails to turn red exactly when the test never ran its subject.
- Part of the area profile [[area/testing]].
