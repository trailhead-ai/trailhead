# A test executes its subject: it runs its subject, never inspects source or greps it

A test executes its subject. A test that inspects source (greps a file, checks a symbol is defined, looks for a sentence) passes on broken code; every test must instead run its subject and assert on what came back.

## Context

A test's subject is a behaviour, and a behaviour is only observable by running the code that produces it. An assertion about the source observes the artifact, not the behaviour. It stays green on code that is present and wrong, and goes red on a harmless rename. It is a grep with a green checkmark.

Worked example, entirely synthetic. A function `apply_discount(price, percent)` is meant to return the price after a percentage discount. A bug makes it subtract `percent` as an absolute amount instead.

An inspecting test:

    def test_apply_discount_exists():
        assert "def apply_discount" in read("pricing.py")

This passes with the bug, and passes again after any later regression, because it never calls the function. A running test:

    def test_apply_discount_takes_a_percentage_off():
        assert apply_discount(200, 10) == 180
        assert apply_discount(50, 10) == 45

Against the bug the first assertion returns 190 and fails. The two calls also vary the input, so a version that hardcodes one answer fails the other. Only the running test can go red for a real reason.

## Decision

Every test runs its subject: it calls the function, invokes the command, or loads the file through its real loader, then asserts on what came back. Nothing executed before the assertion means it is not a test and it does not count as coverage.

Presence is fine when the test itself produced it. Run an installer, then assert the file it wrote exists and holds the right contents. Asserting that a committed file exists, or that a prompt contains a sentence, is not.

A test also needs an input it can vary. Where a unit has one possible output there is no behaviour to pin, only a decision to restate. The one exception is a seam smoke: a single test showing that two real pieces are wired together.

## Consequences

- Tests cost more to write and to run than a grep, because they need real inputs and a real consumer.
- A green suite means something: breaking the subject turns a test red, and a mutation check of that break is the quick way to prove it.
- Tests about prose artifacts (skills, rules, prompts) go through their consumer, such as the loader or installer, instead of matching their wording.
- A test that cannot name what would have to break for it to fail should be reworked or dropped rather than kept for coverage.
